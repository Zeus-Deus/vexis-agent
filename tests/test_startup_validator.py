"""Day 5 of model UX — brain.kind consistency canary tests.

Pins the "user edited brain.kind and forgot to restart" canary
landed in Day 5:

  - ``check_brain_kind_consistency(on_disk, running)`` returns a
    warning ValidationFinding when they disagree, None when they
    match.
  - ``brain_instance_to_kind(brain)`` maps each concrete brain
    class to its canonical kind string.
  - ``build_resolution_table(..., running_brain_kind=X)`` appends
    the consistency finding to ``global_findings`` when the
    on-disk and running kinds disagree.
  - The dashboard's ``GET /api/v1/models`` payload surfaces the
    finding when ``WebDashboard(running_brain_kind=...)`` was
    initialized with a value.

At daemon startup the two always match by construction (main.py
reads brain.kind and instantiates accordingly), so the canary is
silent on a fresh boot. The tests exercise the warning path by
constructing the disagreement directly.

Design citation: ``.plans/model-management-ux-research.md`` §6
Day 5.
"""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vexis_agent.core import model_discovery as md
from vexis_agent.core.brain.claude_code import ClaudeCodeBrain
from vexis_agent.core.brain.null import BrainNull
from vexis_agent.core.brain.opencode import OpenCodeBrain
from vexis_agent.core.model_validator import (
    ValidationFinding,
    brain_instance_to_kind,
    build_resolution_table,
    check_brain_kind_consistency,
)
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore
from vexis_agent.core.web_server import DashboardConfig, WebDashboard


_TOKEN = "test-token-startup-validator"


# ──────────────────────────────────────────────────────────────────
# brain_instance_to_kind — class→kind mapping
# ──────────────────────────────────────────────────────────────────


def test_brain_instance_to_kind_claude_code(tmp_path: Path):
    sess = SessionStore(tmp_path / "s.json")
    brain = ClaudeCodeBrain(
        workspace=tmp_path, session=sess, running_tasks=RunningTasks(),
    )
    assert brain_instance_to_kind(brain) == "claude-code"


def test_brain_instance_to_kind_opencode(tmp_path: Path):
    sess = SessionStore(tmp_path / "s.json")
    brain = OpenCodeBrain(
        workspace=tmp_path, session=sess, running_tasks=RunningTasks(),
    )
    assert brain_instance_to_kind(brain) == "opencode"


def test_brain_instance_to_kind_null():
    brain = BrainNull()
    assert brain_instance_to_kind(brain) == "null"


def test_brain_instance_to_kind_unknown_class():
    """Defensive: any non-registered class returns ``"<unknown>"``
    rather than raising. Insulates the canary against a future
    brain implementation that hasn't been added to the mapping."""
    class _MysteryBrain:
        pass
    assert brain_instance_to_kind(_MysteryBrain()) == "<unknown>"


# ──────────────────────────────────────────────────────────────────
# check_brain_kind_consistency — pure helper
# ──────────────────────────────────────────────────────────────────


def test_check_returns_none_on_match():
    """The startup case: brain.kind=X is what main.py read, brain
    instance is X. Canary silent."""
    assert check_brain_kind_consistency("claude-code", "claude-code") is None
    assert check_brain_kind_consistency("opencode", "opencode") is None
    assert check_brain_kind_consistency("null", "null") is None


def test_check_returns_warning_finding_on_mismatch():
    """The canary case: user edited brain.kind from claude-code →
    opencode without restarting. Daemon is running ClaudeCodeBrain
    but the disk says opencode."""
    finding = check_brain_kind_consistency("opencode", "claude-code")
    assert isinstance(finding, ValidationFinding)
    assert finding.severity == "warning"
    assert finding.subsystem is None
    # Both kinds appear in the problem text so the user sees what
    # they're switching from + to.
    assert "opencode" in finding.problem
    assert "claude-code" in finding.problem
    # Suggested fix is the literal restart instruction.
    assert "Restart vexis" in finding.suggested_fix


def test_check_severity_is_warning_not_error():
    """Pin: severity is warning, matching the daemon's existing
    fall-back-to-default posture for brain.kind issues. The user
    can keep using the running brain; the new value just hasn't
    activated yet."""
    finding = check_brain_kind_consistency("opencode", "claude-code")
    assert finding is not None
    assert finding.severity == "warning"


def test_check_unknown_running_kind_still_compares():
    """If the brain class isn't in our mapping (future brain
    implementation), the canary still compares the strings —
    "<unknown>" vs "claude-code" disagree, so warning fires.
    Defensive behaviour — better to over-warn than miss the
    canary."""
    finding = check_brain_kind_consistency("claude-code", "<unknown>")
    assert finding is not None
    assert finding.severity == "warning"


# ──────────────────────────────────────────────────────────────────
# build_resolution_table — canary integration
# ──────────────────────────────────────────────────────────────────


def test_build_resolution_table_no_canary_when_running_kind_omitted():
    """Backwards-compat: callers that don't pass running_brain_kind
    don't see the canary in their global_findings. (Existing test
    fixtures and the slash command's pre-Day-5 path didn't pass
    it.)"""
    table = build_resolution_table({}, "claude-code")
    # No consistency finding in global_findings.
    assert not any(
        "running brain class" in f["problem"]
        for f in table["global_findings"]
    )


def test_build_resolution_table_no_canary_when_kinds_match():
    table = build_resolution_table(
        {}, "claude-code", running_brain_kind="claude-code",
    )
    assert not any(
        "running brain class" in f["problem"]
        for f in table["global_findings"]
    )


def test_build_resolution_table_appends_canary_on_mismatch():
    """Day 5 wiring: when running_brain_kind is supplied AND
    differs from the on-disk brain_kind argument, the canary
    finding appears in global_findings."""
    table = build_resolution_table(
        {"brain": {"kind": "opencode"}},
        "opencode",
        running_brain_kind="claude-code",
    )
    canary_findings = [
        f for f in table["global_findings"]
        if "running brain class" in f["problem"]
    ]
    assert len(canary_findings) == 1
    assert canary_findings[0]["severity"] == "warning"
    assert canary_findings[0]["subsystem"] is None
    assert "Restart vexis" in canary_findings[0]["suggested_fix"]


# ──────────────────────────────────────────────────────────────────
# Dashboard payload integration
# ──────────────────────────────────────────────────────────────────


class _FakeSessions:
    def get(self) -> str:
        return "test-sess"


def _build_dashboard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    running_brain_kind: str | None,
) -> WebDashboard:
    """Same construction trick as test_models_api.py with the
    Day 5 ``running_brain_kind`` parameter."""
    cfg_dir = tmp_path / "vexis"
    cfg_dir.mkdir(exist_ok=True)
    monkeypatch.setattr("vexis_agent.core.paths.vexis_dir", lambda: cfg_dir)
    monkeypatch.setattr("vexis_agent.core.yaml_config.vexis_dir", lambda: cfg_dir)
    monkeypatch.setattr(
        "vexis_agent.core.yaml_config._config_path", lambda: cfg_dir / "config.yaml",
    )

    dashboard = WebDashboard.__new__(WebDashboard)
    dashboard._workspace = tmp_path  # type: ignore[attr-defined]
    dashboard._token = _TOKEN  # type: ignore[attr-defined]
    dashboard._learning = None  # type: ignore[attr-defined]
    dashboard._relationships_mutation_window_seconds = 600  # type: ignore[attr-defined]
    dashboard._relationships_mutation_limit = 100  # type: ignore[attr-defined]
    dashboard._relationships_mutation_log = defaultdict(deque)  # type: ignore[attr-defined]
    dashboard._config = DashboardConfig(  # type: ignore[attr-defined]
        host="127.0.0.1", port=0,
        web_dist=tmp_path / "no-frontend", manage_tailscale=False,
    )
    dashboard._sessions = _FakeSessions()  # type: ignore[attr-defined]
    dashboard._running_tasks = RunningTasks()  # type: ignore[attr-defined]
    dashboard._background_tasks = None  # type: ignore[attr-defined]
    dashboard._curator = None  # type: ignore[attr-defined]
    dashboard._browser = None  # type: ignore[attr-defined]
    dashboard._started_at = None  # type: ignore[attr-defined]
    dashboard._tailscale_url = None  # type: ignore[attr-defined]
    dashboard._tailscale_dns = None  # type: ignore[attr-defined]
    dashboard._server = None  # type: ignore[attr-defined]
    dashboard._serve_task = None  # type: ignore[attr-defined]
    dashboard._profile_size_cache = None  # type: ignore[attr-defined]
    dashboard._running_brain_kind = running_brain_kind  # type: ignore[attr-defined]
    dashboard._app = dashboard._build_app()  # type: ignore[attr-defined]
    return dashboard


@pytest.fixture(autouse=True)
def _clear_discovery_cache():
    md.invalidate_discovery_cache()
    yield
    md.invalidate_discovery_cache()


def _hdr() -> dict:
    return {"Authorization": f"Bearer {_TOKEN}"}


def _seed_config(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def test_dashboard_payload_silent_when_kinds_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Startup-equivalent: brain.kind and running brain agree.
    No canary finding in the payload."""
    _seed_config(
        tmp_path / "vexis" / "config.yaml", "brain:\n  kind: claude-code\n",
    )
    dash = _build_dashboard(
        tmp_path, monkeypatch, running_brain_kind="claude-code",
    )
    client = TestClient(dash._app)
    r = client.get("/api/v1/models", headers=_hdr())
    assert r.status_code == 200
    data = r.json()
    assert not any(
        "running brain class" in f["problem"]
        for f in data["global_findings"]
    )


def test_dashboard_payload_canary_fires_on_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """The post-edit case: dashboard was constructed with
    running_brain_kind='claude-code' (from main.py at startup),
    but the user has since edited brain.kind to opencode without
    restarting. Dashboard poll surfaces the canary so the user
    sees the staleness."""
    _seed_config(
        tmp_path / "vexis" / "config.yaml", "brain:\n  kind: opencode\n",
    )
    dash = _build_dashboard(
        tmp_path, monkeypatch, running_brain_kind="claude-code",
    )
    client = TestClient(dash._app)
    r = client.get("/api/v1/models", headers=_hdr())
    assert r.status_code == 200
    data = r.json()
    canary = [
        f for f in data["global_findings"]
        if "running brain class" in f["problem"]
    ]
    assert len(canary) == 1
    assert canary[0]["severity"] == "warning"
    assert "Restart vexis" in canary[0]["suggested_fix"]


def test_dashboard_payload_no_canary_when_running_kind_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Backwards compat: dashboards constructed without
    running_brain_kind (e.g. existing test fixtures) don't get
    a spurious canary finding even if the on-disk brain.kind
    doesn't match... anything in particular. Pin the silent
    fallback."""
    _seed_config(
        tmp_path / "vexis" / "config.yaml", "brain:\n  kind: opencode\n",
    )
    dash = _build_dashboard(
        tmp_path, monkeypatch, running_brain_kind=None,
    )
    client = TestClient(dash._app)
    r = client.get("/api/v1/models", headers=_hdr())
    data = r.json()
    assert not any(
        "running brain class" in f["problem"]
        for f in data["global_findings"]
    )


# ──────────────────────────────────────────────────────────────────
# Slash command integration — _model_status_text surfaces canary
# ──────────────────────────────────────────────────────────────────


def test_slash_status_surfaces_canary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """The slash command's _model_status_text pulls the running
    brain kind via brain_instance_to_kind(handler._brain) and
    passes it through build_resolution_table. Pin: an opencode-
    on-disk + claude-code-running scenario surfaces the canary
    in the rendered text."""
    cfg_dir = tmp_path / "vexis"
    cfg_dir.mkdir()
    monkeypatch.setattr("vexis_agent.core.paths.vexis_dir", lambda: cfg_dir)
    monkeypatch.setattr("vexis_agent.core.yaml_config.vexis_dir", lambda: cfg_dir)
    monkeypatch.setattr(
        "vexis_agent.core.yaml_config._config_path", lambda: cfg_dir / "config.yaml",
    )
    _seed_config(
        cfg_dir / "config.yaml", "brain:\n  kind: opencode\n",
    )

    # Construct a fake handler with a real ClaudeCodeBrain so the
    # canary fires (running=claude-code, on-disk=opencode).
    sess = SessionStore(tmp_path / "sessions.json")
    brain = ClaudeCodeBrain(
        workspace=tmp_path, session=sess, running_tasks=RunningTasks(),
    )

    class _FakeHandler:
        def __init__(self, brain):
            self._brain = brain

    from vexis_agent.transports.telegram import TelegramTransport
    transport = TelegramTransport.__new__(TelegramTransport)
    transport._handler = _FakeHandler(brain)  # type: ignore[attr-defined]
    transport._allowed_user_id = 1  # type: ignore[attr-defined]
    transport._running_tasks = RunningTasks()  # type: ignore[attr-defined]

    text = transport._model_status_text()
    # Canary text appears in the validator section.
    assert "Restart vexis" in text or "running brain class" in text


def test_slash_status_silent_when_brain_matches_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Match case: on-disk brain.kind == running brain class.
    Slash text doesn't surface the canary."""
    cfg_dir = tmp_path / "vexis"
    cfg_dir.mkdir()
    monkeypatch.setattr("vexis_agent.core.paths.vexis_dir", lambda: cfg_dir)
    monkeypatch.setattr("vexis_agent.core.yaml_config.vexis_dir", lambda: cfg_dir)
    monkeypatch.setattr(
        "vexis_agent.core.yaml_config._config_path", lambda: cfg_dir / "config.yaml",
    )
    _seed_config(
        cfg_dir / "config.yaml", "brain:\n  kind: claude-code\n",
    )

    sess = SessionStore(tmp_path / "sessions.json")
    brain = ClaudeCodeBrain(
        workspace=tmp_path, session=sess, running_tasks=RunningTasks(),
    )

    class _FakeHandler:
        def __init__(self, brain):
            self._brain = brain

    from vexis_agent.transports.telegram import TelegramTransport
    transport = TelegramTransport.__new__(TelegramTransport)
    transport._handler = _FakeHandler(brain)  # type: ignore[attr-defined]
    transport._allowed_user_id = 1  # type: ignore[attr-defined]
    transport._running_tasks = RunningTasks()  # type: ignore[attr-defined]

    text = transport._model_status_text()
    assert "Restart vexis" not in text
    assert "running brain class" not in text

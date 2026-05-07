"""Day 4 of model UX — POST endpoints + discovery refresh.

Tests the four mutation/refresh endpoints landed in Day 4:
- POST /api/v1/models/set
- POST /api/v1/models/reset
- POST /api/v1/models/brain
- POST /api/v1/models/discovery/refresh

Plus the Day 4 additions to GET /api/v1/models:
- has_comments flag
- available_models per brain
- model_ux_enabled flag

Construction trick mirrors tests/test_models_api.py (which mirrors
test_dashboard_goals_endpoints.py) — bypass daemon wiring, build
the FastAPI app directly.

Design citation: ``.plans/model-management-ux-research.md`` §6 Day 4.
"""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient

from core import model_discovery as md
from core.running_tasks import RunningTasks
from core.web_server import DashboardConfig, WebDashboard


_TOKEN = "test-token-models-set-cafef00d"


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


class _FakeSessions:
    def get(self) -> str:
        return "test-sess"


def _build_dashboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> WebDashboard:
    cfg_dir = tmp_path / "vexis"
    cfg_dir.mkdir()
    monkeypatch.setattr("core.paths.vexis_dir", lambda: cfg_dir)
    monkeypatch.setattr("core.yaml_config.vexis_dir", lambda: cfg_dir)
    monkeypatch.setattr(
        "core.yaml_config._config_path", lambda: cfg_dir / "config.yaml",
    )
    # Force model_ux_enabled True for these mutation tests; the
    # disabled-flag short-circuit gets its own test below.
    monkeypatch.setattr(
        "core.yaml_config.model_ux_enabled", lambda: True,
    )

    dashboard = WebDashboard.__new__(WebDashboard)
    dashboard._workspace = tmp_path  # type: ignore[attr-defined]
    dashboard._token = _TOKEN  # type: ignore[attr-defined]
    dashboard._learning = None  # type: ignore[attr-defined]
    dashboard._relationships_mutation_window_seconds = 600  # type: ignore[attr-defined]
    dashboard._relationships_mutation_limit = 100  # type: ignore[attr-defined]
    dashboard._relationships_mutation_log = defaultdict(deque)  # type: ignore[attr-defined]
    dashboard._config = DashboardConfig(  # type: ignore[attr-defined]
        host="127.0.0.1",
        port=0,
        web_dist=tmp_path / "no-frontend",
        manage_tailscale=False,
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
    dashboard._app = dashboard._build_app()  # type: ignore[attr-defined]
    return dashboard


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    return tmp_path / "vexis" / "config.yaml"


@pytest.fixture
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    return TestClient(_build_dashboard(tmp_path, monkeypatch)._app)


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


# ──────────────────────────────────────────────────────────────────
# Auth + flag-gate
# ──────────────────────────────────────────────────────────────────


def test_post_set_requires_token(client: TestClient):
    r = client.post("/api/v1/models/set", json={"subsystem": "curator", "value": "small"})
    assert r.status_code == 401


def test_post_set_disabled_when_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Explicitly setting model_ux_enabled=False refuses
    mutations with 403. Day 5 default-flipped to True; this test
    covers users who keep the explicit opt-out."""
    cfg_dir = tmp_path / "vexis"
    cfg_dir.mkdir()
    monkeypatch.setattr("core.paths.vexis_dir", lambda: cfg_dir)
    monkeypatch.setattr("core.yaml_config.vexis_dir", lambda: cfg_dir)
    monkeypatch.setattr(
        "core.yaml_config._config_path", lambda: cfg_dir / "config.yaml",
    )
    # Force model_ux_enabled False explicitly. Day 5 default
    # flipped to True so this test pins the explicit opt-out.
    monkeypatch.setattr(
        "core.yaml_config.model_ux_enabled", lambda: False,
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
    dashboard._app = dashboard._build_app()  # type: ignore[attr-defined]
    c = TestClient(dashboard._app)
    r = c.post(
        "/api/v1/models/set",
        json={"subsystem": "curator", "value": "small"},
        headers=_hdr(),
    )
    assert r.status_code == 403
    assert "model_ux.enabled" in r.json()["detail"]


# ──────────────────────────────────────────────────────────────────
# POST /api/v1/models/set
# ──────────────────────────────────────────────────────────────────


def test_post_set_writes_subsystem(client: TestClient, cfg_path: Path):
    r = client.post(
        "/api/v1/models/set",
        json={"subsystem": "goal_judge", "value": "large"},
        headers=_hdr(),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["subsystem"] == "goal_judge"
    assert body["value"] == "large"
    assert body["resolved_tier"] == "large"
    assert body["resolved_model_id"] == "sonnet"

    parsed = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert parsed["models"]["subsystems"]["goal_judge"] == "large"


def test_post_set_unknown_subsystem_400(client: TestClient):
    r = client.post(
        "/api/v1/models/set",
        json={"subsystem": "made_up_thing", "value": "small"},
        headers=_hdr(),
    )
    assert r.status_code == 400
    assert "unknown subsystem" in r.json()["detail"]


def test_post_set_validator_refuses_with_suggested_fix(
    client: TestClient, cfg_path: Path,
):
    """User on opencode tries a bare alias — rule 4 fires error;
    endpoint refuses with the suggested_fix copy in the detail
    body. Same vocabulary the slash + the spawn-site backstop
    use; pinned by the cross-surface contract."""
    _seed_config(cfg_path, "brain:\n  kind: opencode\n")
    r = client.post(
        "/api/v1/models/set",
        json={"subsystem": "goal_judge", "value": "sonnet"},
        headers=_hdr(),
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "goal_judge" in detail
    assert "bare alias" in detail
    # No write — config.yaml stays as the seed.
    parsed = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert "subsystems" not in (parsed.get("models") or {})


def test_post_set_payload_validation(client: TestClient):
    """Missing fields → 400 with a clear message."""
    r = client.post(
        "/api/v1/models/set",
        json={"subsystem": "curator"},  # missing value
        headers=_hdr(),
    )
    assert r.status_code == 400


def test_post_set_runs_comment_backup_when_present(
    client: TestClient, cfg_path: Path,
):
    """Same comment-presence-gated backup as the slash. The
    response body includes the backup_path so the dashboard can
    surface the toast inline."""
    _seed_config(
        cfg_path,
        "# learning curator notes\n"
        "models:\n  learning_review: sonnet\n",
    )
    r = client.post(
        "/api/v1/models/set",
        json={"subsystem": "goal_judge", "value": "large"},
        headers=_hdr(),
    )
    assert r.status_code == 200
    bak = cfg_path.with_suffix(".yaml.bak")
    assert bak.is_file()
    assert "# learning curator notes" in bak.read_text(encoding="utf-8")
    assert r.json()["backup_path"] == str(bak)


def test_post_set_backup_skipped_when_no_comments(
    client: TestClient, cfg_path: Path,
):
    _seed_config(
        cfg_path, "models:\n  learning_review: sonnet\n",
    )
    r = client.post(
        "/api/v1/models/set",
        json={"subsystem": "goal_judge", "value": "large"},
        headers=_hdr(),
    )
    assert r.status_code == 200
    assert r.json()["backup_path"] is None


# ──────────────────────────────────────────────────────────────────
# POST /api/v1/models/reset
# ──────────────────────────────────────────────────────────────────


def test_post_reset_all(client: TestClient, cfg_path: Path):
    _seed_config(
        cfg_path,
        "models:\n"
        "  learning_review: sonnet\n"
        "  subsystems:\n"
        "    curator: small\n"
        "    goal_judge: large\n"
        "  tiers:\n"
        "    opencode:\n"
        "      large: openai/gpt-4o\n",
    )
    r = client.post(
        "/api/v1/models/reset", json={}, headers=_hdr(),
    )
    assert r.status_code == 200
    assert r.json()["scope"] == "all subsystems"
    parsed = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    models = parsed.get("models") or {}
    assert "subsystems" not in models
    assert "learning_review" not in models
    # tier override preserved.
    assert models["tiers"]["opencode"]["large"] == "openai/gpt-4o"


def test_post_reset_one_subsystem(
    client: TestClient, cfg_path: Path,
):
    _seed_config(
        cfg_path,
        "models:\n"
        "  subsystems:\n"
        "    curator: small\n"
        "    goal_judge: large\n",
    )
    r = client.post(
        "/api/v1/models/reset",
        json={"subsystem": "curator"},
        headers=_hdr(),
    )
    assert r.status_code == 200
    assert r.json()["scope"] == "curator"
    parsed = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert parsed["models"]["subsystems"] == {"goal_judge": "large"}


def test_post_reset_unknown_subsystem_400(client: TestClient):
    r = client.post(
        "/api/v1/models/reset",
        json={"subsystem": "made_up"},
        headers=_hdr(),
    )
    assert r.status_code == 400


# ──────────────────────────────────────────────────────────────────
# POST /api/v1/models/brain
# ──────────────────────────────────────────────────────────────────


def test_post_brain_writes_kind_and_returns_restart_required(
    client: TestClient, cfg_path: Path,
):
    r = client.post(
        "/api/v1/models/brain",
        json={"kind": "opencode"},
        headers=_hdr(),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "opencode"
    assert body["restart_required"] is True
    parsed = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert parsed["brain"]["kind"] == "opencode"


def test_post_brain_invalid_kind_400(client: TestClient):
    """Policy refusal on typo — slash behavior parity."""
    r = client.post(
        "/api/v1/models/brain",
        json={"kind": "claudecode"},  # missing dash
        headers=_hdr(),
    )
    assert r.status_code == 400
    assert "claudecode" in r.json()["detail"]


def test_post_brain_preview_warnings_surfaced(
    client: TestClient, cfg_path: Path,
):
    """Switch from claude-code → opencode with legacy raw-string
    keys present. The preview-mode validator runs against the
    new brain; rule 4 errors fire as warnings in the response
    body. The endpoint still WRITES (not refused — user opted
    in) so the user can restart and fix the rest."""
    _seed_config(
        cfg_path, "models:\n  learning_review: sonnet\n",
    )
    r = client.post(
        "/api/v1/models/brain",
        json={"kind": "opencode"},
        headers=_hdr(),
    )
    assert r.status_code == 200
    warnings = r.json()["warnings"]
    # rule 4 surfaces as error-severity in the warnings list.
    assert any(
        w["severity"] == "error" and w["subsystem"] == "learning_review"
        for w in warnings
    )
    # Write still happened.
    parsed = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert parsed["brain"]["kind"] == "opencode"


def test_post_brain_runs_comment_backup(
    client: TestClient, cfg_path: Path,
):
    _seed_config(
        cfg_path, "# notes\nbrain:\n  kind: claude-code\n",
    )
    r = client.post(
        "/api/v1/models/brain",
        json={"kind": "opencode"},
        headers=_hdr(),
    )
    assert r.status_code == 200
    bak = cfg_path.with_suffix(".yaml.bak")
    assert bak.is_file()


# ──────────────────────────────────────────────────────────────────
# POST /api/v1/models/discovery/refresh
# ──────────────────────────────────────────────────────────────────


def test_post_discovery_refresh_busts_cache_and_returns_lists(
    client: TestClient,
):
    fake_stdout = "anthropic/x\nopenai/y\n"
    with patch(
        "subprocess.run",
        return_value=type("CP", (), {
            "stdout": fake_stdout, "stderr": "", "returncode": 0,
        })(),
    ):
        r = client.post(
            "/api/v1/models/discovery/refresh", headers=_hdr(),
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "claude-code" in body["available_models"]
    assert "opencode" in body["available_models"]
    # claude-code is hardcoded; check for haiku.
    assert "haiku" in body["available_models"]["claude-code"]
    # opencode list comes from our mocked subprocess.
    assert "anthropic/x" in body["available_models"]["opencode"]


def test_post_discovery_refresh_not_flag_gated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """Refresh works even when model_ux is off — discovery is
    read-only and useful for inspecting what's available before
    flipping the flag."""
    cfg_dir = tmp_path / "vexis"
    cfg_dir.mkdir()
    monkeypatch.setattr("core.paths.vexis_dir", lambda: cfg_dir)
    monkeypatch.setattr("core.yaml_config.vexis_dir", lambda: cfg_dir)
    monkeypatch.setattr(
        "core.yaml_config._config_path", lambda: cfg_dir / "config.yaml",
    )
    # Force model_ux_enabled False explicitly so this test
    # exercises the "flag off" case post-Day-5 default flip.
    monkeypatch.setattr(
        "core.yaml_config.model_ux_enabled", lambda: False,
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
    dashboard._app = dashboard._build_app()  # type: ignore[attr-defined]
    c = TestClient(dashboard._app)

    md.invalidate_discovery_cache()
    with patch(
        "subprocess.run",
        return_value=type("CP", (), {
            "stdout": "x\n", "stderr": "", "returncode": 0,
        })(),
    ):
        r = c.post("/api/v1/models/discovery/refresh", headers=_hdr())
    assert r.status_code == 200


# ──────────────────────────────────────────────────────────────────
# Day 4 additions to GET /api/v1/models
# ──────────────────────────────────────────────────────────────────


def test_get_models_payload_includes_has_comments_flag(
    client: TestClient, cfg_path: Path,
):
    _seed_config(
        cfg_path,
        "# user notes here\nmodels:\n  brain: default\n",
    )
    r = client.get("/api/v1/models", headers=_hdr())
    assert r.status_code == 200
    body = r.json()
    assert body["has_comments"] is True


def test_get_models_payload_has_comments_false_without_comments(
    client: TestClient, cfg_path: Path,
):
    _seed_config(cfg_path, "models:\n  brain: default\n")
    r = client.get("/api/v1/models", headers=_hdr())
    assert r.json()["has_comments"] is False


def test_get_models_payload_includes_available_models(
    client: TestClient,
):
    """Day 4: discovery results inlined in the GET payload so the
    dashboard's dropdown doesn't need a separate request per row."""
    md.invalidate_discovery_cache()
    with patch(
        "subprocess.run",
        return_value=type("CP", (), {
            "stdout": "a/b\n", "stderr": "", "returncode": 0,
        })(),
    ):
        r = client.get("/api/v1/models", headers=_hdr())
    body = r.json()
    assert "available_models" in body
    assert "claude-code" in body["available_models"]
    assert "haiku" in body["available_models"]["claude-code"]
    assert "opencode" in body["available_models"]
    assert "a/b" in body["available_models"]["opencode"]


def test_get_models_payload_includes_model_ux_enabled_flag(
    client: TestClient,
):
    r = client.get("/api/v1/models", headers=_hdr())
    body = r.json()
    assert body["model_ux_enabled"] is True

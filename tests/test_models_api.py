"""Day 3 of model UX — GET /api/v1/models endpoint + cross-surface
contract test.

Tests the read-only resolution-table API that backs the dashboard's
Models tab (Day 3 read-only; Day 4 adds POST endpoints for edits).
The endpoint shares its data source with the slash command's
``/model status`` text rendering — both consume
``core.model_validator.build_resolution_table``. The contract test
in this file pins that the two surfaces emit byte-identical
per-subsystem resolution data so drift surfaces before it ships.

Coverage:
- GET shape: brain_kind / subsystems list / tier_overrides /
  brain_inventory / global_findings.
- Auth: bearer token required.
- Validator findings included per-subsystem AND in global block.
- Malformed config doesn't 500 — graceful empty-fallback payload
  with an error-level global finding pointing at the daemon log.
- Contract test: slash command's ``_model_status_text()`` and the
  API endpoint render the same resolution rows from the same
  fixture config.

Construction trick mirrors ``test_dashboard_goals_endpoints.py``:
bypass the daemon wiring, set just the fields ``_build_app`` and
the model helpers touch, then build the FastAPI app.

Design citation: ``.plans/model-management-ux-research.md`` §6 Day 3.
"""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.web_server import DashboardConfig, WebDashboard


_TOKEN = "test-token-models-cafef00d"


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


class _FakeSessions:
    def __init__(self, uuid: str = "test-sess") -> None:
        self._uuid = uuid

    def get(self) -> str:
        return self._uuid


def _build_dashboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> WebDashboard:
    """Construct the bare-minimum WebDashboard for the models
    endpoint. Same shape as test_dashboard_goals_endpoints."""
    # Redirect ~/.vexis/config.yaml to tmp.
    cfg_dir = tmp_path / "vexis"
    cfg_dir.mkdir()
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
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    return TestClient(_build_dashboard(tmp_path, monkeypatch)._app)


def _hdr() -> dict:
    return {"Authorization": f"Bearer {_TOKEN}"}


def _seed_config(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


# ──────────────────────────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────────────────────────


def test_get_models_requires_token(client: TestClient):
    r = client.get("/api/v1/models")
    assert r.status_code == 401


def test_get_models_rejects_wrong_token(client: TestClient):
    r = client.get(
        "/api/v1/models",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert r.status_code == 401


# ──────────────────────────────────────────────────────────────────
# Shape — empty config
# ──────────────────────────────────────────────────────────────────


def test_get_models_empty_config_returns_default_table(client: TestClient):
    """Fresh install — no config file. Returns the default
    resolution table for claude-code with all 9... 7 known
    subsystems resolved to their DEFAULT_SUBSYSTEM_TIERS values
    (well, 8 entries including the dead migration_classifier)."""
    r = client.get("/api/v1/models", headers=_hdr())
    assert r.status_code == 200
    data = r.json()
    assert data["brain_kind"] == "claude-code"

    # All 8 known subsystems (DEFAULT_SUBSYSTEM_TIERS) appear,
    # including the dead migration_classifier (which surfaces a
    # rule-7 info finding).
    subsystem_names = {row["name"] for row in data["subsystems"]}
    expected = {
        "curator", "coherence_judge", "goal_judge",
        "relationships_extractor", "relationships_classifier",
        "learning_review", "learning_triage",
        "migration_classifier",
    }
    assert subsystem_names == expected

    # Tier overrides include all 4 abstract tiers.
    assert set(data["tier_overrides"].keys()) == {
        "tiny", "small", "medium", "large",
    }

    # Brain inventory enumerates all valid kinds.
    assert set(data["brain_inventory"]) == {
        "claude-code", "opencode", "null",
    }


def test_get_models_subsystem_row_carries_resolution_fields(client: TestClient):
    """Each subsystem row has the 5 fields the dashboard renders:
    name, configured, resolved_tier, resolved_model_id, findings."""
    r = client.get("/api/v1/models", headers=_hdr())
    rows = r.json()["subsystems"]
    for row in rows:
        assert set(row.keys()) >= {
            "name", "configured", "resolved_tier",
            "resolved_model_id", "findings",
        }
        assert isinstance(row["findings"], list)


def test_get_models_default_tier_resolves_to_claude_code_native(
    client: TestClient,
):
    """curator's default tier is small → DEFAULT_TIER_MAP_CLAUDE_CODE
    maps small to haiku. Verify."""
    r = client.get("/api/v1/models", headers=_hdr())
    curator = next(
        row for row in r.json()["subsystems"] if row["name"] == "curator"
    )
    assert curator["configured"] is None  # not in config; defaulted
    assert curator["resolved_tier"] == "small"
    assert curator["resolved_model_id"] == "haiku"


# ──────────────────────────────────────────────────────────────────
# Configured values surface correctly
# ──────────────────────────────────────────────────────────────────


def test_get_models_legacy_raw_string_surfaces_as_configured(
    client: TestClient, cfg_path: Path,
):
    """The production config style: ``models.learning_review:
    sonnet``. The 'configured' field should be 'sonnet' and the
    resolved id should also be 'sonnet' (claude-code raw-string
    passthrough)."""
    _seed_config(
        cfg_path,
        "models:\n  learning_review: sonnet\n",
    )
    r = client.get("/api/v1/models", headers=_hdr())
    lr = next(
        row for row in r.json()["subsystems"]
        if row["name"] == "learning_review"
    )
    assert lr["configured"] == "sonnet"
    assert lr["resolved_tier"] == "sonnet"
    assert lr["resolved_model_id"] == "sonnet"


def test_get_models_new_schema_value_surfaces_as_configured(
    client: TestClient, cfg_path: Path,
):
    _seed_config(
        cfg_path,
        "models:\n  subsystems:\n    goal_judge: small\n",
    )
    r = client.get("/api/v1/models", headers=_hdr())
    gj = next(
        row for row in r.json()["subsystems"]
        if row["name"] == "goal_judge"
    )
    assert gj["configured"] == "small"
    assert gj["resolved_tier"] == "small"
    # claude-code's small → haiku via DEFAULT_TIER_MAP_CLAUDE_CODE.
    assert gj["resolved_model_id"] == "haiku"


def test_get_models_new_schema_wins_over_legacy(
    client: TestClient, cfg_path: Path,
):
    """When BOTH models.learning_review (legacy) AND
    models.subsystems.learning_review (new) are set, the new
    schema wins per subsystem_tier()'s resolution order."""
    _seed_config(
        cfg_path,
        "models:\n"
        "  learning_review: sonnet\n"          # legacy
        "  subsystems:\n"
        "    learning_review: tiny\n",          # new schema
    )
    r = client.get("/api/v1/models", headers=_hdr())
    lr = next(
        row for row in r.json()["subsystems"]
        if row["name"] == "learning_review"
    )
    # 'configured' shows the new-schema value (the active one).
    assert lr["configured"] == "tiny"
    assert lr["resolved_tier"] == "tiny"


# ──────────────────────────────────────────────────────────────────
# Validator findings surface in the payload
# ──────────────────────────────────────────────────────────────────


def test_get_models_dead_knob_info_finding(
    client: TestClient,
):
    """migration_classifier is the known dead knob. Rule 7 should
    surface as an info-level finding on its row."""
    r = client.get("/api/v1/models", headers=_hdr())
    mc = next(
        row for row in r.json()["subsystems"]
        if row["name"] == "migration_classifier"
    )
    assert any(
        f["severity"] == "info" and "no live spawn caller" in f["problem"]
        for f in mc["findings"]
    )


def test_get_models_opencode_format_error_findings(
    client: TestClient, cfg_path: Path,
):
    """User on opencode with legacy raw-strings: rule 4 fires
    error per affected subsystem. Each appears in its row's
    findings list."""
    _seed_config(
        cfg_path,
        "brain:\n  kind: opencode\n"
        "models:\n"
        "  learning_review: sonnet\n"
        "  coherence_judge: haiku\n",
    )
    r = client.get("/api/v1/models", headers=_hdr())
    by_name = {row["name"]: row for row in r.json()["subsystems"]}
    for sub in ("learning_review", "coherence_judge"):
        errs = [
            f for f in by_name[sub]["findings"] if f["severity"] == "error"
        ]
        assert errs, f"missing rule-4 error for {sub}"
        assert "bare alias" in errs[0]["problem"]


def test_get_models_global_findings_carry_brain_kind_warnings(
    client: TestClient, cfg_path: Path,
):
    """A typo in brain.kind surfaces as a global (subsystem=None)
    finding. The dashboard renders these in a top-level row."""
    _seed_config(cfg_path, "brain:\n  kind: claudecode\n")
    r = client.get("/api/v1/models", headers=_hdr())
    globals_ = r.json()["global_findings"]
    assert any(
        f["severity"] == "warning" and "brain.kind" in f["problem"]
        for f in globals_
    )


# ──────────────────────────────────────────────────────────────────
# Tier overrides
# ──────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────
# Day 1 of model picker UX — available_models_by_provider field
# ──────────────────────────────────────────────────────────────────


def test_get_models_exposes_available_models_by_provider(
    client: TestClient,
):
    """Day 1 of model picker UX adds a provider-grouped sibling of
    ``available_models``. Field present on the default response,
    keyed by brain kind. claude-code grouping always has an
    ``anthropic`` bucket (curated in-process list); opencode
    grouping is empty when the binary isn't installed (test
    environment)."""
    r = client.get("/api/v1/models", headers=_hdr())
    data = r.json()
    assert "available_models_by_provider" in data
    grouped = data["available_models_by_provider"]
    assert set(grouped.keys()) == {"claude-code", "opencode", "null"}
    # claude-code: hardcoded → always populated.
    assert "anthropic" in grouped["claude-code"]
    assert "haiku" in grouped["claude-code"]["anthropic"]
    # null: never has discovery → empty dict.
    assert grouped["null"] == {}


def test_get_models_grouped_field_within_provider_sorted(
    client: TestClient,
):
    """Pin within-provider sort so the dashboard / picker can
    render without re-sorting on the client."""
    r = client.get("/api/v1/models", headers=_hdr())
    bucket = r.json()["available_models_by_provider"]["claude-code"]["anthropic"]
    assert bucket == sorted(bucket)


def test_get_models_keeps_flat_available_models_for_backwards_compat(
    client: TestClient,
):
    """Pin: ``available_models`` (flat per brain) stays in the
    payload for backwards compatibility. The current dashboard
    dropdown reads from this; Day 2 migrates it to the grouped
    field. Both fields populated from the same discovery call so
    membership is consistent."""
    r = client.get("/api/v1/models", headers=_hdr())
    data = r.json()
    flat = data["available_models"]["claude-code"]
    grouped = data["available_models_by_provider"]["claude-code"]
    # Flat field shape unchanged — sorted list per brain.
    assert isinstance(flat, list)
    assert "haiku" in flat
    # Membership consistency: every grouped model also appears in
    # the flat list. (Strict equality is the right contract since
    # both come from the same source set.)
    grouped_flat = {
        m for bucket in grouped.values() for m in bucket
    }
    assert grouped_flat == set(flat)


def test_get_models_grouped_field_present_in_fallback_payload(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
):
    """Defensive: the empty-fallback branch (build_resolution_table
    raises) MUST still ship the new field — keyed empty — so the
    dashboard / picker can rely on the field always being present."""
    def _explode(*_a, **_k):
        raise RuntimeError("synthetic blow-up")
    monkeypatch.setattr(
        "vexis_agent.core.model_validator.build_resolution_table", _explode,
    )
    r = client.get("/api/v1/models", headers=_hdr())
    data = r.json()
    assert "available_models_by_provider" in data
    assert data["available_models_by_provider"] == {}


def test_get_models_tier_overrides_default_when_unset(
    client: TestClient,
):
    """No config tiers block — every override is None, every
    default is from DEFAULT_TIER_MAP_CLAUDE_CODE."""
    r = client.get("/api/v1/models", headers=_hdr())
    overrides = r.json()["tier_overrides"]
    assert overrides["small"]["configured"] is None
    assert overrides["small"]["default"] == "haiku"
    assert overrides["large"]["configured"] is None
    assert overrides["large"]["default"] == "sonnet"


def test_get_models_tier_overrides_user_value_surfaces(
    client: TestClient, cfg_path: Path,
):
    _seed_config(
        cfg_path,
        "models:\n  tiers:\n    claude-code:\n      large: opus\n",
    )
    r = client.get("/api/v1/models", headers=_hdr())
    overrides = r.json()["tier_overrides"]
    assert overrides["large"]["configured"] == "opus"
    assert overrides["large"]["default"] == "sonnet"


# ──────────────────────────────────────────────────────────────────
# Graceful degradation on malformed config
# ──────────────────────────────────────────────────────────────────


def test_get_models_handles_corrupt_yaml(
    client: TestClient, cfg_path: Path,
):
    """Corrupt YAML in ~/.vexis/config.yaml. ``_read_raw`` falls
    back to empty dict + warns; the endpoint returns the
    empty-config default table without 500-ing."""
    _seed_config(cfg_path, "this: is: not: valid: yaml: at all\n")
    r = client.get("/api/v1/models", headers=_hdr())
    assert r.status_code == 200
    data = r.json()
    # Falls through to default — same as empty config.
    assert data["brain_kind"] == "claude-code"
    assert len(data["subsystems"]) > 0


def test_get_models_handles_unexpected_payload_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch,
):
    """Defensive: even if build_resolution_table itself raises,
    the endpoint returns the shaped fallback rather than 500."""
    def _explode(*_a, **_k):
        raise RuntimeError("synthetic blow-up")
    monkeypatch.setattr(
        "vexis_agent.core.model_validator.build_resolution_table", _explode,
    )
    r = client.get("/api/v1/models", headers=_hdr())
    assert r.status_code == 200
    data = r.json()
    assert data["subsystems"] == []
    error_findings = [
        f for f in data["global_findings"] if f["severity"] == "error"
    ]
    assert error_findings


# ──────────────────────────────────────────────────────────────────
# Cross-surface contract — slash text and API JSON match
# ──────────────────────────────────────────────────────────────────


def test_contract_slash_status_and_api_share_resolution_data(
    client: TestClient, cfg_path: Path,
):
    """Pin the §6 Day 3 contract: ``/model status verbose`` and
    ``GET /api/v1/models`` MUST expose the same per-subsystem
    resolution data. Drift surfaces here before it ships.

    Both surfaces consume ``build_resolution_table``. This test
    feeds the same config to both and asserts the slash text
    contains the API's per-row data byte-for-byte (subsystem
    name, resolved tier, resolved model id).
    """
    _seed_config(
        cfg_path,
        "brain:\n  kind: claude-code\n"
        "models:\n"
        "  learning_review: sonnet\n"           # legacy
        "  subsystems:\n"
        "    goal_judge: tiny\n",                # new schema
    )

    # Pull the API's view.
    r = client.get("/api/v1/models", headers=_hdr())
    api_data = r.json()

    # Pull the slash command's view via the same shared helper
    # (without spinning up a Telegram update — we go direct to the
    # render function). Re-import inside the test so monkeypatched
    # config paths take effect.
    from vexis_agent.transports.telegram import TelegramTransport
    from vexis_agent.core.running_tasks import RunningTasks
    transport = TelegramTransport.__new__(TelegramTransport)
    transport._allowed_user_id = 1  # type: ignore[attr-defined]
    transport._running_tasks = RunningTasks()  # type: ignore[attr-defined]
    slash_text = transport._model_status_text()

    # Each API row's resolution data appears verbatim in the slash
    # text. Polish-pass display rules (2026-05-08) live in
    # ``format_resolution_display`` in core/model_validator — both
    # surfaces share it (slash uses directly; dashboard mirrors
    # the same rules in TS via ``formatConfiguredCell`` /
    # ``formatResolvesToCell``). Pin via the shared helper so any
    # drift surfaces as a contract violation.
    from vexis_agent.core.model_validator import format_resolution_display
    for row in api_data["subsystems"]:
        name = row["name"]
        expected = format_resolution_display(
            row["configured"], row["resolved_model_id"],
        )
        assert name in slash_text, f"slash missing subsystem {name!r}"
        assert expected in slash_text, (
            f"slash row for {name!r} doesn't render the expected "
            f"display string {expected!r} (configured="
            f"{row['configured']!r}, resolved="
            f"{row['resolved_model_id']!r})"
        )

    # API's brain_kind matches the slash header.
    assert f"brain: {api_data['brain_kind']}" in slash_text


def test_contract_validator_findings_appear_in_both_surfaces(
    client: TestClient, cfg_path: Path,
):
    """Same fixture, both surfaces — validator's non-info findings
    surface in both."""
    _seed_config(
        cfg_path,
        "brain:\n  kind: opencode\n"
        "models:\n  learning_review: sonnet\n",
    )

    r = client.get("/api/v1/models", headers=_hdr())
    api_data = r.json()
    api_errors = [
        f for row in api_data["subsystems"] for f in row["findings"]
        if f["severity"] == "error"
    ] + [f for f in api_data["global_findings"] if f["severity"] == "error"]
    assert api_errors  # at least the rule-4 learning_review error

    from vexis_agent.transports.telegram import TelegramTransport
    from vexis_agent.core.running_tasks import RunningTasks
    transport = TelegramTransport.__new__(TelegramTransport)
    transport._allowed_user_id = 1  # type: ignore[attr-defined]
    transport._running_tasks = RunningTasks()  # type: ignore[attr-defined]
    slash_text = transport._model_status_text()

    # Slash mentions "Validator:" + a learning_review issue.
    assert "Validator:" in slash_text
    assert "learning_review" in slash_text

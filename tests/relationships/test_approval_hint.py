"""v3c Day 4c: approval-hint suffix + flag.

After a successful approve, Vexis appends a one-line hint
reminding the user that the new fact takes effect on the next
session. Suppressible via
``relationships.approval_hint_enabled: false`` in
``~/.vexis/config.yaml``.

Tests cover:

- Slash-command path: hint appears when flag is on (default).
- Slash-command path: hint absent when flag is off.
- Dashboard endpoint: ``approval_hint`` field present + populated
  on 200; populated under default flag, null when flag off.
- Hint NOT appended when approve fails (404, 422, 409, etc.).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.learning_curator import LearningController
from core.relationships.curator import RelationshipsCurator
from core.web_server import DashboardConfig, WebDashboard


_TOKEN = "test-token-hint"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _seed(curator, slug, fact):
    curator.candidate_store.add_observation(
        slug=slug, display_name=slug.capitalize(),
        qualifier=None, fact_text=fact,
        session_uuid=f"sess-{slug}", turn_index=1,
    )


def _slash_controller(curator):
    controller = LearningController.__new__(LearningController)
    controller._relationships_curator = curator  # type: ignore[attr-defined]
    return controller


def _dashboard(workspace, curator):
    class _FakeLearning:
        relationships_curator = curator

    dashboard = WebDashboard.__new__(WebDashboard)
    dashboard._workspace = workspace
    dashboard._token = _TOKEN
    dashboard._learning = _FakeLearning()
    dashboard._relationships_mutation_window_seconds = 600
    dashboard._relationships_mutation_limit = 100
    dashboard._relationships_mutation_log = defaultdict(deque)
    dashboard._config = DashboardConfig(
        host="127.0.0.1", port=0,
        web_dist=workspace / "no-frontend",
        manage_tailscale=False,
    )
    for f in (
        "_sessions", "_running_tasks", "_background_tasks",
        "_curator", "_browser", "_started_at",
        "_tailscale_url", "_tailscale_dns",
        "_server", "_serve_task", "_profile_size_cache",
    ):
        setattr(dashboard, f, None)
    dashboard._app = dashboard._build_app()
    return dashboard


# ---------------------------------------------------------------- slash command


def test_slash_approve_appends_hint_default(workspace: Path):
    curator = RelationshipsCurator(workspace=workspace)
    _seed(curator, "mom", "loves classical")
    controller = _slash_controller(curator)
    reply = asyncio.run(
        controller.handle_telegram("relationships-approve", ["mom"])
    )
    assert "Approved" in reply
    assert "Active in your next session" in reply
    assert "/clear" in reply


def test_slash_approve_omits_hint_when_flag_off(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "core.yaml_config.relationships_approval_hint_enabled",
        lambda: False,
    )
    curator = RelationshipsCurator(workspace=workspace)
    _seed(curator, "mom", "loves classical")
    controller = _slash_controller(curator)
    reply = asyncio.run(
        controller.handle_telegram("relationships-approve", ["mom"])
    )
    assert "Approved" in reply
    assert "Active in your next session" not in reply
    assert "/clear" not in reply


def test_slash_approve_no_hint_on_failure(workspace: Path):
    """Approving a slug that isn't in the queue → not-in-queue;
    hint should NOT append on failure."""
    curator = RelationshipsCurator(workspace=workspace)
    controller = _slash_controller(curator)
    reply = asyncio.run(
        controller.handle_telegram("relationships-approve", ["nobody"])
    )
    assert "No candidate in the queue" in reply
    assert "Active in your next session" not in reply


# ---------------------------------------------------------------- dashboard


def test_dashboard_approve_returns_hint_field_default(workspace: Path):
    curator = RelationshipsCurator(workspace=workspace)
    _seed(curator, "mom", "loves classical")
    dashboard = _dashboard(workspace, curator)
    client = TestClient(dashboard._app)
    resp = client.post(
        "/api/v1/relationships/candidates/mom/approve",
        headers={"Authorization": f"Bearer {_TOKEN}"},
        json={},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["approval_hint"] is not None
    assert "next session" in body["approval_hint"]
    assert "/clear" in body["approval_hint"]


def test_dashboard_approve_hint_null_when_flag_off(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "core.yaml_config.relationships_approval_hint_enabled",
        lambda: False,
    )
    curator = RelationshipsCurator(workspace=workspace)
    _seed(curator, "mom", "loves classical")
    dashboard = _dashboard(workspace, curator)
    client = TestClient(dashboard._app)
    resp = client.post(
        "/api/v1/relationships/candidates/mom/approve",
        headers={"Authorization": f"Bearer {_TOKEN}"},
        json={},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_hint"] is None


def test_dashboard_approve_no_hint_on_failure(workspace: Path):
    """On 4xx (e.g. 422 sensitive-pattern), the hint payload doesn't
    appear at all (only the error body does). 404/4xx responses
    don't surface approval_hint."""
    curator = RelationshipsCurator(workspace=workspace)
    dashboard = _dashboard(workspace, curator)
    client = TestClient(dashboard._app)
    resp = client.post(
        "/api/v1/relationships/candidates/missing/approve",
        headers={"Authorization": f"Bearer {_TOKEN}"},
        json={},
    )
    # not-in-queue path returns 400; either way no approval_hint.
    assert resp.status_code in (400, 404)
    body = resp.json()
    assert "approval_hint" not in body

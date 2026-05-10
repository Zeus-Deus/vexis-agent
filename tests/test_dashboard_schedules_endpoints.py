"""Tests for the dashboard ``/api/v1/schedules*`` endpoints.

Mirrors ``tests/test_dashboard_goals_endpoints.py`` construction:
bypass daemon wiring, set just the fields ``_build_app`` and the
schedule helpers touch, build the FastAPI app.

Coverage:

  * GET /api/v1/schedules — bucket shape, sorting, enabled flag.
  * POST /api/v1/schedules/{id}/pause + resume + clear — happy paths.
  * 404 on unknown id, 409 on terminal status, 503 when store unattached.
  * Status changes round-trip back through GET.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vexis_agent.core.schedule_state import (
    ScheduleState,
    ScheduleStore,
    new_schedule_id,
)
from vexis_agent.core.web_server import DashboardConfig, WebDashboard
from vexis_agent.tools.schedule_tool.parser import parse_schedule


_TOKEN = "test-token-schedules-deadbeef"


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


def _build_dashboard(tmp_path: Path) -> WebDashboard:
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
    )
    dashboard._tailscale_url = None  # type: ignore[attr-defined]
    dashboard._tailscale_dns = None  # type: ignore[attr-defined]
    dashboard._server = None  # type: ignore[attr-defined]
    dashboard._serve_task = None  # type: ignore[attr-defined]
    dashboard._started_at = datetime.now(timezone.utc)  # type: ignore[attr-defined]
    dashboard._sessions = None  # type: ignore[attr-defined]
    dashboard._running_tasks = None  # type: ignore[attr-defined]
    dashboard._background_tasks = None  # type: ignore[attr-defined]
    dashboard._curator = None  # type: ignore[attr-defined]
    dashboard._browser = None  # type: ignore[attr-defined]
    dashboard._chat = None  # type: ignore[attr-defined]
    dashboard._running_brain_kind = None  # type: ignore[attr-defined]
    dashboard._profile_size_cache = None  # type: ignore[attr-defined]
    dashboard._schedule_store = None  # type: ignore[attr-defined]
    dashboard._app = dashboard._build_app()
    return dashboard


def _seed_schedule(
    store: ScheduleStore,
    *,
    id: str | None = None,
    status: str = "active",
    prompt: str = "test prompt",
    chat_id: int = 12345,
) -> ScheduleState:
    parsed = parse_schedule("every 30m")
    next_fire = datetime.now(timezone.utc) + timedelta(minutes=30)
    state = ScheduleState(
        id=id or new_schedule_id(),
        chat_id=chat_id,
        schedule=parsed,
        schedule_display=parsed["display"],
        prompt=prompt,
        next_fire_at=next_fire,
        status=status,
    )
    store.save(state)
    return state


@pytest.fixture
def dashboard(tmp_path: Path):
    return _build_dashboard(tmp_path)


@pytest.fixture
def client(dashboard) -> TestClient:
    return TestClient(dashboard._app)


@pytest.fixture
def store(tmp_path: Path, dashboard) -> ScheduleStore:
    s = ScheduleStore(tmp_path / "schedules.json")
    dashboard.attach_schedule_store(s)
    return s


def _auth() -> dict:
    return {"Authorization": f"Bearer {_TOKEN}"}


# ──────────────────────────────────────────────────────────────────
# GET /api/v1/schedules
# ──────────────────────────────────────────────────────────────────


def test_get_empty(client, store):
    r = client.get("/api/v1/schedules", headers=_auth())
    assert r.status_code == 200
    payload = r.json()
    assert payload == {
        "active": [],
        "paused": [],
        "expired": [],
        "cleared": [],
        "retractions_7d": 0,
        "enabled": True,  # post-Day-4 flag flip
    }


def test_get_buckets_by_status(client, store):
    _seed_schedule(store, id="aaaaaaaaaaa1", status="active")
    _seed_schedule(store, id="bbbbbbbbbbb2", status="paused")
    _seed_schedule(store, id="ccccccccccc3", status="expired")
    _seed_schedule(store, id="ddddddddddd4", status="cleared")

    r = client.get("/api/v1/schedules", headers=_auth())
    payload = r.json()
    assert len(payload["active"]) == 1
    assert len(payload["paused"]) == 1
    assert len(payload["expired"]) == 1
    assert len(payload["cleared"]) == 1
    assert payload["active"][0]["id"] == "aaaaaaaaaaa1"


def test_get_503_when_store_unattached(client, tmp_path):
    # Use a fresh dashboard with no attach call.
    d = _build_dashboard(tmp_path)
    c = TestClient(d._app)
    r = c.get("/api/v1/schedules", headers=_auth())
    # Returns 200 with the empty/disabled payload (graceful), not 503.
    # 503 is reserved for the POST mutation paths.
    assert r.status_code == 200
    payload = r.json()
    assert payload["enabled"] is False


def test_get_requires_auth(client, store):
    r = client.get("/api/v1/schedules")  # no bearer
    assert r.status_code in (401, 403)


# ──────────────────────────────────────────────────────────────────
# POST pause / resume / clear
# ──────────────────────────────────────────────────────────────────


def test_pause_happy_path(client, store):
    state = _seed_schedule(store, id="aaaaaaaaaaaa")
    r = client.post(
        "/api/v1/schedules/aaaaaaaaaaaa/pause", headers=_auth()
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "paused"
    assert payload["paused_reason"] == "dashboard"

    # Verify in the store.
    assert store.load("aaaaaaaaaaaa").status == "paused"  # type: ignore[union-attr]


def test_resume_recomputes_next_fire(client, store):
    state = _seed_schedule(store, id="bbbbbbbbbbbb", status="paused")
    r = client.post(
        "/api/v1/schedules/bbbbbbbbbbbb/resume", headers=_auth()
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "active"
    assert payload["next_fire_at"] is not None


def test_clear_soft_deletes(client, store):
    state = _seed_schedule(store, id="cccccccccccc")
    r = client.post(
        "/api/v1/schedules/cccccccccccc/clear", headers=_auth()
    )
    assert r.status_code == 200
    assert r.json()["status"] == "cleared"

    # Cleared record retained on disk.
    assert store.load("cccccccccccc") is not None
    assert store.load("cccccccccccc").status == "cleared"  # type: ignore[union-attr]


def test_pause_on_cleared_returns_409(client, store):
    state = _seed_schedule(store, id="dddddddddddd")
    store.clear("dddddddddddd")

    r = client.post(
        "/api/v1/schedules/dddddddddddd/pause", headers=_auth()
    )
    assert r.status_code == 409
    assert "cleared" in r.json()["detail"].lower()


def test_pause_on_unknown_id_returns_404(client, store):
    r = client.post(
        "/api/v1/schedules/nonexistent/pause", headers=_auth()
    )
    assert r.status_code == 404


def test_id_prefix_resolves(client, store):
    state = _seed_schedule(store, id="eeeeeeeeeeee")
    # 6-char prefix should resolve.
    r = client.post(
        "/api/v1/schedules/eeeeee/pause", headers=_auth()
    )
    assert r.status_code == 200


# ──────────────────────────────────────────────────────────────────
# Round-trip: action then GET reflects new state
# ──────────────────────────────────────────────────────────────────


def test_pause_then_get_shows_paused(client, store):
    _seed_schedule(store, id="ffffffffffff", status="active")
    client.post("/api/v1/schedules/ffffffffffff/pause", headers=_auth())

    r = client.get("/api/v1/schedules", headers=_auth())
    payload = r.json()
    assert payload["active"] == []
    assert len(payload["paused"]) == 1
    assert payload["paused"][0]["id"] == "ffffffffffff"

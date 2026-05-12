"""Dashboard /api/v1/kanban/* endpoints + WebSocket events stream.

Mirrors ``tests/test_dashboard_schedules_endpoints.py`` construction:
bypass main.py wiring, build a bare WebDashboard via __new__, attach
a real KanbanStore, drive the FastAPI app with TestClient.

Coverage:

  * Auth: 401 without bearer token (REST), 4401 close on WS.
  * 503: every endpoint returns 503 when store not attached.
  * Reads: GET /board (filters), /lanes, /tasks/{id}, /tasks/{id}/events.
  * Writes: POST /tasks, /status, /complete, /block, /unblock,
    /archive, /assign, /comment.
  * Links: POST /links + /links/delete.
  * WebSocket: connects with valid token, streams events past since cursor.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vexis_agent.core.kanban.db import KanbanStore
from vexis_agent.core.web_server import DashboardConfig, WebDashboard


_TOKEN = "test-token-kanban-cafe1234"


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    private_root = tmp_path / "_vexis_isolated"
    private_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "vexis_agent.core.yaml_config.vexis_dir", lambda: private_root,
    )
    monkeypatch.setattr(
        "vexis_agent.core.paths.vexis_dir", lambda: private_root,
    )
    yield


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
    dashboard._kanban_store = None  # type: ignore[attr-defined]
    dashboard._app = dashboard._build_app()
    return dashboard


@pytest.fixture
def dashboard(tmp_path: Path):
    d = _build_dashboard(tmp_path)
    yield d


@pytest.fixture
def client(dashboard) -> TestClient:
    return TestClient(dashboard._app)


@pytest.fixture
def store(tmp_path: Path, dashboard) -> KanbanStore:
    s = KanbanStore(tmp_path / "kanban.db")
    dashboard.attach_kanban_store(s)
    yield s
    s.close()


def _auth() -> dict:
    return {"Authorization": f"Bearer {_TOKEN}"}


# ──────────────────────────────────────────────────────────────────
# Auth + 503
# ──────────────────────────────────────────────────────────────────


def test_get_board_unauthorized(client):
    r = client.get("/api/v1/kanban/board")
    assert r.status_code == 401


def test_get_board_503_when_store_not_attached(client):
    r = client.get("/api/v1/kanban/board", headers=_auth())
    assert r.status_code == 503
    assert r.json()["detail"]["kind"] == "KanbanDisabled"


def test_post_task_503_when_store_not_attached(client):
    r = client.post(
        "/api/v1/kanban/tasks", headers=_auth(), json={"title": "x"},
    )
    assert r.status_code == 503


# ──────────────────────────────────────────────────────────────────
# GET /board, /lanes, /tasks/{id}
# ──────────────────────────────────────────────────────────────────


def test_get_board_empty(client, store):
    r = client.get("/api/v1/kanban/board", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert "summary" in body
    assert body["tasks"] == []


def test_get_board_with_tasks(client, store):
    store.create_task(title="t1", lane="research")
    store.create_task(title="t2", lane="implementation")
    r = client.get("/api/v1/kanban/board", headers=_auth())
    assert r.status_code == 200
    titles = [t["title"] for t in r.json()["tasks"]]
    assert {"t1", "t2"} <= set(titles)


def test_get_board_filtered_by_lane(client, store):
    store.create_task(title="r1", lane="research")
    store.create_task(title="i1", lane="implementation")
    r = client.get(
        "/api/v1/kanban/board?lane=research", headers=_auth(),
    )
    body = r.json()
    assert all(t["lane"] == "research" for t in body["tasks"])


def test_get_board_invalid_lane_returns_400(client, store):
    r = client.get("/api/v1/kanban/board?lane=nope", headers=_auth())
    assert r.status_code == 400
    assert r.json()["detail"]["kind"] == "LaneNotFoundError"


def test_get_lanes(client, store):
    r = client.get("/api/v1/kanban/lanes", headers=_auth())
    assert r.status_code == 200
    names = {lane["name"] for lane in r.json()["lanes"]}
    for d in ("research", "implementation", "review", "ops", "triage", "default"):
        assert d in names


def test_get_task_detail(client, store):
    t = store.create_task(title="hello", body="b")
    r = client.get(f"/api/v1/kanban/tasks/{t.id}", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert body["task"]["title"] == "hello"
    assert body["task"]["body"] == "b"
    assert body["parents"] == []
    assert body["children"] == []


def test_get_task_404(client, store):
    r = client.get("/api/v1/kanban/tasks/ghost", headers=_auth())
    assert r.status_code == 404
    assert r.json()["detail"]["kind"] == "TaskNotFoundError"


def test_get_task_events(client, store):
    t = store.create_task(title="x")
    store.add_comment(t.id, author="user", body="comment")
    r = client.get(
        f"/api/v1/kanban/tasks/{t.id}/events", headers=_auth(),
    )
    assert r.status_code == 200
    events = r.json()["events"]
    assert any(e["kind"] == "commented" for e in events)


def test_get_task_events_404(client, store):
    r = client.get("/api/v1/kanban/tasks/ghost/events", headers=_auth())
    assert r.status_code == 404


# ──────────────────────────────────────────────────────────────────
# POST /tasks
# ──────────────────────────────────────────────────────────────────


def test_post_task_create(client, store):
    r = client.post(
        "/api/v1/kanban/tasks", headers=_auth(),
        json={
            "title": "ship X", "lane": "implementation",
            "priority": 5, "body": "details",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "ship X"
    assert body["lane"] == "implementation"
    assert body["priority"] == 5
    # Verify it landed in the store.
    assert store.require_task(body["id"]).title == "ship X"


def test_post_task_missing_title_400(client, store):
    r = client.post(
        "/api/v1/kanban/tasks", headers=_auth(), json={},
    )
    assert r.status_code == 400


def test_post_task_unknown_lane_400(client, store):
    r = client.post(
        "/api/v1/kanban/tasks", headers=_auth(),
        json={"title": "x", "lane": "nope"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["kind"] == "LaneNotFoundError"


def test_post_task_with_parents(client, store):
    p = store.create_task(title="parent")
    r = client.post(
        "/api/v1/kanban/tasks", headers=_auth(),
        json={"title": "child", "parents": [p.id]},
    )
    assert r.status_code == 200
    # Child forced to todo because parent isn't done.
    assert r.json()["status"] == "todo"


# ──────────────────────────────────────────────────────────────────
# POST /tasks/{id}/status — drag-drop
# ──────────────────────────────────────────────────────────────────


def test_post_status_flips_column(client, store):
    t = store.create_task(title="x", status="todo")
    r = client.post(
        f"/api/v1/kanban/tasks/{t.id}/status",
        headers=_auth(), json={"status": "ready"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ready"
    assert store.require_task(t.id).status == "ready"


def test_post_status_invalid(client, store):
    t = store.create_task(title="x")
    r = client.post(
        f"/api/v1/kanban/tasks/{t.id}/status",
        headers=_auth(), json={"status": "bogus"},
    )
    assert r.status_code == 400


def test_post_status_404(client, store):
    r = client.post(
        "/api/v1/kanban/tasks/ghost/status",
        headers=_auth(), json={"status": "ready"},
    )
    assert r.status_code == 404


# ──────────────────────────────────────────────────────────────────
# POST /complete / /block / /unblock / /archive / /assign / /comment
# ──────────────────────────────────────────────────────────────────


def test_post_complete(client, store):
    t = store.create_task(title="x")
    r = client.post(
        f"/api/v1/kanban/tasks/{t.id}/complete",
        headers=_auth(), json={"summary": "done"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "done"


def test_post_block(client, store):
    t = store.create_task(title="x")
    r = client.post(
        f"/api/v1/kanban/tasks/{t.id}/block",
        headers=_auth(), json={"reason": "waiting on user"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "blocked"


def test_post_block_missing_reason_400(client, store):
    t = store.create_task(title="x")
    r = client.post(
        f"/api/v1/kanban/tasks/{t.id}/block",
        headers=_auth(), json={},
    )
    assert r.status_code == 400


def test_post_unblock(client, store):
    t = store.create_task(title="x")
    store.update_task(t.id, status="blocked")
    r = client.post(
        f"/api/v1/kanban/tasks/{t.id}/unblock",
        headers=_auth(), json={},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_post_unblock_not_blocked_409(client, store):
    t = store.create_task(title="x")
    r = client.post(
        f"/api/v1/kanban/tasks/{t.id}/unblock",
        headers=_auth(), json={},
    )
    assert r.status_code == 409


def test_post_archive(client, store):
    t = store.create_task(title="x")
    r = client.post(
        f"/api/v1/kanban/tasks/{t.id}/archive",
        headers=_auth(),
    )
    assert r.status_code == 200
    assert store.require_task(t.id).status == "archived"


def test_post_assign(client, store):
    t = store.create_task(title="x")
    r = client.post(
        f"/api/v1/kanban/tasks/{t.id}/assign",
        headers=_auth(), json={"lane": "research"},
    )
    assert r.status_code == 200
    assert r.json()["lane"] == "research"


def test_post_comment(client, store):
    t = store.create_task(title="x")
    r = client.post(
        f"/api/v1/kanban/tasks/{t.id}/comment",
        headers=_auth(), json={"body": "looks good"},
    )
    assert r.status_code == 200
    comments = store.list_comments(t.id)
    assert any(c.body == "looks good" for c in comments)


# ──────────────────────────────────────────────────────────────────
# /links
# ──────────────────────────────────────────────────────────────────


def test_post_link(client, store):
    p = store.create_task(title="p")
    c = store.create_task(title="c")
    r = client.post(
        "/api/v1/kanban/links", headers=_auth(),
        json={"parent_id": p.id, "child_id": c.id},
    )
    assert r.status_code == 200
    assert store.get_children(p.id) == [c.id]


def test_post_link_missing_parent_404(client, store):
    c = store.create_task(title="c")
    r = client.post(
        "/api/v1/kanban/links", headers=_auth(),
        json={"parent_id": "ghost", "child_id": c.id},
    )
    assert r.status_code == 404


def test_post_link_self_400(client, store):
    a = store.create_task(title="a")
    r = client.post(
        "/api/v1/kanban/links", headers=_auth(),
        json={"parent_id": a.id, "child_id": a.id},
    )
    assert r.status_code == 400


def test_post_unlink(client, store):
    p = store.create_task(title="p")
    c = store.create_task(title="c")
    store.add_link(p.id, c.id)
    r = client.post(
        "/api/v1/kanban/links/delete", headers=_auth(),
        json={"parent_id": p.id, "child_id": c.id},
    )
    assert r.status_code == 200


# ──────────────────────────────────────────────────────────────────
# WebSocket events stream
# ──────────────────────────────────────────────────────────────────


def test_ws_events_invalid_token_rejects(client, store):
    """Connecting WS without a valid token closes with 4401."""
    with pytest.raises(Exception):
        with client.websocket_connect("/api/v1/kanban/events?token=wrong"):
            pass


def test_ws_events_missing_token_rejects(client, store):
    with pytest.raises(Exception):
        with client.websocket_connect("/api/v1/kanban/events"):
            pass


def test_ws_events_streams_with_valid_token(client, store):
    """Connect with token, with a pre-existing task, receive the event."""
    # Pre-seed so the WS's first poll has something to return.
    store.create_task(title="ws-test")
    with client.websocket_connect(
        f"/api/v1/kanban/events?token={_TOKEN}&since=0",
    ) as ws:
        # The first poll fires immediately on connection (cursor=0
        # fetches all historical events).
        payload = ws.receive_json()
        assert "events" in payload
        kinds = [e["kind"] for e in payload["events"]]
        assert "created" in kinds


def test_ws_events_503_when_store_not_attached(dashboard):
    """No kanban store attached → WS close with 4503."""
    client_no_store = TestClient(dashboard._app)
    with pytest.raises(Exception):
        with client_no_store.websocket_connect(
            f"/api/v1/kanban/events?token={_TOKEN}",
        ):
            pass

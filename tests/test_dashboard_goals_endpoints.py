"""Tests for the dashboard ``/api/v1/goals*`` endpoints.

Mirrors the construction trick in ``test_dashboard_tailscale_endpoint.py``
and ``tests/relationships/test_dashboard_endpoints.py``: bypass the
daemon wiring, set just the fields ``_build_app`` and the goal
helpers touch, then build the FastAPI app.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.goal_manager import GoalManager
from core.goal_state import GoalState, GoalStateStore
from core.running_tasks import RunningTasks
from core.web_server import DashboardConfig, WebDashboard


_TOKEN = "test-token-goals-cafef00d"
_SESSION = "test-session-goals"


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


class _FakeSessions:
    """SessionStore stand-in. Only ``get()`` is read by the goal
    endpoint — everything else stays unused."""

    def __init__(self, uuid: str) -> None:
        self._uuid = uuid

    def get(self) -> str:
        return self._uuid


def _build_dashboard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WebDashboard:
    # Redirect goals.json to tmp so each test has an isolated store.
    goals_file = tmp_path / "goals.json"
    monkeypatch.setattr("core.paths.goals_path", lambda: goals_file)

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
    dashboard._sessions = _FakeSessions(_SESSION)  # type: ignore[attr-defined]
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
def goals_file(tmp_path: Path) -> Path:
    return tmp_path / "goals.json"


@pytest.fixture
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    return TestClient(_build_dashboard(tmp_path, monkeypatch)._app)


@pytest.fixture
def store(tmp_path: Path) -> GoalStateStore:
    return GoalStateStore(tmp_path / "goals.json")


def _hdr() -> dict:
    return {"Authorization": f"Bearer {_TOKEN}"}


# ──────────────────────────────────────────────────────────────────
# GET /api/v1/goals — auth + happy-path shape
# ──────────────────────────────────────────────────────────────────


def test_get_goals_rejects_missing_token(client: TestClient) -> None:
    resp = client.get("/api/v1/goals")
    assert resp.status_code == 401


def test_get_goals_no_active_returns_null(client: TestClient) -> None:
    """No goal record on disk → ``active=null`` and ``history=[]``."""
    resp = client.get("/api/v1/goals", headers=_hdr())
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"active": None, "history": []}


def test_get_goals_with_active(
    client: TestClient, store: GoalStateStore
) -> None:
    """An active goal for the current session is returned in the
    ``active`` field with the full record shape."""
    GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    ).set("port the goal command")
    resp = client.get("/api/v1/goals", headers=_hdr())
    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is not None
    assert body["active"]["session_uuid"] == _SESSION
    assert body["active"]["goal"] == "port the goal command"
    assert body["active"]["status"] == "active"
    assert body["active"]["max_turns"] == 20
    assert body["active"]["turns_used"] == 0
    assert body["history"] == []


def test_get_goals_history_sort_desc_and_capped(
    client: TestClient, store: GoalStateStore
) -> None:
    """History sorted by ``last_turn_at`` desc and capped at 20."""
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    # Stage 25 done records — newer last_turn_at on higher index.
    for i in range(25):
        sid = f"sid-{i:02d}"
        state = GoalState(
            goal=f"goal-{i}",
            status="done",
            turns_used=i,
            max_turns=20,
            last_turn_at=base + timedelta(minutes=i),
            last_verdict="done",
            last_reason=f"finished {i}",
        )
        store.save(sid, state)

    resp = client.get("/api/v1/goals", headers=_hdr())
    assert resp.status_code == 200
    body = resp.json()
    history = body["history"]
    # Capped at 20.
    assert len(history) == 20
    # Sorted desc — first entry should be sid-24 (highest last_turn_at).
    assert history[0]["session_uuid"] == "sid-24"
    assert history[-1]["session_uuid"] == "sid-05"
    # All non-active rows.
    assert all(row["status"] != "active" for row in history)


def test_get_goals_active_shows_paused_too(
    client: TestClient, store: GoalStateStore
) -> None:
    """A paused goal for the current session is treated as the
    'active' row from the dashboard's perspective — paused is the
    other state with action affordances; done / cleared go to history."""
    mgr = GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    )
    mgr.set("ship feature X")
    state = mgr.state
    assert state is not None
    state.turns_used = 5
    state.status = "paused"
    state.paused_reason = "user-cancelled"
    store.save(_SESSION, state)

    resp = client.get("/api/v1/goals", headers=_hdr())
    body = resp.json()
    assert body["active"] is not None
    assert body["active"]["status"] == "paused"
    assert body["active"]["paused_reason"] == "user-cancelled"


def test_get_goals_done_record_shows_in_history_not_active(
    client: TestClient, store: GoalStateStore
) -> None:
    """Done records appear in history, NOT in the active slot."""
    mgr = GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    )
    mgr.set("g")
    mgr.mark_done("delivered")

    resp = client.get("/api/v1/goals", headers=_hdr())
    body = resp.json()
    assert body["active"] is None
    assert len(body["history"]) == 1
    assert body["history"][0]["session_uuid"] == _SESSION
    assert body["history"][0]["status"] == "done"


# ──────────────────────────────────────────────────────────────────
# POST pause / resume / clear
# ──────────────────────────────────────────────────────────────────


def test_post_pause_writes_dashboard_paused_reason(
    client: TestClient, store: GoalStateStore
) -> None:
    """The dashboard pause writes ``paused_reason="dashboard-paused"``
    so the audit trail can distinguish dashboard mutations from
    Telegram-driven ones."""
    GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    ).set("g")

    resp = client.post("/api/v1/goals/pause", headers=_hdr())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "paused"
    assert body["paused_reason"] == "dashboard-paused"
    # And on disk.
    on_disk = store.load(_SESSION)
    assert on_disk is not None
    assert on_disk.status == "paused"
    assert on_disk.paused_reason == "dashboard-paused"


def test_post_pause_404_when_no_active(client: TestClient) -> None:
    resp = client.post("/api/v1/goals/pause", headers=_hdr())
    assert resp.status_code == 404
    assert "no active goal" in resp.json()["detail"].lower()


def test_post_pause_404_when_already_paused(
    client: TestClient, store: GoalStateStore
) -> None:
    """Idempotent semantics: pausing a paused goal is a 404 (the
    user clicked pause but there's nothing active to pause)."""
    mgr = GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    )
    mgr.set("g")
    mgr.pause()
    resp = client.post("/api/v1/goals/pause", headers=_hdr())
    assert resp.status_code == 404


def test_post_resume_resets_turns_used(
    client: TestClient, store: GoalStateStore
) -> None:
    """Resume from the dashboard zeros ``turns_used`` (same contract
    as ``/goal resume`` in Telegram)."""
    mgr = GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    )
    mgr.set("g")
    state = mgr.state
    assert state is not None
    state.turns_used = 4
    state.status = "paused"
    state.paused_reason = "user-paused"
    store.save(_SESSION, state)

    resp = client.post("/api/v1/goals/resume", headers=_hdr())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "active"
    assert body["turns_used"] == 0
    assert body["paused_reason"] is None


def test_post_resume_404_when_no_paused_goal(client: TestClient) -> None:
    resp = client.post("/api/v1/goals/resume", headers=_hdr())
    assert resp.status_code == 404


def test_post_resume_404_when_active_not_paused(
    client: TestClient, store: GoalStateStore
) -> None:
    """An active goal can't be resumed — it's already running. The
    button should be disabled in the UI; this test pins the API
    contract for completeness."""
    GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    ).set("g")
    resp = client.post("/api/v1/goals/resume", headers=_hdr())
    assert resp.status_code == 404


def test_post_clear_marks_cleared(
    client: TestClient, store: GoalStateStore
) -> None:
    GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    ).set("g")
    resp = client.post("/api/v1/goals/clear", headers=_hdr())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "cleared"
    on_disk = store.load(_SESSION)
    assert on_disk is not None
    assert on_disk.status == "cleared"


def test_post_clear_works_on_paused_goal(
    client: TestClient, store: GoalStateStore
) -> None:
    """Clear should work whether the goal is active or paused —
    ``has_goal`` returns True for both states."""
    mgr = GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    )
    mgr.set("g")
    mgr.pause()
    resp = client.post("/api/v1/goals/clear", headers=_hdr())
    assert resp.status_code == 200


def test_post_clear_404_when_no_goal(client: TestClient) -> None:
    resp = client.post("/api/v1/goals/clear", headers=_hdr())
    assert resp.status_code == 404


# ──────────────────────────────────────────────────────────────────
# Continuation-queue cleanup on dashboard mutations
# ──────────────────────────────────────────────────────────────────


def test_post_pause_drops_pending_goal_continuations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirrors the Telegram /goal pause cleanup: dashboard pause
    drops any queued ``goal_continuation`` messages so a continuation
    enqueued just before the click doesn't sneak through after the
    state change. User messages in the queue must survive."""
    dashboard = _build_dashboard(tmp_path, monkeypatch)
    store = GoalStateStore(tmp_path / "goals.json")
    GoalManager(
        session_uuid=_SESSION, workspace=Path("/tmp"), store=store
    ).set("g")

    async def seed() -> None:
        await dashboard._running_tasks.claim(123)
        await dashboard._running_tasks.enqueue(
            123, 99, "stale continuation",
            origin="goal_continuation",
        )
        await dashboard._running_tasks.enqueue(
            123, 99, "real user message",
            origin="user",
        )

    asyncio.run(seed())
    assert dashboard._running_tasks.queue_depth(123) == 2

    client = TestClient(dashboard._app)
    resp = client.post("/api/v1/goals/pause", headers=_hdr())
    assert resp.status_code == 200

    # Continuation dropped, user message survives.
    assert dashboard._running_tasks.queue_depth(123) == 1

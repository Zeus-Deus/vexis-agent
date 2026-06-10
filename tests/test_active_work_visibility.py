"""Unified active-work visibility (subagent-visibility fix).

Regression pins for the screenshot bug: the brain delegates work to a
watched workspace (`vexis-watch register`) and truthfully reports
"still working — 9 minutes in", while `/tasks` replies "No background
tasks running." and `/status` says "Nothing running". Root cause:
both surfaces read ONLY the vexis-bg registry; the watcher registry
(and, on /tasks, kanban background goals) were invisible.

Three layers pinned here:

  1. ``core/watcher/views.py`` — the render/payload views every
     surface composes. None-safe, deterministic with ``now=``.
  2. Telegram ``/tasks`` + ``/status`` — compose the watcher block;
     "No background tasks running." ONLY when every registry is empty.
  3. Dashboard ``_status_payload`` — carries ``watched_agents`` so the
     web status page can't claim idle while a delegation is mid-flight.

Transport construction is the same ``__new__`` bypass the rest of the
telegram tests use; the handlers under test only touch the attributes
stubbed in ``_make_transport``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.watcher import WatcherController, WatcherRegistry, WatchStatus
from vexis_agent.core.watcher.sources import (
    Source,
    SourceDescription,
    clear_sources,
    register_source,
)
from vexis_agent.core.watcher.views import (
    render_watched_status,
    watched_work_payload,
)
from vexis_agent.transports.telegram import TelegramTransport, _NO_BG_TASKS


_USER = 99
_CHAT = 42


class _Source(Source):
    source_type = "fake"

    async def read_recent_output(self, identifier: str) -> bytes:
        return b"working...\n"

    async def is_alive(self, identifier: str) -> bool:
        return True

    async def describe(self, identifier: str) -> SourceDescription:
        return SourceDescription()


@pytest.fixture(autouse=True)
def _stub_source():
    clear_sources()
    register_source(_Source())
    yield
    clear_sources()


def _make_watcher(tmp_path: Path) -> WatcherController:
    return WatcherController(
        registry=WatcherRegistry(state_path=tmp_path / "wr.json"),
    )


def _register(
    watcher: WatcherController,
    name: str = "vin-lookup",
    *,
    goal_hint: str | None = "live VIN lookup on partslink24",
) -> None:
    asyncio.run(watcher.register_agent(
        name=name,
        source_type="fake",
        identifier="session-7",
        agent_kind="claude-code",
        chat_id=_CHAT,
        goal_hint=goal_hint,
        workspace_id="ws-7",
    ))


def _now_after(watcher: WatcherController, name: str, minutes: int) -> datetime:
    """A deterministic 'now' N minutes after the agent registered."""
    agent = watcher.get_agent(name)
    assert agent is not None
    return datetime.fromisoformat(agent.registered_at) + timedelta(minutes=minutes)


# ──────────────────────────────────────────────────────────────────
# 1. views — render_watched_status / watched_work_payload
# ──────────────────────────────────────────────────────────────────


def test_views_are_none_safe():
    assert render_watched_status(None) is None
    assert watched_work_payload(None) == []


def test_views_empty_registry(tmp_path):
    watcher = _make_watcher(tmp_path)
    assert render_watched_status(watcher) is None
    assert watched_work_payload(watcher) == []


def test_render_running_agent_with_goal_hint(tmp_path):
    watcher = _make_watcher(tmp_path)
    _register(watcher)
    now = _now_after(watcher, "vin-lookup", 9)
    block = render_watched_status(watcher, now=now)
    assert block is not None
    assert block.startswith("👁 Watched workspaces:")
    assert "▶ `vin-lookup` [running 9m] live VIN lookup on partslink24" in block
    # Inline verbs hint so the user knows how to drill in.
    assert "tail <name>" in block


def test_render_idle_agent_anchors_at_transition(tmp_path):
    watcher = _make_watcher(tmp_path)
    _register(watcher, goal_hint=None)
    agent = watcher.get_agent("vin-lookup")
    assert agent is not None
    agent.status = WatchStatus.IDLE.value
    went_quiet = datetime.now(timezone.utc) - timedelta(minutes=3)
    agent.last_status_transition_at = went_quiet.isoformat()
    block = render_watched_status(
        watcher, now=went_quiet + timedelta(minutes=3)
    )
    assert block is not None
    assert "◦ `vin-lookup` [quiet 3m]" in block


def test_render_running_sorts_before_idle_and_marks_muted(tmp_path):
    watcher = _make_watcher(tmp_path)
    _register(watcher, "a-quiet-one", goal_hint=None)
    _register(watcher, "z-running-one", goal_hint=None)
    quiet = watcher.get_agent("a-quiet-one")
    assert quiet is not None
    quiet.status = WatchStatus.IDLE.value
    quiet.muted = True
    block = render_watched_status(watcher)
    assert block is not None
    lines = block.splitlines()
    assert "z-running-one" in lines[1]  # running first despite name sort
    assert "a-quiet-one" in lines[2]
    assert "(muted)" in lines[2]


def test_render_overflow_line(tmp_path):
    watcher = _make_watcher(tmp_path)
    for i in range(3):
        _register(watcher, f"agent-{i}", goal_hint=None)
    block = render_watched_status(watcher, max_agents=2)
    assert block is not None
    assert "(+1 more — /codemux)" in block


def test_payload_rows_are_json_safe_and_complete(tmp_path):
    watcher = _make_watcher(tmp_path)
    _register(watcher)
    now = _now_after(watcher, "vin-lookup", 9)
    rows = watched_work_payload(watcher, now=now)
    assert len(rows) == 1
    row = rows[0]
    json.dumps(rows)  # JSON-safe end to end
    assert row["name"] == "vin-lookup"
    assert row["status"] == "running"
    assert row["state"] == "running"
    assert row["workspace_id"] == "ws-7"
    assert row["agent_kind"] == "claude-code"
    assert row["goal_hint"] == "live VIN lookup on partslink24"
    assert row["elapsed"] == "9m"
    assert row["elapsed_seconds"] == 9 * 60
    assert row["muted"] is False


# ──────────────────────────────────────────────────────────────────
# 2. Telegram /tasks + /status compose all registries
# ──────────────────────────────────────────────────────────────────


class _Msg:
    def __init__(self, text: str) -> None:
        self.text = text
        self.chat_id = _CHAT
        self.replies: list[str] = []

    async def reply_text(self, text: str, parse_mode: str | None = None) -> None:
        self.replies.append(text)


class _User:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class _Update:
    def __init__(self, msg: _Msg, user: _User) -> None:
        self.message = msg
        self.effective_user = user


class _StubBackgroundTasks:
    def __init__(self, summary: list[dict] | None = None) -> None:
        self._summary = summary or []

    async def status_summary(self) -> list[dict]:
        return self._summary


class _StubHandler:
    def current_session_uuid(self) -> str | None:
        return None


def _make_transport(
    tmp_path: Path,
    *,
    bg_summary: list[dict] | None = None,
    with_watcher: bool = True,
) -> tuple[TelegramTransport, WatcherController | None]:
    t = TelegramTransport.__new__(TelegramTransport)
    t._allowed_user_id = _USER  # type: ignore[attr-defined]
    t._running_tasks = RunningTasks()  # type: ignore[attr-defined]
    t._background_tasks = _StubBackgroundTasks(bg_summary)  # type: ignore[attr-defined]
    t._handler = _StubHandler()  # type: ignore[attr-defined]
    watcher: WatcherController | None = None
    if with_watcher:
        watcher = _make_watcher(tmp_path)
        _register(watcher)
    t._watcher = watcher  # type: ignore[attr-defined]
    return t, watcher


def test_tasks_shows_watched_workspace_when_bg_registry_empty(tmp_path):
    """THE regression: a watched delegation mid-flight must never be
    answered with "No background tasks running."."""
    transport, _ = _make_transport(tmp_path)
    msg = _Msg("/tasks")
    asyncio.run(transport._on_tasks(_Update(msg, _User(_USER)), None))
    assert len(msg.replies) == 1
    reply = msg.replies[0]
    assert _NO_BG_TASKS not in reply
    assert "👁 Watched workspaces:" in reply
    assert "vin-lookup" in reply
    assert "live VIN lookup on partslink24" in reply


def test_tasks_composes_bg_tasks_and_watched_block(tmp_path):
    spawned = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    transport, _ = _make_transport(
        tmp_path,
        bg_summary=[{
            "name": "fix-login-bug",
            "status": "running",
            "spawned_at": spawned,
        }],
    )
    msg = _Msg("/tasks")
    asyncio.run(transport._on_tasks(_Update(msg, _User(_USER)), None))
    reply = msg.replies[0]
    assert "fix-login-bug — running" in reply
    assert "👁 Watched workspaces:" in reply
    # Sections separated by a blank line, vexis-bg first.
    assert reply.index("fix-login-bug") < reply.index("👁")


def test_tasks_empty_everywhere_keeps_the_classic_reply(tmp_path):
    transport, _ = _make_transport(tmp_path, with_watcher=False)
    msg = _Msg("/tasks")
    asyncio.run(transport._on_tasks(_Update(msg, _User(_USER)), None))
    assert msg.replies == [_NO_BG_TASKS]


def test_tasks_watcher_with_empty_registry_keeps_the_classic_reply(tmp_path):
    transport, watcher = _make_transport(tmp_path)
    assert watcher is not None
    asyncio.run(watcher.unregister_agent("vin-lookup"))
    msg = _Msg("/tasks")
    asyncio.run(transport._on_tasks(_Update(msg, _User(_USER)), None))
    assert msg.replies == [_NO_BG_TASKS]


def test_status_appends_watched_block(tmp_path, monkeypatch):
    import vexis_agent.transports.telegram as tg

    monkeypatch.setattr(tg, "read_status", lambda chat_id: None)
    transport, _ = _make_transport(tmp_path)
    msg = _Msg("/status")
    asyncio.run(transport._on_status(_Update(msg, _User(_USER)), None))
    assert len(msg.replies) == 1
    reply = msg.replies[0]
    # Foreground is genuinely idle…
    assert "Nothing running" in reply
    # …but the watched delegation is listed right below, not hidden.
    assert "👁 Watched workspaces:" in reply
    assert "vin-lookup" in reply


def test_status_without_watcher_unchanged(tmp_path, monkeypatch):
    import vexis_agent.transports.telegram as tg

    monkeypatch.setattr(tg, "read_status", lambda chat_id: None)
    transport, _ = _make_transport(tmp_path, with_watcher=False)
    msg = _Msg("/status")
    asyncio.run(transport._on_status(_Update(msg, _User(_USER)), None))
    assert "👁" not in msg.replies[0]


# ──────────────────────────────────────────────────────────────────
# 3. Dashboard status payload carries watched_agents
# ──────────────────────────────────────────────────────────────────


class _StubSessions:
    def list(self):
        return []

    def active_name(self):
        return "default"


class _StubRunningTasks:
    async def snapshot(self):
        return []


def _make_dashboard(watcher: WatcherController | None):
    from vexis_agent.core.web_server import WebDashboard

    d = WebDashboard.__new__(WebDashboard)
    d._background_tasks = _StubBackgroundTasks()  # type: ignore[attr-defined]
    d._running_tasks = _StubRunningTasks()  # type: ignore[attr-defined]
    d._sessions = _StubSessions()  # type: ignore[attr-defined]
    d._started_at = datetime.now(timezone.utc)  # type: ignore[attr-defined]
    d._watcher = watcher  # type: ignore[attr-defined]
    return d


def test_dashboard_payload_includes_watched_agents(tmp_path):
    watcher = _make_watcher(tmp_path)
    _register(watcher)
    payload = asyncio.run(_make_dashboard(watcher)._status_payload())
    assert [a["name"] for a in payload["watched_agents"]] == ["vin-lookup"]
    json.dumps(payload["watched_agents"])


def test_dashboard_payload_watcher_absent_is_empty_list():
    payload = asyncio.run(_make_dashboard(None)._status_payload())
    assert payload["watched_agents"] == []

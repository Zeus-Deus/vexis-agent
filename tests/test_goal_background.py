"""Tests for background-by-default /goal (v0.11).

Covers three things:

  * ``core.goal_background`` — the read-only projection over the kanban
    store that powers the ``[BACKGROUND GOALS]`` context block and the
    ``/goal status`` / ``/status`` background list.
  * ``core.yaml_config.goals_default_mode`` — the config knob.
  * ``transports.telegram._on_goal`` routing — plain ``/goal`` files a
    kanban task (background default); ``/goal --fg`` runs the foreground
    loop even when the default is background.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from vexis_agent.core.goal_background import (
    GOAL_TASK_CREATED_BY,
    list_background_goals,
    render_background_goal_block,
    render_background_goals_status,
)
from vexis_agent.core.kanban.db import KanbanStore
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.transports.telegram import TelegramTransport


_USER = 99
_CHAT = 42
_SESSION = "test-session-bg"
_NOW = 1_000_000


# ──────────────────────────────────────────────────────────────────
# core.goal_background — projection over the kanban store
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path: Path) -> KanbanStore:
    s = KanbanStore(tmp_path / "kanban.db")
    yield s
    s.close()


def _file_goal(store: KanbanStore, title: str, *, status: str = "ready") -> str:
    from vexis_agent.tools.kanban import api as kanban_api
    res = kanban_api.create_task(
        store, title=title, body=title, lane="implementation",
        status=status, created_by=GOAL_TASK_CREATED_BY,
    )
    assert res.get("ok"), res
    return res["data"]["id"]


def test_list_background_goals_filters_to_goal_tasks(store: KanbanStore) -> None:
    _file_goal(store, "ship the rocket")
    # A plain /kanban task must NOT show up as a background goal.
    from vexis_agent.tools.kanban import api as kanban_api
    kanban_api.create_task(
        store, title="unrelated chore", status="ready", created_by="user",
    )
    goals = list_background_goals(store)
    assert [t.title for t in goals] == ["ship the rocket"]


def test_list_background_goals_excludes_done(store: KanbanStore) -> None:
    tid = _file_goal(store, "finish me")
    store.update_task(tid, status="done")
    assert list_background_goals(store) == []


def test_render_block_none_when_no_goals(store: KanbanStore) -> None:
    assert render_background_goal_block(store) is None
    assert render_background_goals_status(store) is None


def test_render_block_lists_active_goals(store: KanbanStore) -> None:
    tid = _file_goal(store, "ship the rocket")
    block = render_background_goal_block(store, now=_NOW)
    assert block is not None
    assert "BACKGROUND GOALS" in block
    assert "ship the rocket" in block
    assert tid in block
    # The block teaches the brain how to talk about it.
    assert "vexis-kanban show" in block


def test_render_status_lists_active_goals(store: KanbanStore) -> None:
    _file_goal(store, "ship the rocket")
    status = render_background_goals_status(store, now=_NOW)
    assert status is not None
    assert "Background goals" in status
    assert "ship the rocket" in status


def test_render_block_surfaces_heartbeat_progress(store: KanbanStore) -> None:
    tid = _file_goal(store, "long job")
    from vexis_agent.core.kanban.constants import EVENT_HEARTBEAT
    store.append_event(tid, EVENT_HEARTBEAT, {"progress": "wrote module X"})
    block = render_background_goal_block(store, now=_NOW)
    assert block is not None
    assert "wrote module X" in block


def test_list_background_goals_handles_none_store() -> None:
    assert list_background_goals(None) == []  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────
# core.yaml_config.goals_default_mode
# ──────────────────────────────────────────────────────────────────


def test_goals_default_mode_default_is_background(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vexis_agent.core import yaml_config
    monkeypatch.setattr(yaml_config, "_section", lambda _n: {})
    assert yaml_config.goals_default_mode() == "background"


def test_goals_default_mode_foreground_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vexis_agent.core import yaml_config
    monkeypatch.setattr(
        yaml_config, "_section", lambda _n: {"default_mode": "foreground"}
    )
    assert yaml_config.goals_default_mode() == "foreground"


def test_goals_default_mode_garbage_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vexis_agent.core import yaml_config
    monkeypatch.setattr(
        yaml_config, "_section", lambda _n: {"default_mode": "sideways"}
    )
    assert yaml_config.goals_default_mode() == "background"


# ──────────────────────────────────────────────────────────────────
# transports.telegram._on_goal routing
# ──────────────────────────────────────────────────────────────────


class _FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, *, chat_id: int, text: str, **_kw: Any) -> None:
        self.sent.append((chat_id, text))


class _FakeMessage:
    def __init__(self, text: str, bot: _FakeBot) -> None:
        self.text = text
        self.chat_id = _CHAT
        self._bot = bot
        self.reply_log: list[str] = []

    async def reply_text(self, text: str, **_kw: Any) -> None:
        self.reply_log.append(text)

    def get_bot(self) -> _FakeBot:
        return self._bot


class _FakeUser:
    id = _USER


class _FakeUpdate:
    def __init__(self, msg: _FakeMessage) -> None:
        self.message = msg
        self.effective_user = _FakeUser()


class _FakeCtx:
    def __init__(self, args: list[str]) -> None:
        self.args = args


class _FakeHandler:
    def current_session_uuid(self) -> str:
        return _SESSION


def _transport(kanban_store: KanbanStore | None) -> TelegramTransport:
    t = TelegramTransport.__new__(TelegramTransport)
    t._handler = _FakeHandler()  # type: ignore[attr-defined]
    t._allowed_user_id = _USER  # type: ignore[attr-defined]
    t._running_tasks = RunningTasks()  # type: ignore[attr-defined]
    t._kanban_store = kanban_store  # type: ignore[attr-defined]
    t._background_dispatch_tasks = set()  # type: ignore[attr-defined]
    return t


def _mk(text: str) -> tuple[_FakeUpdate, _FakeBot, _FakeMessage]:
    bot = _FakeBot()
    msg = _FakeMessage(text, bot)
    return _FakeUpdate(msg), bot, msg


def test_plain_goal_defaults_to_background(
    store: KanbanStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Plain ``/goal <text>`` (no flag) with the background default files
    a kanban task and does NOT touch the foreground GoalManager."""
    monkeypatch.setattr(
        "vexis_agent.core.yaml_config.goals_enabled", lambda: True
    )
    monkeypatch.setattr(
        "vexis_agent.core.yaml_config.goals_default_mode", lambda: "background"
    )
    monkeypatch.setattr(
        "vexis_agent.core.paths.goals_path", lambda: tmp_path / "goals.json"
    )
    transport = _transport(store)
    upd, _bot, msg = _mk("/goal ship the rocket")
    asyncio.run(transport._on_goal(upd, _FakeCtx(["ship", "the", "rocket"])))

    tasks = store.list_tasks()
    assert len(tasks) == 1
    assert tasks[0].created_by == "user:/goal"
    assert tasks[0].status == "ready"
    assert tasks[0].title == "ship the rocket"
    # No foreground goal state was created.
    from vexis_agent.core.goal_state import GoalStateStore
    assert GoalStateStore(tmp_path / "goals.json").load(_SESSION) is None
    # User got the background ack.
    assert any("background" in r.lower() for r in msg.reply_log)


def test_fg_flag_runs_foreground_even_when_default_background(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``/goal --fg <text>`` forces the foreground loop: sets goal state
    and spawns a background dispatch (the kickoff turn)."""
    monkeypatch.setattr(
        "vexis_agent.core.yaml_config.goals_enabled", lambda: True
    )
    monkeypatch.setattr(
        "vexis_agent.core.yaml_config.goals_default_mode", lambda: "background"
    )
    monkeypatch.setattr(
        "vexis_agent.core.paths.goals_path", lambda: tmp_path / "goals.json"
    )
    transport = _transport(None)  # no kanban store needed for foreground
    spawned: list[dict[str, Any]] = []

    def _spy(bot, chat_id, user_id, text, *, queue_origin="user", label="", **kw):
        spawned.append({"text": text, "label": label})

    monkeypatch.setattr(transport, "_spawn_background_dispatch", _spy)

    upd, _bot, msg = _mk("/goal --fg ship the rocket")
    asyncio.run(
        transport._on_goal(upd, _FakeCtx(["--fg", "ship", "the", "rocket"]))
    )

    from vexis_agent.core.goal_state import GoalStateStore
    state = GoalStateStore(tmp_path / "goals.json").load(_SESSION)
    assert state is not None
    assert state.status == "active"
    assert state.goal == "ship the rocket"
    assert len(spawned) == 1
    assert spawned[0]["label"] == "goal_kickoff"
    assert spawned[0]["text"] == "ship the rocket"

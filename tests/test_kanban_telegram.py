"""Telegram /kanban handler tests.

Pure-logic tests on the parsing/formatting helpers, plus end-to-end
tests of the TelegramKanban facade against a fake Message + real
KanbanStore.

Doesn't spin up PTB — the handler signatures take generic Message-
shaped objects so we can exercise the code path with stubs.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from vexis_agent.core.kanban.db import KanbanStore
from vexis_agent.transports.telegram_kanban import (
    TelegramKanban,
    _format_show_text,
    _format_task_line,
    _md_escape,
    _parse_add_args,
    _strip_command_prefix,
    _summary_line,
)


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
    monkeypatch.setattr(
        "vexis_agent.tools.kanban.api.vexis_dir", lambda: private_root,
    )
    yield


@pytest.fixture
def store(tmp_path: Path):
    s = KanbanStore(tmp_path / "k.db")
    yield s
    s.close()


class FakeMessage:
    """Minimal stand-in for a PTB Message. Records reply_text calls."""

    def __init__(self, text: str = "") -> None:
        self.text = text
        self.replies: list[tuple[str, dict[str, Any]]] = []

    async def reply_text(self, text: str, **kwargs: Any) -> None:
        self.replies.append((text, kwargs))

    @property
    def last_reply(self) -> str | None:
        return self.replies[-1][0] if self.replies else None


class FakeUpdate:
    def __init__(self, message: FakeMessage) -> None:
        self.message = message


class FakeCtx:
    """Stand-in for PTB ContextTypes.DEFAULT_TYPE."""

    def __init__(self, args: list[str]) -> None:
        self.args = list(args)
        self.user_data: dict[str, Any] = {}


# ──────────────────────────────────────────────────────────────────
# Pure helpers
# ──────────────────────────────────────────────────────────────────


def test_parse_add_args_title_only() -> None:
    title, lane, ready = _parse_add_args("ship the API")
    assert title == "ship the API"
    assert lane is None
    assert ready is False


def test_parse_add_args_with_lane() -> None:
    title, lane, ready = _parse_add_args("ship the API @implementation")
    assert title == "ship the API"
    assert lane == "implementation"
    assert ready is False


def test_parse_add_args_with_ready_flag() -> None:
    title, lane, ready = _parse_add_args("ship the API !")
    assert title == "ship the API"
    assert lane is None
    assert ready is True


def test_parse_add_args_with_lane_and_ready() -> None:
    title, lane, ready = _parse_add_args("ship API @implementation !")
    assert title == "ship API"
    assert lane == "implementation"
    assert ready is True


def test_parse_add_args_lane_anywhere() -> None:
    """@lane can appear in the middle of the title — strips cleanly."""
    title, lane, _ = _parse_add_args("ship @ops the API")
    assert title == "ship the API"
    assert lane == "ops"


def test_strip_command_prefix_no_extra_words() -> None:
    out = _strip_command_prefix("/kanban add ship the API", "kanban", "add")
    assert out == "ship the API"


def test_strip_command_prefix_handles_bot_suffix() -> None:
    out = _strip_command_prefix("/kanban@vexis_bot add ship", "kanban", "add")
    assert out == "ship"


def test_strip_command_prefix_with_id_arg() -> None:
    out = _strip_command_prefix(
        "/kanban complete abc123 done with it", "kanban", "complete", "abc123",
    )
    assert out == "done with it"


def test_summary_line_renders_active_columns() -> None:
    out = _summary_line({"triage": 1, "todo": 2, "ready": 0, "in_progress": 1})
    assert "triage:1" in out
    assert "todo:2" in out
    # ready:0 should still appear because it's in the input dict.
    assert "ready:0" in out


def test_summary_line_empty() -> None:
    assert _summary_line({}) == "(empty board)"


def test_format_task_line_one_liner(store) -> None:
    t = store.create_task(title="hello", lane="research").to_dict()
    line = _format_task_line(t)
    assert t["id"] in line
    assert "hello" in line
    assert "research" in line


def test_format_show_text_basic(store) -> None:
    t = store.create_task(title="big task", body="long\nbody")
    store.add_comment(t.id, author="user", body="comment")
    detail = {
        "task": t.to_dict(),
        "parents": [],
        "children": [],
        "comments": [c.to_dict() for c in store.list_comments(t.id)],
        "runs": [],
        "events": [],
    }
    text = _format_show_text(detail)
    assert "big task" in text
    assert t.id in text
    assert "comment" in text


def test_md_escape_special_chars() -> None:
    out = _md_escape("hello *world* _test_ [link]")
    assert "\\*" in out
    assert "\\_" in out
    assert "\\[" in out


# ──────────────────────────────────────────────────────────────────
# TelegramKanban end-to-end (against fake message + real store)
# ──────────────────────────────────────────────────────────────────


def _make_kanban(store: KanbanStore) -> TelegramKanban:
    return TelegramKanban(lambda: store)


def test_no_args_renders_summary(store):
    k = _make_kanban(store)
    msg = FakeMessage(text="/kanban")
    update = FakeUpdate(msg)
    ctx = FakeCtx([])

    asyncio.run(k.handle(update, ctx))
    assert msg.last_reply is not None
    assert "Kanban" in msg.last_reply


def test_add_subcommand_creates_task(store):
    k = _make_kanban(store)
    msg = FakeMessage(text="/kanban add ship the API")
    update = FakeUpdate(msg)
    ctx = FakeCtx(["add", "ship", "the", "API"])

    asyncio.run(k.handle(update, ctx))
    # Task created.
    tasks = store.list_tasks()
    assert any(t.title == "ship the API" for t in tasks)
    assert "created" in msg.last_reply


def test_add_with_lane_via_at_syntax(store):
    k = _make_kanban(store)
    msg = FakeMessage(text="/kanban add ship API @implementation")
    update = FakeUpdate(msg)
    ctx = FakeCtx(["add", "ship", "API", "@implementation"])

    asyncio.run(k.handle(update, ctx))
    tasks = store.list_tasks()
    # First (most recent) task has the requested lane.
    assert tasks[0].lane == "implementation"


def test_add_with_ready_flag(store):
    k = _make_kanban(store)
    msg = FakeMessage(text="/kanban add ship API !")
    update = FakeUpdate(msg)
    ctx = FakeCtx(["add", "ship", "API", "!"])

    asyncio.run(k.handle(update, ctx))
    tasks = store.list_tasks()
    assert tasks[0].status == "ready"


def test_show_renders_detail(store):
    t = store.create_task(title="x", body="b")
    k = _make_kanban(store)
    msg = FakeMessage(text=f"/kanban show {t.id}")
    update = FakeUpdate(msg)
    ctx = FakeCtx(["show", t.id])

    asyncio.run(k.handle(update, ctx))
    assert t.id in msg.last_reply or "x" in msg.last_reply
    # reply_markup attached (inline buttons).
    assert msg.replies[-1][1].get("reply_markup") is not None


def test_complete_via_subcommand(store):
    t = store.create_task(title="x")
    k = _make_kanban(store)
    msg = FakeMessage(text=f"/kanban complete {t.id} all done")
    update = FakeUpdate(msg)
    ctx = FakeCtx(["complete", t.id, "all", "done"])

    asyncio.run(k.handle(update, ctx))
    after = store.require_task(t.id)
    assert after.status == "done"


def test_block_with_inline_reason(store):
    t = store.create_task(title="x")
    k = _make_kanban(store)
    msg = FakeMessage(text=f"/kanban block {t.id} waiting on user")
    update = FakeUpdate(msg)
    ctx = FakeCtx(["block", t.id, "waiting", "on", "user"])

    asyncio.run(k.handle(update, ctx))
    after = store.require_task(t.id)
    assert after.status == "blocked"


def test_block_without_reason_sets_pending_input(store):
    t = store.create_task(title="x")
    k = _make_kanban(store)
    msg = FakeMessage(text=f"/kanban block {t.id}")
    update = FakeUpdate(msg)
    ctx = FakeCtx(["block", t.id])

    asyncio.run(k.handle(update, ctx))
    # Pending state recorded.
    assert ctx.user_data.get("kanban_pending_input") == ("block", t.id)
    # Task NOT yet blocked.
    assert store.require_task(t.id).status != "blocked"


def test_pending_block_capture_consumes_next_text(store):
    t = store.create_task(title="x")
    k = _make_kanban(store)
    ctx = FakeCtx([])
    ctx.user_data["kanban_pending_input"] = ("block", t.id)
    next_msg = FakeMessage(text="here is the reason")
    update = FakeUpdate(next_msg)

    consumed = asyncio.run(k.maybe_capture_pending_input(update, ctx))
    assert consumed is True
    assert store.require_task(t.id).status == "blocked"
    assert "kanban_pending_input" not in ctx.user_data


def test_pending_capture_returns_false_when_no_state(store):
    k = _make_kanban(store)
    ctx = FakeCtx([])
    msg = FakeMessage(text="just a regular message")
    update = FakeUpdate(msg)
    consumed = asyncio.run(k.maybe_capture_pending_input(update, ctx))
    assert consumed is False


def test_unblock_subcommand(store):
    t = store.create_task(title="x")
    store.update_task(t.id, status="blocked")
    k = _make_kanban(store)
    msg = FakeMessage(text=f"/kanban unblock {t.id}")
    update = FakeUpdate(msg)
    ctx = FakeCtx(["unblock", t.id])

    asyncio.run(k.handle(update, ctx))
    assert store.require_task(t.id).status == "ready"


def test_assign_subcommand(store):
    t = store.create_task(title="x")
    k = _make_kanban(store)
    msg = FakeMessage(text=f"/kanban assign {t.id} research")
    update = FakeUpdate(msg)
    ctx = FakeCtx(["assign", t.id, "research"])

    asyncio.run(k.handle(update, ctx))
    assert store.require_task(t.id).lane == "research"


def test_archive_subcommand(store):
    t = store.create_task(title="x")
    k = _make_kanban(store)
    msg = FakeMessage(text=f"/kanban archive {t.id}")
    update = FakeUpdate(msg)
    ctx = FakeCtx(["archive", t.id])

    asyncio.run(k.handle(update, ctx))
    assert store.require_task(t.id).status == "archived"


def test_lanes_subcommand_lists_defaults(store):
    k = _make_kanban(store)
    msg = FakeMessage(text="/kanban lanes")
    update = FakeUpdate(msg)
    ctx = FakeCtx(["lanes"])

    asyncio.run(k.handle(update, ctx))
    reply = msg.last_reply or ""
    for d in ("research", "implementation", "review"):
        assert d in reply


def test_unknown_subcommand_emits_usage(store):
    k = _make_kanban(store)
    msg = FakeMessage(text="/kanban frobnicate")
    update = FakeUpdate(msg)
    ctx = FakeCtx(["frobnicate"])

    asyncio.run(k.handle(update, ctx))
    assert "Usage" in (msg.last_reply or "")


def test_disabled_when_store_is_none():
    """Provider returning None means kanban is disabled — replies with
    the disabled note instead of crashing."""
    k = TelegramKanban(lambda: None)
    msg = FakeMessage(text="/kanban")
    update = FakeUpdate(msg)
    ctx = FakeCtx([])

    asyncio.run(k.handle(update, ctx))
    assert "disabled" in (msg.last_reply or "").lower()


# ──────────────────────────────────────────────────────────────────
# Inline-callback path
# ──────────────────────────────────────────────────────────────────


class FakeCallbackQuery:
    def __init__(self, data: str) -> None:
        self.data = data
        self.answered: list[str] = []

    async def answer(self, text: str = "") -> None:
        self.answered.append(text)


class FakeUpdateCb:
    def __init__(self, cq: FakeCallbackQuery) -> None:
        self.callback_query = cq


def test_callback_complete_finalises_task(store):
    t = store.create_task(title="x")
    k = _make_kanban(store)
    cq = FakeCallbackQuery(f"kanban:complete:{t.id}")
    update = FakeUpdateCb(cq)
    ctx = FakeCtx([])
    consumed = asyncio.run(k.handle_callback(update, ctx))
    assert consumed is True
    assert store.require_task(t.id).status == "done"
    assert cq.answered  # at least one answer recorded


def test_callback_block_sets_pending_input(store):
    t = store.create_task(title="x")
    k = _make_kanban(store)
    cq = FakeCallbackQuery(f"kanban:block:{t.id}")
    update = FakeUpdateCb(cq)
    ctx = FakeCtx([])
    consumed = asyncio.run(k.handle_callback(update, ctx))
    assert consumed is True
    assert ctx.user_data.get("kanban_pending_input") == ("block", t.id)


def test_callback_archive_archives(store):
    t = store.create_task(title="x")
    k = _make_kanban(store)
    cq = FakeCallbackQuery(f"kanban:archive:{t.id}")
    update = FakeUpdateCb(cq)
    ctx = FakeCtx([])
    asyncio.run(k.handle_callback(update, ctx))
    assert store.require_task(t.id).status == "archived"


def test_callback_non_kanban_payload_returns_false(store):
    k = _make_kanban(store)
    cq = FakeCallbackQuery("model:set:foo")  # different feature
    update = FakeUpdateCb(cq)
    ctx = FakeCtx([])
    consumed = asyncio.run(k.handle_callback(update, ctx))
    assert consumed is False

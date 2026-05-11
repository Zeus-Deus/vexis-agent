"""Telegram /kanban command surface — wraps the action layer.

Lives in its own module so ``transports/telegram.py`` doesn't bloat
past readable. The transport imports :class:`TelegramKanban` and
calls ``handle(...)`` from its own ``_on_kanban`` method registered
via PTB's ``CommandHandler("kanban", ...)``.

Subcommand grammar (subset of the action layer; designed for thumb-
typing on a phone):

    /kanban                          → board summary
    /kanban list [lane]              → list active tasks (optional lane filter)
    /kanban show <id>                → task detail + inline buttons
    /kanban add <title>              → create in triage
    /kanban add <title> @lane        → create + assign lane
    /kanban add <title> !            → create in ready (skip triage)
    /kanban add <title> @lane !      → create in ready + lane
    /kanban complete <id> [summary]  → mark done
    /kanban block <id> <reason>      → mark blocked
    /kanban unblock <id>             → flip back to ready
    /kanban comment <id> <body>      → add comment
    /kanban archive <id>             → soft-delete
    /kanban assign <id> <lane>       → change lane
    /kanban lanes                    → list available lanes

Reasoning for the @lane / ! suffixes
====================================

Mobile typing optimisation. ``/kanban add ship the API`` is the most
common flow and shouldn't require nested flags. ``@lane`` is a short
infix (``ship the API @implementation``) and ``!`` is a one-char
"skip triage, go straight to ready" suffix. Both are optional; defaults
work without either.

Inline buttons on /kanban show
==============================

The detail message includes a row of inline buttons:

    [✅ Complete] [⛔ Block] [💬 Comment] [🗑 Archive]

Tapping triggers a CallbackQuery with payload ``kanban:<action>:<id>``.
The transport's :meth:`_on_callback` delegates to
:meth:`handle_callback`. ``Comment`` and ``Block`` open a force-reply
prompt; the next text message from the user gets captured as the
input. Capture is stateful via ``ctx.user_data``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from vexis_agent.tools.kanban import api

if TYPE_CHECKING:
    from telegram import Update
    from telegram.ext import ContextTypes

    from vexis_agent.core.kanban.db import KanbanStore

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# User-facing string templates
# ──────────────────────────────────────────────────────────────────

_DISABLED_NOTE = (
    "/kanban is disabled. Set kanban.enabled: true in "
    "~/.vexis/config.yaml and restart the daemon."
)

_USAGE = (
    "Usage:\n"
    "  /kanban — board summary\n"
    "  /kanban list [lane] — list tasks\n"
    "  /kanban show <id> — task detail\n"
    "  /kanban add <title> [@lane] [!] — create task\n"
    "  /kanban complete <id> [summary]\n"
    "  /kanban block <id> <reason>\n"
    "  /kanban unblock <id>\n"
    "  /kanban comment <id> <body>\n"
    "  /kanban archive <id>\n"
    "  /kanban assign <id> <lane>\n"
    "  /kanban lanes — list lanes"
)

# Pending-input flow: /kanban block <id> with no reason → we ask for
# the reason. Same for comment. Stored in ctx.user_data under this key.
_PENDING_INPUT_KEY = "kanban_pending_input"

_STATUS_GLYPH = {
    "triage": "🆕",
    "todo": "📋",
    "ready": "✅",
    "in_progress": "🔄",
    "blocked": "⛔",
    "done": "✔️",
    "archived": "🗄",
}


def _short(s: str | None, n: int = 60) -> str:
    if not s:
        return ""
    s = " ".join(s.split())  # collapse whitespace for one-line render
    return s if len(s) <= n else s[: n - 1] + "…"


def _summary_line(summary: dict[str, int]) -> str:
    """Compact one-line board summary: ``triage:1 todo:3 ready:0 …``"""
    order = ["triage", "todo", "ready", "in_progress", "blocked", "done"]
    parts: list[str] = []
    for k in order:
        if k in summary:
            parts.append(f"{k}:{summary[k]}")
    return "  ".join(parts) if parts else "(empty board)"


def _format_task_line(task: dict[str, Any]) -> str:
    """One-line task render for list output."""
    glyph = _STATUS_GLYPH.get(task.get("status", ""), "•")
    lane = task.get("lane") or "-"
    return f"{glyph} `{task['id']}`  [{lane}]  {_short(task.get('title') or '', 60)}"


def _format_show_text(detail: dict[str, Any]) -> str:
    t = detail["task"]
    glyph = _STATUS_GLYPH.get(t.get("status", ""), "•")
    parts = [f"{glyph} *{_md_escape(t['title'])}*"]
    parts.append(
        f"`{t['id']}`  ·  {t['status']}  ·  lane: {t.get('lane') or '(none)'}"
        f"  ·  priority: {t.get('priority', 0)}"
    )
    if t.get("body"):
        parts.append(f"\n{_md_escape(_short(t['body'], 400))}")
    if detail.get("parents"):
        parts.append(f"\nparents: {', '.join(detail['parents'])}")
    if detail.get("children"):
        parts.append(f"children: {', '.join(detail['children'])}")
    if detail.get("comments"):
        parts.append("\ncomments:")
        for c in detail["comments"][-5:]:
            parts.append(
                f"  • [{_md_escape(c['author'])}] "
                f"{_md_escape(_short(c.get('body') or '', 80))}"
            )
    if detail.get("runs"):
        latest = detail["runs"][0]
        parts.append(
            f"\nlatest run: {latest.get('outcome') or 'in progress'}"
            f"  ({len(detail['runs'])} total)"
        )
    return "\n".join(parts)


def _md_escape(s: str) -> str:
    """Markdown V2 escape for safe Telegram rendering. Conservative —
    only escapes the characters that break our own templates."""
    if not s:
        return ""
    return (
        s.replace("\\", "\\\\")
         .replace("*", "\\*")
         .replace("_", "\\_")
         .replace("`", "\\`")
         .replace("[", "\\[")
    )


# ──────────────────────────────────────────────────────────────────
# Subcommand parsing
# ──────────────────────────────────────────────────────────────────


def _parse_add_args(args_text: str) -> tuple[str, str | None, bool]:
    """Parse the tail of ``/kanban add <text>`` into (title, lane, ready_flag).

    ``@lane`` and ``!`` may appear anywhere in the tail; they're
    stripped from the title.
    """
    tokens = args_text.split()
    lane: str | None = None
    ready = False
    title_parts: list[str] = []
    for tok in tokens:
        if tok.startswith("@") and len(tok) > 1 and lane is None:
            lane = tok[1:]
        elif tok == "!":
            ready = True
        else:
            title_parts.append(tok)
    return (" ".join(title_parts).strip(), lane, ready)


# ──────────────────────────────────────────────────────────────────
# TelegramKanban facade
# ──────────────────────────────────────────────────────────────────


class TelegramKanban:
    """Stateless command facade. The transport instantiates one and
    forwards updates to :meth:`handle` / :meth:`handle_callback`."""

    def __init__(self, store_provider: "callable[[], KanbanStore | None]") -> None:
        # store_provider is a callable so the transport doesn't have to
        # hold the store directly — it's owned by the daemon. None
        # return = kanban not available (e.g. disabled by config).
        self._store_provider = store_provider

    # ─── slash command entry ─────────────────────────────────────

    async def handle(
        self,
        update: "Update",
        ctx: "ContextTypes.DEFAULT_TYPE",
    ) -> None:
        """Top-level /kanban dispatcher. The transport's ``_on_kanban``
        delegates here after the standard auth check."""
        msg = update.message
        if msg is None:
            return
        store = self._store_provider()
        if store is None:
            await msg.reply_text(_DISABLED_NOTE)
            return

        args = ctx.args or []
        if not args:
            return await self._cmd_summary(msg, store)
        sub = args[0].lower()
        rest = list(args[1:])

        if sub == "list":
            return await self._cmd_list(msg, store, rest)
        if sub == "show":
            return await self._cmd_show(msg, store, rest)
        if sub == "add":
            # Use raw text so punctuation in titles is preserved.
            raw = msg.text or ""
            tail = _strip_command_prefix(raw, "kanban", "add")
            return await self._cmd_add(msg, store, tail)
        if sub == "complete":
            return await self._cmd_complete(msg, store, rest, msg.text or "")
        if sub == "block":
            return await self._cmd_block(msg, store, rest, msg.text or "", ctx)
        if sub == "unblock":
            return await self._cmd_unblock(msg, store, rest)
        if sub == "comment":
            return await self._cmd_comment(msg, store, rest, msg.text or "", ctx)
        if sub == "archive":
            return await self._cmd_archive(msg, store, rest)
        if sub == "assign":
            return await self._cmd_assign(msg, store, rest)
        if sub == "lanes":
            return await self._cmd_lanes(msg, store)
        await msg.reply_text(_USAGE)

    # ─── subcommand handlers ─────────────────────────────────────

    async def _cmd_summary(self, msg, store) -> None:
        result = api.list_board(store)
        if not result.get("ok"):
            await msg.reply_text(f"error: {result.get('error')}")
            return
        summary = result["data"]["summary"]
        tasks = result["data"]["tasks"]
        lines = [
            f"📋 *Kanban*  ·  {_summary_line(summary)}",
        ]
        if not tasks:
            lines.append("\n_(empty board — `/kanban add <title>` to create one)_")
        else:
            lines.append("")
            for t in tasks[:15]:
                lines.append(_format_task_line(t))
            if len(tasks) > 15:
                lines.append(f"\n…and {len(tasks) - 15} more · `/kanban list`")
        await _safe_send_md(msg, "\n".join(lines))

    async def _cmd_list(self, msg, store, args: list[str]) -> None:
        lane = args[0] if args else None
        result = api.list_board(store, lane=lane)
        if not result.get("ok"):
            await msg.reply_text(f"error: {result.get('error')}")
            return
        tasks = result["data"]["tasks"]
        if not tasks:
            await msg.reply_text("(no matching tasks)")
            return
        lines = [_format_task_line(t) for t in tasks]
        await _safe_send_md(msg, "\n".join(lines))

    async def _cmd_show(self, msg, store, args: list[str]) -> None:
        if not args:
            await msg.reply_text("usage: /kanban show <id>")
            return
        result = api.show_task(store, args[0])
        if not result.get("ok"):
            await msg.reply_text(f"error: {result.get('error')}")
            return
        text = _format_show_text(result["data"])
        kb = _show_inline_keyboard(args[0])
        await _safe_send_md(msg, text, reply_markup=kb)

    async def _cmd_add(self, msg, store, tail: str) -> None:
        if not tail.strip():
            await msg.reply_text("usage: /kanban add <title> [@lane] [!]")
            return
        title, lane, ready = _parse_add_args(tail)
        if not title:
            await msg.reply_text("title cannot be empty (after stripping @lane/!)")
            return
        status = "ready" if ready else None
        result = api.create_task(
            store, title=title, lane=lane, status=status,
            created_by="user",
        )
        if not result.get("ok"):
            await msg.reply_text(f"error: {result.get('error')}")
            return
        t = result["data"]
        glyph = _STATUS_GLYPH.get(t["status"], "•")
        await msg.reply_text(
            f"{glyph} created `{t['id']}`: {t['title']}"
            f" [{t['status']}{', ' + (t.get('lane') or '') if t.get('lane') else ''}]",
            parse_mode="Markdown",
        )

    async def _cmd_complete(self, msg, store, args: list[str], raw_text: str) -> None:
        if not args:
            await msg.reply_text("usage: /kanban complete <id> [summary]")
            return
        tid = args[0]
        summary = _strip_command_prefix(raw_text, "kanban", "complete", tid).strip() or None
        result = api.complete_task(
            store, tid, summary=summary, author="user",
        )
        if not result.get("ok"):
            await msg.reply_text(f"error: {result.get('error')}")
            return
        await msg.reply_text(f"✔️ completed: `{tid}`", parse_mode="Markdown")

    async def _cmd_block(
        self, msg, store, args: list[str], raw_text: str, ctx,
    ) -> None:
        if not args:
            await msg.reply_text("usage: /kanban block <id> <reason>")
            return
        tid = args[0]
        reason = _strip_command_prefix(raw_text, "kanban", "block", tid).strip()
        if not reason:
            # Capture the next text message as the reason via state.
            ctx.user_data[_PENDING_INPUT_KEY] = ("block", tid)
            await msg.reply_text(f"reason for blocking `{tid}`?")
            return
        result = api.block_task(
            store, tid, reason=reason, author="user",
        )
        if not result.get("ok"):
            await msg.reply_text(f"error: {result.get('error')}")
            return
        await msg.reply_text(f"⛔ blocked `{tid}`: {reason}", parse_mode="Markdown")

    async def _cmd_unblock(self, msg, store, args: list[str]) -> None:
        if not args:
            await msg.reply_text("usage: /kanban unblock <id>")
            return
        result = api.unblock_task(store, args[0], author="user")
        if not result.get("ok"):
            await msg.reply_text(f"error: {result.get('error')}")
            return
        await msg.reply_text(
            f"unblocked `{args[0]}` → {result['data']['status']}",
            parse_mode="Markdown",
        )

    async def _cmd_comment(
        self, msg, store, args: list[str], raw_text: str, ctx,
    ) -> None:
        if not args:
            await msg.reply_text("usage: /kanban comment <id> <body>")
            return
        tid = args[0]
        body = _strip_command_prefix(raw_text, "kanban", "comment", tid).strip()
        if not body:
            ctx.user_data[_PENDING_INPUT_KEY] = ("comment", tid)
            await msg.reply_text(f"comment text for `{tid}`?")
            return
        result = api.comment_on_task(
            store, tid, body=body, author="user",
        )
        if not result.get("ok"):
            await msg.reply_text(f"error: {result.get('error')}")
            return
        await msg.reply_text(f"💬 added comment to `{tid}`", parse_mode="Markdown")

    async def _cmd_archive(self, msg, store, args: list[str]) -> None:
        if not args:
            await msg.reply_text("usage: /kanban archive <id>")
            return
        result = api.archive_task(store, args[0])
        if not result.get("ok"):
            await msg.reply_text(f"error: {result.get('error')}")
            return
        await msg.reply_text(f"🗄 archived `{args[0]}`", parse_mode="Markdown")

    async def _cmd_assign(self, msg, store, args: list[str]) -> None:
        if len(args) < 2:
            await msg.reply_text("usage: /kanban assign <id> <lane>")
            return
        tid, lane = args[0], args[1]
        result = api.assign_lane(store, tid, lane=lane)
        if not result.get("ok"):
            await msg.reply_text(f"error: {result.get('error')}")
            return
        await msg.reply_text(
            f"assigned `{tid}` → {result['data']['lane']}",
            parse_mode="Markdown",
        )

    async def _cmd_lanes(self, msg, store) -> None:
        result = api.list_lanes_info(store)
        if not result.get("ok"):
            await msg.reply_text(f"error: {result.get('error')}")
            return
        lines = ["*Lanes*"]
        for lane in result["data"]["lanes"]:
            tier = lane.get("tier") or "default"
            lines.append(
                f"  `{lane['name']}` (tier: {tier}) — "
                f"{_short(lane.get('description') or '', 60)}"
            )
        await _safe_send_md(msg, "\n".join(lines))

    # ─── inline-button callbacks ─────────────────────────────────

    async def handle_callback(
        self,
        update: "Update",
        ctx: "ContextTypes.DEFAULT_TYPE",
    ) -> bool:
        """Handle a CallbackQuery whose payload starts with ``kanban:``.
        Returns ``True`` if we handled it, ``False`` if not (transport
        falls through to other callback handlers).
        """
        cq = update.callback_query
        if cq is None or cq.data is None:
            return False
        if not cq.data.startswith("kanban:"):
            return False
        store = self._store_provider()
        if store is None:
            await cq.answer("kanban disabled")
            return True
        try:
            _, action, tid = cq.data.split(":", 2)
        except ValueError:
            await cq.answer("bad payload")
            return True
        if action == "complete":
            result = api.complete_task(store, tid, author="user")
            await cq.answer("completed" if result["ok"] else f"err: {result.get('error', '')[:30]}")
            return True
        if action == "archive":
            result = api.archive_task(store, tid)
            await cq.answer("archived" if result["ok"] else f"err: {result.get('error', '')[:30]}")
            return True
        if action == "block":
            ctx.user_data[_PENDING_INPUT_KEY] = ("block", tid)
            await cq.answer("send the block reason as your next message")
            return True
        if action == "comment":
            ctx.user_data[_PENDING_INPUT_KEY] = ("comment", tid)
            await cq.answer("send the comment body as your next message")
            return True
        await cq.answer("unknown action")
        return True

    # ─── pending-input capture ───────────────────────────────────

    async def maybe_capture_pending_input(
        self,
        update: "Update",
        ctx: "ContextTypes.DEFAULT_TYPE",
    ) -> bool:
        """If the user has a pending block/comment input, capture this
        text message as that input. Returns ``True`` if consumed, in
        which case the transport SHOULD NOT forward this message to the
        brain. ``False`` means proceed normally."""
        msg = update.message
        if msg is None or msg.text is None:
            return False
        pending = ctx.user_data.get(_PENDING_INPUT_KEY)
        if not pending:
            return False
        action, tid = pending
        ctx.user_data.pop(_PENDING_INPUT_KEY, None)
        store = self._store_provider()
        if store is None:
            return False
        text = msg.text.strip()
        if not text:
            await msg.reply_text("(empty input — cancelled)")
            return True
        if action == "block":
            result = api.block_task(store, tid, reason=text, author="user")
            await msg.reply_text(
                f"⛔ blocked `{tid}`" if result["ok"] else f"err: {result.get('error')}",
                parse_mode="Markdown",
            )
            return True
        if action == "comment":
            result = api.comment_on_task(store, tid, body=text, author="user")
            await msg.reply_text(
                f"💬 added comment to `{tid}`" if result["ok"] else f"err: {result.get('error')}",
                parse_mode="Markdown",
            )
            return True
        return False


# ──────────────────────────────────────────────────────────────────
# Inline keyboard
# ──────────────────────────────────────────────────────────────────


def _show_inline_keyboard(task_id: str):
    """Build the row of action buttons for /kanban show."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✔️ Complete", callback_data=f"kanban:complete:{task_id}"),
        InlineKeyboardButton("⛔ Block", callback_data=f"kanban:block:{task_id}"),
        InlineKeyboardButton("💬 Comment", callback_data=f"kanban:comment:{task_id}"),
        InlineKeyboardButton("🗄 Archive", callback_data=f"kanban:archive:{task_id}"),
    ]])


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _strip_command_prefix(raw_text: str, *prefix_words: str) -> str:
    """Strip ``/<cmd>`` and any subsequent literal words from the start
    of a Telegram message. Used so we can recover the raw tail after
    a ``/kanban add ...`` for free-form title input.

    Example:
        _strip_command_prefix("/kanban add ship the API", "kanban", "add")
        → "ship the API"
    """
    if not raw_text:
        return ""
    text = raw_text.strip()
    if not text.startswith("/"):
        return text
    # Drop the leading "/word" (with optional @bot suffix) — that's
    # the command itself.
    space_idx = text.find(" ")
    if space_idx < 0:
        return ""
    text = text[space_idx + 1:]
    # Strip the prefix_words (subcommand + any positional args we
    # already parsed) from the front.
    for word in prefix_words[1:]:  # skip the cmd name (already stripped)
        word = word.strip()
        if not word:
            continue
        if text.lower().startswith(word.lower()):
            after = text[len(word):]
            if after and after[0] != " ":
                # Word boundary mismatch — bail out.
                break
            text = after.lstrip()
        else:
            break
    return text


async def _safe_send_md(msg, text: str, *, reply_markup=None) -> None:
    """Send with Markdown parse mode, falling back to plain text on
    Telegram's "can't parse entities" error so a stray underscore in
    a task title doesn't black-hole the reply."""
    try:
        await msg.reply_text(
            text, parse_mode="Markdown", reply_markup=reply_markup,
        )
    except Exception as exc:
        log.debug("kanban telegram: markdown send failed (%s); plain", exc)
        await msg.reply_text(text, reply_markup=reply_markup)


__all__ = [
    "TelegramKanban",
    "_format_show_text",
    "_format_task_line",
    "_parse_add_args",
    "_strip_command_prefix",
    "_summary_line",
]

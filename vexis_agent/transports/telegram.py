"""Telegram transport: PTB Application + chunked sending + typing indicator."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from telegram import (
    BotCommand as TelegramBotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler as PtbMessageHandler,
    filters,
)

from vexis_agent.core.auth import is_allowed
from vexis_agent.core.background_tasks import (
    BackgroundTasks,
    TaskNotFound,
    TaskStatus,
)
from vexis_agent.core import tailscale as tailscale_mod
from vexis_agent.core.commands import COMMANDS
from vexis_agent.core.curator import CuratorController
from vexis_agent.core.learning_curator import LearningController
from vexis_agent.core.handler import MessageHandler
from vexis_agent.core.notify import Notifier
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionInfo
from vexis_agent.core.status import StatusSnapshot, cleanup_all as cleanup_status_files, read_status
from vexis_agent.core.web_server import WebDashboard
from vexis_agent.core.yaml_config import (
    telegram_streaming_enabled,
    telegram_streaming_min_interval_seconds,
)
from vexis_agent.tools.capture_source import (
    CaptureSourceError,
    RouterContext,
    caption_hint,
    caption_label,
    resolve_source,
)
from vexis_agent.tools.desktop import CaptureError, capture_desktop
from vexis_agent.tools.session_lock import is_session_locked
from vexis_agent.tools.voxtype import TranscriptionEmpty, TranscriptionError, transcribe_audio

log = logging.getLogger(__name__)

_TYPING_REFRESH_SECONDS = 4
_MAX_CHUNK = 4000
# Streaming reply path. Telegram's hard message ceiling is 4096
# chars; we leave 296 chars of headroom so the rollover boundary
# search (paragraph break → line break → hard cut) has room to
# pick a clean break without overflowing.
_STREAMING_ROLLOVER_THRESHOLD = 3800
# Initial bubble shown to the user immediately so they know the
# brain heard them. Telegram refuses to edit-to-empty-string, so
# this same character is the safe fallback whenever an edit would
# otherwise blank the bubble.
_STREAMING_PLACEHOLDER = "…"
_VOICE_ECHO_PREFIX = "🎙️ "
_VOICE_BRAIN_TAG = "[transcribed voice memo] "
_PICKING_UP_PREFIX = "Picking up: "
_PICKING_UP_PREVIEW_LEN = 40
_PICKING_UP_FALLBACK = "(empty)"
_INCOMING_IMAGE_PREFIX_RE = re.compile(r"^\[user sent image: [^\]]+\]\s*")
_DRAIN_TURN_BROKE = "⚠️ Something broke handling that. Logs have details."
_TRANSCRIPTION_EMPTY = "⚠️ Couldn't hear anything in that. Try again?"
_TRANSCRIPTION_FAILED = "⚠️ Couldn't transcribe that. Logs have details."
_VOICE_TOO_SHORT = "That voice memo was too short to transcribe."
_MIN_VOICE_DURATION_SECONDS = 1
_DELETE_CONFIRM_WINDOW = timedelta(seconds=60)
_CB_DATA_MAX_BYTES = 64
_HIDDEN_NOTE = "(Some sessions hidden — type the name directly to use them.)"
_SCREENSHOT_PATH_RE = re.compile(r"(?<![\w/])/tmp/vexis-screenshot-\d+\.png")
# Browser screenshots land in the user's workspace at
# ``<workspace>/browser/screenshots/<ts>.png`` where ``<ts>`` is the
# fixed UTC stamp ``YYYYMMDDTHHMMSSZ``. We anchor on the suffix
# ``/browser/screenshots/<ts>.png`` so the regex doesn't bake in a
# specific workspace location.
_BROWSER_SCREENSHOT_PATH_RE = re.compile(
    r"(?<![\w])(?:/[\w.-]+)+/browser/screenshots/\d{8}T\d{6}Z\.png"
)
_INCOMING_PHOTO_DIR = Path("/tmp")
_INCOMING_PHOTO_GLOB = "vexis-incoming-*.png"
_INCOMING_PHOTO_MAX_AGE = timedelta(hours=1)
_INCOMING_PHOTO_CLEANUP_INTERVAL_SECONDS = 600
_INCOMING_BRAIN_PREFIX = "[user sent image: {path}]"
_CANCEL_OK = "Cancelled, sir. What next?"
_NOTHING_TO_CANCEL = "Nothing to cancel — I'm not working on anything right now."
# /goal user-facing strings. Kept module-level so tests can import
# them without rendering their own copies. The §4 command matrix in
# `.plans/goal-command-research.md` is the source of truth for the
# wording — drift surfaces as a test failure.
# /model UX templates (Day 2 of model-management UX research).
# Source-of-truth for wording: ``.plans/model-management-ux-research.md``
# §4. Drift surfaces as test failures.
_MODEL_DISABLED_NOTE = (
    "/model is disabled. Set model_ux.enabled: true in "
    "~/.vexis/config.yaml to turn it on. (Off by default until "
    "the Day 5 dogfood pass clears.)"
)
_MODEL_USAGE = (
    "/model — show current resolution\n"
    "/model list — enumerate subsystems + brains\n"
    "/model list <brain> — list models for that brain\n"
    "/model set brain <name> — change brain.kind (restart required)\n"
    "/model set <subsystem> <tier-or-name> — set subsystem assignment\n"
    "/model set <subsystem> — picker: tap a provider then a model\n"
    "/model refresh — refresh opencode discovery cache\n"
    "/model reset [<subsystem>] — back to defaults"
)
_MODEL_INVALID_BRAIN_KIND_TMPL = (
    "Won't write — '{kind}' is not a valid brain.kind. "
    "Use one of: claude-code, opencode, null."
)
_MODEL_VALIDATOR_ERROR_TMPL = (
    "Won't write — validator rejected the proposed config:\n"
    "{problems}\n"
    "Fix and re-try, or /model status verbose for the full report."
)
_MODEL_SET_OK_TMPL = (
    "✓ {key} → {value} (resolves to {resolved} on {brain})\n"
    "Takes effect on the next {key} call."
)
_MODEL_SET_BRAIN_OK_TMPL = (
    "✓ brain.kind → {kind}\n"
    "⚠ Restart vexis to take effect (e.g. systemctl --user "
    "restart vexis-agent). brain.kind is read once at startup."
)
_MODEL_RESET_OK_TMPL = (
    "✓ Reset {scope}. New resolution table available via /model."
)
_MODEL_BACKUP_REPLY_TMPL = (
    "📋 Backed up commented config to ~/.vexis/config.yaml.bak. "
    "Future edits won't re-back-up until you re-add comments to "
    "your config."
)

# Day 3 of model picker UX — picker flow templates. Source-of-truth
# for wording: ``.plans/model-picker-ux-research.md`` §5. Drift
# surfaces as test failures.
_PICKER_PROMPT_TMPL = (
    "Pick a model for {subsystem} (currently: {current}). "
    "Tap a provider, then a model. Aliases (haiku/sonnet/opus) "
    "are omitted from the picker — use /model set {subsystem} "
    "<alias> directly to keep using one."
)
_PICKER_MODEL_PROMPT_TMPL = (
    "{subsystem} → {provider}. Tap a model{page_suffix}.{hidden_suffix}"
)
# Surfaces in the model picker reply only when the collapsed
# default view is hiding dated variants. Wording mirrors
# ``.plans/model-picker-ux-research.md`` family-grouping spec
# (auto-tracking-latest by default; pin a specific date via
# the toggle).
_PICKER_HIDDEN_VERSIONS_TMPL = (
    " ({n} older versions hidden — tap [Show all versions] to "
    "pin a specific date.)"
)
# Reasoning-step prompt — added 2026-05-08. Surfaces after the
# model step when the chosen model exposes reasoning levels via
# the brain's capability discovery (effort levels on claude-code,
# variant names on opencode). Models that don't expose any
# reasoning level skip this step entirely.
_PICKER_REASONING_PROMPT_TMPL = (
    "{subsystem} → {model}. Pick a reasoning level (or default)."
)
# Cross-brain switch templates (added 2026-05-08).
_PICKER_CROSS_BRAIN_CONFIRM_TMPL = (
    "{model} runs on {target_brain} (current: {current_brain}).\n\n"
    "Switching writes both brain.kind AND your {subsystem} assignment, "
    "then restarts vexis. In-flight turns drop; scheduled fires within "
    "the next ~30s might miss.{supervisor_note}\n\n"
    "(Reasoning level isn't picked across the switch — re-run "
    "/model set {subsystem} after restart if you want one.)"
)
_PICKER_CROSS_BRAIN_NO_SUPERVISOR_TMPL = (
    "\n\n⚠ No supervisor detected (INVOCATION_ID unset). Daemon will "
    "exit; you'll need to restart manually."
)
_MODEL_CROSS_BRAIN_SWITCHING_TMPL = (
    "✓ Switching brain.kind → {target_brain}, {subsystem} → {model}.\n"
    "Restarting now…{supervisor_note}"
)
_MODEL_CROSS_BRAIN_BRAIN_NOT_CONFIGURED_TMPL = (
    "Won't write — model {model!r} requires brain '{required}', which "
    "isn't configured on this system.\n\n"
    "Install + auth steps for {required}:\n{install}"
)
_INSTALL_HINT_CLAUDE_CODE = (
    "  curl -fsSL https://claude.ai/install.sh | bash\n"
    "  claude  # login on first run"
)
_INSTALL_HINT_OPENCODE = (
    "  curl -fsSL https://opencode.ai/install | bash\n"
    "  opencode providers login"
)
_PICKER_NO_DISCOVERY_TMPL = (
    "No discovered models for brain '{brain}'. Use the typed-arg "
    "path:\n  /model set {subsystem} <model-name>\n"
    "or run /model refresh to retry discovery."
)
_PICKER_STALE_SUBSYSTEM_TMPL = (
    "This picker references an unknown subsystem id. Re-issue "
    "/model set {subsystem} to start a fresh picker."
)
_MODEL_REFRESH_NOOP_TMPL = (
    "Current brain: {brain} has no live discovery to refresh. "
    "/model refresh is meaningful on claude-code (Anthropic /v1/models) "
    "and opencode (`opencode models --refresh`)."
)
_MODEL_REFRESH_OK_TMPL = (
    "✓ Refreshed opencode discovery cache.\n{counts}"
)
_MODEL_REFRESH_EMPTY_TMPL = (
    "Discovery refresh ran but returned no models. Is opencode "
    "installed and authenticated? (Check `opencode models` in a "
    "shell.)"
)


_GOAL_DISABLED_NOTE = (
    "/goal is disabled. Set goals.enabled: true in ~/.vexis/config.yaml "
    "to turn it on."
)
_GOAL_NO_ACTIVE = "No active goal. Set one with /goal <text>."
_GOAL_NO_GOAL_TO_PAUSE = "No goal set."
_GOAL_NO_GOAL_TO_RESUME = "No goal to resume."
_GOAL_ALREADY_PAUSED = "Already paused."
_GOAL_PAUSE_REPLY_TMPL = (
    "⏸ Goal paused. (Current turn finishes first; loop won't auto-continue after.)\n{status}"
)
_GOAL_RESUME_REPLY_TMPL = "▶ Goal resumed: {goal}"
_GOAL_CLEAR_REPLY = "✓ Goal cleared."
_GOAL_KICKOFF_REPLY_TMPL = (
    "⊙ Goal set ({budget}-turn budget): {goal}\n"
    "I'll keep working until the goal is done, you pause/clear it, or "
    "the budget is exhausted.\n"
    "Controls: /goal status · /goal pause · /goal resume · /goal clear"
)
_GOAL_REJECT_ALREADY_ACTIVE = (
    "Goal already active. /goal clear it first or wait for the current "
    "loop to finish."
)
_GOAL_REJECT_MIDRUN = (
    "Brain is busy. /cancel first, then /goal <text>."
)
_GOAL_INVALID_TMPL = "Invalid goal: {reason}"
# Single-word inputs to /goal that almost certainly meant /cancel.
# We redirect with a hint rather than treating them as goal text or
# letting the mid-run reject hide the typo. Multi-word phrases that
# happen to start with "stop" / "cancel" / etc. ARE goal text — only
# the bareword case (after strip) hits this branch.
_GOAL_BAREWORD_CANCEL_LIKE = frozenset(
    {"cancel", "stop", "abort", "kill", "halt"}
)
_GOAL_BAREWORD_HINT = (
    "Did you mean /cancel? (Or /goal clear to drop the goal entirely.)"
)
_GOAL_ALREADY_TERMINAL_TMPL = (
    "Goal is already {status} — no action taken. /goal status to confirm, "
    "or /goal <text> to start a new one."
)
_CANCEL_OK_GOAL_PAUSED_TMPL = (
    "Cancelled, sir. (Goal paused at {turns}/{budget} turns — "
    "/goal resume to keep going.)"
)

# /schedule strings — kept short so they fit in Telegram message
# windows without truncation; longer reference material lives in
# docs/schedules.md (Day 4) and the brain's tool description (which
# the user typically doesn't see).
_SCHEDULE_DISABLED_NOTE = (
    "Scheduling is disabled. Set schedules.enabled: true in "
    "~/.vexis/config.yaml to enable."
)
_SCHEDULE_HELP = (
    "/schedule — Set reminders and recurring tasks.\n\n"
    "Examples:\n"
    "  /schedule remind me every weekday at 9am to do standup\n"
    "  /schedule in 30 minutes ping me about the build\n\n"
    "Management:\n"
    "  /schedule list           — show active schedules\n"
    "  /schedule pause <id>     — temporarily stop one\n"
    "  /schedule resume <id>    — start a paused one\n"
    "  /schedule clear <id>     — remove (audit-retained)"
)
_SCHEDULE_ACK = "On it…"
_SCHEDULE_LIST_EMPTY = (
    "No active schedules. Create one by telling me what to schedule, "
    "in chat or after /schedule."
)
_SCHEDULE_LIST_HEADER = "Schedules:"
_SCHEDULE_NOT_FOUND_TMPL = (
    "No schedule matches {id} (or prefix is ambiguous / shorter than 3 chars). "
    "Try /schedule list to see ids."
)
_SCHEDULE_ALREADY_TERMINAL_TMPL = (
    "Schedule is already {status} — no action taken. /schedule list to confirm."
)
_SCHEDULE_PAUSED_TMPL = "⏸ Schedule {id} paused. /schedule resume {id} to re-arm."
_SCHEDULE_RESUMED_TMPL = "▶ Schedule {id} resumed. Next fire: {next_fire}"
_SCHEDULE_CLEARED_TMPL = "✓ Schedule {id} cleared (record retained for audit)."
_STATUS_IDLE = "Nothing running, sir."
_STATUS_NO_TOOLS_YET = "No tool activity yet."

# Verbs for rendering the most-recent tool_use event in /status. Tools
# absent from this map fall back to "used <ToolName>".
_TOOL_VERB: dict[str, str] = {
    "Edit": "edited",
    "MultiEdit": "edited",
    "NotebookEdit": "edited",
    "Write": "wrote",
    "Read": "read",
    "Bash": "ran",
    "Task": "delegated",
    "Glob": "searched for",
    "Grep": "searched for",
    "WebFetch": "fetched",
    "WebSearch": "searched the web for",
}
_NO_BG_TASKS = "No background tasks running."
_DAEMON_RESTART_LOST = (
    "Sir, when the daemon restarted, background task `{name}` didn't survive. "
    "Want me to relaunch it?"
)


def split_for_telegram(text: str, max_len: int = _MAX_CHUNK) -> list[str]:
    """Split text into Telegram-safe chunks, preferring paragraph/line boundaries."""
    if len(text) <= max_len:
        return [text]
    for sep in ("\n\n", "\n"):
        if sep in text:
            return _greedy_join(text.split(sep), sep, max_len)
    return [text[i : i + max_len] for i in range(0, len(text), max_len)]


def _split_at_streaming_boundary(
    text: str, threshold: int,
) -> tuple[str, str]:
    """Pick a clean rollover boundary for streaming.

    Returns ``(head, tail)`` where ``head`` is the text to leave
    in the active message and ``tail`` is the remainder that
    seeds the next message. Preference order: paragraph break
    (``\\n\\n``), then line break (``\\n``), then a hard cut at
    ``threshold``. Operates only on the slice up to ``threshold``
    so we never put more than that in ``head``.

    Why a separate helper from :func:`split_for_telegram`: that
    one returns a *list* of chunks for batch sending. The
    streaming path needs a single bisection because the tail
    keeps growing as more deltas arrive.
    """
    if len(text) <= threshold:
        return text, ""
    window = text[:threshold]
    for sep in ("\n\n", "\n"):
        idx = window.rfind(sep)
        if idx > 0:
            return text[:idx], text[idx + len(sep):]
    return text[:threshold], text[threshold:]


def _build_button_rows(
    sessions: list[SessionInfo], action: str, *, skip_active: bool
) -> tuple[list[list[InlineKeyboardButton]], bool]:
    rows: list[list[InlineKeyboardButton]] = []
    hidden = False
    for s in sessions:
        if skip_active and s.is_active:
            continue
        data = f"{action}:{s.name}"
        if len(data.encode("utf-8")) > _CB_DATA_MAX_BYTES:
            hidden = True
            continue
        label = f"→ {s.name}" if s.is_active else s.name
        rows.append([InlineKeyboardButton(text=label, callback_data=data)])
    return rows, hidden


def _build_incoming_photo_path() -> Path:
    """Allocate a fresh /tmp path for an inbound Telegram photo."""
    return _INCOMING_PHOTO_DIR / f"vexis-incoming-{uuid.uuid4().hex}.png"


def _make_pickup_preview(text: str, max_len: int = _PICKING_UP_PREVIEW_LEN) -> str:
    """Render a short preview of a queued message for the 'Picking up:' ack.

    Strips the internal routing prefixes used for voice/photo so the user
    sees something they recognise from what they sent (echoed voice text,
    a 📷 marker for an image) rather than the synthetic ``[user sent
    image: /tmp/...]`` form.
    """
    cleaned = text
    if cleaned.startswith(_VOICE_BRAIN_TAG):
        cleaned = _VOICE_ECHO_PREFIX + cleaned[len(_VOICE_BRAIN_TAG) :]
    else:
        match = _INCOMING_IMAGE_PREFIX_RE.match(cleaned)
        if match:
            rest = cleaned[match.end() :].strip()
            cleaned = f"📷 {rest}" if rest else "📷"
    cleaned = cleaned.strip().replace("\n", " ")
    if not cleaned:
        return _PICKING_UP_FALLBACK
    if len(cleaned) > max_len:
        return cleaned[:max_len].rstrip() + "…"
    return cleaned


def _format_incoming_image_message(image_path: Path, caption: str | None) -> str:
    """Build the synthetic brain message for an inbound image."""
    prefix = _INCOMING_BRAIN_PREFIX.format(path=image_path)
    body = (caption or "").strip()
    if body:
        return f"{prefix} {body}"
    return prefix


def _cleanup_incoming_images(
    now: datetime,
    *,
    directory: Path = _INCOMING_PHOTO_DIR,
    max_age: timedelta = _INCOMING_PHOTO_MAX_AGE,
) -> int:
    """Delete vexis-incoming-*.png files older than max_age. Returns count removed."""
    removed = 0
    for path in directory.glob(_INCOMING_PHOTO_GLOB):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except FileNotFoundError:
            continue
        if now - mtime <= max_age:
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            log.exception("Failed to clean up incoming image %s", path)
            continue
        removed += 1
    return removed


def _extract_screenshot_paths(text: str) -> tuple[list[Path], str]:
    """Pull every screenshot path out of `text`.

    Recognises two flavours:

    - ``/tmp/vexis-screenshot-<n>.png`` — desktop captures from
      vexis-look. Ephemeral; deleted after send.
    - ``<workspace>/browser/screenshots/<ts>.png`` — captures from
      vexis-browse screenshot. Archived; left on disk after send so
      the brain (or the user) can re-reference them later.

    Returns the list of unique paths (in first-seen order) and the
    cleaned text with each match replaced by the placeholder
    ``[screenshot]`` so the surrounding prose still reads naturally.
    """
    seen: list[Path] = []
    seen_set: set[str] = set()
    for pattern in (_SCREENSHOT_PATH_RE, _BROWSER_SCREENSHOT_PATH_RE):
        for match in pattern.findall(text):
            if match in seen_set:
                continue
            seen_set.add(match)
            seen.append(Path(match))
    cleaned = _SCREENSHOT_PATH_RE.sub("[screenshot]", text)
    cleaned = _BROWSER_SCREENSHOT_PATH_RE.sub("[screenshot]", cleaned)
    return seen, cleaned


def _is_ephemeral_screenshot(path: Path) -> bool:
    """``/tmp/...`` captures are deleted after send; workspace
    screenshots are archived for later reference."""
    return path.parent == Path("/tmp")


# Telegram's Bot API caps photo uploads at 10000px in either
# dimension. Full-page browser screenshots of long pages routinely
# blow past this, so we fall back to send_document (no dimension
# cap, 50 MB file ceiling). Same upload mechanism, the image still
# appears inline in the chat — just rendered as a file attachment.
# The check is purely dimensional; nothing here is keyed on URL,
# domain, or which page produced the image.
_TELEGRAM_PHOTO_MAX_DIM = 10000


def _photo_too_large_for_telegram(path: Path) -> bool:
    """True when ``path`` exceeds Telegram's 10000px-per-dimension
    photo cap and should be sent via send_document instead. Purely
    dimensional — same predicate for any image, any source.
    """
    try:
        with Image.open(path) as img:
            width, height = img.size
    except (UnidentifiedImageError, OSError, ValueError):
        # Can't read dimensions — let the existing send_photo path
        # surface whatever the real error is.
        return False
    return width > _TELEGRAM_PHOTO_MAX_DIM or height > _TELEGRAM_PHOTO_MAX_DIM


# ---------------------------------------------------------------------------
# /screenshot source routing
# ---------------------------------------------------------------------------


def _extract_screenshot_modifier(text: str) -> str | None:
    """Strip the leading ``/screenshot`` (and optional ``@botname``)
    from the message text, returning everything else. ``None`` when
    no modifier was supplied.

    Examples:
        ``/screenshot`` → ``None``
        ``/screenshot host`` → ``"host"``
        ``/screenshot sandbox foo`` → ``"sandbox foo"``
        ``/screenshot@vexis_bot sandbox`` → ``"sandbox"``
    """
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    parts = stripped.split(None, 1)
    rest = parts[1].strip() if len(parts) == 2 else ""
    return rest or None


def _screenshot_help_text() -> str:
    return (
        "/screenshot — capture the host desktop (or a sandbox display).\n\n"
        "Modifiers:\n"
        "  /screenshot                  auto: current task's sandbox if any, "
        "else host\n"
        "  /screenshot host             real desktop\n"
        "  /screenshot sandbox          latest active sandbox\n"
        "  /screenshot sandbox <id>     specific sandbox by task-id\n"
        "  /screenshot help             this message\n\n"
        "Tip: when your host screen is locked, replying `sandbox` (or "
        "`sandbox <id>`) switches the next capture."
    )


async def _build_router_context(modifier: str | None) -> RouterContext:
    """Collect the live inputs the source router needs.

    Lazy imports + best-effort probes: a missing docker or a failing
    loginctl must not break the screenshot path, so we fall back to
    empty/false rather than raising.
    """
    active: tuple[str, ...] = tuple()
    try:
        from vexis_agent.tools.sandbox import Sandbox  # type: ignore[import-not-found]

        rows = await asyncio.to_thread(Sandbox.list_all)
        active = tuple(r["task_id"] for r in rows if r.get("running"))
    except Exception as exc:
        log.debug("Sandbox.list_all probe failed: %s", exc)

    try:
        host_locked = await is_session_locked()
    except Exception as exc:
        log.debug("session-lock probe failed: %s", exc)
        host_locked = False

    return RouterContext(
        requested=modifier,
        current_task_id=os.environ.get("VEXIS_SANDBOX_TASK_ID"),
        active_sandbox_task_ids=active,
        host_locked=host_locked,
    )


def _format_screenshot_caption(source, ctx: RouterContext, summary: str) -> str | None:
    """Build the Telegram photo caption.

    Layout: ``📺 Host — <summary>`` or ``📦 Sandbox <id> — <summary>``,
    plus an appended ``\\n⚠️ <hint>`` line when applicable.
    """
    emoji = "📺" if source.kind == "host" else "📦"
    head = f"{emoji} {caption_label(source)}"
    if summary:
        head = f"{head} — {summary}"
    hint = caption_hint(source, ctx)
    if hint:
        return f"{head}\n⚠️ {hint}"
    return head


def _greedy_join(parts: list[str], sep: str, max_len: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for part in parts:
        candidate = f"{current}{sep}{part}" if current else part
        if len(candidate) <= max_len:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(part) > max_len:
            sub = split_for_telegram(part, max_len)
            chunks.extend(sub[:-1])
            current = sub[-1]
        else:
            current = part
    if current:
        chunks.append(current)
    return chunks


class TelegramTransport:
    def __init__(
        self,
        token: str,
        handler: MessageHandler,
        running_tasks: RunningTasks,
        allowed_user_id: int,
        background_tasks: BackgroundTasks,
        notifier: Notifier,
        curator: "CuratorController | None" = None,
        learning_curator: "LearningController | None" = None,
        dashboard: "WebDashboard | None" = None,
        schedule_store: "object | None" = None,  # ScheduleStore; weak-typed to avoid import cycle
        kanban_store: "object | None" = None,  # KanbanStore; weak-typed to avoid import cycle
    ) -> None:
        self._handler = handler
        self._running_tasks = running_tasks
        self._background_tasks = background_tasks
        self._notifier = notifier
        self._allowed_user_id = allowed_user_id
        self._curator = curator
        self._learning_curator = learning_curator
        self._dashboard = dashboard
        self._schedule_store = schedule_store
        self._kanban_store = kanban_store
        # Wire the kanban command facade. The provider closure lets
        # us swap kanban_store at runtime without re-wiring the handler
        # (a future toggle could enable/disable kanban via dashboard).
        from vexis_agent.transports.telegram_kanban import TelegramKanban
        self._kanban = TelegramKanban(lambda: self._kanban_store)
        # Telegram bot commands can't contain hyphens, so /confirm-delete from
        # the spec becomes /confirm_delete here.
        self._pending_deletes: dict[str, datetime] = {}
        # Per-process picker session state. Keyed on (chat_id,
        # message_id) of the picker reply; value is the partial
        # selection accumulated mid-flow (after a model is picked
        # but before a reasoning level is). Used by the multi-step
        # picker so the reasoning callback can recover the
        # already-chosen model without exceeding the 64-byte
        # callback_data budget. Dies on daemon restart — user
        # re-issues /model set <sub> and starts a fresh picker.
        self._picker_pending: dict[tuple[int, int], dict] = {}
        # Streaming reply config (see docs/telegram-streaming.md).
        # Read once at startup to avoid hot-path disk reads on every
        # turn — the rest of vexis follows the same pattern for
        # transport-level flags. Toggling at runtime requires daemon
        # restart, same as ``brain.kind``. See yaml_config for the
        # rate-limit math behind the default 1.0s edit cadence.
        self._streaming_enabled = telegram_streaming_enabled()
        self._streaming_min_interval = telegram_streaming_min_interval_seconds()
        # Tasks for kickoffs we deliberately do NOT await in the PTB
        # handler (today: /goal kickoff). Without a strong reference
        # asyncio will GC a still-running fire-and-forget task and
        # the drain dies silently mid-flight — see CPython issue
        # gh-91887 and PEP 484 docs on ``asyncio.create_task``.
        # ``_spawn_background_dispatch`` adds entries here and a
        # done-callback prunes them; ``flush_background_dispatch``
        # is the test-side drain.
        self._background_dispatch_tasks: set[asyncio.Task] = set()
        # Outcome callback for scheduled fires. Set by main.py to
        # ``schedule_manager.report_fire_outcome``. The drain calls
        # this AFTER each brain turn whose QueuedMessage carries a
        # ``schedule_id``, so the schedule's ``last_status`` reflects
        # the actual brain outcome instead of the pre-emptive "ok"
        # the dispatcher writes at enqueue time. ``None`` is the
        # legacy / test wiring (no callback ⇒ no outcome reporting,
        # behaviour identical to pre-fix).
        # Signature: ``(schedule_id: str, *, success: bool,
        #              error_message: str | None,
        #              is_permanent: bool) -> None``
        self._schedule_outcome_cb = None
        # concurrent_updates(True) is load-bearing for /cancel: PTB's default
        # serializes every update through one task, so a /cancel sent while
        # a brain call is in flight queues behind it for up to 30 minutes
        # and the user's "Cancelled, sir" reply never arrives in time.
        self._app = Application.builder().token(token).concurrent_updates(True).build()
        self._app.add_handler(
            PtbMessageHandler(filters.TEXT & ~filters.COMMAND, self._on_text)
        )
        self._app.add_handler(PtbMessageHandler(filters.VOICE, self._on_voice))
        self._app.add_handler(PtbMessageHandler(filters.PHOTO, self._on_photo))
        self._app.add_handler(CommandHandler("clear", self._on_clear))
        self._app.add_handler(CommandHandler("new", self._on_new))
        self._app.add_handler(CommandHandler("switch", self._on_switch))
        self._app.add_handler(CommandHandler("sessions", self._on_sessions))
        self._app.add_handler(CommandHandler("rename", self._on_rename))
        self._app.add_handler(CommandHandler("delete", self._on_delete))
        self._app.add_handler(CommandHandler("confirm_delete", self._on_confirm_delete))
        self._app.add_handler(CommandHandler("screenshot", self._on_screenshot))
        self._app.add_handler(CommandHandler("cancel", self._on_cancel))
        self._app.add_handler(CommandHandler("tasks", self._on_tasks))
        self._app.add_handler(CommandHandler("status", self._on_status))
        self._app.add_handler(CommandHandler("pin", self._on_pin))
        self._app.add_handler(CommandHandler("unpin", self._on_unpin))
        self._app.add_handler(CommandHandler("curator", self._on_curator))
        self._app.add_handler(CommandHandler("learning", self._on_learning))
        self._app.add_handler(CommandHandler("goal", self._on_goal))
        self._app.add_handler(CommandHandler("schedule", self._on_schedule))
        self._app.add_handler(CommandHandler("model", self._on_model))
        self._app.add_handler(CommandHandler("dashboard", self._on_dashboard))
        self._app.add_handler(CommandHandler("tailscale", self._on_tailscale))
        self._app.add_handler(CommandHandler("kanban", self._on_kanban))
        self._app.add_handler(CallbackQueryHandler(self._on_callback))

    async def _on_kanban(
        self, update: "Update", ctx: "ContextTypes.DEFAULT_TYPE",
    ) -> None:
        """/kanban dispatcher. Auth-gated; delegates to TelegramKanban
        which wraps the action layer."""
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /kanban from user_id=%s", user.id)
            return
        await self._kanban.handle(update, ctx)

    async def _run_relationships_hook(
        self, bot, chat_id: int, text: str
    ) -> None:
        """Fire the relationships trigger detector for one user turn.

        v3c Day 4a default: this hook short-circuits at function
        entry when ``relationships.explicit_consent_enabled`` is
        ``false`` (the default). Silent extraction at curator tick
        time replaces the per-message trigger detection. Set the
        flag to ``true`` in ``~/.vexis/config.yaml`` to re-enable
        the v3a/v3b explicit path.

        When the flag IS on: each turn fires sequentially against
        the JSONL state ``claude -p`` is about to append to. On a
        positive ADD/DELETE verdict the staged-ack / DELETE-receipt
        is sent BEFORE the brain dispatch (receipt-then-reply UX,
        scoping doc §3.2). On collision — ``claim_next_turn_index``
        returns None because the JSONL hasn't advanced past our
        last mint — we log warning and skip staging silently; the
        brain dispatch proceeds normally (option (a) per scoping
        doc §3.1).
        """
        # v3c Day 4a flag: zero-cost short-circuit when the legacy
        # explicit path is disabled (the default). No detector
        # call, no cursor claim, nothing.
        from vexis_agent.core.yaml_config import relationships_explicit_consent_enabled
        if not relationships_explicit_consent_enabled():
            return
        if self._learning_curator is None:
            return
        relationships = self._learning_curator.relationships_curator
        if relationships is None:
            return
        session_uuid = self._handler.current_session_uuid()
        turn_index = await self._handler.claim_next_turn_index(session_uuid)
        if turn_index is None:
            log.warning(
                "relationships.cursor_collision sess=%s "
                "(JSONL did not advance past last mint; skipping stage)",
                session_uuid,
            )
            relationships.increment_counter("cursor_collision")
            return
        try:
            result = await relationships.process_user_turn(
                text,
                session_uuid=session_uuid,
                turn_index=turn_index,
                chat_id=chat_id,
            )
        except NotImplementedError:
            # Reserved for any future verdict not yet wired. 3a/3b
            # cover ADD / DELETE / SUPERSEDE / AMBIGUOUS — none of
            # these raise.
            return
        except Exception:
            log.exception(
                "relationships hook raised; proceeding to brain"
            )
            relationships.increment_counter("hook_errors")
            return
        if result is None or not result.reply_text:
            return
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=result.reply_text,
                parse_mode=None,
            )
        except Exception:
            log.exception(
                "Failed to send relationships hook reply"
            )

    async def _on_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None or msg.text is None:
            return
        # Kanban pending-input capture: if the user just tapped
        # ``[⛔ Block]`` or ``[💬 Comment]`` on a /kanban show card,
        # this next text message IS the block reason / comment body
        # — don't forward it to the brain.
        if await self._kanban.maybe_capture_pending_input(update, ctx):
            return
        # Preempt any pending goal continuations so the user's message
        # runs after the current in-flight turn — not behind a backlog
        # of continuations that the post-turn hook enqueued.
        await self._preempt_goal_continuations(msg.chat_id)
        await self._dispatch_to_brain(msg.get_bot(), msg.chat_id, user.id, msg.text)

    async def _dispatch_to_brain(
        self,
        bot,
        chat_id: int,
        user_id: int,
        text: str,
        *,
        queue_origin: str = "user",
        schedule_id: str | None = None,
    ) -> None:
        """Submit a message to the brain.

        If a drain loop is already processing this chat, the message is
        appended to its queue silently — the user gets a "Picking up:"
        ack when the drain reaches it. Otherwise we claim the chat and
        run the drain loop ourselves until the queue is empty.

        ``queue_origin`` is the tag applied to the message when the
        chat is already claimed and we fall back to enqueue. Default
        ``"user"`` covers all real-user message paths; ``/schedule``
        passes ``"schedule_command"`` so its synthetic prompt is
        distinguishable in the queue (and survives /goal pause's
        ``goal_continuation``-only predicate).

        v3b Day 3a moved the relationships hook from this pre-claim
        position into ``_drain_chat`` so the hook fires sequentially
        per drain iteration with the brain's real session UUID and
        a JSONL-derived turn_index. See scoping doc §1.5 / §3.1.
        """
        if not is_allowed(user_id, self._allowed_user_id):
            log.warning("Rejected message from user_id=%s", user_id)
            return

        if not await self._running_tasks.claim(chat_id):
            await self._running_tasks.enqueue(
                chat_id, user_id, text, origin=queue_origin,  # type: ignore[arg-type]
                schedule_id=schedule_id,
            )
            return

        typing_task = asyncio.create_task(self._keep_typing(bot, chat_id))
        try:
            await self._drain_chat(
                bot, chat_id, user_id, text,
                first_schedule_id=schedule_id,
            )
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass
            # Belt-and-suspenders: if _drain_chat aborted via an
            # unexpected exception before its own pop_or_release ran,
            # the chat would stay permanently "claimed" and the user
            # would never get another reply without a daemon restart.
            # force_release_drain is a no-op when the drain released
            # cleanly, and logs at warning level when it had to force.
            await self._running_tasks.force_release_drain(chat_id)

    def _spawn_background_dispatch(
        self,
        bot,
        chat_id: int,
        user_id: int,
        text: str,
        *,
        label: str,
        queue_origin: str = "user",
        schedule_id: str | None = None,
    ) -> asyncio.Task:
        """Fire ``_dispatch_to_brain`` in a tracked background task.

        Used by `/goal <text>` and `/schedule <text>` kickoffs so the
        PTB command handler returns immediately after the ack reply,
        instead of being held for the duration of the entire drain
        (kickoff turn + any continuations enqueued post-turn). The
        user can keep sending messages while the brain works;
        subsequent messages still hit ``_dispatch_to_brain``, find
        the chat claimed by the spawned drain, and FIFO-enqueue
        behind it — identical semantics to Hermes' goal kickoff path
        (``gateway/run.py:_handle_goal_command`` → ``_enqueue_fifo``
        → existing drain).

        The task is added to ``self._background_dispatch_tasks`` for
        the lifetime of the dispatch — without that strong reference
        asyncio is allowed to GC a still-running task. A done-callback
        prunes the set and logs any exception (asyncio's default
        unhandled-exception path only fires at GC time, which can be
        seconds-to-minutes after the failure).

        ``label`` is a short tag ("goal_kickoff", "schedule_kickoff",
        etc.) used purely for log lines — diagnostic, not semantic.
        ``queue_origin`` is forwarded to ``_dispatch_to_brain`` so the
        synthetic text gets tagged correctly when the chat is already
        claimed and we fall back to enqueue.
        """
        task = asyncio.create_task(
            self._dispatch_to_brain(
                bot, chat_id, user_id, text,
                queue_origin=queue_origin,
                schedule_id=schedule_id,
            ),
            name=f"vexis-bg-dispatch-{label}-{chat_id}",
        )
        self._background_dispatch_tasks.add(task)

        def _on_done(t: asyncio.Task, *, _label: str = label) -> None:
            self._background_dispatch_tasks.discard(t)
            if t.cancelled():
                log.info("background dispatch '%s' cancelled", _label)
                return
            exc = t.exception()
            if exc is not None:
                log.error(
                    "background dispatch '%s' raised: %r", _label, exc,
                    exc_info=exc,
                )

        task.add_done_callback(_on_done)
        return task

    async def dispatch_scheduled_fire(
        self,
        *,
        chat_id: int,
        user_id: int,
        text: str,
        schedule_id: str | None = None,
    ) -> bool:
        """Entry point for ``ScheduleManager`` fires.

        Wired into ``ScheduleManager.set_dispatch_fn`` at startup so
        scheduled fires route through ``_spawn_background_dispatch`` →
        ``_dispatch_to_brain``, which does ``claim() ? drain : enqueue``.
        Without this hop, a fire at idle wall-clock time (e.g. 02:30
        CEST) would only call ``running_tasks.enqueue`` — landing the
        prompt in the FIFO with no drain owner — and the prompt would
        sit there until a real user message woke a fresh claim. That
        was the v0.4.0 bug:

            02:30:00  Enqueued message ... origin=scheduled_fire
            ...
            14:56:05  Claimed drain for chat ... (user typed something)
            15:02:00  Popped queued message  ← stranded 02:30 fire runs

        Tagging with ``queue_origin="scheduled_fire"`` keeps the message
        distinguishable from user / goal_continuation / schedule_command
        in the queue (e.g. ``/goal pause``'s drop predicate only targets
        goal_continuation, so scheduled fires survive a goal pause).

        Returns True when the dispatch task was spawned. False on
        failure to spawn — the manager increments ``consecutive_errors``
        and auto-pauses at threshold. Note: True does NOT mean the
        brain turn completed; the brain runs in the background and
        replies through the normal ``_drain_chat`` reply mechanism.
        """
        try:
            self._spawn_background_dispatch(
                self._app.bot,
                chat_id,
                user_id,
                text,
                label="schedule_fire",
                queue_origin="scheduled_fire",
                schedule_id=schedule_id,
            )
            return True
        except Exception as exc:
            log.warning(
                "dispatch_scheduled_fire failed for chat=%d: %s",
                chat_id,
                exc,
            )
            return False

    async def _preempt_goal_continuations(self, chat_id: int) -> int:
        """Drop any queued ``goal_continuation`` messages for ``chat_id``.

        Called BEFORE every real-user dispatch (``_on_text``,
        ``_on_voice``, ``_on_photo``) so a user message arriving
        during an active goal loop runs immediately after the
        current in-flight turn — not behind a stack of goal
        continuations the post-turn hook already enqueued.

        Without preemption, a user message landing between the
        hook's enqueue and the drain's next ``pop_or_release`` sits
        behind 1+ continuations. With ``_run_goal_hook``'s dedup
        (drop-before-enqueue, see that method) the worst case is one
        continuation in the queue at a time, so the latency cost is
        ~1 brain turn. With preemption it's ~0 (user runs as soon as
        the current turn — whatever it is — completes).

        The post-turn hook re-enqueues a continuation after the
        user's turn finishes, so the goal naturally resumes once the
        user stops typing. No state mutation on the goal itself —
        ``turns_used`` still accumulates per the existing accounting
        rules; this is pure queue management.

        No-op when the chat has no queue state or no continuations
        are queued. Cheap to call unconditionally — the lock contention
        on an empty queue is trivial in the single-user deployment.

        Returns the number of continuations dropped, for logging /
        tests.
        """
        return await self._running_tasks.drop_messages_matching(
            chat_id,
            lambda m: m.origin == "goal_continuation",
        )

    async def flush_background_dispatch(self) -> None:
        """Await every in-flight background-dispatch task.

        Test-only entry point: production code never calls this
        (the whole point of the spawn is to NOT block the handler).
        Tests that exercise the kickoff and then assert on brain
        calls / queue state need a way to wait for the spawned
        drain to finish — calling ``asyncio.run`` on a coroutine
        that doesn't await its scheduled tasks would let
        ``asyncio.run`` tear the loop down with the task pending.
        """
        # Snapshot the set: the done-callback mutates it during
        # gather, which would raise "set changed size during
        # iteration" if we passed the live set directly.
        pending = list(self._background_dispatch_tasks)
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)

    async def _drain_chat(
        self, bot, chat_id: int, user_id: int, first_text: str,
        *,
        first_schedule_id: str | None = None,
    ) -> None:
        """Process one message after another for chat_id until the queue
        empties or /cancel flags the drain. The caller must already hold
        the drain claim; this method releases it on exit.

        Each turn's brain call and reply send are wrapped individually,
        so a single broken turn (handler raising, Telegram send failing)
        logs and surfaces an error reply but does not kill the drain —
        queued follow-ups still run.

        ``first_schedule_id`` is the schedule.id of the first message
        when the drain was claimed directly by a scheduled fire (the
        common case: schedule fires at idle wall-clock time, claims
        the chat, runs the brain). Subsequent turns read the
        schedule_id off their popped ``QueuedMessage`` instead.
        """
        text = first_text
        # The first turn's origin is "user" by construction:
        # _dispatch_to_brain only ever passes user-typed (or /goal
        # kickoff text, which is also a user-driven message) text in
        # as ``first_text``. Continuations only appear via
        # ``pop_or_release`` on subsequent iterations, where we read
        # the QueuedMessage's ``origin`` field directly.
        origin = "scheduled_fire" if first_schedule_id else "user"
        current_schedule_id: str | None = first_schedule_id
        is_first = True
        while True:
            if not is_first and origin != "goal_continuation":
                # Goal continuations get their own user-visible
                # status line via ``_run_goal_hook`` ("↻ Continuing
                # toward goal (N/M): <reason>"). The "Picking up:"
                # preview would be redundant chat clutter on top.
                preview = _make_pickup_preview(text)
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"{_PICKING_UP_PREFIX}{preview}",
                        parse_mode=None,
                    )
                except Exception:
                    log.exception("Failed to send pickup ack for chat %s", chat_id)
            # v3b Day 3a: relationships hook fires per drain iteration,
            # BEFORE the brain dispatch — receipt-then-reply UX. The
            # helper handles all error / cursor-collision cases
            # internally; the drain proceeds to the brain regardless.
            await self._run_relationships_hook(bot, chat_id, text)
            # Allocate a TurnOutcome only when the message is a
            # scheduled fire — the brain's outcome is the only thing
            # we use it for, and avoiding the allocation on real-user
            # turns keeps the hot path identical to pre-fix.
            from vexis_agent.core.handler import TurnOutcome
            turn_outcome = (
                TurnOutcome() if current_schedule_id else None
            )
            reply = await self._dispatch_brain_turn(
                bot, chat_id, user_id, text,
                outcome=turn_outcome,
            )
            # Report the outcome BEFORE the goal hook so the schedule
            # manager learns about it even if the goal hook raises.
            if (
                current_schedule_id
                and turn_outcome is not None
                and self._schedule_outcome_cb is not None
            ):
                try:
                    self._schedule_outcome_cb(
                        current_schedule_id,
                        success=turn_outcome.succeeded,
                        error_message=turn_outcome.error_message,
                        is_permanent=turn_outcome.is_permanent_failure,
                    )
                except Exception:
                    log.exception(
                        "schedule_outcome_cb failed for schedule %s",
                        current_schedule_id,
                    )
            # /goal post-turn hook: for chats with an active standing
            # goal, judge whether the reply satisfies the goal and
            # (if not + under budget) enqueue a continuation. No-op
            # when goals.enabled is False or no active goal is set
            # for the current session UUID.
            await self._run_goal_hook(bot, chat_id, reply or "")
            next_msg = await self._running_tasks.pop_or_release(chat_id)
            if next_msg is None:
                # If pop_or_release released because /cancel fired and a
                # follow-up message landed *during* the cancel cleanup,
                # those items are still in the queue. Reclaim and keep
                # draining so they don't get silently lost.
                next_msg = await self._running_tasks.take_over_if_pending(chat_id)
                if next_msg is None:
                    return
            text = next_msg.text
            user_id = next_msg.user_id
            origin = next_msg.origin
            current_schedule_id = next_msg.schedule_id
            is_first = False

    async def _on_clear(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        self._pending_deletes.clear()
        reply = await self._handler.handle_clear(user.id)
        if reply is None:
            return
        await msg.get_bot().send_message(
            chat_id=msg.chat_id, text=reply, parse_mode=None
        )

    async def _on_new(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        self._pending_deletes.clear()
        args = ctx.args or []
        if len(args) > 1:
            await msg.reply_text("Usage: /new [name]")
            return
        reply = await self._handler.handle_new(user.id, args[0] if args else None)
        if reply is not None:
            await msg.reply_text(reply)

    async def _on_switch(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        self._pending_deletes.clear()
        args = ctx.args or []
        if len(args) > 1:
            await msg.reply_text("Usage: /switch [name]")
            return
        if args:
            reply = await self._handler.handle_switch(user.id, args[0])
            if reply is not None:
                await msg.reply_text(reply)
            return
        sessions = self._handler.sessions_for(user.id)
        if sessions is None:
            return
        if len(sessions) <= 1:
            await msg.reply_text("Only one session exists. Nothing to switch to.")
            return
        ordered = sorted(sessions, key=lambda s: s.created_at, reverse=True)
        rows, hidden = _build_button_rows(ordered, "switch", skip_active=False)
        text = "Pick a session:" + (f"\n{_HIDDEN_NOTE}" if hidden else "")
        await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))

    async def _on_sessions(
        self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        self._pending_deletes.clear()
        reply = await self._handler.handle_sessions(user.id)
        if reply is not None:
            await msg.reply_text(reply)

    async def _on_rename(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        self._pending_deletes.clear()
        args = ctx.args or []
        if len(args) != 2:
            await msg.reply_text("Usage: /rename <old> <new>")
            return
        reply = await self._handler.handle_rename(user.id, args[0], args[1])
        if reply is not None:
            await msg.reply_text(reply)

    async def _on_delete(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /delete from user_id=%s", user.id)
            return
        args = ctx.args or []
        if len(args) > 1:
            await msg.reply_text("Usage: /delete [name]")
            return
        if args:
            await msg.reply_text(self._arm_delete_confirmation(args[0]))
            return
        sessions = self._handler.sessions_for(user.id)
        if sessions is None:
            return
        ordered = sorted(sessions, key=lambda s: s.created_at, reverse=True)
        rows, hidden = _build_button_rows(ordered, "delete", skip_active=True)
        if not rows:
            await msg.reply_text("No sessions can be deleted right now.")
            return
        text = "Pick a session to delete:" + (f"\n{_HIDDEN_NOTE}" if hidden else "")
        await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(rows))

    def _arm_delete_confirmation(self, name: str) -> str:
        self._pending_deletes[name] = (
            datetime.now(timezone.utc) + _DELETE_CONFIRM_WINDOW
        )
        return (
            f"Are you sure you want to delete '{name}'? "
            f"Send /confirm_delete {name} within 60s to confirm."
        )

    async def _on_callback(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        if query is None or query.from_user is None or query.data is None:
            return
        user_id = query.from_user.id
        if not is_allowed(user_id, self._allowed_user_id):
            log.warning("Rejected callback from user_id=%s", user_id)
            return
        # Kanban inline-button callbacks (e.g. ``kanban:complete:<id>``)
        # are handled by the kanban facade — it owns its own
        # ``query.answer()`` so we don't double-answer here.
        if query.data.startswith("kanban:"):
            handled = await self._kanban.handle_callback(update, ctx)
            if handled:
                return
        await query.answer()
        action, _, payload = query.data.partition(":")
        if action == "switch":
            self._pending_deletes.clear()
            reply = await self._handler.handle_switch(user_id, payload)
        elif action == "delete":
            reply = self._arm_delete_confirmation(payload)
        elif action == "model_pick_provider":
            # payload = "<subsystem>:<brain_short>:<provider>:<flag>"
            # (post-cross-brain shape; brain_short ∈ {cc, oc}).
            # Older formats (no brain_short) come back as
            # "stale picker" — daemon-restart wipes picker
            # sessions anyway so in-flight legacy callbacks are
            # rare.
            subsystem, brain_kind_resolved, provider, expanded = (
                self._parse_provider_payload(payload)
            )
            if subsystem is None:
                await query.edit_message_text(
                    _PICKER_STALE_SUBSYSTEM_TMPL.format(subsystem=payload)
                )
                return
            await self._render_model_picker(
                query, subsystem, provider, page=0,
                expanded=expanded, brain_kind=brain_kind_resolved,
            )
            return
        elif action == "model_pick_page":
            # payload = "<sub>:<brain_short>:<provider>:<page>:<flag>"
            subsystem, brain_kind_resolved, provider, page, expanded = (
                self._parse_page_payload(payload)
            )
            if subsystem is None or page is None:
                return
            await self._render_model_picker(
                query, subsystem, provider, page=page,
                expanded=expanded, brain_kind=brain_kind_resolved,
            )
            return
        elif action == "model_pick_model":
            # payload = "<sidx>:<brain_short>:<full_id>" (sidx is the
            # sorted-DEFAULT_SUBSYSTEM_TIERS index; brain_short is
            # cc/oc — see _BRAIN_TO_SHORT for the byte-budget
            # rationale).
            sidx_str, _, rest = payload.partition(":")
            brain_short, _, model_id = rest.partition(":")
            subsystem = self._index_to_subsystem(sidx_str)
            target_brain = self._SHORT_TO_BRAIN.get(brain_short)
            if subsystem is None or not model_id or target_brain is None:
                await query.edit_message_text(
                    _PICKER_STALE_SUBSYSTEM_TMPL.format(subsystem=sidx_str)
                )
                return
            # Cross-brain check (added 2026-05-08): if the picked
            # model's brain isn't the active brain, render the
            # confirmation step instead of writing immediately.
            # Confirmation flow handles brain.kind switch + restart.
            from vexis_agent.core.yaml_config import brain_kind
            if target_brain != brain_kind():
                await self._render_cross_brain_confirm(
                    query, subsystem, target_brain, model_id,
                )
                return
            # Same-brain: existing reasoning-step gate. If the
            # chosen model exposes reasoning levels, stash + render
            # reasoning picker; otherwise write immediately.
            from vexis_agent.core.model_discovery import reasoning_levels_for
            levels = reasoning_levels_for(target_brain, model_id)
            if levels:
                key = self._picker_session_key(query)
                if key is not None:
                    self._get_picker_pending()[key] = {
                        "subsystem": subsystem,
                        "model_id": model_id,
                    }
                await self._render_reasoning_picker(
                    query, subsystem, model_id, levels,
                )
                return
            _ok, reply = self._apply_subsystem_set(subsystem, model_id)
            await query.edit_message_text(reply)
            return
        elif action == "model_pick_swap":
            # payload = "<sidx>:<brain_short>:<full_id>" — same
            # shape as model_pick_model. User confirmed they want
            # to switch brains. Write both brain.kind AND the
            # subsystem assignment, then trigger restart.
            sidx_str, _, rest = payload.partition(":")
            brain_short, _, model_id = rest.partition(":")
            subsystem = self._index_to_subsystem(sidx_str)
            target_brain = self._SHORT_TO_BRAIN.get(brain_short)
            if subsystem is None or not model_id or target_brain is None:
                await query.edit_message_text(
                    _PICKER_STALE_SUBSYSTEM_TMPL.format(subsystem=sidx_str)
                )
                return
            await self._handle_cross_brain_switch(
                query, subsystem, target_brain, model_id,
            )
            return
        elif action == "model_pick_reasoning":
            # payload = "<sidx>:<level>" (level may be empty string
            # = "default; no reasoning override"). Recovers the
            # previously-chosen model from picker session state
            # keyed on (chat_id, message_id) — see
            # _picker_pending docstring.
            sidx_str, _, level = payload.partition(":")
            subsystem = self._index_to_subsystem(sidx_str)
            key = self._picker_session_key(query)
            stash = self._get_picker_pending().pop(key, None) if key else None
            if (
                subsystem is None
                or stash is None
                or stash.get("subsystem") != subsystem
                or not stash.get("model_id")
            ):
                # Daemon restart between model-pick and
                # reasoning-pick → state lost. Recover gracefully
                # by telling the user to re-issue.
                await query.edit_message_text(
                    _PICKER_STALE_SUBSYSTEM_TMPL.format(
                        subsystem=subsystem or sidx_str
                    )
                )
                return
            reasoning = level.strip() or None
            _ok, reply = self._apply_subsystem_set(
                subsystem, stash["model_id"], reasoning=reasoning,
            )
            await query.edit_message_text(reply)
            return
        elif action == "model_pick_back":
            # payload = "<subsystem>" — re-render the provider
            # picker over the existing message so the user lands
            # back at the provider step without scrollback drift.
            # Also clear any reasoning-step session state — Back
            # from anywhere in the picker resets the partial
            # selection.
            key = self._picker_session_key(query)
            if key is not None:
                self._get_picker_pending().pop(key, None)
            await self._render_provider_picker(
                msg=None, subsystem=payload, edit_query=query,
            )
            return
        elif action == "model_pick_cancel":
            # payload = "<subsystem>". Per
            # ``.plans/model-picker-ux-research.md`` §5 cancel
            # deletes the picker reply entirely (the user's slash
            # message persists — only the bot's interactive UI is
            # cleaned up so the chat doesn't accumulate dead UI).
            #
            # Telegram constraint: bots can only delete their own
            # messages within 48 hours of sending. Picker replies
            # are seconds-old when cancelled in normal flow, so
            # this is moot in practice — but if a user starts a
            # pick, walks away, comes back hours later, and taps
            # Cancel, the delete will fail. We catch + log + fall
            # back to editing the message to "(cancelled)" so the
            # chat doesn't silently leave the picker buttons live.
            chat_id = query.message.chat.id if query.message else None
            message_id = query.message.message_id if query.message else None
            bot = query.message.get_bot() if query.message else None
            # Clear picker session state if the cancel happens
            # mid-multi-step flow (e.g. user picked a model but
            # cancelled before picking reasoning).
            key = self._picker_session_key(query)
            if key is not None:
                self._get_picker_pending().pop(key, None)
            if bot is not None and chat_id is not None and message_id is not None:
                try:
                    await bot.delete_message(
                        chat_id=chat_id, message_id=message_id,
                    )
                except Exception as exc:
                    log.warning(
                        "model_pick_cancel: delete_message failed (likely "
                        "stale, > 48h old); falling back to edit. err=%s",
                        exc,
                    )
                    try:
                        await query.edit_message_text("(cancelled)")
                    except Exception:
                        log.warning(
                            "model_pick_cancel: edit fallback also failed; "
                            "picker UI may persist",
                        )
            return
        elif action == "model_pick_noop":
            # The page-indicator label sends this; deliberately
            # ignored. query.answer() above acks the tap so the
            # client doesn't show a loading spinner.
            return
        else:
            return
        if reply is not None:
            await query.edit_message_text(reply)

    async def _on_confirm_delete(
        self, update: Update, ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /confirm_delete from user_id=%s", user.id)
            return
        args = ctx.args or []
        if len(args) != 1:
            await msg.reply_text("Usage: /confirm_delete <name>")
            return
        name = args[0]
        expiry = self._pending_deletes.pop(name, None)
        now = datetime.now(timezone.utc)
        if expiry is None or expiry <= now:
            await msg.reply_text(f"No pending deletion for '{name}'.")
            return
        reply = await self._handler.handle_delete(user.id, name)
        if reply is not None:
            await msg.reply_text(reply)

    async def _on_voice(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None or msg.voice is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected voice memo from user_id=%s", user.id)
            return

        if msg.voice.duration < _MIN_VOICE_DURATION_SECONDS:
            log.info(
                "Skipping %ds voice memo from user_id=%s (under %ds threshold)",
                msg.voice.duration,
                user.id,
                _MIN_VOICE_DURATION_SECONDS,
            )
            await msg.reply_text(_VOICE_TOO_SHORT)
            return

        chat_id = msg.chat_id
        bot = msg.get_bot()

        fd = tempfile.NamedTemporaryFile(suffix=".ogg", delete=False)
        ogg_path = Path(fd.name)
        fd.close()

        typing_task: asyncio.Task[None] | None = None
        transcription: str | None = None
        try:
            tg_file = await msg.voice.get_file()
            await tg_file.download_to_drive(custom_path=ogg_path)

            typing_task = asyncio.create_task(self._keep_typing(bot, chat_id))

            try:
                transcription = await transcribe_audio(ogg_path)
            except TranscriptionEmpty:
                await bot.send_message(
                    chat_id=chat_id, text=_TRANSCRIPTION_EMPTY, parse_mode=None
                )
                return
            except TranscriptionError:
                log.exception("Transcription failed")
                await bot.send_message(
                    chat_id=chat_id, text=_TRANSCRIPTION_FAILED, parse_mode=None
                )
                return

            await bot.send_message(
                chat_id=chat_id,
                text=f"{_VOICE_ECHO_PREFIX}{transcription}",
                parse_mode=None,
            )
        finally:
            if typing_task is not None:
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass
            ogg_path.unlink(missing_ok=True)

        if transcription is None:
            return
        # Preempt any pending goal continuations so a voice message
        # gets the same chat-feels-responsive guarantee as typed input.
        await self._preempt_goal_continuations(chat_id)
        await self._dispatch_to_brain(
            bot, chat_id, user.id, f"{_VOICE_BRAIN_TAG}{transcription}"
        )

    async def _on_photo(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None or not msg.photo:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected photo from user_id=%s", user.id)
            return

        chat_id = msg.chat_id
        bot = msg.get_bot()

        # PhotoSize tuples are sorted small → large; the last one is the
        # highest-resolution variant Telegram has on file.
        photo = msg.photo[-1]
        image_path = _build_incoming_photo_path()

        tg_file = await photo.get_file()
        await tg_file.download_to_drive(custom_path=image_path)
        synthetic = _format_incoming_image_message(image_path, msg.caption)
        # Preempt any pending goal continuations — photo turn should
        # run after the current in-flight turn, not behind continuations.
        await self._preempt_goal_continuations(chat_id)
        await self._dispatch_to_brain(bot, chat_id, user.id, synthetic)

    async def _on_cancel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /cancel from user_id=%s", user.id)
            return
        args = ctx.args or []
        if len(args) > 1:
            await msg.reply_text("Usage: /cancel [name]")
            return
        if args:
            name = args[0]
            log.info("Received /cancel %s (background) from chat %d", name, msg.chat_id)
            try:
                cancelled = await self._background_tasks.cancel(name)
            except TaskNotFound:
                await msg.reply_text(f"No background task named '{name}'.")
                return
            if cancelled:
                await msg.reply_text(f"Cancelled background task `{name}`.")
            else:
                await msg.reply_text(f"Task `{name}` isn't running anymore.")
            return
        log.info("Received /cancel (foreground) from chat %d", msg.chat_id)
        cancelled = await self._running_tasks.cancel(msg.chat_id)
        log.info("Cancel result for chat %d: %s", msg.chat_id, cancelled)
        # /cancel auto-pauses an active goal — see §4 of the goal
        # research doc for the trade-off (avoids surprise-continuation
        # when the user cancels mid-goal and re-engages later). Emits
        # the paused-state reply when a goal was active; otherwise the
        # existing _CANCEL_OK / _NOTHING_TO_CANCEL paths stand.
        reply_text = _CANCEL_OK if cancelled else _NOTHING_TO_CANCEL
        try:
            from vexis_agent.core.yaml_config import goals_enabled
            if goals_enabled():
                session_uuid = self._handler.current_session_uuid()
                mgr = self._build_goal_manager(session_uuid)
                if mgr.is_active():
                    state = mgr.pause(reason="user-cancelled")
                    await self._running_tasks.drop_messages_matching(
                        msg.chat_id,
                        lambda m: m.origin == "goal_continuation",
                    )
                    if state is not None:
                        reply_text = _CANCEL_OK_GOAL_PAUSED_TMPL.format(
                            turns=state.turns_used,
                            budget=state.max_turns,
                        )
        except Exception:
            log.exception("goal auto-pause on /cancel failed for chat %d", msg.chat_id)
        await msg.reply_text(reply_text)

    async def _on_tasks(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /tasks from user_id=%s", user.id)
            return
        summary = await self._background_tasks.status_summary()
        await msg.reply_text(_format_tasks(summary))

    async def _on_status(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Read-only window into the running brain.

        Reads the per-chat status file (written by the brain as it
        processes stream-json tool events) plus the running-tasks
        queue depth and last-idle timestamp, and replies with a short
        summary. Touches no shared mutable state — safe to call while
        a drain is mid-flight.

        When a goal exists for the current session UUID and goals are
        enabled, appends a one-line goal summary so the user can see
        whether the loop is active without typing /goal status
        separately.
        """
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /status from user_id=%s", user.id)
            return
        chat_id = msg.chat_id
        snapshot = read_status(chat_id)
        queue_depth = self._running_tasks.queue_depth(chat_id)
        last_idle = self._running_tasks.last_idle_at(chat_id)
        reply = _format_status_reply(
            snapshot, queue_depth, last_idle, datetime.now(timezone.utc)
        )
        goal_line = self._goal_status_line()
        if goal_line:
            reply = f"{reply}\n{goal_line}"
        await msg.reply_text(reply)

    def _goal_status_line(self) -> str | None:
        """Return a one-line goal summary for /status, or None.

        Returns None when goals are disabled, when no goal exists for
        the current session, or when status is ``cleared``. Format
        per `.plans/goal-command-research.md` §6 Day 4:

          * active : "⊙ Goal (N/M turns): <text>"
          * paused : "⏸ Goal (paused, N/M turns — <reason>): <text>"
          * done   : "✓ Goal (done): <text>"
          * cleared: omitted

        Goal text is truncated at 80 chars with "…" so a long goal
        doesn't blow up the /status reply length.
        """
        try:
            from vexis_agent.core.yaml_config import goals_enabled
            if not goals_enabled():
                return None
            session_uuid = self._handler.current_session_uuid()
            if not session_uuid:
                return None
            from vexis_agent.core.goal_state import GoalStateStore
            from vexis_agent.core.paths import goals_path
            store = GoalStateStore(goals_path())
            state = store.load(session_uuid)
        except Exception:
            log.debug("status goal-summary read failed", exc_info=True)
            return None
        if state is None or state.status == "cleared":
            return None
        text = state.goal
        if len(text) > 80:
            text = text[:79] + "…"
        if state.status == "active":
            return f"⊙ Goal ({state.turns_used}/{state.max_turns} turns): {text}"
        if state.status == "paused":
            reason = f" — {state.paused_reason}" if state.paused_reason else ""
            return (
                f"⏸ Goal (paused, {state.turns_used}/{state.max_turns} "
                f"turns{reason}): {text}"
            )
        if state.status == "done":
            return f"✓ Goal (done): {text}"
        return None

    async def _on_pin(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        args = ctx.args or []
        if len(args) != 1:
            await msg.reply_text("Usage: /pin <skill-name>")
            return
        reply = await self._handler.handle_pin(user.id, args[0])
        if reply is not None:
            await msg.reply_text(reply)

    async def _on_unpin(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        args = ctx.args or []
        if len(args) != 1:
            await msg.reply_text("Usage: /unpin <skill-name>")
            return
        reply = await self._handler.handle_unpin(user.id, args[0])
        if reply is not None:
            await msg.reply_text(reply)

    async def _on_curator(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Stub — wired up in the curator task. Keeping the handler
        registered so the slash menu is honest about supporting it."""
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /curator from user_id=%s", user.id)
            return
        if self._curator is None:
            await msg.reply_text("Curator not initialised yet.")
            return
        args = ctx.args or []
        sub = args[0] if args else "status"
        rest = args[1:]
        try:
            reply = await self._curator.handle_telegram(sub, rest)
        except Exception:
            log.exception("/curator handler failed")
            reply = "⚠️ Curator command failed; check the logs."
        if reply is not None:
            await msg.reply_text(reply)

    async def _on_learning(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Dispatch /learning [status|pause|resume|run] to the
        LearningController. Mirrors ``_on_curator``: keep the slash
        registration honest by always replying, but degrade gracefully
        if the controller hasn't been wired in (e.g. older daemon
        instance restoring state)."""
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /learning from user_id=%s", user.id)
            return
        if self._learning_curator is None:
            await msg.reply_text("Learning curator not initialised yet.")
            return
        args = ctx.args or []
        sub = args[0] if args else "status"
        rest = args[1:]
        try:
            reply = await self._learning_curator.handle_telegram(sub, rest)
        except Exception:
            log.exception("/learning handler failed")
            reply = "⚠️ Learning curator command failed; check the logs."
        if reply is not None:
            await msg.reply_text(reply)

    # ────────────────────────────────────────────────────────────────
    # /goal — persistent cross-turn goals (Ralph-style loop, port of
    # the upstream /goal). Source of truth: `.plans/goal-command-research.md`.
    # ────────────────────────────────────────────────────────────────

    def _build_goal_manager(self, session_uuid: str):
        """Construct a GoalManager bound to the given session UUID.

        Lazy-imports the goal modules so daemons with goals disabled
        never pay the import cost. ``self._workspace`` is the path
        ``MessageHandler`` already holds; we read it via the handler
        rather than threading another constructor arg.
        """
        from vexis_agent.core.goal_manager import GoalManager
        from vexis_agent.core.goal_state import GoalStateStore
        from vexis_agent.core.paths import goals_path
        from vexis_agent.core.yaml_config import goals_max_turns

        workspace = getattr(self._handler, "_workspace", None) or Path.cwd()
        store = GoalStateStore(goals_path())
        return GoalManager(
            session_uuid=session_uuid,
            workspace=workspace,
            store=store,
            default_max_turns=goals_max_turns(),
        )

    async def _on_goal(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Dispatch /goal [status|pause|resume|clear|<text>].

        Behind ``goals_enabled()`` — reply with the disabled-note when
        off so the user finds out via the slash menu rather than
        silent no-op. Subcommands ``status``/``pause``/``resume``/
        ``clear`` are control-plane and safe mid-run; ``/goal <text>``
        is rejected mid-run with the §4 reject string and otherwise
        sets the goal + kicks off the first turn through the same
        drain machinery that user messages use.
        """
        from vexis_agent.core.goal_manager import GoalAlreadyActiveError
        from vexis_agent.core.yaml_config import goals_enabled

        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /goal from user_id=%s", user.id)
            return

        if not goals_enabled():
            await msg.reply_text(_GOAL_DISABLED_NOTE)
            return

        # Reassemble the user's args. PTB's ctx.args splits on
        # whitespace; we rejoin for the goal-text path. The
        # subcommand keywords are single tokens so the split is fine
        # for them.
        args = ctx.args or []
        if not args:
            sub = "status"
            rest_text = ""
        else:
            sub = args[0].lower()
            rest_text = " ".join(args[1:]) if len(args) > 1 else ""

        session_uuid = self._handler.current_session_uuid()
        mgr = self._build_goal_manager(session_uuid)

        # Control-plane: always safe, no drain interaction.
        if sub == "status":
            await msg.reply_text(mgr.status_line())
            return

        if sub == "pause":
            from vexis_agent.core.goal_state import TerminalGoalError
            try:
                state = mgr.pause(reason="user-paused")
            except TerminalGoalError as exc:
                # Race lost: disk turned terminal between this
                # handler's manager init and the locked save. Don't
                # confirm a pause that didn't happen — tell the user
                # the goal is already done.
                await msg.reply_text(
                    _GOAL_ALREADY_TERMINAL_TMPL.format(status=exc.status)
                )
                return
            if state is None:
                await msg.reply_text(_GOAL_NO_GOAL_TO_PAUSE)
                return
            # Drop any pending goal continuations from this chat's
            # queue so the loop doesn't run "one more" after the user
            # hit pause. User messages survive the predicate.
            await self._running_tasks.drop_messages_matching(
                msg.chat_id,
                lambda m: m.origin == "goal_continuation",
            )
            await msg.reply_text(
                _GOAL_PAUSE_REPLY_TMPL.format(status=mgr.status_line())
            )
            return

        if sub == "resume":
            from vexis_agent.core.goal_state import TerminalGoalError
            try:
                state = mgr.resume()
            except TerminalGoalError as exc:
                await msg.reply_text(
                    _GOAL_ALREADY_TERMINAL_TMPL.format(status=exc.status)
                )
                return
            if state is None:
                await msg.reply_text(_GOAL_NO_GOAL_TO_RESUME)
                return
            await msg.reply_text(_GOAL_RESUME_REPLY_TMPL.format(goal=state.goal))
            return

        if sub == "clear":
            had = mgr.has_goal()
            mgr.clear()
            await self._running_tasks.drop_messages_matching(
                msg.chat_id,
                lambda m: m.origin == "goal_continuation",
            )
            await msg.reply_text(_GOAL_CLEAR_REPLY if had else _GOAL_NO_ACTIVE)
            return

        # Otherwise — treat the entire arg blob as the new goal text.
        # Reconstruct from the raw command tail so we don't lose
        # punctuation; ``message.text`` looks like "/goal foo bar".
        raw_text = (msg.text or "").strip()
        # Strip the "/goal" prefix (handles "/goal", "/goal@bot", etc.).
        if raw_text.startswith("/"):
            after_slash = raw_text[1:]
            space_idx = after_slash.find(" ")
            goal_text = after_slash[space_idx + 1:] if space_idx >= 0 else ""
        else:
            goal_text = " ".join(args)
        goal_text = goal_text.strip()
        if not goal_text:
            await msg.reply_text(_GOAL_INVALID_TMPL.format(reason="goal text is empty"))
            return

        # Bareword-typo guard. /goal cancel / /goal stop / etc. is
        # almost always a typo for /cancel — never a real goal.
        # Caught BEFORE the mid-run reject so the user gets the
        # right hint regardless of drain state.
        if goal_text.lower() in _GOAL_BAREWORD_CANCEL_LIKE:
            await msg.reply_text(_GOAL_BAREWORD_HINT)
            return

        # Mid-run reject: a drain is already processing this chat.
        # Setting a new goal would race a continuation against the
        # in-flight turn. /cancel is the way out (which auto-pauses
        # any active goal — see _on_cancel below).
        if self._running_tasks.is_running(msg.chat_id):
            await msg.reply_text(_GOAL_REJECT_MIDRUN)
            return

        try:
            state = mgr.set(goal_text)
        except GoalAlreadyActiveError:
            await msg.reply_text(_GOAL_REJECT_ALREADY_ACTIVE)
            return
        except ValueError as exc:
            await msg.reply_text(_GOAL_INVALID_TMPL.format(reason=str(exc)))
            return

        # Acknowledge the set first so the user sees confirmation
        # before the brain starts working. The kickoff turn can take
        # 30+ seconds — the user shouldn't be left guessing.
        await msg.reply_text(
            _GOAL_KICKOFF_REPLY_TMPL.format(budget=state.max_turns, goal=state.goal)
        )

        # Kick off the first turn through the same path a normal
        # message would take. The text fed to the brain is just the
        # goal text — the continuation prompt template only applies
        # to subsequent turns.
        #
        # CRITICAL: the dispatch runs in a background task on purpose.
        # A goal loop can chain dozens of turns over many minutes, and
        # awaiting the dispatch inline would hold the PTB command
        # handler for the entire duration — any message the user
        # typed in the meantime would sit silent until /cancel.
        # Spawning lets the handler return after the kickoff ack
        # while the drain continues to run; subsequent user messages
        # still hit ``_dispatch_to_brain``, find the chat claimed,
        # and FIFO-enqueue alongside goal continuations (matching
        # Hermes' ``gateway/run.py:_handle_goal_command`` →
        # ``_enqueue_fifo`` pattern).
        self._spawn_background_dispatch(
            msg.get_bot(),
            msg.chat_id,
            user.id,
            goal_text,
            label="goal_kickoff",
        )

    async def _on_schedule(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Dispatch /schedule [list|status|pause|resume|clear|<text>].

        Behind ``schedules_enabled()`` — replies with the disabled-note
        when off so the user finds out via the slash menu rather than
        silent no-op.

        Subcommand dispatch:

          /schedule                 → help text (local, no brain)
          /schedule list            → render store rows (local)
          /schedule status          → alias for list
          /schedule pause <id>      → store.update_atomic (local)
          /schedule resume <id>     → store.update_atomic (local)
          /schedule clear <id>      → store.clear (local)
          /schedule <anything else> → enqueue synthetic message via
                                      RunningTasks with origin=
                                      "schedule_command"; brain calls
                                      `vexis-agent schedule create` and
                                      replies with echo-confirmation.

        The slash command handler does NO cron parsing — that lives
        inside the brain-callable CLI surface so user phrasing always
        flows through the brain → tool path (single source of parser
        truth, brain owns echo-confirmation).
        """
        from vexis_agent.core.yaml_config import schedules_enabled

        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /schedule from user_id=%s", user.id)
            return

        if not schedules_enabled():
            await msg.reply_text(_SCHEDULE_DISABLED_NOTE)
            return

        if self._schedule_store is None:
            log.warning(
                "/schedule invoked but no schedule_store wired; "
                "this is a daemon-startup bug"
            )
            await msg.reply_text(_SCHEDULE_DISABLED_NOTE)
            return

        args = ctx.args or []
        if not args:
            await msg.reply_text(_SCHEDULE_HELP)
            return

        sub = args[0].lower()

        # ── local management subcommands ──────────────────────────
        if sub in ("list", "status"):
            await msg.reply_text(self._render_schedule_list())
            return

        if sub == "pause":
            await self._do_schedule_pause(msg, args[1:])
            return

        if sub == "resume":
            await self._do_schedule_resume(msg, args[1:])
            return

        if sub == "clear":
            await self._do_schedule_clear(msg, args[1:])
            return

        # ── create path: dispatch text to the brain via the FIFO ──
        # Reconstruct the user's text from message.text so punctuation
        # survives. Strip the leading "/schedule" token.
        raw_text = (msg.text or "").strip()
        if raw_text.startswith("/"):
            after_slash = raw_text[1:]
            space_idx = after_slash.find(" ")
            schedule_text = (
                after_slash[space_idx + 1:] if space_idx >= 0 else ""
            )
        else:
            schedule_text = " ".join(args)
        schedule_text = schedule_text.strip()
        if not schedule_text:
            await msg.reply_text(_SCHEDULE_HELP)
            return

        # Synthetic message body: the leading [user invoked /schedule]
        # tag is an explicit-intent hint so the brain treats the
        # following text as a scheduling request even if the phrasing
        # is ambiguous.
        synthetic = f"[user invoked /schedule]\n{schedule_text}"

        # Ack first so the user sees something immediately; the brain
        # response takes longer.
        await msg.reply_text(_SCHEDULE_ACK)

        # Dispatch to the brain in a background task so the PTB handler
        # returns immediately after the ack. ``_dispatch_to_brain``
        # internally claims the chat (if no drain is active) or
        # enqueues with ``queue_origin="schedule_command"`` (if one is)
        # — both paths preserve the chat-not-blocked-while-brain-thinks
        # property the /goal kickoff fix established. The previous
        # outer-claim + inline-await pattern double-claimed the slot
        # (the inner ``_dispatch_to_brain.claim()`` returned False
        # because we already owned it) and then enqueued with the
        # default ``user`` origin instead of ``schedule_command`` — a
        # latent bug that the test fixture's AsyncMock masked.
        self._spawn_background_dispatch(
            msg.get_bot(),
            msg.chat_id,
            user.id,
            synthetic,
            label="schedule_kickoff",
            queue_origin="schedule_command",
        )

    def _render_schedule_list(self) -> str:
        """Render the active+paused schedule list as Telegram-friendly text."""
        from vexis_agent.core.schedule_state import ScheduleStore
        store: ScheduleStore = self._schedule_store  # type: ignore[assignment]
        rows = [r for r in store.list_all() if r.status != "cleared"]
        if not rows:
            return _SCHEDULE_LIST_EMPTY
        # Sort: active first (by next_fire_at asc), then paused, then expired.
        def _key(r):
            order = {"active": 0, "paused": 1, "expired": 2}.get(r.status, 3)
            nfa = r.next_fire_at or datetime.max.replace(tzinfo=timezone.utc)
            return (order, nfa)
        rows.sort(key=_key)
        lines = [_SCHEDULE_LIST_HEADER]
        for r in rows:
            preview = r.prompt[:50]
            if len(r.prompt) > 50:
                preview += "…"
            nfa = (
                r.next_fire_at.isoformat()
                if r.next_fire_at
                else "—"
            )
            lines.append(
                f"  {r.id[:6]} · {r.status} · {r.schedule_display}\n"
                f"      next: {nfa}\n"
                f"      \"{preview}\""
            )
        return "\n".join(lines)

    async def _do_schedule_pause(self, msg, args: list[str]) -> None:
        if not args:
            await msg.reply_text("Usage: /schedule pause <id>")
            return
        from dataclasses import replace
        from vexis_agent.core.schedule_state import (
            ScheduleStore,
            TerminalScheduleError,
        )
        store: ScheduleStore = self._schedule_store  # type: ignore[assignment]
        sid = store.resolve_id_prefix(args[0]) or args[0]
        if not store.load(sid):
            await msg.reply_text(_SCHEDULE_NOT_FOUND_TMPL.format(id=args[0]))
            return
        try:
            store.update_atomic(
                sid,
                lambda s: replace(
                    s, status="paused", paused_reason="user", next_fire_at=None
                ),
            )
        except TerminalScheduleError as exc:
            await msg.reply_text(
                _SCHEDULE_ALREADY_TERMINAL_TMPL.format(status=exc.status)
            )
            return
        await msg.reply_text(_SCHEDULE_PAUSED_TMPL.format(id=sid[:6]))

    async def _do_schedule_resume(self, msg, args: list[str]) -> None:
        if not args:
            await msg.reply_text("Usage: /schedule resume <id>")
            return
        from dataclasses import replace
        from vexis_agent.core.schedule_state import (
            ScheduleStore,
            TerminalScheduleError,
        )
        from vexis_agent.tools.schedule_tool.parser import compute_next_fire
        store: ScheduleStore = self._schedule_store  # type: ignore[assignment]
        sid = store.resolve_id_prefix(args[0]) or args[0]
        if not store.load(sid):
            await msg.reply_text(_SCHEDULE_NOT_FOUND_TMPL.format(id=args[0]))
            return
        try:
            result = store.update_atomic(
                sid,
                lambda s: replace(
                    s,
                    status="active",
                    paused_reason=None,
                    next_fire_at=compute_next_fire(s.schedule, last_fire_at=None),
                    consecutive_errors=0,
                ),
            )
        except TerminalScheduleError as exc:
            await msg.reply_text(
                _SCHEDULE_ALREADY_TERMINAL_TMPL.format(status=exc.status)
            )
            return
        nfa_str = (
            result.next_fire_at.isoformat() if result.next_fire_at else "—"
        )
        await msg.reply_text(
            _SCHEDULE_RESUMED_TMPL.format(id=sid[:6], next_fire=nfa_str)
        )

    async def _do_schedule_clear(self, msg, args: list[str]) -> None:
        if not args:
            await msg.reply_text("Usage: /schedule clear <id>")
            return
        from vexis_agent.core.schedule_state import ScheduleStore
        store: ScheduleStore = self._schedule_store  # type: ignore[assignment]
        sid = store.resolve_id_prefix(args[0]) or args[0]
        if not store.load(sid):
            await msg.reply_text(_SCHEDULE_NOT_FOUND_TMPL.format(id=args[0]))
            return
        store.clear(sid)
        await msg.reply_text(_SCHEDULE_CLEARED_TMPL.format(id=sid[:6]))

    async def _on_model(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Dispatch /model [status|list|set|reset|...].

        Day 2 of model-management UX. Behind ``model_ux_enabled()``
        — flag default off until Day 5 dogfood. Even with the flag
        off, the spawn-site BrainModelNotFoundError backstop fires
        regardless because it's catching real errors that should
        always have actionable messaging.

        Subcommand grammar (per
        ``.plans/model-management-ux-research.md`` §4):

          /model                   show current resolution table
          /model status            same as bare /model
          /model list              enumerate subsystems + brains
          /model list <brain>      list models for that brain
          /model set brain <name>  change brain.kind (restart req)
          /model set <name> <val>  set per-subsystem assignment
          /model reset             reset all subsystems to defaults
          /model reset <name>      reset one subsystem

        Every ``set`` runs the validator pre-write; refuses to
        write on error-severity findings. Every write runs the
        comment-presence-gated backup helper before the atomic
        rewrite — preserves user-curated comments in
        ``~/.vexis/config.yaml.bak`` (self-managing across daemon
        restarts; see core/yaml_config_writer.py).
        """
        from vexis_agent.core.yaml_config import (
            DEFAULT_SUBSYSTEM_TIERS,
            VALID_BRAIN_KINDS,
            _read_raw,
            brain_kind,
            model_ux_enabled,
        )
        from vexis_agent.core.yaml_config_writer import (
            atomic_write_yaml,
            backup_if_commented,
        )
        from vexis_agent.core.paths import vexis_dir

        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /model from user_id=%s", user.id)
            return

        if not model_ux_enabled():
            await msg.reply_text(_MODEL_DISABLED_NOTE)
            return

        args = ctx.args or []
        sub = args[0].lower() if args else "status"

        # ── status ─────────────────────────────────────────────
        if sub == "status":
            await msg.reply_text(self._model_status_text())
            return

        # ── list ───────────────────────────────────────────────
        if sub == "list":
            target_brain = args[1] if len(args) > 1 else None
            await msg.reply_text(self._model_list_text(target_brain))
            return

        # ── reset ──────────────────────────────────────────────
        if sub == "reset":
            target = args[1] if len(args) > 1 else None
            cfg_path = vexis_dir() / "config.yaml"
            backup_msg = ""
            if cfg_path.exists():
                bak = backup_if_commented(cfg_path)
                if bak is not None:
                    backup_msg = "\n" + _MODEL_BACKUP_REPLY_TMPL
            current = _read_raw()
            models = dict(current.get("models") or {})
            if target is None:
                # Reset every subsystem assignment (legacy + new schema)
                # but leave models.tiers and models.brain alone.
                models.pop("subsystems", None)
                for sub_name in list(models):
                    if sub_name in DEFAULT_SUBSYSTEM_TIERS:
                        models.pop(sub_name)
                scope = "all subsystems"
            else:
                if target not in DEFAULT_SUBSYSTEM_TIERS:
                    await msg.reply_text(
                        f"Unknown subsystem '{target}'. Known: "
                        f"{', '.join(sorted(DEFAULT_SUBSYSTEM_TIERS))}"
                    )
                    return
                # Pop both the legacy and the new-schema slots.
                models.pop(target, None)
                subs_block = models.get("subsystems")
                if isinstance(subs_block, dict):
                    subs_block.pop(target, None)
                    if not subs_block:
                        models.pop("subsystems", None)
                scope = target
            new_cfg = {**current, "models": models}
            if not models:
                new_cfg.pop("models", None)
            atomic_write_yaml(cfg_path, new_cfg)
            await msg.reply_text(
                _MODEL_RESET_OK_TMPL.format(scope=scope) + backup_msg
            )
            return

        # ── refresh ────────────────────────────────────────────
        # Calls the same in-process helper the dashboard's
        # POST /api/v1/models/discovery/refresh route wraps —
        # single backend primitive, two surfaces (per
        # ``.plans/model-picker-ux-research.md`` §6 Day 3 + §7
        # staleness mitigation revision).
        #
        # Both brains have a live cache to refresh now: opencode
        # via ``opencode models --refresh`` subprocess, claude-code
        # via the Anthropic /v1/models endpoint with the user's
        # OAuth bearer / ANTHROPIC_API_KEY. Pre-2026-05-07 the
        # claude-code branch was an informational no-op (curated
        # hardcoded list); the live-discovery work made the
        # refresh meaningful for both.
        if sub == "refresh":
            from vexis_agent.core.model_discovery import (
                discovery_grouped_for_brain,
                refresh_claude_code_models,
                refresh_opencode_models,
            )
            kind = brain_kind()
            if kind == "opencode":
                refresh_opencode_models()  # invalidates + re-runs subprocess
            elif kind == "claude-code":
                refresh_claude_code_models()  # invalidates + re-fetches /v1/models
            else:
                # null brain or future brain without discovery —
                # report cleanly rather than crashing.
                await msg.reply_text(
                    _MODEL_REFRESH_NOOP_TMPL.format(brain=kind)
                )
                return
            grouped = discovery_grouped_for_brain(kind)
            if not grouped:
                await msg.reply_text(_MODEL_REFRESH_EMPTY_TMPL)
                return
            counts = "\n".join(
                f"  {provider}: {len(models)} models"
                for provider, models in grouped.items()
            )
            await msg.reply_text(
                _MODEL_REFRESH_OK_TMPL.format(counts=counts)
            )
            return

        # ── set ────────────────────────────────────────────────
        if sub == "set":
            # Day 3 of model picker UX adds two trigger shapes:
            #   /model set <subsystem>          → picker flow
            #   /model set <subsystem> ?        → picker flow (alias)
            #   /model set <subsystem> <model>  → typed-arg (unchanged)
            #
            # Picker-trigger detection happens FIRST so the typed-arg
            # validation path stays byte-equivalent for users who
            # supply a value. ``brain`` is never picker-triggerable —
            # there are only 3 brain kinds and no discovery flow,
            # so /model set brain falls through to the typed-arg
            # path and errors out without a value.
            if len(args) < 2:
                await msg.reply_text(_MODEL_USAGE)
                return
            key = args[1].lower()

            picker_trigger = (
                key != "brain"
                and key in DEFAULT_SUBSYSTEM_TIERS
                and (len(args) == 2 or (len(args) >= 3 and args[2] == "?"))
            )
            if picker_trigger:
                await self._render_provider_picker(msg, key)
                return

            if len(args) < 3:
                await msg.reply_text(_MODEL_USAGE)
                return
            value = args[2]

            cfg_path = vexis_dir() / "config.yaml"
            current = _read_raw()

            # Special case: /model set brain <name>
            if key == "brain":
                # Policy refusal on invalid kind even though the
                # validator's rule 1 only warns (severity matches
                # daemon fallback). Typos here are user-hostile to
                # recover from — the user thinks they switched but
                # didn't.
                if value not in VALID_BRAIN_KINDS:
                    await msg.reply_text(
                        _MODEL_INVALID_BRAIN_KIND_TMPL.format(kind=value)
                    )
                    return
                # Backup → write → reply with restart-required note.
                backup_msg = ""
                if cfg_path.exists():
                    bak = backup_if_commented(cfg_path)
                    if bak is not None:
                        backup_msg = "\n" + _MODEL_BACKUP_REPLY_TMPL
                brain_block = dict(current.get("brain") or {})
                brain_block["kind"] = value
                new_cfg = {**current, "brain": brain_block}
                atomic_write_yaml(cfg_path, new_cfg)
                await msg.reply_text(
                    _MODEL_SET_BRAIN_OK_TMPL.format(kind=value) + backup_msg
                )
                return

            # Per-subsystem set — share the reply-builder with the
            # picker callback so both surfaces get identical reply
            # text (including the conditional backup line per
            # ``.plans/model-picker-ux-research.md`` §5 cleanup 4).
            if key not in DEFAULT_SUBSYSTEM_TIERS:
                await msg.reply_text(
                    f"Unknown subsystem '{key}'. Known: "
                    f"{', '.join(sorted(DEFAULT_SUBSYSTEM_TIERS))}"
                )
                return
            _ok, reply = self._apply_subsystem_set(key, value)
            await msg.reply_text(reply)
            return

        # Unknown subcommand → usage.
        await msg.reply_text(_MODEL_USAGE)

    # ── Day 3 of model picker UX — shared reply-builder ────────────

    def _apply_subsystem_set(
        self, subsystem: str, value: str,
        reasoning: str | None = None,
    ) -> tuple[bool, str]:
        """Validate + write + render the reply for a per-subsystem
        ``models.subsystems.<sub> = <value>`` mutation.

        Shared by the typed-arg path on /model set AND the Day 3
        picker callback. Returns ``(success, reply_text)`` so the
        callback can decide between editing the picker reply
        (success) or showing a refusal toast that preserves the
        picker state (validator error).

        ``reasoning`` (added 2026-05-08): when set, writes the
        dict shape ``models.subsystems.<sub>: {model: <value>,
        reasoning: <level>}``. When None (the default), writes
        the plain string shape — preserves backwards-compat for
        configs that were never touched by the reasoning picker.

        Reply text matches the typed-arg path byte-for-byte
        (including the conditional comment-preservation backup
        line per ``.plans/model-picker-ux-research.md`` §5
        cleanup 4 — the line surfaces only when
        ``backup_if_commented`` actually wrote a .bak).
        """
        from vexis_agent.core.model_discovery import discovery_for_validator
        from vexis_agent.core.model_validator import validate_models_config
        from vexis_agent.core.yaml_config import (
            VALID_BRAIN_KINDS,
            _read_raw,
            brain_kind,
            model_for_tier_from_config,
            subsystem_tier_from_config,
        )
        from vexis_agent.core.yaml_config_writer import (
            atomic_write_yaml,
            backup_if_commented,
        )
        from vexis_agent.core.paths import vexis_dir

        cfg_path = vexis_dir() / "config.yaml"
        current = _read_raw()
        # Cross-brain refusal (added 2026-05-08): if the value is a
        # known model id of an UNCONFIGURED brain, refuse with an
        # install/auth pointer rather than silently writing a
        # config that the spawn would later reject. Only fires for
        # the typed-arg path on the slash; the picker handles
        # cross-brain via the confirm-and-restart flow instead.
        # Skipped when the value is one of the unambiguous shapes
        # (abstract tier or model already known to the active brain).
        from vexis_agent.core.model_discovery import (
            is_brain_configured,
            model_belongs_to_brain,
        )
        from vexis_agent.core.yaml_config import ABSTRACT_TIERS
        if value not in ABSTRACT_TIERS:
            owner = model_belongs_to_brain(value)
            current_kind = brain_kind()
            if (
                owner is not None
                and owner != current_kind
                and not is_brain_configured(owner)
            ):
                install_hint = (
                    _INSTALL_HINT_CLAUDE_CODE if owner == "claude-code"
                    else _INSTALL_HINT_OPENCODE
                )
                return (
                    False,
                    _MODEL_CROSS_BRAIN_BRAIN_NOT_CONFIGURED_TMPL.format(
                        model=value, required=owner, install=install_hint,
                    ),
                )
        proposed = self._proposed_set_subsystem(
            current, subsystem, value, reasoning=reasoning,
        )
        # Day 4 of model picker UX wires discovery into the slash
        # write path so rule 6 (available-models membership) actually
        # fires here — without it, the picker would write opencode
        # ids the spawn would reject. Same data source as the
        # dashboard's _models_payload (5-min in-process cache; sub-ms
        # for warm calls). Rule 6 promoted to error-severity for
        # opencode in this same Day 4 batch, so this wiring is what
        # turns the promotion into an actual pre-write refusal on
        # the slash + picker path.
        available = discovery_for_validator(VALID_BRAIN_KINDS)
        findings = validate_models_config(
            proposed, brain_kind(),
            available_models_per_brain=available,
        )
        errors = [f for f in findings if f.severity == "error"]
        if errors:
            problems = "\n".join(
                f"  • [{f.subsystem or '<global>'}] "
                f"{f.problem}\n    → {f.suggested_fix}"
                for f in errors
            )
            return (
                False,
                _MODEL_VALIDATOR_ERROR_TMPL.format(problems=problems),
            )

        backup_msg = ""
        if cfg_path.exists():
            bak = backup_if_commented(cfg_path)
            if bak is not None:
                backup_msg = "\n" + _MODEL_BACKUP_REPLY_TMPL
        atomic_write_yaml(cfg_path, proposed)

        resolved = model_for_tier_from_config(
            proposed.get("models"),
            brain_kind(),
            subsystem_tier_from_config(proposed.get("models"), subsystem),
        )
        # Confirmation copy includes the reasoning level when set
        # so the user has visual proof their pick stuck. Plain
        # writes (reasoning=None) keep the original short form.
        reasoning_suffix = (
            f" + reasoning={reasoning}" if reasoning else ""
        )
        return (
            True,
            _MODEL_SET_OK_TMPL.format(
                key=subsystem, value=value + reasoning_suffix,
                resolved=resolved or "<brain default>",
                brain=brain_kind(),
            ) + backup_msg,
        )

    @staticmethod
    def _proposed_set_subsystem(
        current: dict, subsystem: str, value: str,
        reasoning: str | None = None,
    ) -> dict:
        """Build the proposed config dict for ``/model set <name>
        <value>``. Pure function; the writer never sees a partial
        edit.

        Two write shapes:
          - ``reasoning=None`` (default) → plain string under
            ``models.subsystems.<sub>: <value>``. Backwards
            compatible with every config that pre-dates the
            reasoning picker.
          - ``reasoning="<level>"`` → dict shape under
            ``models.subsystems.<sub>: {model: <value>,
            reasoning: <level>}``. Picker writes this when the
            user picks a reasoning level after the model step."""
        models = dict(current.get("models") or {})
        subs = dict(models.get("subsystems") or {})
        if reasoning:
            subs[subsystem] = {"model": value, "reasoning": reasoning}
        else:
            subs[subsystem] = value
        models["subsystems"] = subs
        return {**current, "models": models}

    # ── Day 3 of model picker UX — keyboard helpers + render ───────

    # Picker pagination size. Telegram's `<InlineKeyboardMarkup>` has
    # no hard row limit but UX degrades past ~25 buttons in a single
    # message; opencode's largest provider buckets push 50+ ids so
    # paginated lists keep the picker readable. 20 leaves room for
    # nav row + back/cancel without crowding.
    _PICKER_PAGE_SIZE = 20

    @staticmethod
    def _subsystem_to_index(subsystem: str) -> int:
        """Map a subsystem name to its sorted index in
        ``DEFAULT_SUBSYSTEM_TIERS``. Used in
        ``model_pick_model:<idx>:<full_id>`` callback_data so the
        encoding fits Telegram's 64-byte cap on opencode's
        worst-case full ids (e.g.
        ``openrouter/anthropic/claude-sonnet-4.5`` is 38 bytes;
        plus a 24-char subsystem name and the ``model_pick_model:``
        prefix would push past 64). The 4 short callback shapes
        (provider/page/back/cancel) keep the verbose subsystem
        name — they fit comfortably and stay greppable."""
        from vexis_agent.core.yaml_config import DEFAULT_SUBSYSTEM_TIERS
        return sorted(DEFAULT_SUBSYSTEM_TIERS).index(subsystem)

    @staticmethod
    def _index_to_subsystem(idx_str: str) -> str | None:
        """Inverse of :meth:`_subsystem_to_index`. Returns ``None``
        when ``idx_str`` doesn't parse or is out of range — the
        callback handler renders a stale-picker message in that
        case rather than crashing."""
        from vexis_agent.core.yaml_config import DEFAULT_SUBSYSTEM_TIERS
        try:
            idx = int(idx_str)
        except ValueError:
            return None
        sortlist = sorted(DEFAULT_SUBSYSTEM_TIERS)
        if 0 <= idx < len(sortlist):
            return sortlist[idx]
        return None

    # Brain-kind ↔ short-code map for callback_data. The picker
    # encodes brain in the callback because cross-brain switching
    # (added 2026-05-08) means a button can reference a model
    # under a brain other than the active one. Two-char codes
    # keep the byte budget under Telegram's 64-byte cap on the
    # worst-case ``model_pick_model:<sidx>:<brain>:<full_id>``
    # shape (the longest opencode id is 38 bytes; using full
    # brain names ``"claude-code"``/``"opencode"`` would push past
    # the cap).
    _BRAIN_TO_SHORT: dict[str, str] = {"claude-code": "cc", "opencode": "oc"}
    _SHORT_TO_BRAIN: dict[str, str] = {"cc": "claude-code", "oc": "opencode"}

    @classmethod
    def _make_provider_keyboard(
        cls, subsystem: str,
        providers_per_brain: dict[str, list[str]],
    ) -> InlineKeyboardMarkup:
        """One button per (brain, provider) combo, plus a Cancel
        row. ``providers_per_brain`` is ordered:
        ``{brain_kind: [provider_name, ...]}``. Brain order in the
        dict (claude-code first, then opencode) drives row order.

        When only one brain is in the dict (the common case), the
        button label is just the provider name (matches pre-cross-
        brain behavior). When multiple brains are present, labels
        gain a ``" (<brain>)"`` suffix so the user can distinguish
        ``Anthropic (claude-code)`` from ``Anthropic (opencode)``.

        Aliases are NOT exposed as separate buttons here per the
        ``.plans/model-picker-ux-research.md`` §5 alias-omission
        decision: bare aliases (haiku/sonnet/opus) drift over time
        as Anthropic releases new models behind the same name, so
        the picker enforces version pinning by surfacing only full
        ids. Users who want an alias keep using the typed-arg path."""
        rows: list[list[InlineKeyboardButton]] = []
        multi_brain = len(providers_per_brain) > 1
        for brain, providers in providers_per_brain.items():
            brain_short = cls._BRAIN_TO_SHORT.get(brain)
            if brain_short is None:
                continue  # null or future brain — picker doesn't offer it
            for provider in providers:
                # Always include the trailing :0 flag (collapsed
                # default view) so the parser's segment-count
                # check matches the rendered shape. The toggle
                # button rebuilds the same callback with :1 to
                # flip into expanded view.
                data = (
                    f"model_pick_provider:{subsystem}:{brain_short}:"
                    f"{provider}:0"
                )
                # Worst-case byte budget: prefix(19) + sub(24) +
                # short(2) + provider(14) + flag(1) + 4 separators
                # = 64 bytes. At the cap; longer inputs would need
                # encoding revisits.
                label = f"{provider} ({brain})" if multi_brain else provider
                rows.append([InlineKeyboardButton(
                    text=label, callback_data=data,
                )])
        rows.append([InlineKeyboardButton(
            text="✗ Cancel",
            callback_data=f"model_pick_cancel:{subsystem}",
        )])
        return InlineKeyboardMarkup(rows)

    @classmethod
    def _make_model_keyboard(
        cls, subsystem: str, provider: str, models: list[str],
        page: int = 0, expanded: bool = False,
        brain_kind: str | None = None,
    ) -> InlineKeyboardMarkup:
        """Model picker for a given provider. Aliases already
        filtered out by the caller (the picker uses full ids only).

        Family grouping (added 2026-05-07): when ``expanded`` is
        False (default), dated variants are collapsed into one
        button per family via :func:`default_view_models`. Tapping
        the new ``Show all versions`` toggle re-renders with
        ``expanded=True``, exposing every variant. The toggle only
        renders when collapsing actually hides something — opencode
        and other providers without dated variants never see it.

        Pagination: page-size buttons per screen, with a nav row
        (``← Prev`` / ``page n/m`` / ``Next →``) at the top when
        there's more than one page. The page indicator is a
        no-op-callback button (``model_pick_noop``) — Telegram
        requires every InlineKeyboardButton to have either a URL
        or callback_data; sending a deliberately-ignored callback
        is the cleanest way to render an inert label.

        Subsystem-as-name (not index) in the callback_data for the
        navigation/back/cancel/toggle buttons (they don't carry a
        long full id so the byte budget is comfortable). Model
        selection switches to subsystem-as-INDEX because the full
        id pushes the prefix-name combination past 64 bytes on
        opencode."""
        from vexis_agent.core.model_discovery import (
            default_view_models,
            expanded_view_models,
        )
        # Compute collapsed + expanded counts so we can decide
        # whether the toggle is meaningful (don't render it when
        # the two views are identical — opencode case).
        collapsed = default_view_models(models)
        expanded_models = expanded_view_models(models)
        has_hidden = len(expanded_models) > len(collapsed)
        visible_set = expanded_models if expanded else collapsed

        sidx = cls._subsystem_to_index(subsystem)
        # ``brain_kind`` is required after the cross-brain switch
        # work (2026-05-08) — it threads through every callback so
        # the model-tap handler knows which brain's discovery to
        # use AND so cross-brain selections trigger the
        # confirmation flow. Default to claude-code for safety
        # when callers haven't migrated yet (only the typed-arg
        # path could hit this; production picker always passes it).
        brain_short = cls._BRAIN_TO_SHORT.get(brain_kind or "claude-code", "cc")
        total_pages = max(1, (len(visible_set) + cls._PICKER_PAGE_SIZE - 1) // cls._PICKER_PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        start = page * cls._PICKER_PAGE_SIZE
        visible = visible_set[start:start + cls._PICKER_PAGE_SIZE]
        rows: list[list[InlineKeyboardButton]] = []

        if total_pages > 1:
            # Pagination preserves the expand flag AND the brain
            # short — paging within an expanded view of one brain's
            # provider doesn't accidentally swap to the other brain.
            nav: list[InlineKeyboardButton] = []
            flag = 1 if expanded else 0
            if page > 0:
                nav.append(InlineKeyboardButton(
                    text="← Prev",
                    callback_data=(
                        f"model_pick_page:{subsystem}:{brain_short}:"
                        f"{provider}:{page - 1}:{flag}"
                    ),
                ))
            nav.append(InlineKeyboardButton(
                text=f"{page + 1}/{total_pages}",
                callback_data="model_pick_noop",
            ))
            if page < total_pages - 1:
                nav.append(InlineKeyboardButton(
                    text="Next →",
                    callback_data=(
                        f"model_pick_page:{subsystem}:{brain_short}:"
                        f"{provider}:{page + 1}:{flag}"
                    ),
                ))
            rows.append(nav)

        for m in visible:
            # Brain short is part of model_pick_model so the handler
            # knows which brain owns this id (cross-brain switching).
            data = f"model_pick_model:{sidx}:{brain_short}:{m}"
            if len(data.encode("utf-8")) > _CB_DATA_MAX_BYTES:
                # Defensive: a realistic full id should always fit
                # under 64 bytes with sidx + brain_short, but if a
                # discovery source ever ships a freakishly long id
                # we silently skip it rather than crash. The user
                # can still typed-arg-set it via the slash if they
                # know the name.
                log.warning(
                    "model_pick_model callback_data exceeds %d bytes "
                    "for %s; skipping button",
                    _CB_DATA_MAX_BYTES, m,
                )
                continue
            rows.append([InlineKeyboardButton(text=m, callback_data=data)])

        if has_hidden:
            # Toggle re-renders the same provider with the flipped
            # flag from page 0. Carries brain short so toggling
            # within a cross-brain provider's models stays on that
            # brain.
            toggle_label = "Hide versions" if expanded else "Show all versions"
            new_flag = 0 if expanded else 1
            rows.append([InlineKeyboardButton(
                text=toggle_label,
                callback_data=(
                    f"model_pick_provider:{subsystem}:{brain_short}:"
                    f"{provider}:{new_flag}"
                ),
            )])

        rows.append([
            InlineKeyboardButton(
                text="← Back",
                callback_data=f"model_pick_back:{subsystem}",
            ),
            InlineKeyboardButton(
                text="✗ Cancel",
                callback_data=f"model_pick_cancel:{subsystem}",
            ),
        ])
        return InlineKeyboardMarkup(rows)

    async def _render_provider_picker(
        self, msg, subsystem: str, *, edit_query=None,
    ) -> None:
        """Render the provider keyboard as a fresh reply (when
        ``edit_query`` is None) or by editing an existing picker
        message (when called from the Back button's callback).

        Cross-brain (added 2026-05-08): queries discovery for
        EVERY configured brain (per :func:`configured_brains`).
        When both shipping brains are configured, providers from
        both appear in the same keyboard with brain-suffix labels
        (e.g. ``Anthropic (claude-code)`` vs ``Anthropic (opencode)``).
        Picking a model from a non-active brain triggers the
        cross-brain confirmation step (see ``model_pick_model``
        callback handler).

        When only one brain is configured, the keyboard renders
        with bare provider labels (no brain suffix) — matches
        pre-cross-brain behavior for single-brain users."""
        from vexis_agent.core.model_discovery import (
            configured_brains,
            discovery_grouped_for_brain,
        )
        from vexis_agent.core.yaml_config import (
            _read_raw,
            brain_kind,
            subsystem_tier_from_config,
        )

        kind = brain_kind()
        # Query each configured brain. The active brain always
        # comes first in the dict iteration order (so its
        # providers render at the top of the keyboard) — this
        # reads naturally as "your current brain's providers,
        # then the other brain's".
        all_brains = configured_brains()
        ordered = [kind] if kind in all_brains else []
        ordered += [b for b in all_brains if b != kind]
        aliases = {"haiku", "sonnet", "opus"}
        per_brain: dict[str, list[str]] = {}
        for b in ordered:
            grouped = discovery_grouped_for_brain(b)
            filtered = {
                p: [m for m in models if m not in aliases]
                for p, models in grouped.items()
            }
            providers = [p for p, models in filtered.items() if models]
            if providers:
                per_brain[b] = providers

        if not per_brain:
            # No discovery data on any configured brain → text-only
            # fallback. Same wording whether opencode binary is
            # missing or null brain is active.
            text = _PICKER_NO_DISCOVERY_TMPL.format(
                brain=kind, subsystem=subsystem,
            )
            if edit_query is not None:
                await edit_query.edit_message_text(text)
            else:
                await msg.reply_text(text)
            return

        # Render the prompt with the user's current pick (if any)
        # so they have context for what they're replacing.
        current_value = subsystem_tier_from_config(
            _read_raw().get("models"), subsystem,
        )
        text = _PICKER_PROMPT_TMPL.format(
            subsystem=subsystem,
            current=current_value or "default",
        )
        keyboard = self._make_provider_keyboard(subsystem, per_brain)
        if edit_query is not None:
            await edit_query.edit_message_text(text, reply_markup=keyboard)
        else:
            await msg.reply_text(text, reply_markup=keyboard)

    async def _render_model_picker(
        self, query, subsystem: str, provider: str,
        page: int = 0, expanded: bool = False,
        brain_kind: str | None = None,
    ) -> None:
        """Edit the existing picker message to show the model
        keyboard for ``provider``. Called from the
        ``model_pick_provider`` (initial tap + toggle) and
        ``model_pick_page`` callback branches.

        Re-fetches discovery rather than carrying the model list
        through callback_data (which would blow the 64-byte cap).
        The 5-min discovery cache means this is a sub-ms read; if
        the cache invalidated between provider-tap and re-render
        the user just sees the freshly-grouped list, which is
        fine.

        ``brain_kind`` (added 2026-05-08): the brain whose
        discovery to query. Defaults to the active brain when
        unset (the typed-arg path may call without specifying).
        Cross-brain pickers always pass this so the right
        brain's models render — even when the user is browsing
        the OTHER brain's providers from the unified keyboard.

        ``expanded`` toggles family-grouping: False (default) shows
        one button per family via ``default_view_models``; True
        shows every dated variant. Reply text gains a
        ``hidden_suffix`` mentioning the count when collapsed
        view actually hides anything."""
        from vexis_agent.core.model_discovery import (
            default_view_models,
            discovery_grouped_for_brain,
            expanded_view_models,
        )
        from vexis_agent.core.yaml_config import brain_kind as active_brain_kind

        kind = brain_kind or active_brain_kind()
        grouped = discovery_grouped_for_brain(kind)
        aliases = {"haiku", "sonnet", "opus"}
        models = [m for m in grouped.get(provider, []) if m not in aliases]
        if not models:
            # Provider disappeared from discovery between picker
            # render and tap (rare — discovery cache is 5 min). Fall
            # back to the provider keyboard so the user can pick a
            # still-valid provider.
            await self._render_provider_picker(
                msg=None, subsystem=subsystem, edit_query=query,
            )
            return
        # Pick collapsed vs expanded count for pagination math +
        # hidden-count surfacing.
        collapsed = default_view_models(models)
        expanded_models = expanded_view_models(models)
        active = expanded_models if expanded else collapsed
        total_pages = max(
            1, (len(active) + self._PICKER_PAGE_SIZE - 1)
            // self._PICKER_PAGE_SIZE,
        )
        page_suffix = (
            f" (page {page + 1}/{total_pages})" if total_pages > 1 else ""
        )
        # Hidden-count surfaces only in the collapsed view AND only
        # when collapse actually hides something. Reads the same
        # collapse-vs-expand counts the keyboard builder uses so the
        # text stays in sync with what the buttons render.
        hidden_count = (
            len(expanded_models) - len(collapsed) if not expanded else 0
        )
        hidden_suffix = (
            _PICKER_HIDDEN_VERSIONS_TMPL.format(n=hidden_count)
            if hidden_count > 0 else ""
        )
        text = _PICKER_MODEL_PROMPT_TMPL.format(
            subsystem=subsystem, provider=provider,
            page_suffix=page_suffix, hidden_suffix=hidden_suffix,
        )
        keyboard = self._make_model_keyboard(
            subsystem, provider, models, page=page, expanded=expanded,
            brain_kind=kind,
        )
        await query.edit_message_text(text, reply_markup=keyboard)

    @classmethod
    def _parse_provider_payload(
        cls, payload: str,
    ) -> tuple[str | None, str, str, bool]:
        """Parse ``model_pick_provider`` callback_data payload.

        Expected shape (post-cross-brain):
        ``<subsystem>:<brain_short>:<provider>:<flag>`` — 4 parts.
        Returns ``(subsystem | None, brain_kind, provider, expanded)``;
        subsystem is None when the payload doesn't unpack cleanly
        (callback handler renders a stale-picker message)."""
        parts = payload.split(":")
        if len(parts) != 4:
            return None, "claude-code", "", False
        sub, brain_short, provider, flag = parts
        brain = cls._SHORT_TO_BRAIN.get(brain_short)
        if brain is None:
            return None, "claude-code", "", False
        return sub, brain, provider, flag == "1"

    @classmethod
    def _parse_page_payload(
        cls, payload: str,
    ) -> tuple[str | None, str, str, int | None, bool]:
        """Parse ``model_pick_page`` callback_data payload.

        Expected shape:
        ``<subsystem>:<brain_short>:<provider>:<page>:<flag>`` — 5 parts."""
        parts = payload.split(":")
        if len(parts) != 5:
            return None, "claude-code", "", None, False
        sub, brain_short, provider, page_str, flag = parts
        brain = cls._SHORT_TO_BRAIN.get(brain_short)
        if brain is None:
            return None, "claude-code", "", None, False
        try:
            page = int(page_str)
        except ValueError:
            return None, "claude-code", "", None, False
        return sub, brain, provider, page, flag == "1"

    @staticmethod
    def _picker_session_key(query) -> tuple[int, int] | None:
        """Return ``(chat_id, message_id)`` for a picker callback's
        message, or ``None`` if the message reference isn't fully
        populated (defensive — Telegram's CallbackQuery.message can
        be absent in edge cases). Used as the
        :attr:`_picker_pending` dict key for multi-step picker
        sessions."""
        msg = getattr(query, "message", None)
        if msg is None:
            return None
        chat = getattr(msg, "chat", None)
        chat_id = getattr(chat, "id", None) if chat is not None else None
        message_id = getattr(msg, "message_id", None)
        if chat_id is None or message_id is None:
            return None
        return (chat_id, message_id)

    def _get_picker_pending(self) -> dict[tuple[int, int], dict]:
        """Defensive accessor for :attr:`_picker_pending`. Test
        fixtures construct ``TelegramTransport`` via ``__new__``
        and skip ``__init__``, so the attribute may not exist.
        Lazy-init on first access keeps both production code and
        bare-bones test fixtures working without per-test setup
        boilerplate."""
        pending = getattr(self, "_picker_pending", None)
        if pending is None:
            pending = {}
            self._picker_pending = pending
        return pending

    @classmethod
    def _make_reasoning_keyboard(
        cls, subsystem: str, levels: list[str],
    ) -> InlineKeyboardMarkup:
        """Reasoning-level picker. One button per level (driven
        from the brain's per-model capability discovery — the
        caller has already filtered to levels the chosen model
        supports), plus a ``(default)`` button that writes
        ``reasoning=None`` (brain picks default).

        callback_data shape: ``model_pick_reasoning:<sidx>:<level>``.
        Spec lists a trailing ``:<flag>`` for parity with the
        provider/page callbacks; deliberately omitted here because
        no flag is meaningful at the reasoning step (no
        expand-toggle to flip). Documented inline so the absence
        is obvious.

        Byte budget: longest sidx=1 byte + longest level (e.g.
        ``medium``=6) + prefix ``model_pick_reasoning:``=21 bytes
        = 30 bytes total. Comfortably under 64."""
        sidx = cls._subsystem_to_index(subsystem)
        rows: list[list[InlineKeyboardButton]] = []
        for level in levels:
            rows.append([InlineKeyboardButton(
                text=level,
                callback_data=f"model_pick_reasoning:{sidx}:{level}",
            )])
        # Sentinel: empty string for "no reasoning override; brain
        # picks default". Stays under-budget. Decoded in the
        # callback as "reasoning=None".
        rows.append([InlineKeyboardButton(
            text="(default — brain picks)",
            callback_data=f"model_pick_reasoning:{sidx}:",
        )])
        rows.append([InlineKeyboardButton(
            text="✗ Cancel",
            callback_data=f"model_pick_cancel:{subsystem}",
        )])
        return InlineKeyboardMarkup(rows)

    async def _render_reasoning_picker(
        self, query, subsystem: str, model_id: str, levels: list[str],
    ) -> None:
        """Edit the picker reply to show the reasoning-level
        keyboard. Caller has already verified that ``levels`` is
        non-empty (single-level lists still render — let the user
        confirm the only available level rather than auto-picking
        on their behalf)."""
        text = _PICKER_REASONING_PROMPT_TMPL.format(
            subsystem=subsystem, model=model_id,
        )
        keyboard = self._make_reasoning_keyboard(subsystem, levels)
        await query.edit_message_text(text, reply_markup=keyboard)

    # ── Cross-brain switch (added 2026-05-08) ──────────────────────

    @staticmethod
    def _supervisor_detected() -> bool:
        """systemd sets ``INVOCATION_ID`` for spawned services.
        Use that as the canary for "supervisor will restart us
        cleanly". Other supervisor patterns (s6, runit, nohup
        loops) likely don't set it; users on those configurations
        get the explicit warning in the cross-brain confirm copy."""
        import os
        return bool(os.environ.get("INVOCATION_ID"))

    @classmethod
    def _make_cross_brain_confirm_keyboard(
        cls, subsystem: str, target_brain: str, model_id: str,
    ) -> InlineKeyboardMarkup:
        """Two-button confirm keyboard for cross-brain switching.
        callback_data shape mirrors model_pick_model so the byte
        budget is identical (worst case 60 bytes)."""
        sidx = cls._subsystem_to_index(subsystem)
        brain_short = cls._BRAIN_TO_SHORT.get(target_brain, "cc")
        return InlineKeyboardMarkup([
            [InlineKeyboardButton(
                text=f"✓ Yes, switch to {target_brain}",
                callback_data=(
                    f"model_pick_swap:{sidx}:{brain_short}:"
                    f"{model_id}"
                ),
            )],
            [InlineKeyboardButton(
                text="✗ Cancel",
                callback_data=f"model_pick_cancel:{subsystem}",
            )],
        ])

    async def _render_cross_brain_confirm(
        self, query, subsystem: str, target_brain: str, model_id: str,
    ) -> None:
        """Render the cross-brain-switch confirmation keyboard.
        User has just tapped a model whose brain != current brain.
        Confirmation copy includes the supervisor warning when
        no INVOCATION_ID is set so the user knows they'll need
        to manually restart."""
        from vexis_agent.core.yaml_config import brain_kind
        supervisor_note = (
            "" if self._supervisor_detected()
            else _PICKER_CROSS_BRAIN_NO_SUPERVISOR_TMPL
        )
        text = _PICKER_CROSS_BRAIN_CONFIRM_TMPL.format(
            model=model_id,
            target_brain=target_brain,
            current_brain=brain_kind(),
            subsystem=subsystem,
            supervisor_note=supervisor_note,
        )
        keyboard = self._make_cross_brain_confirm_keyboard(
            subsystem, target_brain, model_id,
        )
        await query.edit_message_text(text, reply_markup=keyboard)

    async def _handle_cross_brain_switch(
        self, query, subsystem: str, target_brain: str, model_id: str,
    ) -> None:
        """Confirmed cross-brain switch: write brain.kind +
        subsystem assignment, send the "restarting" reply, then
        schedule the daemon exit so the supervisor restarts us.

        Daemon exits via :func:`_request_daemon_restart` after a
        small delay so the Telegram reply has time to flush. If
        no supervisor is configured the daemon dies; user has to
        manually restart (warning surfaced in the confirm copy)."""
        from vexis_agent.core.yaml_config import _read_raw, brain_kind
        from vexis_agent.core.yaml_config_writer import (
            atomic_write_yaml, backup_if_commented,
        )
        from vexis_agent.core.paths import vexis_dir

        cfg_path = vexis_dir() / "config.yaml"
        current = _read_raw()
        # Build the proposed config: brain.kind switch + subsystem
        # assignment. Plain string subsystem value (no reasoning) —
        # post-restart capability data may differ; user re-runs the
        # picker if they want a reasoning level.
        proposed = self._proposed_set_subsystem(
            current, subsystem, model_id, reasoning=None,
        )
        brain_block = dict(proposed.get("brain") or {})
        brain_block["kind"] = target_brain
        proposed["brain"] = brain_block
        # Backup commented config before mutating (same posture as
        # the typed-arg paths).
        if cfg_path.exists():
            backup_if_commented(cfg_path)
        atomic_write_yaml(cfg_path, proposed)

        supervisor_note = (
            "" if self._supervisor_detected()
            else _PICKER_CROSS_BRAIN_NO_SUPERVISOR_TMPL
        )
        await query.edit_message_text(
            _MODEL_CROSS_BRAIN_SWITCHING_TMPL.format(
                target_brain=target_brain,
                subsystem=subsystem,
                model=model_id,
                supervisor_note=supervisor_note,
            )
        )
        # Schedule the exit so the reply flushes first. asyncio
        # task survives the handler return and fires from the
        # main event loop tick.
        log.info(
            "cross-brain switch: %s → %s (%s); requesting restart "
            "(current brain=%s)",
            subsystem, model_id, target_brain, brain_kind(),
        )
        asyncio.create_task(self._exit_for_restart_soon())

    @staticmethod
    async def _exit_for_restart_soon() -> None:
        """Brief sleep so the Telegram reply has time to flush,
        then ``os._exit(0)`` for the supervisor to restart us.

        ``os._exit`` (not ``sys.exit``) is intentional — sys.exit
        raises SystemExit which asyncio's task wrapper catches and
        logs, so the process doesn't actually terminate.
        ``os._exit`` is immediate and unconditional, which is what
        we want for restart. Skips Python cleanup (atexit handlers,
        gc finalizers) — fine for restart since the supervisor
        comes back up clean."""
        import os
        await asyncio.sleep(0.5)
        log.info("cross-brain switch: exiting now for supervisor restart")
        os._exit(0)

    def _model_status_text(self) -> str:
        """Render the current resolution table for ``/model``
        (bare) and ``/model status``.

        Pulls structured data from ``build_resolution_table`` —
        the same helper the dashboard's GET /api/v1/models endpoint
        consumes. Renders to plain text. The contract test in
        ``tests/test_models_api.py`` pins that the slash text and
        the dashboard JSON expose the same per-subsystem
        resolution data byte-for-byte (catches drift before it
        ships).

        Day 5 wires the running brain kind through the helper so
        the "edited brain.kind without restarting" canary surfaces
        in the slash output too. Pulled from the message handler's
        brain reference via the ``brain_instance_to_kind`` mapper
        — keeps the slash decoupled from the brain class hierarchy.
        """
        from vexis_agent.core.model_validator import (
            brain_instance_to_kind,
            build_resolution_table,
            format_resolution_display,
        )
        from vexis_agent.core.yaml_config import (
            DEFAULT_SUBSYSTEM_TIERS,
            _read_raw,
            brain_kind,
        )

        # Defensive getattr — Day 5 added the running-brain
        # consistency check that pulls through self._handler._brain.
        # Some test fixtures construct TelegramTransport via
        # __new__ and never set _handler; default the canary to
        # silent (running=None) for them rather than raising.
        handler = getattr(self, "_handler", None)
        brain = getattr(handler, "_brain", None) if handler is not None else None
        running_kind = brain_instance_to_kind(brain) if brain is not None else None
        table = build_resolution_table(
            _read_raw(), brain_kind(),
            running_brain_kind=running_kind,
        )
        kind = table["brain_kind"]
        lines = [f"Current resolution (brain: {kind}):"]
        max_name = max(len(n) for n in DEFAULT_SUBSYSTEM_TIERS)
        for row in table["subsystems"]:
            # Polish-pass display rules (2026-05-08): use
            # ``format_resolution_display`` so unconfigured
            # subsystems show "(default → <resolved>)" rather than
            # the resolved tier name; picker-written models that
            # resolve to themselves drop the redundant "X → X"
            # arrow. Dashboard mirrors the same rules in TS.
            display = format_resolution_display(
                row["configured"], row["resolved_model_id"],
            )
            lines.append(
                f"  {row['name'].ljust(max_name)}  {display}"
            )
        non_info = [
            f for f in (table["global_findings"] + [
                f for row in table["subsystems"] for f in row["findings"]
            ])
            if f["severity"] != "info"
        ]
        if non_info:
            lines.append("")
            lines.append(f"Validator: {len(non_info)} issue(s):")
            for f in non_info:
                lines.append(
                    f"  ⚠ [{f['subsystem'] or '<global>'}] {f['problem']}"
                )
        return "\n".join(lines)

    def _model_list_text(self, target_brain: str | None) -> str:
        """Render ``/model list`` (subsystems + brains) or
        ``/model list <brain>`` (per-brain model hints)."""
        from vexis_agent.core.yaml_config import (
            DEFAULT_SUBSYSTEM_TIERS,
            VALID_BRAIN_KINDS,
        )

        if target_brain is None:
            lines = ["Subsystems:"]
            for name in sorted(DEFAULT_SUBSYSTEM_TIERS):
                lines.append(f"  • {name} (default tier: {DEFAULT_SUBSYSTEM_TIERS[name]})")
            lines.append("")
            lines.append("Brains:")
            for k in sorted(VALID_BRAIN_KINDS):
                lines.append(f"  • {k}")
            lines.append("")
            lines.append("Per-brain model lists: /model list <brain>")
            return "\n".join(lines)

        if target_brain == "claude-code":
            return (
                "claude-code accepts:\n"
                "  Aliases: sonnet, opus, haiku\n"
                "  Full names: claude-haiku-4-5, claude-sonnet-4-6, "
                "claude-opus-4-1, etc.\n"
                "  Reference: https://docs.anthropic.com/claude/models"
            )
        if target_brain == "opencode":
            return (
                "opencode accepts ~270 models across ~20 providers.\n"
                "Format: provider/model (e.g. anthropic/claude-haiku-3-5).\n"
                "Run `opencode models` in a shell to see the live list.\n"
                "Day 4 will surface the dashboard picker; for now, the "
                "shell is the discovery path."
            )
        return f"Unknown brain '{target_brain}'. Known: {', '.join(sorted(VALID_BRAIN_KINDS))}"

    async def _run_goal_hook(self, bot, chat_id: int, last_response: str) -> None:
        """After-each-turn hook called inside ``_drain_chat``.

        Skipped entirely when ``goals.enabled`` is False (so the
        feature stays dormant in production until the Day 4
        release-gate flip). When on, loads the goal for the current
        session UUID, calls :meth:`GoalManager.evaluate_after_turn`,
        sends the user-visible status line, and enqueues the
        continuation prompt (tagged ``origin="goal_continuation"``)
        when the judge says continue.

        Two race guards protect the user from surprise continuations:

        * **Cancel guard at entry.** If ``running_tasks.is_drain_cancelled``
          returns True, the brain turn that just finished was aborted
          mid-flight by /cancel — its reply is empty/cancelled, and
          feeding that to the judge would fold to ``continue`` per
          the empty-response rule, enqueuing a continuation that the
          drain's post-cancel ``take_over_if_pending`` would then
          run. Bail before we touch the judge.
        * **Active recheck before enqueue.** Between
          :meth:`evaluate_after_turn` returning and the enqueue, a
          concurrent ``/goal pause`` / ``/goal clear`` / cancel
          auto-pause may have flipped status. Reload from disk and
          bail if the goal is no longer active. We also suppress the
          status message in this case — state changed under us and
          the user already triggered the change, so they don't need
          a "↻ Continuing toward goal" line that contradicts their
          own pause/clear reply.

        Failures are isolated — a broken judge or store I/O error
        logs and returns, never breaks the drain loop.
        """
        try:
            from vexis_agent.core.yaml_config import goals_enabled
            if not goals_enabled():
                return

            # Race guard 1: drain-cancelled. The brain turn was
            # aborted mid-flight; its reply isn't a real signal.
            if self._running_tasks.is_drain_cancelled(chat_id):
                log.debug(
                    "goal hook: skipping (drain cancelled) for chat %s", chat_id
                )
                return

            session_uuid = self._handler.current_session_uuid()
            mgr = self._build_goal_manager(session_uuid)
            if not mgr.is_active():
                return

            decision = await mgr.evaluate_after_turn(
                last_response or "", self._handler._brain
            )
        except Exception:
            log.exception("goal hook failed before/at evaluate; chat %s", chat_id)
            return

        msg_text = decision.get("message") or ""
        should_continue = decision.get("should_continue", False)

        # Terminal branch (done / budget-exhausted): send the status
        # message and stop. No reload needed — the reload guard only
        # exists to suppress continuations that would race a
        # concurrent pause/clear, not terminal status updates.
        if not should_continue:
            if msg_text:
                try:
                    await bot.send_message(
                        chat_id=chat_id, text=msg_text, parse_mode=None
                    )
                except Exception:
                    log.exception(
                        "goal hook: terminal status send failed for chat %s", chat_id
                    )
            return

        # Continue branch: race guard 2. Re-read state from disk
        # before sending the "↻ Continuing" line OR enqueuing the
        # continuation. A concurrent pause/clear between
        # evaluate_after_turn and here means the user already saw
        # their own /goal reply; tacking a "Continuing" message on
        # top would contradict it.
        #
        # TODO(brain-abstraction): Phase B's async migration of
        # evaluate_after_turn (commit 962dd71) widened the race
        # window — /cancel can now land while ``await
        # brain.spawn_aux(...)`` is awaiting. A planned automated
        # test ``test_cancel_during_async_judge_drops_continuation``
        # was prototyped in commit f07bdc7 and pulled because it
        # required coordinating three event-loop-aware actors that
        # the existing test harness doesn't model cleanly. Coverage
        # currently relies on the Day 8 dogfood checklist step #12
        # (``.plans/brain-abstraction-research.md`` §7). If a real
        # regression slips through — symptom: a "Continuing"
        # continuation arrives after a /cancel mid-judge — the bug
        # is likely in evaluate_after_turn's save path overwriting
        # the cancel-induced paused state; fix would be a
        # read-and-update CAS inside the save (or a reload-and-
        # bail INSIDE evaluate_after_turn's save block).
        try:
            mgr.reload()
        except Exception:
            log.exception(
                "goal hook: reload failed for chat %s; bailing safe", chat_id
            )
            return
        if not mgr.is_active():
            log.debug(
                "goal hook: state flipped during evaluate; dropping "
                "continuation for chat %s",
                chat_id,
            )
            return

        if msg_text:
            try:
                await bot.send_message(
                    chat_id=chat_id, text=msg_text, parse_mode=None
                )
            except Exception:
                log.exception("goal hook: status send failed for chat %s", chat_id)

        prompt = decision.get("continuation_prompt")
        if not prompt:
            return
        try:
            # Dedup: drop any stale goal_continuation already in the
            # queue before enqueuing the next one. The hook runs after
            # EVERY turn (user, voice, photo, continuation) — without
            # this, a stretch of user turns each appends a fresh
            # continuation while the previous ones sit unprocessed,
            # so the user "catches up" to a backlog of identical
            # continuation prompts once they stop typing. The continuation
            # text is deterministic (same goal → same prompt), so
            # dropping older copies costs nothing.
            await self._running_tasks.drop_messages_matching(
                chat_id,
                lambda m: m.origin == "goal_continuation",
            )
            await self._running_tasks.enqueue(
                chat_id,
                self._allowed_user_id,
                prompt,
                origin="goal_continuation",
            )
        except Exception:
            log.exception(
                "goal hook: continuation enqueue failed for chat %s", chat_id
            )

    async def _on_dashboard(
        self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Reply with the dashboard URL plus current token.

        The token-in-URL pattern is acceptable here because the only
        people who can reach the dashboard host are on the user's
        tailnet, same security model as the livestream URL surfaced by
        the brain. The token rotates on every daemon restart, so old
        URLs from a previous session stop working automatically.
        """
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /dashboard from user_id=%s", user.id)
            return
        if self._dashboard is None:
            await msg.reply_text("Dashboard not initialised.")
            return
        url = self._dashboard.url or self._dashboard.local_url
        token = self._dashboard.token
        sep = "&" if "?" in url else "?"
        full = f"{url}{sep}token={token}"
        if self._dashboard.url is None:
            full += "\n\nNote: Tailscale Serve mapping unavailable; "
            full += "this URL only resolves on this host."
        await msg.reply_text(full)

    async def _on_tailscale(
        self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Read-only `tailscale serve|funnel|status` summary.

        Uses the same in-memory cache the dashboard endpoint hits, so
        a `/tailscale` ping right after a dashboard refresh is free.
        Subprocess work runs in a thread to keep the PTB event loop
        responsive while ``tailscale`` shells out.
        """
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /tailscale from user_id=%s", user.id)
            return
        node, serve, funnel, peers = await asyncio.gather(
            asyncio.to_thread(tailscale_mod.get_node_info),
            asyncio.to_thread(tailscale_mod.get_serve_status),
            asyncio.to_thread(tailscale_mod.get_funnel_status),
            asyncio.to_thread(tailscale_mod.get_peers),
        )
        await msg.reply_text(_format_tailscale_reply(node, serve, funnel, peers))

    async def _on_screenshot(
        self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE
    ) -> None:
        msg = update.message
        user = update.effective_user
        if msg is None or user is None:
            return
        if not is_allowed(user.id, self._allowed_user_id):
            log.warning("Rejected /screenshot from user_id=%s", user.id)
            return

        # Everything after the command word is the source modifier.
        # ``/screenshot`` → None, ``/screenshot host`` → "host",
        # ``/screenshot sandbox foo`` → "sandbox foo". Empty string and
        # None both mean "auto".
        modifier = _extract_screenshot_modifier(msg.text or "")

        # ``help`` is a UX cheat: surface the modifier grammar without
        # making the user re-read the docs.
        if modifier and modifier.strip().lower() in ("help", "?", "--help"):
            await msg.reply_text(_screenshot_help_text())
            return

        try:
            ctx = await _build_router_context(modifier)
            source = resolve_source(ctx)
        except CaptureSourceError as exc:
            await msg.reply_text(f"⚠️ {exc}")
            return

        try:
            result = await capture_desktop(source=source)
        except CaptureError as exc:
            log.warning("/screenshot failed (source=%s): %s", source.kind, exc)
            await msg.reply_text(f"⚠️ Screenshot failed: {exc}")
            return

        caption = _format_screenshot_caption(source, ctx, result.summary)
        try:
            with result.image_path.open("rb") as fh:
                await msg.reply_photo(photo=fh, caption=caption)
        finally:
            result.image_path.unlink(missing_ok=True)

    async def _dispatch_brain_turn(
        self, bot, chat_id: int, user_id: int, text: str,
        *,
        outcome=None,
    ) -> str | None:
        """One brain turn for the drain loop.

        Routes to either the buffered (``handler.handle`` →
        ``_send_brain_reply``) or the streaming
        (``handler.stream`` → ``_send_brain_reply_streaming``)
        path based on ``self._streaming_enabled``. Returns the
        final assistant text so :meth:`_run_goal_hook` can judge
        the turn — or :data:`_DRAIN_TURN_BROKE` on an unexpected
        exception in either path so the goal judge sees the same
        "broken turn" string the user sees.

        Both paths preserve the historical "send-error swallow"
        semantics: a Telegram-side send/edit failure logs but
        doesn't propagate, so a single broken delivery doesn't
        kill the drain loop's queued follow-ups.

        ``getattr`` fallback on ``_streaming_enabled`` keeps the
        dispatcher safe under tests that build the transport via
        ``TelegramTransport.__new__`` to bypass the PTB
        Application wiring — most of those fixtures don't model
        streaming, so default-OFF gives them the legacy buffered
        behaviour without forcing every fixture to be touched
        every time a new transport-level flag lands.
        """
        if not getattr(self, "_streaming_enabled", False):
            try:
                # Only pass ``outcome`` when the caller actually wants
                # it — test fakes and older handler signatures don't
                # accept the kwarg, and we don't want to force every
                # fixture in the tree to be touched. ``None`` means
                # "I don't care," which is what every non-scheduled
                # drain pass uses anyway.
                if outcome is None:
                    reply = await self._handler.handle(user_id, chat_id, text)
                else:
                    reply = await self._handler.handle(
                        user_id, chat_id, text, outcome=outcome,
                    )
            except Exception as exc:
                log.exception(
                    "Drain turn raised unexpectedly for chat %s", chat_id,
                )
                # Outcome was either populated by handle() before the
                # exception (e.g. computer-use override raised) or not
                # populated at all — either way, escalate to "unknown"
                # so the schedule manager sees a real failure.
                if outcome is not None and outcome.kind == "unknown":
                    outcome.error_message = (
                        outcome.error_message or str(exc) or "drain turn raised"
                    )
                reply = _DRAIN_TURN_BROKE
            if reply is not None:
                try:
                    await self._send_brain_reply(bot, chat_id, reply)
                except Exception:
                    log.exception(
                        "Failed to send brain reply for chat %s", chat_id,
                    )
            return reply

        try:
            if outcome is None:
                reply = await self._send_brain_reply_streaming(
                    bot, chat_id, user_id, text,
                )
            else:
                reply = await self._send_brain_reply_streaming(
                    bot, chat_id, user_id, text, outcome=outcome,
                )
        except Exception as exc:
            log.exception(
                "Streaming drain turn raised unexpectedly for chat %s", chat_id,
            )
            if outcome is not None and outcome.kind == "unknown":
                outcome.error_message = (
                    outcome.error_message or str(exc) or "streaming drain raised"
                )
            try:
                await bot.send_message(
                    chat_id=chat_id, text=_DRAIN_TURN_BROKE, parse_mode=None,
                )
            except Exception:
                log.exception(
                    "Failed to send broken-turn ack for chat %s", chat_id,
                )
            reply = _DRAIN_TURN_BROKE
        return reply

    async def _send_brain_reply_streaming(
        self, bot, chat_id: int, user_id: int, text: str,
        *,
        outcome=None,
    ) -> str | None:
        """Stream the brain's reply via incremental edit_message_text.

        Lifecycle for one turn:
          1. Send a placeholder ``…`` message so the user sees an
             immediate ack while the brain spins up.
          2. Iterate ``handler.stream(...)`` events:
             - ``("chunk", str)`` — accumulate; on a throttled
               cadence (``self._streaming_min_interval`` per chat,
               default 1.0s) edit the active message with the
               accumulated text. When the active message would
               exceed :data:`_STREAMING_ROLLOVER_THRESHOLD` chars,
               finalize it on a paragraph/line boundary and start
               a fresh placeholder for the remainder.
             - ``("tool", dict)`` — currently dropped on the
               Telegram side; ``/status`` already exposes per-turn
               tool activity and adding inline tool lines would
               compete with the streamed text for the same edit
               budget. Web dashboard renders these inline because
               it has a separate UI lane for them.
             - ``("done", str)`` — canonical final text. Final-
               flush + screenshot extraction happens here.
             - ``("error", dict | None)`` — replace the active
               message with the user-facing error message
               (cancelled = silent — the /cancel handler already
               replied) and return the error string so the goal
               hook records "broken turn" semantics consistently
               with the buffered path.
          3. At done: extract screenshot paths from the full text;
             if any were found, edit the active message with the
             cleaned tail (so the path token isn't visible in the
             final state) then send each screenshot as a separate
             photo/document message AFTER the text. This is a UX
             change from the buffered path (which sent photos
             BEFORE text) — documented in
             ``docs/telegram-streaming.md``.

        Returns the full accumulated text (canonical from the
        ``done`` event when it fires; otherwise the locally
        accumulated copy as a fallback). Returns the error message
        string on the error path. Returns None only when the brain
        produced literally zero output AND no error fired —
        defensive against future stream sources that close cleanly
        with nothing to show.
        """
        # 1. Placeholder bubble. Doubles as the first ack so the
        #    user sees a reaction inside ~200ms, even if the brain
        #    takes 20s to emit its first delta.
        placeholder = await bot.send_message(
            chat_id=chat_id, text=_STREAMING_PLACEHOLDER, parse_mode=None,
        )
        message_id = placeholder.message_id
        # ``current_buffer``: text in the message identified by
        # ``message_id`` right now (before any rollover, equal to
        # ``full_text``; after rollover, equal to the suffix since
        # the last rollover boundary).
        current_buffer = ""
        # ``last_edited``: the text we most recently *successfully*
        # asked Telegram to render in ``message_id``. Used to skip
        # no-op edits — Telegram returns 400 "message is not
        # modified" on identical-text edits, which would burn the
        # rate-limit budget for nothing.
        last_edited = ""
        # ``full_text``: total text across all messages. Authority
        # for screenshot extraction at done time AND the return
        # value forwarded to the goal hook.
        full_text = ""
        last_edit_at = 0.0
        loop = asyncio.get_event_loop()
        error_payload: dict | None = None

        def _update_active_message(text: str) -> tuple[str, str]:
            """Closure-free helper to compute the next edit shape.
            Returns (text_to_render, text_recorded_as_last_edited)."""
            return text, text

        stream_iter = (
            self._handler.stream(user_id, chat_id, text)
            if outcome is None
            else self._handler.stream(user_id, chat_id, text, outcome=outcome)
        )
        async for event in stream_iter:
            kind, payload = event
            if kind == "chunk":
                if not isinstance(payload, str) or not payload:
                    continue
                current_buffer += payload
                full_text += payload

                # Rollover before throttling: if the active message
                # has grown past the threshold, seal it on a clean
                # boundary and start a new bubble for the rest.
                # Doing this BEFORE the throttle check ensures we
                # never edit a message past the 4096 ceiling even
                # on a single oversized chunk.
                if len(current_buffer) > _STREAMING_ROLLOVER_THRESHOLD:
                    head, tail = _split_at_streaming_boundary(
                        current_buffer, _STREAMING_ROLLOVER_THRESHOLD,
                    )
                    if head and head != last_edited:
                        try:
                            await bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=head,
                                parse_mode=None,
                            )
                        except Exception:
                            log.debug(
                                "rollover-finalize edit failed for chat %s",
                                chat_id, exc_info=True,
                            )
                    try:
                        new_msg = await bot.send_message(
                            chat_id=chat_id,
                            text=tail or _STREAMING_PLACEHOLDER,
                            parse_mode=None,
                        )
                    except Exception:
                        log.exception(
                            "Failed to start rollover message for chat %s",
                            chat_id,
                        )
                        # Stop streaming; let the brain stream
                        # finish in the background but stop trying
                        # to edit. Return what we have so the goal
                        # hook still runs.
                        return full_text or None
                    message_id = new_msg.message_id
                    current_buffer = tail
                    last_edited = tail or _STREAMING_PLACEHOLDER
                    last_edit_at = loop.time()
                    continue

                # Throttle check. The first chunk should never wait
                # — the user has been staring at "…" since the
                # placeholder went out — so we bypass the throttle
                # when ``last_edit_at == 0``.
                now = loop.time()
                if (
                    last_edit_at != 0.0
                    and (now - last_edit_at) < self._streaming_min_interval
                ):
                    continue
                if current_buffer == last_edited:
                    continue
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=current_buffer,
                        parse_mode=None,
                    )
                except Exception:
                    # Throttled-edit failures (429s, transient
                    # network) log at debug — the next chunk will
                    # retry, and the final-flush at done time is
                    # the safety net for the user-visible result.
                    log.debug(
                        "throttled streaming edit failed for chat %s",
                        chat_id, exc_info=True,
                    )
                else:
                    last_edited = current_buffer
                    last_edit_at = now
            elif kind == "tool":
                # Reserved for a future Telegram surface; see
                # docstring for why it's currently dropped.
                continue
            elif kind == "done":
                if isinstance(payload, str):
                    # Canonical full text from the handler — prefer
                    # this over our locally-accumulated copy
                    # because the handler has the final word on
                    # what counts as the reply (e.g. trims, empty-
                    # response substitution).
                    full_text = payload
                break
            elif kind == "error":
                error_payload = payload if isinstance(payload, dict) else None
                break

        # Error path. The web dashboard maps each code to a
        # specific UI affordance; on Telegram we just render the
        # message text (or stay silent for ``cancelled``, which is
        # the user-initiated /cancel — the cancel handler already
        # replied "Cancelled, sir").
        if error_payload is not None:
            err_msg = (error_payload.get("message") or "").strip()
            if not err_msg:
                # Cancelled: leave the placeholder in place rather
                # than blanking it (Telegram refuses empty-string
                # edits anyway). The /cancel handler is the source
                # of truth for the user-visible cancel ack.
                return ""
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=err_msg,
                    parse_mode=None,
                )
            except Exception:
                log.debug(
                    "error-state edit failed for chat %s",
                    chat_id, exc_info=True,
                )
                # Edit failed — fall back to a fresh send so the
                # user still sees what went wrong.
                try:
                    await bot.send_message(
                        chat_id=chat_id, text=err_msg, parse_mode=None,
                    )
                except Exception:
                    log.exception(
                        "Failed to surface streaming error for chat %s",
                        chat_id,
                    )
            return err_msg

        # Done path — final flush. Three sub-cases.
        paths, cleaned = _extract_screenshot_paths(full_text)
        cleaned = cleaned.strip()
        if not cleaned and not paths:
            # Brain produced nothing useful. Replace placeholder
            # with the standard empty-response marker so the user
            # gets the same UX as the buffered path.
            from vexis_agent.core.handler import _EMPTY_RESPONSE  # local: avoid import cycle
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=_EMPTY_RESPONSE,
                    parse_mode=None,
                )
            except Exception:
                log.debug(
                    "empty-response edit failed for chat %s",
                    chat_id, exc_info=True,
                )
            return _EMPTY_RESPONSE

        if not paths:
            # No paths to strip — just make sure the active
            # message reflects the final state of ``current_buffer``
            # (the throttle may have skipped the last chunk).
            target = current_buffer.rstrip()
            if target and target != last_edited:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=target,
                        parse_mode=None,
                    )
                except Exception:
                    log.debug(
                        "final-flush edit failed for chat %s",
                        chat_id, exc_info=True,
                    )
            return full_text

        # Paths present. Strategy: edit the active message with
        # the cleaned tail (so the path token isn't left visible
        # in the bubble) then send each photo as a follow-up
        # message. We can't retroactively strip paths from earlier
        # rolled-over messages — but in practice paths land at the
        # tail of a turn (the "I just took a screenshot at ..."
        # pattern), so a single tail-edit covers the common case.
        chunks = split_for_telegram(cleaned) if cleaned else []
        if chunks:
            tail_chunk = chunks[-1]
            if tail_chunk != last_edited:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=tail_chunk,
                        parse_mode=None,
                    )
                except Exception:
                    log.debug(
                        "final-cleaned edit failed for chat %s",
                        chat_id, exc_info=True,
                    )
        for path in paths:
            ephemeral = _is_ephemeral_screenshot(path)
            try:
                if not path.is_file():
                    log.warning(
                        "Brain referenced missing screenshot %s", path,
                    )
                    continue
                oversize = _photo_too_large_for_telegram(path)
                with path.open("rb") as fh:
                    if oversize:
                        log.info(
                            "Sending %s as document (exceeds Telegram "
                            "photo dimension cap)", path.name,
                        )
                        await bot.send_document(
                            chat_id=chat_id, document=fh,
                            filename=path.name,
                        )
                    else:
                        await bot.send_photo(chat_id=chat_id, photo=fh)
            except Exception:
                log.exception("Failed to forward screenshot %s", path)
            finally:
                if ephemeral:
                    path.unlink(missing_ok=True)
        return full_text

    async def _send_brain_reply(self, bot, chat_id: int, reply: str) -> None:
        """Send a brain response, extracting and forwarding any screenshot
        paths it referenced as photos before the text body."""
        paths, cleaned = _extract_screenshot_paths(reply)
        for path in paths:
            ephemeral = _is_ephemeral_screenshot(path)
            try:
                if not path.is_file():
                    log.warning("Brain referenced missing screenshot %s", path)
                    continue
                oversize = _photo_too_large_for_telegram(path)
                with path.open("rb") as fh:
                    if oversize:
                        log.info(
                            "Sending %s as document (exceeds Telegram photo "
                            "dimension cap)",
                            path.name,
                        )
                        await bot.send_document(
                            chat_id=chat_id, document=fh, filename=path.name
                        )
                    else:
                        await bot.send_photo(chat_id=chat_id, photo=fh)
            except Exception:
                log.exception("Failed to forward screenshot %s", path)
            finally:
                if ephemeral:
                    path.unlink(missing_ok=True)

        text = cleaned.strip()
        if not text:
            return
        for chunk in split_for_telegram(text):
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=None)

    @staticmethod
    async def _keep_typing(bot, chat_id: int) -> None:
        while True:
            try:
                await bot.send_chat_action(chat_id, ChatAction.TYPING)
            except Exception:
                log.debug("send_chat_action failed", exc_info=True)
            await asyncio.sleep(_TYPING_REFRESH_SECONDS)

    async def run(self) -> None:
        await self._app.initialize()
        await _register_commands(self._app)
        # Sweep status files left behind by a previous daemon's brain
        # that exited via SIGKILL (its finally never ran). Without this
        # /status would show stale "Working for 3 days" data forever.
        try:
            removed = cleanup_status_files()
            if removed:
                log.info("Swept %d stale status file(s) at startup", removed)
        except Exception:
            log.exception("Status-file cleanup failed at startup")
        # Bind the notifier to the freshly-initialised PTB application
        # and route background-task completions through it. Lost-task
        # warnings from the previous daemon restart fire before polling
        # opens so they're queued in both Telegram and the brain context
        # buffer for the user's first reply of this session.
        self._notifier.bind_app(self._app)
        self._background_tasks.set_notify(self._notifier.send)
        await self._notify_lost_tasks()
        await self._app.start()
        await self._app.updater.start_polling()
        log.info("Telegram polling started")
        cleanup_task = asyncio.create_task(_incoming_image_cleanup_loop())
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_task.cancel()
            try:
                await cleanup_task
            except asyncio.CancelledError:
                pass
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    async def _notify_lost_tasks(self) -> None:
        try:
            lost = await self._background_tasks.detect_lost_from_previous_run()
        except Exception:
            log.exception("Lost-task detection failed during startup")
            return
        for entry in lost:
            try:
                await self._notifier.send(
                    entry["chat_id"], _DAEMON_RESTART_LOST.format(name=entry["name"])
                )
            except Exception:
                log.exception(
                    "Failed to send daemon-restart notice for task '%s'",
                    entry.get("name"),
                )


async def _incoming_image_cleanup_loop() -> None:
    """Sweep stale /tmp/vexis-incoming-*.png files every 10 minutes."""
    while True:
        try:
            removed = _cleanup_incoming_images(datetime.now(timezone.utc))
            if removed:
                log.info("Cleaned up %d expired incoming image(s)", removed)
        except Exception:
            log.exception("Incoming-image cleanup failed")
        await asyncio.sleep(_INCOMING_PHOTO_CLEANUP_INTERVAL_SECONDS)


def _format_tasks(tasks: list[dict]) -> str:
    """Render the /tasks reply: running first, then recently finished."""
    if not tasks:
        return _NO_BG_TASKS
    now = datetime.now(timezone.utc)
    running: list[str] = []
    finished: list[str] = []
    for task in tasks:
        name = task["name"]
        status = task["status"]
        if status in (TaskStatus.RUNNING.value, TaskStatus.PENDING.value):
            spawned = _parse_iso(task.get("spawned_at"))
            age = _short_duration(now - spawned) if spawned else "?"
            running.append(f"  {name} — running {age}")
            continue
        finished_at = _parse_iso(task.get("finished_at"))
        age = _short_duration(now - finished_at) if finished_at else "?"
        if status == TaskStatus.FINISHED.value:
            label = "success"
        elif status == TaskStatus.FAILED.value:
            label = f"failed (exit {task.get('exit_code')})"
        elif status == TaskStatus.CANCELLED.value:
            label = "cancelled"
        else:
            label = status
        finished.append(f"  {name} — finished {age} ago, {label}")
    sections: list[str] = []
    if running:
        sections.append("Running:\n" + "\n".join(running))
    if finished:
        sections.append("Recently finished (last hour):\n" + "\n".join(finished))
    return "\n\n".join(sections) if sections else _NO_BG_TASKS


def _parse_iso(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _short_duration(td: timedelta) -> str:
    seconds = max(0, int(td.total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if minutes:
        return f"{hours}h{minutes}m"
    return f"{hours}h"


def _format_status_duration(td: timedelta) -> str:
    """Render a duration the way /status wants it.

    < 60s → "8s"; 60s–1h → "4m 12s" (seconds dropped if zero);
    ≥ 1h → "1h 4m" (minutes dropped if zero). Negative input clamps
    to zero so a clock skew can't produce gibberish.
    """
    secs = max(0, int(td.total_seconds()))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        m, s = divmod(secs, 60)
        return f"{m}m {s}s" if s else f"{m}m"
    h, rem = divmod(secs, 3600)
    m = rem // 60
    return f"{h}h {m}m" if m else f"{h}h"


def _format_last_action(tool: str | None, target: str | None) -> str | None:
    """Render the 'Last action: ...' line, or None to omit it."""
    if tool is None:
        return None
    verb = _TOOL_VERB.get(tool)
    if target and verb:
        return f"Last action: {verb} `{target}`"
    if target:
        return f"Last action: used {tool} on `{target}`"
    return f"Last action: {tool}"


def _format_status_reply(
    snapshot: StatusSnapshot | None,
    queue_depth: int,
    last_idle_at: datetime | None,
    now: datetime,
) -> str:
    """Compose the user-visible /status reply.

    Pure function: takes the three pieces of state /status reads and
    returns the formatted string. Easy to unit-test and avoids
    interleaving formatting concerns with the I/O in ``_on_status``.
    """
    if snapshot is not None:
        lines = [f"Working for {_format_status_duration(now - snapshot.started_at)}."]
        if snapshot.tool_count > 0:
            action_line = _format_last_action(
                snapshot.last_tool, snapshot.last_target
            )
            if action_line is not None:
                lines.append(action_line)
            lines.append(f"Tools used: {snapshot.tool_count}")
        else:
            lines[0] = lines[0].rstrip(".") + ". " + _STATUS_NO_TOOLS_YET
        if queue_depth > 0:
            lines.append(f"Queued follow-ups: {queue_depth}")
        return "\n".join(lines)
    if last_idle_at is not None:
        return f"{_STATUS_IDLE} Idle for {_format_status_duration(now - last_idle_at)}."
    return _STATUS_IDLE


def _format_tailscale_reply(
    node: tailscale_mod.NodeStatus,
    serve: tailscale_mod.ServeStatus,
    funnel: tailscale_mod.FunnelStatus,
    peers: tailscale_mod.PeersStatus,
) -> str:
    """Render the /tailscale plain-text reply.

    The node call is the canonical health probe — if it failed, the
    rest is almost certainly broken too, so we short-circuit with a
    single error line. Any other section's error becomes an inline
    "(error: ...)" so the user still sees what *did* work.
    """
    if node.error is not None:
        return f"Tailscale status unavailable: {node.error}"

    lines: list[str] = ["Tailscale status", ""]

    if node.node is not None:
        host = node.node.hostname or "(unknown)"
        ip = node.node.ip or "(no IP)"
        state = "online" if node.node.online else "offline"
        lines.append(f"Node: {host} ({ip}) — {state}")
    else:
        lines.append("Node: (unavailable)")
    lines.append("")

    if serve.error is not None:
        lines.append(f"Active serves: (error: {serve.error})")
    else:
        lines.append(f"Active serves ({len(serve.serves)}):")
        if not serve.serves:
            lines.append("  none")
        else:
            for s in serve.serves:
                proto = "HTTPS" if s.tls else "HTTP"
                lines.append(
                    f"  • :{s.port} {s.mount} → {s.target} ({proto})"
                )
    lines.append("")

    if funnel.error is not None:
        lines.append(f"Active funnels: (error: {funnel.error})")
    else:
        lines.append(f"Active funnels ({len(funnel.funnels)}):")
        if not funnel.funnels:
            lines.append("  none")
        else:
            for f in funnel.funnels:
                proto = "HTTPS" if f.tls else "HTTP"
                lines.append(
                    f"  • :{f.port} {f.mount} → {f.target} ({proto})"
                )
    lines.append("")

    if peers.error is not None:
        lines.append(f"Peers: (error: {peers.error})")
    else:
        online_peers = [p for p in peers.peers if p.online]
        lines.append(
            f"Peers online ({len(online_peers)} of {len(peers.peers)}):"
        )
        if not online_peers:
            lines.append("  none")
        else:
            for p in online_peers:
                lines.append(f"  • {p.hostname or '(unknown)'} ({p.ip or '—'})")

    # Strip the trailing blank line for a cleaner Telegram message.
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


async def _register_commands(application: Application) -> None:
    """Mirror the canonical COMMANDS list to Telegram's slash menu.

    Failure here (network error, bad token, API hiccup) must not block
    daemon startup — the menu would just stay stale until the next
    successful restart.
    """
    bot_commands = [
        TelegramBotCommand(cmd.command, cmd.description) for cmd in COMMANDS
    ]
    try:
        await application.bot.set_my_commands(bot_commands)
    except Exception as exc:
        log.warning("Could not register Telegram commands: %s", exc)
        return
    log.info("Registered %d Telegram commands with Bot API", len(bot_commands))

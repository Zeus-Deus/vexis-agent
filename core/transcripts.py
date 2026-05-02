"""Read Claude Code session JSONLs.

Claude Code persists every session to
``~/.claude/projects/<encoded-cwd>/<uuid>.jsonl``. The encoded-cwd
replaces both ``/`` and ``.`` with ``-`` (so ``/home/zeus/vexis-workspace``
becomes ``-home-zeus-vexis-workspace``, and ``/home/zeus/.codemux``
becomes ``-home-zeus--codemux``). Don't confuse this with simple
slash-replacement — the dots get squashed too, which is why dotfile
parents produce double-dashes.

Each line is one JSON record with a ``type`` field. Conversational
turns are ``user`` or ``assistant``; the rest are metadata
(``permission-mode``, ``file-history-snapshot``, ``attachment``,
``last-prompt``, ``system``). Crucially, the LAST line of a JSONL
is often metadata (``stop_hook_summary``) without a ``timestamp``
field — so anything wanting "when did the session last see activity"
must scan the tail and pick the latest timestamp it finds, not
just look at the final line.

The learning curator's daemon tick uses ``iter_session_metas`` for
cheap eligibility checks (stat + tail-read) and ``iter_messages``
for the full parse of an eligible session.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# How many bytes to tail-read for the cheap last-message-timestamp
# probe. 8 KiB comfortably contains the last few message lines plus
# their metadata neighbors. Bigger reads slow the per-tick scan; the
# overall budget per tick is ~50 ms across all sessions in the dir.
_TAIL_READ_BYTES = 8192

# Conversational message types. iter_messages emits these.
_CONVERSATIONAL_TYPES: frozenset[str] = frozenset({"user", "assistant"})


def _claude_projects_root() -> Path:
    """Per-account Claude Code projects directory."""
    return Path.home() / ".claude" / "projects"


def claude_session_jsonl_dir(workspace: Path) -> Path:
    """Locate the directory holding session JSONLs for ``workspace``.

    The encoding rule is ``s/[/.]/-/g`` against the workspace's
    absolute path. Example: ``/home/zeus/.codemux/worktrees/foo``
    → ``-home-zeus--codemux-worktrees-foo``.
    """
    encoded = re.sub(r"[/.]", "-", str(Path(workspace).resolve()))
    return _claude_projects_root() / encoded


@dataclass(frozen=True)
class TranscriptMessage:
    """One conversational turn from a session JSONL."""

    role: str                     # "user" | "assistant"
    text: str                     # flattened content (text blocks joined)
    timestamp: datetime
    uuid: str                     # the message's uuid (NOT the session's)
    tool_calls: tuple[dict, ...]  # tool_use blocks from assistant content
    raw: dict                     # original JSONL line for fallthrough


@dataclass(frozen=True)
class SessionMeta:
    """Cheap (stat + tail-read) summary of a session JSONL.

    ``last_message_timestamp`` is None for files that are empty,
    unreadable, or contain no parseable timestamps in the tail
    region. The caller filters those before treating the session
    as eligible.
    """

    session_uuid: str
    jsonl_path: Path
    last_message_timestamp: datetime | None
    message_count_estimate: int   # cheap: line count, not parsed


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _tail_read(path: Path, n_bytes: int = _TAIL_READ_BYTES) -> str:
    """Read up to ``n_bytes`` from the end of ``path``.

    Drops a leading partial line when the file is bigger than the
    read window so callers can safely ``json.loads`` each remaining
    line. Returns "" on read errors (logged at debug); callers treat
    that as "no parseable tail" and downgrade to None timestamps.
    """
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            offset = max(0, size - n_bytes)
            fh.seek(offset)
            data = fh.read()
    except OSError as exc:
        log.debug("tail-read failed for %s: %s", path, exc)
        return ""
    text = data.decode("utf-8", errors="replace")
    if size > n_bytes and "\n" in text:
        text = text.split("\n", 1)[1]
    return text


def _last_message_timestamp(path: Path) -> datetime | None:
    """Find the maximum ``timestamp`` across the tail-read region.

    Robust to last-line-is-metadata (``stop_hook_summary`` and
    ``last-prompt`` don't carry timestamps). We scan every parseable
    line in the tail and return the max — that's the most recent
    point where Vexis was definitely doing something for this session.
    """
    text = _tail_read(path)
    latest: datetime | None = None
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = _parse_iso(obj.get("timestamp"))
        if ts is None:
            continue
        if latest is None or ts > latest:
            latest = ts
    return latest


def _line_count_estimate(path: Path) -> int:
    """Cheap line count via newline scan. Approximate (won't catch a
    missing trailing newline) but good enough for "rough size"."""
    try:
        with path.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def iter_session_metas(workspace: Path) -> Iterator[SessionMeta]:
    """Yield one SessionMeta per ``*.jsonl`` in the workspace's
    Claude Code projects directory.

    Files that fail to read or have no parseable timestamps yield
    a SessionMeta with ``last_message_timestamp=None``; the caller
    filters. Cheap by design — stat + tail-read of last 8 KiB.
    The full parse via ``iter_messages`` is reserved for the
    eligible session.
    """
    projects_dir = claude_session_jsonl_dir(workspace)
    if not projects_dir.exists():
        return
    for jsonl in sorted(projects_dir.glob("*.jsonl")):
        if not jsonl.is_file():
            continue
        # File stem is the session UUID.
        yield SessionMeta(
            session_uuid=jsonl.stem,
            jsonl_path=jsonl,
            last_message_timestamp=_last_message_timestamp(jsonl),
            message_count_estimate=_line_count_estimate(jsonl),
        )


def list_eligible_sessions(
    workspace: Path,
    *,
    reviewed: dict[str, datetime],
    idle_threshold: timedelta,
    now: datetime,
    spawned_by_curator: set[str] | None = None,
) -> list[SessionMeta]:
    """Filter ``iter_session_metas`` down to sessions that need review.

    A session is eligible when:
      - ``last_message_timestamp`` is not None,
      - ``last_message_timestamp > reviewed.get(uuid, datetime.min)``,
      - ``now - last_message_timestamp >= idle_threshold``,
      - ``session_uuid`` is not in ``spawned_by_curator``
        (recursion guard — sessions started by the curator's own
        review forks get filtered out even if their JSONLs land in
        the same projects directory).

    Returned in oldest-last_message order so abandoned sessions
    (those waiting longest) get reviewed first when there's a
    backlog. ``reviewed`` here carries the per-session
    ``last_message_at_review_time`` snapshot from reviewed.json,
    not the wall-clock review time — that's what makes "user
    resumed and added new content" naturally re-eligible.
    """
    spawned = spawned_by_curator or set()
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    out: list[SessionMeta] = []
    for meta in iter_session_metas(workspace):
        if meta.last_message_timestamp is None:
            continue
        if meta.session_uuid in spawned:
            continue
        last_reviewed = reviewed.get(meta.session_uuid, epoch)
        if meta.last_message_timestamp <= last_reviewed:
            continue
        if (now - meta.last_message_timestamp) < idle_threshold:
            continue
        out.append(meta)
    out.sort(key=lambda m: m.last_message_timestamp or epoch)
    return out


def _flatten_content(content: Any) -> tuple[str, tuple[dict, ...]]:
    """Extract (text, tool_use_blocks) from a message's ``content`` field.

    User content is usually a string; assistant content is usually a
    list of blocks (``text``, ``tool_use``, occasionally ``tool_result``).
    We join text blocks with newlines and keep tool_use blocks as
    structured records so callers can decide what to surface.
    """
    if isinstance(content, str):
        return content, ()
    if not isinstance(content, list):
        return "", ()
    texts: list[str] = []
    tool_calls: list[dict] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            t = block.get("text")
            if isinstance(t, str):
                texts.append(t)
        elif btype == "tool_use":
            tool_calls.append({
                "id": block.get("id"),
                "name": block.get("name"),
                "input": block.get("input"),
            })
    return "\n".join(texts), tuple(tool_calls)


def iter_messages(jsonl_path: Path) -> Iterator[TranscriptMessage]:
    """Stream user + assistant turns from a session JSONL.

    Skips non-conversational types (``file-history-snapshot``,
    ``attachment``, ``permission-mode``, ``system``, ``last-prompt``)
    and sidechain messages (subagent threads that aren't part of the
    main conversation). Lines that fail to parse are silently skipped
    — the file may be truncated mid-write; we don't want one bad
    line to take out the whole transcript.
    """
    try:
        fh = jsonl_path.open("r", encoding="utf-8")
    except OSError as exc:
        log.debug("Could not open %s: %s", jsonl_path, exc)
        return
    with fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            mtype = obj.get("type")
            if mtype not in _CONVERSATIONAL_TYPES:
                continue
            if obj.get("isSidechain") is True:
                continue
            msg = obj.get("message")
            if not isinstance(msg, dict):
                continue
            ts = _parse_iso(obj.get("timestamp"))
            if ts is None:
                continue
            text, tool_calls = _flatten_content(msg.get("content"))
            yield TranscriptMessage(
                role=str(msg.get("role") or mtype),
                text=text,
                timestamp=ts,
                uuid=str(obj.get("uuid") or ""),
                tool_calls=tool_calls,
                raw=obj,
            )


def session_ended_at(jsonl_path: Path) -> datetime | None:
    """Best-effort end timestamp via full parse.

    Use ``SessionMeta.last_message_timestamp`` for the cheap tick
    path; this is for callers that already need the full message
    list and want the same answer without a second tail-read.
    """
    latest: datetime | None = None
    for msg in iter_messages(jsonl_path):
        if latest is None or msg.timestamp > latest:
            latest = msg.timestamp
    return latest

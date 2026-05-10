"""Per-chat brain status file for read-only `/status` queries.

The brain writes a small JSON file as it processes stream-json tool
events from `claude -p`; the Telegram `/status` handler reads it back.
File path: ``runtime_dir() / status-<chat_id>.json``.

Why a file rather than in-memory state: it survives across Python
imports, is trivially inspectable from outside the daemon (handy when
debugging), and lives on tmpfs (`/run/user/<uid>`) so write cost is
negligible. The trade-off is staleness on daemon crash — the brain
deletes the file in its ``finally`` block on every exit path, but a
SIGKILL of the daemon will leave the file behind. ``cleanup_all`` is
called at daemon startup to sweep stale files from the previous run.

Writes are synchronous (sub-millisecond on tmpfs) but go via
tmpfile+rename so a partial write never produces unparseable JSON for
``/status`` to choke on.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from vexis_agent.core.paths import runtime_dir

log = logging.getLogger(__name__)

_FILENAME_PREFIX = "status-"
_FILENAME_SUFFIX = ".json"
_TARGET_MAX_LEN = 60

# Map of tool name → key in the tool's input dict that holds the
# "what's it acting on" identifier. Tools missing here render with no
# `last_target` field; the /status reply just shows the tool name.
_TARGET_KEY_BY_TOOL: dict[str, str] = {
    "Edit": "file_path",
    "Write": "file_path",
    "Read": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
    "Bash": "command",
    "Task": "description",
    "Glob": "pattern",
    "Grep": "pattern",
    "WebFetch": "url",
    "WebSearch": "query",
}


def _isoformat(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat()


def _path_for(chat_id: int) -> Path:
    return runtime_dir() / f"{_FILENAME_PREFIX}{chat_id}{_FILENAME_SUFFIX}"


def extract_tool_target(tool_name: str, tool_input: dict) -> str | None:
    """Best-effort extraction of the 'target' for a tool_use event.

    Returns the file_path / command / pattern / etc., truncated to
    ``_TARGET_MAX_LEN`` chars with an ellipsis if longer. None for
    tools where no obvious target exists or the input is malformed.
    """
    key = _TARGET_KEY_BY_TOOL.get(tool_name)
    if key is None:
        return None
    value = tool_input.get(key) if isinstance(tool_input, dict) else None
    if not isinstance(value, str) or not value:
        return None
    cleaned = value.replace("\n", " ").strip()
    if not cleaned:
        return None
    if len(cleaned) > _TARGET_MAX_LEN:
        return cleaned[:_TARGET_MAX_LEN].rstrip() + "…"
    return cleaned


@dataclass(frozen=True)
class StatusSnapshot:
    """Decoded contents of a status file at one moment in time."""

    chat_id: int
    started_at: datetime
    last_event_at: datetime
    tool_count: int
    last_tool: str | None
    last_target: str | None


class StatusFile:
    """Mutable handle to one chat's status file.

    Construct once per ``brain.respond()`` invocation; call ``start()``
    when work begins, ``record_tool()`` for each tool_use event, and
    ``delete()`` in the brain's ``finally``.
    """

    def __init__(self, chat_id: int) -> None:
        self.chat_id = chat_id
        self._path = _path_for(chat_id)
        self._started_at: datetime | None = None
        self._tool_count = 0
        self._last_tool: str | None = None
        self._last_target: str | None = None

    def start(self) -> None:
        self._started_at = datetime.now(timezone.utc)
        self._tool_count = 0
        self._last_tool = None
        self._last_target = None
        self._write()

    def record_tool(self, tool_name: str, target: str | None) -> None:
        self._tool_count += 1
        self._last_tool = tool_name
        self._last_target = target
        self._write()

    def delete(self) -> None:
        try:
            self._path.unlink()
        except FileNotFoundError:
            return
        except OSError:
            log.warning(
                "Failed to delete status file %s", self._path, exc_info=True
            )

    def _write(self) -> None:
        if self._started_at is None:
            return
        payload: dict = {
            "chat_id": self.chat_id,
            "started_at": _isoformat(self._started_at),
            "tool_count": self._tool_count,
            "last_event_at": _isoformat(datetime.now(timezone.utc)),
        }
        if self._last_tool:
            payload["last_tool"] = self._last_tool
        if self._last_target:
            payload["last_target"] = self._last_target
        try:
            tmp = self._path.with_suffix(_FILENAME_SUFFIX + ".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            log.warning(
                "Failed to write status file %s", self._path, exc_info=True
            )


def read_status(chat_id: int) -> StatusSnapshot | None:
    """Decode the status file for chat_id, or None if absent / corrupt.

    Read-only; safe to call from any context including while the brain
    is mid-write — the tmpfile+rename guarantees readers either see the
    previous valid file or the new one, never a torn write.
    """
    path = _path_for(chat_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        log.warning("Failed to read status file %s", path, exc_info=True)
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning("Status file %s is not valid JSON", path)
        return None
    try:
        return StatusSnapshot(
            chat_id=int(data["chat_id"]),
            started_at=datetime.fromisoformat(data["started_at"]),
            last_event_at=datetime.fromisoformat(data["last_event_at"]),
            tool_count=int(data.get("tool_count", 0)),
            last_tool=data.get("last_tool"),
            last_target=data.get("last_target"),
        )
    except (KeyError, ValueError, TypeError):
        log.warning("Status file %s has invalid shape", path)
        return None


def cleanup_all() -> int:
    """Delete every status-*.json file (and any orphaned .tmp).

    Call once at daemon startup — anything still present is from a
    previous daemon's brain that crashed without running its finally.
    Returns the number of regular status files removed (tmp files are
    swept silently).
    """
    base = runtime_dir()
    removed = 0
    for f in base.glob(f"{_FILENAME_PREFIX}*{_FILENAME_SUFFIX}"):
        try:
            f.unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError:
            log.warning("Failed to clean stale status file %s", f, exc_info=True)
    for f in base.glob(f"{_FILENAME_PREFIX}*{_FILENAME_SUFFIX}.tmp"):
        try:
            f.unlink()
        except (FileNotFoundError, OSError):
            continue
    return removed

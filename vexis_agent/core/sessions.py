"""Persistent multi-session store.

The brain-facing surface (``get`` / ``is_initialized`` /
``mark_initialized`` / ``rotate`` / ``set``) operates on the store's
single *active* session by default — Telegram's active-session UX. As
of issue #48 the same surface is also reachable, unchanged in shape, for
any *named* session via :class:`SessionView`: a thin binding that routes
every one of those five calls to one named session instead of the active
pointer, without ever reading or writing ``_active``. That is the seam
per-conversation web sessions ride on — core never learns the word
"conversation"; it only learns "run this turn against THIS named
session".
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_AUTO_NAME_FMT = "%Y-%m-%d-%H%M"
_MAX_AUTO_SUFFIX = 100


@dataclass(frozen=True)
class SessionInfo:
    name: str
    uuid: str
    initialized: bool
    created_at: datetime
    is_active: bool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError(
            f"Invalid session name '{name}'. "
            "Use letters, digits, hyphens, or underscores (1-64 chars)."
        )


def _gen_name(taken: set[str]) -> str:
    # Local time for the human-facing name; created_at storage stays UTC.
    base = datetime.now().astimezone().strftime(_AUTO_NAME_FMT)
    if base not in taken:
        return base
    for n in range(2, _MAX_AUTO_SUFFIX + 1):
        candidate = f"{base}-{n}"
        if candidate not in taken:
            return candidate
    raise RuntimeError(
        f"Could not generate a unique auto-name after {_MAX_AUTO_SUFFIX} attempts"
    )


class SessionStore:
    def __init__(self, state_path: Path) -> None:
        self._state_path = state_path
        self._active: str = ""
        self._sessions: dict[str, dict] = {}
        self._load_or_init()

    # ----- load / migrate / save -----

    def _load_or_init(self) -> None:
        if not self._state_path.exists():
            self._init_fresh()
            return
        try:
            data = json.loads(self._state_path.read_text())
        except (json.JSONDecodeError, OSError):
            self._handle_corrupt()
            return
        if not isinstance(data, dict):
            self._handle_corrupt()
            return
        # Step 3 single-session format.
        if "session_id" in data and "initialized" in data:
            try:
                self._migrate_from_old(data)
            except (KeyError, TypeError, ValueError):
                log.exception("Migration from single-session format failed")
                self._handle_corrupt()
            return
        # Step 4 multi-session format.
        if "active" in data and "sessions" in data:
            try:
                self._load_new_format(data)
            except (KeyError, TypeError, ValueError):
                log.exception("Loading multi-session format failed")
                self._handle_corrupt()
            return
        self._handle_corrupt()

    def _load_new_format(self, data: dict) -> None:
        sessions = data["sessions"]
        active = data["active"]
        if not isinstance(sessions, dict) or not sessions:
            raise ValueError("'sessions' must be a non-empty object")
        if active not in sessions:
            raise ValueError(f"active '{active}' not present in sessions")
        for name, meta in sessions.items():
            if not isinstance(meta, dict):
                raise ValueError(f"session '{name}' is not an object")
            for key in ("uuid", "initialized", "created_at"):
                if key not in meta:
                    raise ValueError(f"session '{name}' missing key '{key}'")
        self._sessions = sessions
        self._active = active

    def _migrate_from_old(self, old: dict) -> None:
        name = _gen_name(set())
        self._sessions = {
            name: {
                "uuid": str(old["session_id"]),
                "initialized": bool(old["initialized"]),
                "created_at": _utcnow().isoformat(),
            }
        }
        self._active = name
        self._save()
        log.info("Migrated single session to multi-session format as '%s'", name)

    def _handle_corrupt(self) -> None:
        ts = int(_utcnow().timestamp())
        backup = self._state_path.with_name(f"{self._state_path.name}.corrupt-{ts}")
        try:
            self._state_path.replace(backup)
            log.warning(
                "Session state corrupt at %s; backed up to %s and starting fresh",
                self._state_path,
                backup,
            )
        except OSError:
            log.warning(
                "Session state corrupt at %s; starting fresh (backup failed)",
                self._state_path,
            )
        self._init_fresh()

    def _init_fresh(self) -> None:
        name = _gen_name(set())
        self._sessions = {
            name: {
                "uuid": str(uuid.uuid4()),
                "initialized": False,
                "created_at": _utcnow().isoformat(),
            }
        }
        self._active = name
        self._save()

    def _save(self) -> None:
        # Atomic write: same-fs temp + rename.
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"active": self._active, "sessions": self._sessions}, indent=2)
        )
        tmp.replace(self._state_path)

    # ----- per-name primitives (active API + SessionView share these) -----
    #
    # The five brain-facing operations are defined once against an
    # arbitrary session name; the active-session methods below delegate
    # with ``self._active`` and :class:`SessionView` delegates with its
    # bound name (issue #48). Keeping the mutation-and-``_save`` logic in
    # one place is what guarantees a rotate/set through a view persists
    # byte-identically to a rotate/set on the active session.

    def _meta(self, name: str) -> dict:
        """Return the metadata dict for ``name`` or raise ``ValueError``.

        A ``KeyError`` here means the named session was deleted between
        the caller resolving the name and using it (a conversation whose
        session was removed from the sidebar mid-turn). Translate to a
        ``ValueError`` with an actionable message so the caller surfaces
        it the same way as any other bad-name error rather than leaking a
        raw ``KeyError``."""
        try:
            return self._sessions[name]
        except KeyError as exc:
            raise ValueError(
                f"Session '{name}' no longer exists (deleted while in use)."
            ) from exc

    def _get_uuid(self, name: str) -> str:
        return self._meta(name)["uuid"]

    def _is_initialized(self, name: str) -> bool:
        return self._meta(name)["initialized"]

    def _mark_initialized(self, name: str) -> None:
        self._meta(name)["initialized"] = True
        self._save()

    def _rotate(self, name: str) -> str:
        new = str(uuid.uuid4())
        meta = self._meta(name)
        meta["uuid"] = new
        meta["initialized"] = False
        self._save()
        return new

    def _set_token(self, name: str, token: str) -> None:
        if not isinstance(token, str) or not token:
            raise ValueError("session token must be a non-empty string")
        self._meta(name)["uuid"] = token
        self._save()

    # ----- brain-facing API (operates on the active session) -----

    def get(self) -> str:
        return self._get_uuid(self._active)

    def is_initialized(self) -> bool:
        return self._is_initialized(self._active)

    def mark_initialized(self) -> None:
        self._mark_initialized(self._active)

    def rotate(self) -> str:
        return self._rotate(self._active)

    def set(self, token: str) -> None:
        """Overwrite the active session's token without rotating to a
        fresh UUID and without flipping ``initialized``.

        Phase C Day 4: ``BrainOpenCode`` doesn't accept caller-pinned
        session ids — opencode generates the id itself and reports
        it on the first ``SessionEstablished`` event. The brain
        harvests that id and writes it back via this setter so
        subsequent ``respond()`` calls can pass ``--session <id>`` to
        resume the same conversation.

        The token is opaque to ``SessionStore`` — claude-code stores
        a UUID, opencode stores its own id (typically prefixed
        ``ses_``). Validation is the brain's job, not the store's.
        """
        self._set_token(self._active, token)

    # ----- multi-session API -----

    def ensure(self, name: str) -> str:
        """Create the named session if it doesn't exist yet, WITHOUT
        touching the active pointer; return ``name``. Idempotent.

        This is the multi-session counterpart to :meth:`create` that
        per-conversation web sessions need (issue #48). ``create``
        always mints a fresh session AND flips ``_active`` to it — the
        wrong semantics for a conversation, which must get its own named
        session on first use without hijacking whatever Telegram / the
        dashboard sidebar currently has active. ``ensure`` only writes
        when it actually created the session, so repeat calls for an
        existing conversation are free."""
        _validate_name(name)
        if name in self._sessions:
            return name
        self._sessions[name] = {
            "uuid": str(uuid.uuid4()),
            "initialized": False,
            "created_at": _utcnow().isoformat(),
        }
        self._save()
        return name

    def list(self) -> list[SessionInfo]:
        return [
            SessionInfo(
                name=name,
                uuid=meta["uuid"],
                initialized=meta["initialized"],
                created_at=datetime.fromisoformat(meta["created_at"]),
                is_active=(name == self._active),
            )
            for name, meta in self._sessions.items()
        ]

    def active_name(self) -> str:
        return self._active

    def create(self, name: str | None = None) -> str:
        if name is None:
            name = _gen_name(set(self._sessions.keys()))
        else:
            _validate_name(name)
            if name in self._sessions:
                raise ValueError(
                    f"A session named '{name}' already exists. "
                    f"Try /switch {name} or pick a different name."
                )
        self._sessions[name] = {
            "uuid": str(uuid.uuid4()),
            "initialized": False,
            "created_at": _utcnow().isoformat(),
        }
        self._active = name
        self._save()
        return name

    def switch(self, name: str) -> bool:
        if name not in self._sessions:
            return False
        if self._active != name:
            self._active = name
            self._save()
        return True

    def rename(self, old: str, new: str) -> bool:
        if old not in self._sessions:
            return False
        if new == old:
            return True
        _validate_name(new)
        if new in self._sessions:
            return False
        # Preserve insertion order so /sessions output stays stable.
        self._sessions = {
            (new if k == old else k): v for k, v in self._sessions.items()
        }
        if self._active == old:
            self._active = new
        self._save()
        return True

    def delete(self, name: str) -> bool:
        if name not in self._sessions:
            return False
        if len(self._sessions) <= 1:
            raise ValueError("Cannot delete the last remaining session.")
        if name == self._active:
            raise ValueError(
                "Cannot delete the active session. Switch to another first."
            )
        del self._sessions[name]
        self._save()
        return True


class SessionView:
    """Brain-facing session API bound to one *named* session instead of
    the store's single active pointer (issue #48).

    ``SessionStore``'s ``get`` / ``is_initialized`` / ``mark_initialized``
    / ``rotate`` / ``set`` all read and mutate ``_active``. A brain turn
    that must run against a specific conversation takes a ``SessionView``
    instead: it exposes the identical five-method surface, but every call
    routes to ``name`` and never reads or writes ``_active``. The view
    holds no state of its own — it is a thin binding over the store's
    per-name primitives, so a ``rotate`` / ``set`` / ``mark_initialized``
    through the view ``_save()``s to disk exactly like the active variant
    and is immediately reflected in :meth:`SessionStore.list`.

    Duck-types the ``SessionLike`` protocol in ``core.brain.base`` so a
    brain can drive a turn against either the store (active) or a view
    (one conversation) without importing either concrete class. A
    ``ValueError`` surfaces if the named session was deleted between view
    construction and use (see :meth:`SessionStore._meta`)."""

    __slots__ = ("_store", "_name")

    def __init__(self, store: SessionStore, name: str) -> None:
        self._store = store
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def get(self) -> str:
        return self._store._get_uuid(self._name)

    def is_initialized(self) -> bool:
        return self._store._is_initialized(self._name)

    def mark_initialized(self) -> None:
        self._store._mark_initialized(self._name)

    def rotate(self) -> str:
        return self._store._rotate(self._name)

    def set(self, token: str) -> None:
        self._store._set_token(self._name, token)

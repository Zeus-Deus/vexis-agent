"""Kanban storage layer — SQLite WAL, single-user.

Adapted from Hermes' ``hermes_cli/kanban_db.py`` with the following
trims (per ``.plans/kanban-research.md`` §3):

  * Drop ``tenant`` (single-user; multi-tenant was the only justification).
  * Drop ``idempotency_key`` and its index (no external dedup need yet).
  * Drop ``kanban_notify_subs`` table (single Telegram chat; notification
    policy is in-process — see ``notifier.py``).
  * Drop ``workflow_template_id`` / ``current_step_key`` (Hermes v2 stubs,
    unused even there).
  * Rename ``assignee`` → ``lane`` (matches our terminology).
  * Drop ``profile`` from ``task_runs`` (single-brain; lane is the worker
    type discriminator).
  * Drop the legacy-DB migration scaffolding — vexis ships with v1 schema;
    when v2 lands we'll add real migrations.

Public API: :class:`KanbanStore`. Construct once per daemon, reuse across
every dispatcher tick / MCP tool call / dashboard request. The store owns
one ``sqlite3.Connection`` in WAL mode; SQLite's WAL serialises writers
and allows concurrent readers, which is what we want for the dispatcher
(write-heavy) plus dashboard (read-heavy) workload.

Concurrency model: callers serialise their own writes by doing
single-statement ops or wrapping multi-statement ops in
``with store.transaction():``. Race-prone primitives — :meth:`claim_task`
and :meth:`recompute_ready` — use SQL-level atomicity (CAS-style
``UPDATE ... WHERE`` and ``IN`` subqueries) so two dispatchers can't
both win a race for the same task even if we ever spun up a second one.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from vexis_agent.core.kanban.constants import (
    ACTIVE_STATUSES,
    EVENT_ARCHIVED,
    EVENT_CLAIMED,
    EVENT_COMMENTED,
    EVENT_CREATED,
    EVENT_EDITED,
    EVENT_LINKED,
    EVENT_PROMOTED,
    EVENT_UNLINKED,
    RUN_STATUS_DONE,
    RUN_STATUS_RUNNING,
    STATUS_ARCHIVED,
    STATUS_DONE,
    STATUS_READY,
    STATUS_TODO,
    STATUS_TRIAGE,
    VALID_EVENT_KINDS,
    VALID_RUN_STATUSES,
    VALID_STATUSES,
)

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Schema
# ──────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id                   TEXT PRIMARY KEY,
    title                TEXT NOT NULL,
    body                 TEXT,
    lane                 TEXT,
    status               TEXT NOT NULL,
    priority             INTEGER NOT NULL DEFAULT 0,
    created_by           TEXT,
    created_at           INTEGER NOT NULL,
    started_at           INTEGER,
    completed_at         INTEGER,
    workspace_kind       TEXT NOT NULL DEFAULT 'dir',
    workspace_path       TEXT,
    claim_lock           TEXT,
    claim_expires        INTEGER,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    worker_pid           INTEGER,
    last_failure_error   TEXT,
    max_runtime_seconds  INTEGER,
    last_heartbeat_at    INTEGER,
    current_run_id       INTEGER,
    skills               TEXT,
    max_retries          INTEGER
);

CREATE TABLE IF NOT EXISTS task_links (
    parent_id  TEXT NOT NULL,
    child_id   TEXT NOT NULL,
    PRIMARY KEY (parent_id, child_id)
);

CREATE TABLE IF NOT EXISTS task_comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    author     TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS task_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    run_id     INTEGER,
    kind       TEXT NOT NULL,
    payload    TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS task_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id             TEXT NOT NULL,
    lane                TEXT,
    status              TEXT NOT NULL,
    claim_lock          TEXT,
    claim_expires       INTEGER,
    worker_pid          INTEGER,
    max_runtime_seconds INTEGER,
    last_heartbeat_at   INTEGER,
    started_at          INTEGER NOT NULL,
    ended_at            INTEGER,
    outcome             TEXT,
    summary             TEXT,
    metadata            TEXT,
    error               TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_lane_status   ON tasks(lane, status);
CREATE INDEX IF NOT EXISTS idx_tasks_status        ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_priority      ON tasks(priority DESC);
CREATE INDEX IF NOT EXISTS idx_links_child         ON task_links(child_id);
CREATE INDEX IF NOT EXISTS idx_links_parent        ON task_links(parent_id);
CREATE INDEX IF NOT EXISTS idx_comments_task       ON task_comments(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_task         ON task_events(task_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_run          ON task_events(run_id, id);
CREATE INDEX IF NOT EXISTS idx_runs_task           ON task_runs(task_id, started_at);
CREATE INDEX IF NOT EXISTS idx_runs_status         ON task_runs(status);
"""


# ──────────────────────────────────────────────────────────────────
# Dataclasses
# ──────────────────────────────────────────────────────────────────


@dataclass
class Task:
    """One kanban task. Mirrors the ``tasks`` row 1:1."""

    id: str
    title: str
    body: str | None = None
    lane: str | None = None
    status: str = STATUS_TRIAGE
    priority: int = 0
    created_by: str | None = None
    created_at: int = 0
    started_at: int | None = None
    completed_at: int | None = None
    workspace_kind: str = "dir"
    workspace_path: str | None = None
    claim_lock: str | None = None
    claim_expires: int | None = None
    consecutive_failures: int = 0
    worker_pid: int | None = None
    last_failure_error: str | None = None
    max_runtime_seconds: int | None = None
    last_heartbeat_at: int | None = None
    current_run_id: int | None = None
    skills: list[str] = field(default_factory=list)
    max_retries: int | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Task":
        skills_raw = row["skills"]
        skills: list[str] = []
        if skills_raw:
            try:
                parsed = json.loads(skills_raw)
                if isinstance(parsed, list):
                    skills = [str(s) for s in parsed if isinstance(s, str)]
            except (ValueError, TypeError):
                # Tolerant: corrupt JSON → empty skill list, log once.
                log.warning(
                    "task %s has corrupt skills JSON %r — treating as empty",
                    row["id"], skills_raw,
                )
        return cls(
            id=row["id"],
            title=row["title"],
            body=row["body"],
            lane=row["lane"],
            status=row["status"],
            priority=row["priority"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            workspace_kind=row["workspace_kind"],
            workspace_path=row["workspace_path"],
            claim_lock=row["claim_lock"],
            claim_expires=row["claim_expires"],
            consecutive_failures=row["consecutive_failures"],
            worker_pid=row["worker_pid"],
            last_failure_error=row["last_failure_error"],
            max_runtime_seconds=row["max_runtime_seconds"],
            last_heartbeat_at=row["last_heartbeat_at"],
            current_run_id=row["current_run_id"],
            skills=skills,
            max_retries=row["max_retries"],
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable dict for the dashboard / WS payload.

        Schema is deliberately shallow — the dashboard wants flat
        objects, not nested. Skills come back as a list (not the JSON
        string the column stores).
        """
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body,
            "lane": self.lane,
            "status": self.status,
            "priority": self.priority,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "workspace_kind": self.workspace_kind,
            "workspace_path": self.workspace_path,
            "claim_lock": self.claim_lock,
            "claim_expires": self.claim_expires,
            "consecutive_failures": self.consecutive_failures,
            "worker_pid": self.worker_pid,
            "last_failure_error": self.last_failure_error,
            "max_runtime_seconds": self.max_runtime_seconds,
            "last_heartbeat_at": self.last_heartbeat_at,
            "current_run_id": self.current_run_id,
            "skills": list(self.skills),
            "max_retries": self.max_retries,
        }


@dataclass
class TaskRun:
    """One attempt at executing a task. Multiple per task on retry."""

    id: int
    task_id: str
    lane: str | None
    status: str
    claim_lock: str | None
    claim_expires: int | None
    worker_pid: int | None
    max_runtime_seconds: int | None
    last_heartbeat_at: int | None
    started_at: int
    ended_at: int | None
    outcome: str | None
    summary: str | None
    metadata: dict[str, Any] | None
    error: str | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "TaskRun":
        meta_raw = row["metadata"]
        metadata: dict[str, Any] | None = None
        if meta_raw:
            try:
                parsed = json.loads(meta_raw)
                if isinstance(parsed, dict):
                    metadata = parsed
            except (ValueError, TypeError):
                metadata = None
        return cls(
            id=row["id"],
            task_id=row["task_id"],
            lane=row["lane"],
            status=row["status"],
            claim_lock=row["claim_lock"],
            claim_expires=row["claim_expires"],
            worker_pid=row["worker_pid"],
            max_runtime_seconds=row["max_runtime_seconds"],
            last_heartbeat_at=row["last_heartbeat_at"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            outcome=row["outcome"],
            summary=row["summary"],
            metadata=metadata,
            error=row["error"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "lane": self.lane,
            "status": self.status,
            "claim_lock": self.claim_lock,
            "claim_expires": self.claim_expires,
            "worker_pid": self.worker_pid,
            "max_runtime_seconds": self.max_runtime_seconds,
            "last_heartbeat_at": self.last_heartbeat_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "outcome": self.outcome,
            "summary": self.summary,
            "metadata": dict(self.metadata) if self.metadata else None,
            "error": self.error,
        }


@dataclass
class Comment:
    id: int
    task_id: str
    author: str
    body: str
    created_at: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Comment":
        return cls(
            id=row["id"],
            task_id=row["task_id"],
            author=row["author"],
            body=row["body"],
            created_at=row["created_at"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "author": self.author,
            "body": self.body,
            "created_at": self.created_at,
        }


@dataclass
class Event:
    id: int
    task_id: str
    run_id: int | None
    kind: str
    payload: dict[str, Any] | None
    created_at: int

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Event":
        payload_raw = row["payload"]
        payload: dict[str, Any] | None = None
        if payload_raw:
            try:
                parsed = json.loads(payload_raw)
                if isinstance(parsed, dict):
                    payload = parsed
            except (ValueError, TypeError):
                payload = None
        return cls(
            id=row["id"],
            task_id=row["task_id"],
            run_id=row["run_id"],
            kind=row["kind"],
            payload=payload,
            created_at=row["created_at"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "kind": self.kind,
            "payload": dict(self.payload) if self.payload else None,
            "created_at": self.created_at,
        }


# ──────────────────────────────────────────────────────────────────
# Errors
# ──────────────────────────────────────────────────────────────────


class KanbanError(Exception):
    """Base for all kanban storage errors."""


class TaskNotFoundError(KanbanError):
    """The given task_id doesn't exist (or was deleted)."""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"task not found: {task_id}")


class InvalidStatusError(KanbanError):
    """A status value not in :data:`VALID_STATUSES` was supplied."""


class InvalidEventKindError(KanbanError):
    """An event kind not in :data:`VALID_EVENT_KINDS` was supplied."""


class ClaimContentionError(KanbanError):
    """Another worker beat us to the claim. Caller should skip this
    task and try the next ready one."""


# ──────────────────────────────────────────────────────────────────
# KanbanStore
# ──────────────────────────────────────────────────────────────────


def _now() -> int:
    return int(time.time())


def _new_task_id() -> str:
    """Short slug task id. UUID4 first 8 chars is plenty for a
    single-user board (collision probability ≈ 1 in 4 billion at
    100 tasks). Hermes uses full UUIDs but they're awful to type
    in Telegram (``/kanban show <id>``) so we trade collision
    risk for typability.
    """
    return uuid.uuid4().hex[:8]


class KanbanStore:
    """SQLite-backed kanban store. Single connection, single thread
    per store instance. Async callers wrap calls in
    ``await asyncio.to_thread(...)``.

    Construct once per daemon. The connection auto-initialises the
    schema on first construction.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(path),
            isolation_level=None,    # autocommit; we manage transactions
            timeout=30.0,
            check_same_thread=False, # async wrapping crosses threads via to_thread
        )
        self._conn.row_factory = sqlite3.Row
        # WAL gives concurrent readers + serialised writers, which is
        # what we want for the dispatcher + dashboard workload.
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as exc:
            # Network filesystems (NFS/SMB) refuse WAL. Fall back.
            log.warning(
                "kanban: WAL not supported at %s (%s); falling back to DELETE",
                path, exc,
            )
            self._conn.execute("PRAGMA journal_mode=DELETE")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA_SQL)

    @property
    def path(self) -> Path:
        return self._path

    def close(self) -> None:
        """Close the connection. Idempotent."""
        try:
            self._conn.close()
        except sqlite3.ProgrammingError:
            pass

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run the body inside a single SQLite transaction.

        Use for multi-statement writes that must commit atomically
        (e.g. update a task and append an event in lockstep). Single
        statements don't need this — autocommit handles them.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
        except Exception:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")

    # ─── tasks ────────────────────────────────────────────────────

    def create_task(
        self,
        *,
        title: str,
        body: str | None = None,
        lane: str | None = None,
        status: str = STATUS_TRIAGE,
        priority: int = 0,
        created_by: str | None = None,
        workspace_kind: str = "dir",
        workspace_path: str | None = None,
        max_runtime_seconds: int | None = None,
        skills: list[str] | None = None,
        max_retries: int | None = None,
        task_id: str | None = None,
        parents: list[str] | None = None,
    ) -> Task:
        """Insert a new task. Returns the created Task.

        ``parents`` adds rows to ``task_links``; the new task's status
        is forced to ``todo`` if any parents exist (to defer promotion
        until they're done) regardless of the requested ``status``.
        Empty ``parents`` (or ``None``) honours the requested status.
        """
        if status not in VALID_STATUSES:
            raise InvalidStatusError(f"invalid status: {status}")
        if not title.strip():
            raise KanbanError("title cannot be empty")
        if parents:
            # Verify every parent exists so we don't create dangling
            # links. Same query batched.
            placeholders = ",".join("?" for _ in parents)
            rows = self._conn.execute(
                f"SELECT id FROM tasks WHERE id IN ({placeholders})",
                parents,
            ).fetchall()
            found = {r["id"] for r in rows}
            missing = [p for p in parents if p not in found]
            if missing:
                raise TaskNotFoundError(missing[0])
            # Force status to ``todo`` so promotion runs after parents.
            status = STATUS_TODO
        tid = task_id or _new_task_id()
        now = _now()
        skills_json = json.dumps(skills) if skills else None
        with self.transaction():
            self._conn.execute(
                """
                INSERT INTO tasks (
                    id, title, body, lane, status, priority,
                    created_by, created_at, workspace_kind,
                    workspace_path, max_runtime_seconds, skills,
                    max_retries
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tid, title, body, lane, status, priority,
                    created_by, now, workspace_kind, workspace_path,
                    max_runtime_seconds, skills_json, max_retries,
                ),
            )
            if parents:
                self._conn.executemany(
                    "INSERT INTO task_links (parent_id, child_id) "
                    "VALUES (?, ?)",
                    [(pid, tid) for pid in parents],
                )
            self._append_event_inner(
                tid, EVENT_CREATED,
                {"title": title, "lane": lane, "parents": list(parents or [])},
                run_id=None,
            )
            if parents:
                for pid in parents:
                    self._append_event_inner(
                        pid, EVENT_LINKED,
                        {"child_id": tid},
                        run_id=None,
                    )
        return self.get_task(tid)  # type: ignore[return-value]

    def get_task(self, task_id: str) -> Task | None:
        row = self._conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        return Task.from_row(row)

    def require_task(self, task_id: str) -> Task:
        task = self.get_task(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    def list_tasks(
        self,
        *,
        status: str | None = None,
        lane: str | None = None,
        include_archived: bool = False,
        limit: int | None = None,
    ) -> list[Task]:
        """List tasks ordered by (priority DESC, created_at DESC).

        ``include_archived=False`` (default) hides archived tasks; the
        dashboard's "show archived" toggle flips this.
        """
        where: list[str] = []
        args: list[Any] = []
        if status is not None:
            if status not in VALID_STATUSES:
                raise InvalidStatusError(f"invalid status: {status}")
            where.append("status = ?")
            args.append(status)
        elif not include_archived:
            where.append("status != ?")
            args.append(STATUS_ARCHIVED)
        if lane is not None:
            where.append("lane = ?")
            args.append(lane)
        sql = "SELECT * FROM tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY priority DESC, created_at DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = self._conn.execute(sql, args).fetchall()
        return [Task.from_row(r) for r in rows]

    def update_task(
        self,
        task_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        lane: str | None = None,
        status: str | None = None,
        priority: int | None = None,
        workspace_kind: str | None = None,
        workspace_path: str | None = None,
        max_runtime_seconds: int | None = None,
        skills: list[str] | None = None,
        max_retries: int | None = None,
    ) -> Task:
        """Update mutable fields on a task. Returns the updated Task.

        All args are keyword-only and ``None`` means "don't touch".
        Status transitions are validated against the enum but otherwise
        unchecked here — callers (dispatcher, MCP tools) enforce
        legal transitions.
        """
        existing = self.require_task(task_id)
        sets: list[str] = []
        args: list[Any] = []
        payload: dict[str, Any] = {}
        if title is not None:
            sets.append("title = ?")
            args.append(title)
            payload["title"] = title
        if body is not None:
            sets.append("body = ?")
            args.append(body)
            payload["body_changed"] = True
        if lane is not None:
            sets.append("lane = ?")
            args.append(lane)
            payload["lane"] = lane
        if status is not None:
            if status not in VALID_STATUSES:
                raise InvalidStatusError(f"invalid status: {status}")
            sets.append("status = ?")
            args.append(status)
            payload["status"] = status
            if status == STATUS_DONE and existing.status != STATUS_DONE:
                sets.append("completed_at = ?")
                args.append(_now())
        if priority is not None:
            sets.append("priority = ?")
            args.append(priority)
            payload["priority"] = priority
        if workspace_kind is not None:
            sets.append("workspace_kind = ?")
            args.append(workspace_kind)
        if workspace_path is not None:
            sets.append("workspace_path = ?")
            args.append(workspace_path)
        if max_runtime_seconds is not None:
            sets.append("max_runtime_seconds = ?")
            args.append(max_runtime_seconds)
        if skills is not None:
            sets.append("skills = ?")
            args.append(json.dumps(skills) if skills else None)
            payload["skills"] = list(skills)
        if max_retries is not None:
            sets.append("max_retries = ?")
            args.append(max_retries)
        if not sets:
            return existing
        args.append(task_id)
        with self.transaction():
            self._conn.execute(
                f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", args,
            )
            self._append_event_inner(
                task_id, EVENT_EDITED, payload, run_id=None,
            )
        return self.require_task(task_id)

    def archive_task(self, task_id: str) -> None:
        """Soft-delete: flip status to ``archived``. Hidden from the
        default board view. Use :meth:`update_task` with another
        status to un-archive."""
        existing = self.require_task(task_id)
        if existing.status == STATUS_ARCHIVED:
            return
        with self.transaction():
            self._conn.execute(
                "UPDATE tasks SET status = ? WHERE id = ?",
                (STATUS_ARCHIVED, task_id),
            )
            self._append_event_inner(
                task_id, EVENT_ARCHIVED, None, run_id=None,
            )

    # ─── links ────────────────────────────────────────────────────

    def add_link(self, parent_id: str, child_id: str) -> None:
        """Add a parent → child dependency. Idempotent. Refuses to
        create a self-link (would deadlock promotion)."""
        if parent_id == child_id:
            raise KanbanError("a task cannot be its own parent")
        # Both must exist.
        if self.get_task(parent_id) is None:
            raise TaskNotFoundError(parent_id)
        if self.get_task(child_id) is None:
            raise TaskNotFoundError(child_id)
        with self.transaction():
            self._conn.execute(
                "INSERT OR IGNORE INTO task_links (parent_id, child_id) "
                "VALUES (?, ?)",
                (parent_id, child_id),
            )
            self._append_event_inner(
                parent_id, EVENT_LINKED,
                {"child_id": child_id}, run_id=None,
            )

    def remove_link(self, parent_id: str, child_id: str) -> None:
        """Remove a parent → child dependency. Idempotent."""
        with self.transaction():
            self._conn.execute(
                "DELETE FROM task_links WHERE parent_id = ? AND child_id = ?",
                (parent_id, child_id),
            )
            self._append_event_inner(
                parent_id, EVENT_UNLINKED,
                {"child_id": child_id}, run_id=None,
            )

    def get_parents(self, task_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT parent_id FROM task_links WHERE child_id = ?",
            (task_id,),
        ).fetchall()
        return [r["parent_id"] for r in rows]

    def get_children(self, task_id: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT child_id FROM task_links WHERE parent_id = ?",
            (task_id,),
        ).fetchall()
        return [r["child_id"] for r in rows]

    # ─── comments ─────────────────────────────────────────────────

    def add_comment(self, task_id: str, author: str, body: str) -> Comment:
        if not body.strip():
            raise KanbanError("comment body cannot be empty")
        self.require_task(task_id)
        now = _now()
        with self.transaction():
            cur = self._conn.execute(
                "INSERT INTO task_comments (task_id, author, body, created_at) "
                "VALUES (?, ?, ?, ?)",
                (task_id, author, body, now),
            )
            cid = cur.lastrowid
            self._append_event_inner(
                task_id, EVENT_COMMENTED,
                {"author": author, "comment_id": cid}, run_id=None,
            )
        row = self._conn.execute(
            "SELECT * FROM task_comments WHERE id = ?", (cid,),
        ).fetchone()
        return Comment.from_row(row)

    def list_comments(self, task_id: str) -> list[Comment]:
        rows = self._conn.execute(
            "SELECT * FROM task_comments WHERE task_id = ? "
            "ORDER BY created_at ASC, id ASC",
            (task_id,),
        ).fetchall()
        return [Comment.from_row(r) for r in rows]

    # ─── claim / promotion ────────────────────────────────────────

    def claim_task(
        self,
        task_id: str,
        *,
        claim_lock: str,
        ttl_seconds: int,
    ) -> Task:
        """Atomically claim a ready task. Raises
        :class:`ClaimContentionError` if another worker beat us.

        The CAS condition is ``status = 'ready' AND claim_lock IS NULL``
        — only ready, unclaimed tasks are claimable. After a successful
        claim the task flips to ``in_progress`` and ``claim_expires`` is
        set to ``now + ttl_seconds``.
        """
        now = _now()
        expires = now + ttl_seconds
        with self.transaction():
            cur = self._conn.execute(
                """
                UPDATE tasks
                   SET status = 'in_progress',
                       claim_lock = ?,
                       claim_expires = ?,
                       started_at = COALESCE(started_at, ?),
                       last_heartbeat_at = ?
                 WHERE id = ?
                   AND status = 'ready'
                   AND claim_lock IS NULL
                """,
                (claim_lock, expires, now, now, task_id),
            )
            if cur.rowcount == 0:
                raise ClaimContentionError(
                    f"task {task_id} not claimable (already claimed or "
                    f"not ready)"
                )
            self._append_event_inner(
                task_id, EVENT_CLAIMED,
                {"claim_lock": claim_lock, "ttl_seconds": ttl_seconds},
                run_id=None,
            )
        return self.require_task(task_id)

    def release_claim(
        self,
        task_id: str,
        *,
        new_status: str = STATUS_READY,
    ) -> None:
        """Release a claim and reset to ``new_status`` (default
        ``ready`` so the dispatcher re-picks). Used by stale-claim
        cleanup, by ``/cancel``, and by worker error paths."""
        if new_status not in VALID_STATUSES:
            raise InvalidStatusError(f"invalid status: {new_status}")
        self._conn.execute(
            """
            UPDATE tasks
               SET status = ?,
                   claim_lock = NULL,
                   claim_expires = NULL,
                   worker_pid = NULL
             WHERE id = ?
            """,
            (new_status, task_id),
        )

    def heartbeat(
        self,
        task_id: str,
        *,
        claim_lock: str,
        ttl_seconds: int,
    ) -> bool:
        """Bump the heartbeat + claim expiry for a task we own.
        Returns True if the heartbeat succeeded (we still hold the
        claim), False if we lost it (another worker took over)."""
        now = _now()
        expires = now + ttl_seconds
        cur = self._conn.execute(
            """
            UPDATE tasks
               SET last_heartbeat_at = ?,
                   claim_expires = ?
             WHERE id = ? AND claim_lock = ?
            """,
            (now, expires, task_id, claim_lock),
        )
        return cur.rowcount > 0

    def cleanup_stale_claims(self) -> list[str]:
        """Release claims whose ``claim_expires < now``. Returns the
        list of task ids released. Called every dispatcher tick."""
        now = _now()
        rows = self._conn.execute(
            "SELECT id FROM tasks "
            "WHERE claim_lock IS NOT NULL AND claim_expires < ?",
            (now,),
        ).fetchall()
        released: list[str] = []
        for row in rows:
            tid = row["id"]
            with self.transaction():
                cur = self._conn.execute(
                    """
                    UPDATE tasks
                       SET status = 'ready',
                           claim_lock = NULL,
                           claim_expires = NULL,
                           worker_pid = NULL,
                           consecutive_failures = consecutive_failures + 1,
                           last_failure_error = 'claim expired (no heartbeat)'
                     WHERE id = ? AND claim_expires < ?
                    """,
                    (tid, now),
                )
                if cur.rowcount > 0:
                    self._append_event_inner(
                        tid, "released",
                        {"reason": "claim_expired"}, run_id=None,
                    )
                    released.append(tid)
        return released

    def recompute_ready(self) -> list[str]:
        """Promote ``todo`` tasks whose every parent reached ``done``.
        Returns the list of task ids promoted. Called every dispatcher
        tick. Tasks with no parents are also promoted (vacuous truth).
        """
        rows = self._conn.execute(
            f"""
            SELECT t.id
              FROM tasks t
             WHERE t.status = '{STATUS_TODO}'
               AND NOT EXISTS (
                 SELECT 1 FROM task_links l
                  JOIN tasks p ON p.id = l.parent_id
                  WHERE l.child_id = t.id
                    AND p.status != '{STATUS_DONE}'
               )
            """
        ).fetchall()
        promoted: list[str] = []
        for row in rows:
            tid = row["id"]
            with self.transaction():
                cur = self._conn.execute(
                    f"UPDATE tasks SET status = '{STATUS_READY}' "
                    f"WHERE id = ? AND status = '{STATUS_TODO}'",
                    (tid,),
                )
                if cur.rowcount > 0:
                    self._append_event_inner(
                        tid, EVENT_PROMOTED, None, run_id=None,
                    )
                    promoted.append(tid)
        return promoted

    def list_ready(self, *, lane: str | None = None) -> list[Task]:
        """Tasks in ``ready`` status, optionally filtered by lane.
        Ordered by (priority DESC, created_at ASC) — older ready
        tasks first within a priority band, so FIFO within priority."""
        if lane is None:
            rows = self._conn.execute(
                """
                SELECT * FROM tasks
                 WHERE status = 'ready' AND claim_lock IS NULL
                 ORDER BY priority DESC, created_at ASC
                """,
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT * FROM tasks
                 WHERE status = 'ready' AND claim_lock IS NULL
                   AND lane = ?
                 ORDER BY priority DESC, created_at ASC
                """,
                (lane,),
            ).fetchall()
        return [Task.from_row(r) for r in rows]

    def count_in_flight(self) -> int:
        """Number of tasks currently held by a worker. Used by the
        dispatcher's ``max_concurrent_workers`` cap."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE claim_lock IS NOT NULL",
        ).fetchone()
        return int(row["n"])

    # ─── runs ─────────────────────────────────────────────────────

    def start_run(
        self,
        task_id: str,
        *,
        lane: str | None,
        claim_lock: str,
        ttl_seconds: int,
        max_runtime_seconds: int | None = None,
        worker_pid: int | None = None,
    ) -> int:
        """Open a new run row for an in-progress task. Returns run_id.
        Caller must already hold the claim. The run row carries the
        worker_pid + heartbeat for crash detection."""
        now = _now()
        with self.transaction():
            cur = self._conn.execute(
                """
                INSERT INTO task_runs (
                    task_id, lane, status, claim_lock, claim_expires,
                    worker_pid, max_runtime_seconds, last_heartbeat_at,
                    started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id, lane, RUN_STATUS_RUNNING, claim_lock,
                    now + ttl_seconds, worker_pid, max_runtime_seconds,
                    now, now,
                ),
            )
            run_id = cur.lastrowid
            self._conn.execute(
                "UPDATE tasks SET current_run_id = ?, worker_pid = ? "
                "WHERE id = ?",
                (run_id, worker_pid, task_id),
            )
            self._append_event_inner(
                task_id, "started",
                {"run_id": run_id, "lane": lane}, run_id=run_id,
            )
        return int(run_id)

    def finalize_run(
        self,
        run_id: int,
        *,
        outcome: str,
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
        error: str | None = None,
        new_status: str = RUN_STATUS_DONE,
    ) -> None:
        """Close a run row. Outcome is one of ``RUN_OUTCOMES``. The
        caller is responsible for any task-level status flip via
        :meth:`update_task`."""
        if new_status not in VALID_RUN_STATUSES:
            raise InvalidStatusError(f"invalid run status: {new_status}")
        now = _now()
        meta_json = json.dumps(metadata) if metadata else None
        self._conn.execute(
            """
            UPDATE task_runs
               SET status = ?, ended_at = ?, outcome = ?,
                   summary = ?, metadata = ?, error = ?
             WHERE id = ?
            """,
            (new_status, now, outcome, summary, meta_json, error, run_id),
        )

    def get_run(self, run_id: int) -> TaskRun | None:
        row = self._conn.execute(
            "SELECT * FROM task_runs WHERE id = ?", (run_id,),
        ).fetchone()
        if row is None:
            return None
        return TaskRun.from_row(row)

    def list_runs(self, task_id: str) -> list[TaskRun]:
        rows = self._conn.execute(
            "SELECT * FROM task_runs WHERE task_id = ? "
            "ORDER BY started_at DESC, id DESC",
            (task_id,),
        ).fetchall()
        return [TaskRun.from_row(r) for r in rows]

    # ─── events ───────────────────────────────────────────────────

    def append_event(
        self,
        task_id: str,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        run_id: int | None = None,
    ) -> int:
        """Append an event row. Kind must be in :data:`VALID_EVENT_KINDS`.
        Returns the event id."""
        if kind not in VALID_EVENT_KINDS:
            raise InvalidEventKindError(f"invalid event kind: {kind}")
        return self._append_event_inner(task_id, kind, payload, run_id)

    def _append_event_inner(
        self,
        task_id: str,
        kind: str,
        payload: dict[str, Any] | None,
        run_id: int | None,
    ) -> int:
        """Internal: skip the kind validation so callers in this module
        can use additional kinds (``released`` etc) without bloating
        the public enum. External callers should use :meth:`append_event`.
        """
        payload_raw = json.dumps(payload) if payload else None
        cur = self._conn.execute(
            "INSERT INTO task_events (task_id, run_id, kind, payload, "
            "created_at) VALUES (?, ?, ?, ?, ?)",
            (task_id, run_id, kind, payload_raw, _now()),
        )
        return int(cur.lastrowid)

    def events_since(
        self,
        cursor: int,
        *,
        limit: int = 200,
    ) -> list[Event]:
        """Return events with id > cursor, oldest first, capped at
        ``limit``. The dashboard WS and Telegram notifier both call
        this with their saved cursor."""
        rows = self._conn.execute(
            "SELECT * FROM task_events WHERE id > ? "
            "ORDER BY id ASC LIMIT ?",
            (cursor, limit),
        ).fetchall()
        return [Event.from_row(r) for r in rows]

    def latest_event_id(self) -> int:
        """Return the id of the most recent event, or 0 if none. Used
        by subscribers to position their initial cursor at "live"
        (skipping the historical replay)."""
        row = self._conn.execute(
            "SELECT MAX(id) AS m FROM task_events",
        ).fetchone()
        return int(row["m"] or 0)

    def list_events(self, task_id: str, *, limit: int = 100) -> list[Event]:
        """All events for one task, newest first. Used by the dashboard
        task detail view."""
        rows = self._conn.execute(
            "SELECT * FROM task_events WHERE task_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (task_id, limit),
        ).fetchall()
        return [Event.from_row(r) for r in rows]

    # ─── stats ────────────────────────────────────────────────────

    def board_summary(self) -> dict[str, int]:
        """Counts per status (active statuses only — archived hidden).
        Used by the dashboard top-bar and by the Telegram /kanban
        no-args summary."""
        out: dict[str, int] = {s: 0 for s in ACTIVE_STATUSES}
        rows = self._conn.execute(
            "SELECT status, COUNT(*) AS n FROM tasks "
            "WHERE status != ? GROUP BY status",
            (STATUS_ARCHIVED,),
        ).fetchall()
        for row in rows:
            out[row["status"]] = int(row["n"])
        return out


__all__ = [
    "ClaimContentionError",
    "Comment",
    "Event",
    "InvalidEventKindError",
    "InvalidStatusError",
    "KanbanError",
    "KanbanStore",
    "SCHEMA_SQL",
    "Task",
    "TaskNotFoundError",
    "TaskRun",
]

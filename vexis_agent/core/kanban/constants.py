"""Kanban constants — single source of truth.

These values are imported by ``db.py`` (schema), ``dispatcher.py``
(loop cadence), ``spawn.py`` (worker env), and ``learning_curator.py``
(recursion-guard skip set). Centralised here so a copy isn't drifting
between modules.
"""

from __future__ import annotations

# ──────────────────────────────────────────────────────────────────
# Status enum — the columns on the board
# ──────────────────────────────────────────────────────────────────
#
# triage      — newly filed, not yet decided whether to do
# todo        — accepted, waiting for parents (or for promotion)
# ready       — parents done, dispatcher will pick it up
# in_progress — claimed by a worker, run in flight
# blocked     — waiting on user / external event (set by worker)
# done        — completed successfully
# archived    — soft-deleted; hidden from default board view
#
# Promotion ``todo → ready`` runs when every parent (in task_links)
# reaches status ``done``. The dispatcher (``recompute_ready`` in
# ``db.py``) re-evaluates each tick.
STATUS_TRIAGE = "triage"
STATUS_TODO = "todo"
STATUS_READY = "ready"
STATUS_IN_PROGRESS = "in_progress"
STATUS_BLOCKED = "blocked"
STATUS_DONE = "done"
STATUS_ARCHIVED = "archived"

VALID_STATUSES: frozenset[str] = frozenset({
    STATUS_TRIAGE, STATUS_TODO, STATUS_READY, STATUS_IN_PROGRESS,
    STATUS_BLOCKED, STATUS_DONE, STATUS_ARCHIVED,
})

# Statuses considered "active" by the default board filter — i.e.
# everything except ``archived``. ``done`` IS active here because
# the user wants to see what just finished; archive is the explicit
# "hide it" gesture.
ACTIVE_STATUSES: frozenset[str] = frozenset(VALID_STATUSES - {STATUS_ARCHIVED})


# ──────────────────────────────────────────────────────────────────
# Run statuses (per-attempt, on task_runs)
# ──────────────────────────────────────────────────────────────────
RUN_STATUS_RUNNING = "running"
RUN_STATUS_DONE = "done"
RUN_STATUS_BLOCKED = "blocked"
RUN_STATUS_CRASHED = "crashed"
RUN_STATUS_TIMED_OUT = "timed_out"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_RELEASED = "released"

VALID_RUN_STATUSES: frozenset[str] = frozenset({
    RUN_STATUS_RUNNING, RUN_STATUS_DONE, RUN_STATUS_BLOCKED,
    RUN_STATUS_CRASHED, RUN_STATUS_TIMED_OUT, RUN_STATUS_FAILED,
    RUN_STATUS_RELEASED,
})

# Outcomes the dispatcher writes when finalising a run.
RUN_OUTCOMES: frozenset[str] = frozenset({
    "completed", "blocked", "crashed", "timed_out",
    "spawn_failed", "gave_up", "reclaimed",
})


# ──────────────────────────────────────────────────────────────────
# Event kinds (append-only audit log)
# ──────────────────────────────────────────────────────────────────
#
# Every state change appends one of these. Dashboard WS + Telegram
# notifier both subscribe to ``task_events`` and filter by kind.
EVENT_CREATED = "created"
EVENT_EDITED = "edited"
EVENT_COMMENTED = "commented"
EVENT_LINKED = "linked"
EVENT_UNLINKED = "unlinked"
EVENT_PROMOTED = "promoted"          # todo → ready
EVENT_CLAIMED = "claimed"
EVENT_STARTED = "started"            # ready → in_progress (worker began)
EVENT_HEARTBEAT = "heartbeat"        # worker called kanban_heartbeat
EVENT_PROGRESS = "progress"          # worker reported progress
EVENT_BLOCKED = "blocked"
EVENT_UNBLOCKED = "unblocked"
EVENT_COMPLETED = "completed"
EVENT_FAILED = "failed"
EVENT_TIMED_OUT = "timed_out"
EVENT_CRASHED = "crashed"
EVENT_RETRIED = "retried"
EVENT_REASSIGNED = "reassigned"
EVENT_ARCHIVED = "archived"

VALID_EVENT_KINDS: frozenset[str] = frozenset({
    EVENT_CREATED, EVENT_EDITED, EVENT_COMMENTED, EVENT_LINKED,
    EVENT_UNLINKED, EVENT_PROMOTED, EVENT_CLAIMED, EVENT_STARTED,
    EVENT_HEARTBEAT, EVENT_PROGRESS, EVENT_BLOCKED, EVENT_UNBLOCKED,
    EVENT_COMPLETED, EVENT_FAILED, EVENT_TIMED_OUT, EVENT_CRASHED,
    EVENT_RETRIED, EVENT_REASSIGNED, EVENT_ARCHIVED,
})


# ──────────────────────────────────────────────────────────────────
# Worker env / recursion guard
# ──────────────────────────────────────────────────────────────────

# System-prompt prefix attached to every worker spawn. The first
# user-turn of a worker session begins with this string; the
# learning curator's ``list_eligible_sessions`` skip list checks
# for it (alongside ``CURATOR_REVIEW_PROMPT_PREFIX`` and
# ``GOAL_JUDGE_PROMPT_PREFIX``) so worker transcripts don't get
# scraped as user lessons.
#
# CLAUDE.md ## Invariants makes content-prefix the canonical
# recursion guard — env vars (``VEXIS_KANBAN_TASK_ID``) are
# forensic markers only, never used for filtering.
KANBAN_WORKER_PREFIX = "[KANBAN-WORKER]"

# Env var the spawned worker reads to know which task it owns.
# Forensic / introspection only — the recursion guard does NOT
# read this; it filters by content prefix above.
ENV_VAR_KANBAN_TASK_ID = "VEXIS_KANBAN_TASK_ID"
ENV_VAR_KANBAN_LANE = "VEXIS_KANBAN_LANE"
ENV_VAR_KANBAN = "VEXIS_KANBAN"  # set to "1" on every worker spawn


# ──────────────────────────────────────────────────────────────────
# Defaults — overridable via ``kanban:`` in ~/.vexis/config.yaml
# ──────────────────────────────────────────────────────────────────

# Dispatcher tick cadence. Hermes uses 60s; faster polling helps
# responsiveness but burns no real cost — SQLite reads are cheap.
DEFAULT_DISPATCH_INTERVAL_SECONDS = 60

# Max parallel workers. Bounded by the brain's rate limit. Default
# is conservative because you're paying out of your own pocket.
DEFAULT_MAX_CONCURRENT_WORKERS = 2

# Consecutive-failure circuit breaker. After this many crashes/
# timeouts/spawn_failures in a row, the task auto-blocks with a
# ``blocked`` event so the user can intervene.
DEFAULT_FAILURE_LIMIT = 3

# Hard wall on worker runtime in seconds. Workers older than this
# get terminated by the dispatcher and marked ``timed_out``.
DEFAULT_MAX_RUNTIME_SECONDS = 900  # 15 minutes

# How long a claim lock survives without a heartbeat before the
# dispatcher releases it. Heartbeat interval is roughly 1/5 of
# this so a single missed beat doesn't release the claim.
DEFAULT_CLAIM_TTL_SECONDS = 150

# Worker heartbeat interval (worker side). Should be ~ TTL / 5.
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 30


__all__ = [
    "ACTIVE_STATUSES",
    "DEFAULT_CLAIM_TTL_SECONDS",
    "DEFAULT_DISPATCH_INTERVAL_SECONDS",
    "DEFAULT_FAILURE_LIMIT",
    "DEFAULT_HEARTBEAT_INTERVAL_SECONDS",
    "DEFAULT_MAX_CONCURRENT_WORKERS",
    "DEFAULT_MAX_RUNTIME_SECONDS",
    "ENV_VAR_KANBAN",
    "ENV_VAR_KANBAN_LANE",
    "ENV_VAR_KANBAN_TASK_ID",
    "EVENT_ARCHIVED",
    "EVENT_BLOCKED",
    "EVENT_CLAIMED",
    "EVENT_COMMENTED",
    "EVENT_COMPLETED",
    "EVENT_CRASHED",
    "EVENT_CREATED",
    "EVENT_EDITED",
    "EVENT_FAILED",
    "EVENT_HEARTBEAT",
    "EVENT_LINKED",
    "EVENT_PROGRESS",
    "EVENT_PROMOTED",
    "EVENT_REASSIGNED",
    "EVENT_RETRIED",
    "EVENT_STARTED",
    "EVENT_TIMED_OUT",
    "EVENT_UNBLOCKED",
    "EVENT_UNLINKED",
    "KANBAN_WORKER_PREFIX",
    "RUN_OUTCOMES",
    "RUN_STATUS_BLOCKED",
    "RUN_STATUS_CRASHED",
    "RUN_STATUS_DONE",
    "RUN_STATUS_FAILED",
    "RUN_STATUS_RELEASED",
    "RUN_STATUS_RUNNING",
    "RUN_STATUS_TIMED_OUT",
    "STATUS_ARCHIVED",
    "STATUS_BLOCKED",
    "STATUS_DONE",
    "STATUS_IN_PROGRESS",
    "STATUS_READY",
    "STATUS_TODO",
    "STATUS_TRIAGE",
    "VALID_EVENT_KINDS",
    "VALID_RUN_STATUSES",
    "VALID_STATUSES",
]

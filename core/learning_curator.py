"""Background learning curator: promotes lessons from past sessions.

Companion to ``core/curator.py`` — same daemon-thread shape, opposite
job. Where the archive curator looks at the skill tree and consolidates,
the learning curator walks past session JSONLs and promotes
generalized lessons into MEMORY.md (or, while ``learning_shadow_mode``
is True, into MEMORY-SHADOW.md for human review).

The review pipeline itself — prompt, transcript formatting, response
parsing, evidence verification, ``claude -p`` invocation — lives in
``core/learning_review.py``. This module is just the controller and
the dispatcher that decides where to write each verified lesson.

Trigger model (see ``.plans/learning-curator-research.md`` §3):

  Every ``learning_tick_interval_minutes`` (default 5):
    1. Walk ``~/.claude/projects/<workspace-encoded>/*.jsonl``.
    2. A session is eligible when:
         - last_message_timestamp > last_message_at_review_time, AND
         - now - last_message_timestamp >= idle_threshold (default 25m), AND
         - the UUID isn't one our own review forks spawned.
    3. For each eligible session, run the review subprocess
       (``claude -p`` with the §7.2 prompt + transcript).
    4. Update reviewed.json. On success, advance
       ``last_reviewed_at`` and ``last_message_at_review_time``;
       on failure, advance only ``last_review_attempt_at`` so the
       eligibility gate still reopens after the cooldown.

Persistent state:
  - ``~/.vexis/learning/reviewed.json`` — per-session records.
  - ``~/.vexis/learning/state.json``    — daemon-level (paused, last_tick_at).

The split keeps the high-write per-session file separate from the
rarely-mutated daemon flags.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import logging
import os
import threading
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from core.coherence_judge import (
    CoherenceVerdict,
    run_coherence_judge,
)
from core.learning_review import (
    RECURSION_ENV_VAR,
    ReviewOutput,
    run_review,
)
from core.learning_writes import (
    StageResult,
    stage_new_skill,
    stage_skill_patch,
    stage_support_file,
)
from core.memory import MemoryStore, MemorySuccess
from core.notify import Notifier
from core.paths import (
    learning_logs_dir,
    learning_spawned_path,
    learning_state_path,
    memories_dir,
    skills_dir,
    user_candidates_path,
)
from core.skills import discover_skills, parse_skill_md
from core.transcripts import (
    SessionMeta,
    _is_curator_owned,
    claude_session_jsonl_dir,
    iter_messages,
    list_eligible_sessions,
)
from core.relationships.triggers import detect as relationships_detect
from core.user_candidates import (
    DEFAULT_PROMOTION_THRESHOLD,
    DEFAULT_WINDOW,
    UserCandidateStore,
)
from core.yaml_config import (
    learning_enabled,
    learning_failure_cooldown_hours,
    learning_idle_threshold_minutes,
    learning_shadow_mode,
    learning_tick_interval_minutes,
    model_brain,
    model_coherence_judge,
    model_learning_review,
    model_migration_classifier,
)

# Type alias for dependency-injected review functions (used by tests
# to swap the real subprocess pipeline for synthetic outcomes).
# Day 5: returns ``(outcome_str, WriteSummary)`` — the summary
# carries class/tier/dedup/queue counts so the controller can
# aggregate them into the per-tick report. Stubs may return
# ``(outcome_str, WriteSummary())`` when they don't simulate writes.
ReviewFn = Callable[[Path, SessionMeta], tuple[str, "WriteSummary"]]

log = logging.getLogger(__name__)

# Shadow file lives alongside MEMORY.md / USER.md inside the workspace
# so the user can review and `mv MEMORY-SHADOW.md MEMORY.md` once
# they're happy. The shadow file is NOT injected into the system
# prompt — that's the whole point of "shadow mode".
SHADOW_FILE_NAME = "MEMORY-SHADOW.md"
USER_SHADOW_FILE_NAME = "USER-SHADOW.md"

# Same delimiter MEMORY.md uses (core/memory.py:ENTRY_DELIMITER), so a
# manual cutover to live mode is a `mv` not a reformat.
ENTRY_DELIMITER = "\n§\n"

# After this many consecutive failures the controller pins
# ``last_message_at_review_time`` to the current snapshot so the
# eligibility gate filters the session until the user adds new content
# (which advances the JSONL's last_message_timestamp past the pinned
# value, reopening eligibility). Three retries at the 1h cooldown
# default = ~3h of transient-error tolerance, enough for rate-limit
# resets and brief Anthropic outages but bounded for genuine failures
# (parse errors, prompt-format breaks, transcripts that always blow
# the verifier). Constant rather than configurable — promote to
# yaml_config.py if a real production need to tune it emerges.
MAX_REVIEW_FAILURES = 3

# Maximum number of `claude -p` review spawns per tick. A 40-session
# backlog (legacy data, restored backup, dedup-gate regression) used to
# fan out into 40 spawns in one tick — burning quota in minutes. With
# this cap a backlog drains over multiple ticks instead, bounding the
# blast radius of any future eligibility bug. Constant rather than
# configurable; promote to yaml_config.py if a real production need
# emerges to tune it.
MAX_SPAWNS_PER_TICK = 3


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    # Keep microsecond precision so reviewed.json round-trips JSONL
    # message timestamps faithfully (Claude Code writes ms precision).
    # The transcripts.py eligibility gate compares at second precision
    # for legacy-record tolerance, so this is a cleanliness change for
    # new writes — not a correctness fix on its own.
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


_RATE_LIMIT_MARKERS = (
    "hit your limit",
    "usage limit",
    "rate limit",
    "rate-limit",
    "429",
)


def _is_rate_limit_error(message: str) -> bool:
    if not message:
        return False
    lowered = message.lower()
    return any(marker in lowered for marker in _RATE_LIMIT_MARKERS)


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------------
# ReviewedStore — per-session records at ~/.vexis/learning/reviewed.json
# --------------------------------------------------------------------


@dataclass
class ReviewRecord:
    """One session's reviewed-state. Plain dataclass (mutable) because
    the store loads, mutates, and writes back; immutability would mean
    a copy-construct on every field update."""

    last_reviewed_at: datetime | None = None
    last_review_attempt_at: datetime | None = None
    last_message_at_review_time: datetime | None = None
    outcome: str = ""
    # Number of consecutive failed review attempts since the last
    # success. Resets to 0 on success. When it reaches
    # ``MAX_REVIEW_FAILURES`` the store pins ``last_message_at_review_time``
    # so the eligibility gate filters the session until its transcript
    # advances. Defaults to 0 so old-shape records (pre-fix) round-trip
    # cleanly.
    failure_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        def _fmt(dt: datetime | None) -> str | None:
            return _iso(dt) if dt is not None else None
        return {
            "last_reviewed_at": _fmt(self.last_reviewed_at),
            "last_review_attempt_at": _fmt(self.last_review_attempt_at),
            "last_message_at_review_time": _fmt(self.last_message_at_review_time),
            "outcome": self.outcome,
            "failure_count": self.failure_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReviewRecord":
        raw_fc = data.get("failure_count", 0)
        try:
            fc = int(raw_fc)
        except (TypeError, ValueError):
            fc = 0
        return cls(
            last_reviewed_at=_parse_iso(data.get("last_reviewed_at")),
            last_review_attempt_at=_parse_iso(data.get("last_review_attempt_at")),
            last_message_at_review_time=_parse_iso(
                data.get("last_message_at_review_time")
            ),
            outcome=str(data.get("outcome") or ""),
            failure_count=max(0, fc),
        )


class ReviewedStore:
    """Owns ``reviewed.json``. Sidecar ``.lock`` for write integrity
    via fcntl.flock + temp+rename + fsync. Reads do not lock — atomic
    rename means readers see either old or new state, never a tear.

    Mirrors the locking model of ``core/memory.py`` (lock the sidecar,
    not the file you're about to atomically replace)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock_path = path.with_suffix(path.suffix + ".lock")

    def load(self) -> dict[str, ReviewRecord]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning(
                "reviewed.json corrupt at %s; treating as empty", self._path
            )
            return {}
        by_session = data.get("by_session") if isinstance(data, dict) else None
        if not isinstance(by_session, dict):
            return {}
        out: dict[str, ReviewRecord] = {}
        for k, v in by_session.items():
            if isinstance(k, str) and isinstance(v, dict):
                out[k] = ReviewRecord.from_dict(v)
        return out

    def save(self, records: dict[str, ReviewRecord]) -> None:
        payload = {
            "by_session": {k: v.to_dict() for k, v in records.items()},
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def update(
        self,
        session_uuid: str,
        *,
        success: bool,
        last_message_at_review_time: datetime,
        outcome: str,
        now: datetime | None = None,
    ) -> None:
        """Record one review attempt's outcome.

        On success: advance ``last_reviewed_at`` and
        ``last_message_at_review_time`` (the eligibility-gate snapshot);
        reset ``failure_count`` to 0.

        On failure: advance ``last_review_attempt_at`` so the cooldown
        gate kicks in and the eligibility gate stays open (the same
        content can be retried after the cooldown). Increment
        ``failure_count``. Once ``failure_count`` reaches
        ``MAX_REVIEW_FAILURES``, also pin
        ``last_message_at_review_time`` to the current snapshot so the
        eligibility gate closes — the session is filtered until the
        user adds new content (which advances the JSONL's
        ``last_message_timestamp`` past the pinned value, reopening
        eligibility and resetting the retry budget on the next
        successful review).
        """
        records = self.load()
        rec = records.get(session_uuid) or ReviewRecord()
        when = now or _utc_now()
        rec.last_review_attempt_at = when
        rec.outcome = outcome
        if success:
            rec.last_reviewed_at = when
            rec.last_message_at_review_time = last_message_at_review_time
            rec.failure_count = 0
        else:
            rec.failure_count += 1
            if rec.failure_count >= MAX_REVIEW_FAILURES:
                # Bound the retry loop. The session reopens for review
                # only if ``last_message_timestamp`` advances past this
                # snapshot — i.e. the user actually added new content.
                rec.last_message_at_review_time = last_message_at_review_time
        records[session_uuid] = rec
        self.save(records)


# --------------------------------------------------------------------
# SpawnedStore — persistent recursion-guard registry
# (~/.vexis/learning/spawned.json)
# --------------------------------------------------------------------


class SpawnedStore:
    """Owns ``spawned.json``. Records every UUID created by the
    curator's own ``claude -p`` review forks so the eligibility filter
    can exclude them across daemon restarts.

    Same locking model as :class:`ReviewedStore` — sidecar ``.lock`` +
    ``fcntl.flock`` + atomic temp-rename. Reads do not lock; the atomic
    rename guarantees readers see either old or new state, never a
    tear.

    Schema:

    .. code-block:: json

       {
         "version": 1,
         "spawned": {
           "<session-uuid>": {
             "spawned_at": "<iso utc>",
             "parent_session": "<uuid being reviewed at spawn time>"
           }
         }
       }

    ``parent_session`` is forensic only — if missing or wrong the
    recursion guard still filters by UUID alone.
    """

    SCHEMA_VERSION = 1

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock_path = path.with_suffix(path.suffix + ".lock")

    def load(self) -> dict[str, dict[str, str]]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            log.warning(
                "spawned.json corrupt at %s; treating as empty", self._path
            )
            return {}
        spawned = data.get("spawned") if isinstance(data, dict) else None
        if not isinstance(spawned, dict):
            return {}
        out: dict[str, dict[str, str]] = {}
        for k, v in spawned.items():
            if isinstance(k, str) and isinstance(v, dict):
                out[k] = {kk: str(vv) for kk, vv in v.items() if isinstance(kk, str)}
        return out

    def load_uuids(self) -> set[str]:
        """Convenience: just the UUID set, which is what the eligibility
        filter actually consumes. Same I/O cost as ``load`` — saves
        callers a comprehension."""
        return set(self.load().keys())

    def add_many(
        self,
        uuids: set[str],
        *,
        parent_session: str,
        now: datetime | None = None,
    ) -> None:
        """Append the given UUIDs to the on-disk registry. No-op when
        ``uuids`` is empty so the caller doesn't need to guard.

        ``parent_session`` is the UUID being reviewed at the time of
        the spawn — recorded for forensics so a future audit can
        reconstruct which review produced which fork. Never read by
        the eligibility filter.
        """
        if not uuids:
            return
        when = _iso(now or _utc_now())
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            current = self.load()
            for uuid in uuids:
                if uuid in current:
                    continue
                current[uuid] = {
                    "spawned_at": when,
                    "parent_session": parent_session,
                }
            payload = {
                "version": self.SCHEMA_VERSION,
                "spawned": current,
            }
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self._path)
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


# --------------------------------------------------------------------
# Daemon-level state file (~/.vexis/learning/state.json)
# --------------------------------------------------------------------


def _daemon_state_path() -> Path:
    """Sibling of reviewed.json, holds paused / last_tick_at."""
    return learning_state_path().with_name("state.json")


def _load_daemon_state() -> dict[str, Any]:
    path = _daemon_state_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read learning state %s: %s", path, exc)
        return {}


def _save_daemon_state(state: dict[str, Any]) -> None:
    path = _daemon_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("Could not write learning state %s: %s", path, exc)
        tmp.unlink(missing_ok=True)


def is_paused() -> bool:
    return bool(_load_daemon_state().get("paused"))


def set_paused(value: bool) -> None:
    state = _load_daemon_state()
    state["paused"] = bool(value)
    _save_daemon_state(state)


# --------------------------------------------------------------------
# Tick result + stubbed review (Day 1 — Day 2 swaps in real claude -p)
# --------------------------------------------------------------------


@dataclass
class WriteSummary:
    """Detailed breakdown of one session's verified-lesson dispatch.

    Day 5 addition: per-tick REPORT.md and the /learning audit
    surface need to show the user what the curator is doing
    classification-wise. The dispatcher reports counts via this
    struct; ``_run_once`` aggregates across sessions; the report
    writer surfaces them.

    Day 6 (v3a) addition: coherence judge counts and per-flag
    detail records. ``coherence_flagged`` and ``coherence_near_miss``
    aggregate across sessions for the run.json summary; the
    ``coherence_flags`` list carries per-flag detail records (session
    uuid + lesson preview + verdict + reason + explanation) which
    the per-tick REPORT.md narrates under ``## Coherence flags``.
    """

    written: int = 0
    by_class: dict[str, int] = field(default_factory=dict)  # PROCEDURAL/...
    by_tier: dict[str, int] = field(default_factory=dict)   # S1/S2/S3/MEM/USER
    dedup_skipped: int = 0
    queue_added: int = 0
    queue_promoted: int = 0
    stage_refused: int = 0
    # v3a coherence judge — aggregate counts go into run.json's
    # ``summary.coherence`` block; per-flag detail records narrate
    # the REPORT.md ``## Coherence flags`` section.
    coherence_flagged: int = 0       # INCOHERENT verdicts (hard flag)
    coherence_near_miss: int = 0     # NEAR_MISS_REVIEW verdicts (soft)
    coherence_by_reason: dict[str, int] = field(default_factory=dict)
    coherence_flags: list[dict] = field(default_factory=list)

    def merge(self, other: "WriteSummary") -> None:
        """Aggregate ``other`` into self. Used by ``_run_once`` to
        roll up per-session WriteSummary into a tick-level total."""
        self.written += other.written
        self.dedup_skipped += other.dedup_skipped
        self.queue_added += other.queue_added
        self.queue_promoted += other.queue_promoted
        self.stage_refused += other.stage_refused
        for k, v in other.by_class.items():
            self.by_class[k] = self.by_class.get(k, 0) + v
        for k, v in other.by_tier.items():
            self.by_tier[k] = self.by_tier.get(k, 0) + v
        self.coherence_flagged += other.coherence_flagged
        self.coherence_near_miss += other.coherence_near_miss
        for k, v in other.coherence_by_reason.items():
            self.coherence_by_reason[k] = self.coherence_by_reason.get(k, 0) + v
        self.coherence_flags.extend(other.coherence_flags)

    def to_dict(self) -> dict:
        return {
            "written": self.written,
            "by_class": dict(self.by_class),
            "by_tier": dict(self.by_tier),
            "dedup_skipped": self.dedup_skipped,
            "queue_added": self.queue_added,
            "queue_promoted": self.queue_promoted,
            "stage_refused": self.stage_refused,
            # v3a — per-tick run.json shape per the brief:
            # ``summary.coherence = {flagged, near_miss, by_reason}``.
            # Per-flag detail (coherence_flags) lives only in the
            # in-memory WriteSummary and the REPORT.md narrative;
            # not in run.json by design (keep run.json an
            # aggregates-only doc; future dashboards can mine
            # REPORT.md if they want detail).
            "coherence": {
                "flagged": self.coherence_flagged,
                "near_miss": self.coherence_near_miss,
                "by_reason": dict(self.coherence_by_reason),
            },
        }


@dataclass
class TickResult:
    eligible: list[str] = field(default_factory=list)        # session uuids
    reviewed: list[str] = field(default_factory=list)        # successes
    skipped: list[tuple[str, str]] = field(default_factory=list)
    error: str | None = None
    # Per-session outcomes captured for the per-tick REPORT.md. Each
    # entry: (session_uuid, outcome_string). Successes get the
    # outcome string from review_fn; failures get "error: ...";
    # cooldown/busy skips get "cooldown" / "busy".
    outcomes: list[tuple[str, str]] = field(default_factory=list)
    # Day 5: aggregate dispatcher write counts across all sessions
    # reviewed in this tick. Surfaced into REPORT.md/run.json so the
    # /learning audit can show "the curator did N writes across M
    # classes this week".
    summary: WriteSummary = field(default_factory=WriteSummary)


def _format_count_dict(counts: dict[str, int]) -> str:
    """Render a {key: count} dict as ``KEY=N, ...`` for legibility in
    REPORT.md. Returns "" when the dict is empty so the caller can
    substitute a placeholder string."""
    if not counts:
        return ""
    return ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))


def _shadow_file(workspace: Path) -> Path:
    return memories_dir(workspace) / SHADOW_FILE_NAME


def _user_shadow_file(workspace: Path) -> Path:
    """USER-SHADOW.md — staging file for promoted IDENTITY claims.

    Parallel to MEMORY-SHADOW.md: lives alongside USER.md inside the
    workspace, NOT injected into the system prompt. The user reviews
    via ``/learning audit`` and flips with
    ``mv USER-SHADOW.md USER.md`` once happy.
    """
    return memories_dir(workspace) / USER_SHADOW_FILE_NAME


def _read_curator_entries(path: Path) -> list[tuple[str, str]]:
    """Parse a §-delimited memory-style file and return curator-authored
    entries as ``(header, body)`` tuples.

    Curator-authored entries start with the ``[learned YYYY-MM-DD]``
    marker — that's how the audit distinguishes them from hand-written
    memory entries (which never include the tag). For the shadow file,
    every entry is curator-authored; for MEMORY.md (live mode), only
    the tagged subset matches.
    """
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    entries: list[tuple[str, str]] = []
    for chunk in raw.split(ENTRY_DELIMITER):
        chunk = chunk.strip()
        if not chunk:
            continue
        if not chunk.startswith("[learned"):
            continue
        first_line, _, rest = chunk.partition("\n")
        # Truncate the header for telegram display — the body is read
        # via direct file inspection if the user wants more.
        header = first_line[:200]
        entries.append((header, rest))
    return entries


def _parse_curator_entries(path: Path) -> list[dict]:
    """Parse curator-authored entries into structured dicts the
    coherence judge can consume.

    Returns a list of dicts with keys ``lesson``, ``class`` (optional),
    ``tier`` (optional), ``scope``, ``evidence``. Skips any entry
    without ``Scope:`` and ``Evidence:`` lines (degraded entries that
    don't carry the curator's standard layout — the judge needs at
    minimum the lesson + evidence to make a verdict). The previous
    Coherence: line (if any) is intentionally NOT preserved — the
    point of /learning coherence-audit is to RE-judge, so stale
    annotations would just be noise.
    """
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    out: list[dict] = []
    for chunk in raw.split(ENTRY_DELIMITER):
        chunk = chunk.strip()
        if not chunk or not chunk.startswith("[learned"):
            continue
        first_line, _, rest = chunk.partition("\n")
        # Lesson body lives between the ``]`` of the learned-marker
        # and the first metadata line; can span multiple physical
        # lines if the original lesson had embedded newlines.
        bracket_end = first_line.find("]")
        lesson_text = (
            first_line[bracket_end + 1:].strip() if bracket_end >= 0 else first_line
        )
        entry: dict = {"lesson": lesson_text}
        for line in rest.splitlines():
            line = line.strip()
            if line.startswith("Class:"):
                entry["class"] = line.split(":", 1)[1].strip()
            elif line.startswith("Tier:"):
                # Tier line may carry parenthetical context like
                # "S3 (would create new skill: ...)" — keep just the
                # tier letter.
                tier_part = line.split(":", 1)[1].strip()
                entry["tier"] = tier_part.split(" ", 1)[0].strip() if tier_part else None
            elif line.startswith("Scope:"):
                entry["scope"] = line.split(":", 1)[1].strip()
            elif line.startswith("Evidence:"):
                entry["evidence"] = line.split(":", 1)[1].strip()
        # The judge can only score entries with both scope + evidence;
        # drop incomplete entries (legacy v1 shape without those
        # lines wouldn't be useful inputs).
        if "evidence" in entry and "scope" in entry:
            out.append(entry)
    return out


def _parse_curator_entries_annotated(path: Path) -> list[dict]:
    """Parse curator-authored entries into structured dicts WITH the
    trailing-line annotations preserved.

    Sibling of ``_parse_curator_entries``. The judge-facing parser
    deliberately strips the ``Coherence:`` / ``Staged:`` / ``Queue:`` /
    ``Stage refused:`` / ``Source:`` lines because the judge wants
    a clean (lesson, scope, evidence) tuple. The dashboard wants
    those lines preserved so it can render verdicts inline, attribute
    entries to source sessions, and show the staging outcome.

    Output dict keys (Step 15 — dashboard surface):
      - ``lesson``, ``class`` (optional), ``tier`` (optional),
        ``scope``, ``evidence`` — same as ``_parse_curator_entries``.
      - ``coherence_verdict``: ``"INCOHERENT"`` |
        ``"NEAR_MISS_REVIEW"`` | ``None`` (parsed from the
        ``Coherence: FLAGGED (reason) — explanation`` /
        ``Coherence: NEAR_MISS (reason) — explanation`` line shape
        emitted by ``_format_coherence_line``).
      - ``coherence_reason``: str | None
      - ``coherence_explanation``: str | None
      - ``outcome_marker``: verbatim trailing-line text
        (``"Staged: ..."``, ``"Queue: ..."``, ``"Stage refused: ..."``)
        or ``None``.
      - ``source_session_prefix``: 8-char session UUID prefix from
        the ``Source:`` line (Step 15 instrumentation), or ``None``
        for pre-instrumentation entries.
      - ``entry_id``: 12-char blake2s hex of ``lesson + "\\n" +
        evidence`` — stable across whitespace edits, changes when
        the lesson body or evidence text changes. Used by the
        dashboard to key the per-entry re-judge map.

    Entries without both ``scope`` and ``evidence`` are dropped, same
    as ``_parse_curator_entries`` — they don't have enough structure
    for the dashboard to render usefully.
    """
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return _parse_curator_entries_annotated_text(raw)


def _parse_curator_entries_annotated_text(raw: str) -> list[dict]:
    """String-input variant of ``_parse_curator_entries_annotated``.

    Used to parse ``[learned ...]`` preambles inside staged-skill
    SKILL.md bodies where the caller has already stripped YAML
    frontmatter. Same parse rules and output dict shape; just no
    file-IO step.
    """
    out: list[dict] = []
    for chunk in raw.split(ENTRY_DELIMITER):
        chunk = chunk.strip()
        if not chunk or not chunk.startswith("[learned"):
            continue
        first_line, _, rest = chunk.partition("\n")
        bracket_end = first_line.find("]")
        lesson_text = (
            first_line[bracket_end + 1:].strip() if bracket_end >= 0 else first_line
        )
        entry: dict = {
            "lesson": lesson_text,
            "coherence_verdict": None,
            "coherence_reason": None,
            "coherence_explanation": None,
            "outcome_marker": None,
            "source_session_prefix": None,
        }
        for line in rest.splitlines():
            stripped = line.strip()
            if stripped.startswith("Class:"):
                entry["class"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("Tier:"):
                tier_part = stripped.split(":", 1)[1].strip()
                entry["tier"] = tier_part.split(" ", 1)[0].strip() if tier_part else None
            elif stripped.startswith("Scope:"):
                entry["scope"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("Evidence:"):
                entry["evidence"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("Source:"):
                entry["source_session_prefix"] = stripped.split(":", 1)[1].strip() or None
            elif stripped.startswith("Coherence:"):
                # Two shapes from _format_coherence_line:
                #   "Coherence: FLAGGED (reason) — explanation"
                #   "Coherence: NEAR_MISS (reason) — explanation"
                body = stripped.split(":", 1)[1].strip()
                if body.startswith("FLAGGED"):
                    entry["coherence_verdict"] = "INCOHERENT"
                    rest_after = body[len("FLAGGED"):].strip()
                elif body.startswith("NEAR_MISS"):
                    entry["coherence_verdict"] = "NEAR_MISS_REVIEW"
                    rest_after = body[len("NEAR_MISS"):].strip()
                else:
                    rest_after = body
                # rest_after looks like "(reason) — explanation"
                if rest_after.startswith("("):
                    close = rest_after.find(")")
                    if close > 0:
                        entry["coherence_reason"] = rest_after[1:close].strip() or None
                        rest_after = rest_after[close + 1:].strip()
                if rest_after.startswith("—"):
                    rest_after = rest_after[1:].strip()
                entry["coherence_explanation"] = rest_after or None
            elif (
                stripped.startswith("Staged:")
                or stripped.startswith("Queue:")
                or stripped.startswith("Stage refused:")
            ):
                # Last-write wins if multiple — only the staging-tier
                # fallback path could emit two and that path is
                # mutually exclusive in practice.
                entry["outcome_marker"] = stripped
        if "evidence" not in entry or "scope" not in entry:
            continue
        # Stable id for the per-entry re-judge map. blake2s with
        # digest_size=6 → 12 hex chars; collisions are vanishingly
        # rare across the few-dozen entries this file holds.
        digest = hashlib.blake2s(
            (entry["lesson"] + "\n" + entry["evidence"]).encode("utf-8"),
            digest_size=6,
        ).hexdigest()
        entry["entry_id"] = digest
        out.append(entry)
    return out


def _ellipsize(text: str, n: int) -> str:
    """Truncate ``text`` to at most ``n`` characters with a trailing
    ``...`` when shortened. Used by the dashboard payload for
    ``lesson_preview`` / ``claim_preview`` shaping.
    """
    if len(text) <= n:
        return text
    return text[: max(0, n - 3)] + "..."


def _parse_last_n_sessions(args: list[str], *, default: int) -> int:
    """Parse ``--last-n-sessions N`` out of a /learning args list.

    Accepts either ``--last-n-sessions=30`` or
    ``--last-n-sessions 30`` (two tokens). Falls back to
    ``default`` on missing flag or unparseable value.
    """
    for i, tok in enumerate(args):
        if tok.startswith("--last-n-sessions="):
            try:
                return max(1, int(tok.split("=", 1)[1]))
            except ValueError:
                return default
        if tok == "--last-n-sessions" and i + 1 < len(args):
            try:
                return max(1, int(args[i + 1]))
            except ValueError:
                return default
    return default


def _classify_outcome(detail: str) -> str:
    """Map a ``run.json`` outcome string to the dashboard-feed enum.

    Dashboard renders one of ``wrote`` / ``rejected`` /
    ``nothing-to-save`` / ``cooldown`` / ``error``. The outcome
    strings the curator writes are free-form; this is the canonical
    place that turns them into stable categories.
    """
    if not detail:
        return "error"
    if detail.startswith("wrote"):
        return "wrote"
    if detail.startswith("rejected"):
        return "rejected"
    if detail.startswith("nothing to save"):
        return "nothing-to-save"
    if detail == "cooldown" or detail.startswith("cooldown"):
        return "cooldown"
    if detail.startswith("error"):
        return "error"
    return "error"


def _append_shadow_entry(workspace: Path, content: str) -> None:
    """Append one §-delimited entry to ``MEMORY-SHADOW.md``.

    No threat scanner / char cap / verifier here — that machinery
    lands in Day 2. The shadow file is not injected into the system
    prompt, so a stubbed entry can't reach the model. Atomic temp+rename
    keeps concurrent readers safe even though writes are infrequent.
    """
    _append_to_shadow_file(_shadow_file(workspace), content)


def _append_user_shadow_entry(workspace: Path, content: str) -> None:
    """Append one §-delimited entry to ``USER-SHADOW.md`` (Day 3).

    Same atomic-write semantics as ``_append_shadow_entry``. Used
    when an IDENTITY claim crosses the cross-session threshold and
    the curator promotes it. The user reviews and flips
    ``mv USER-SHADOW.md USER.md`` after the longer (2-week) USER.md
    soak window from §3.4 of the v2 research doc.
    """
    _append_to_shadow_file(_user_shadow_file(workspace), content)


def _append_to_shadow_file(path: Path, content: str) -> None:
    """Shared §-delimited-append + atomic-write helper for both
    shadow files. Same pattern, two destinations.

    Atomicity model: sidecar ``.lock`` file + ``fcntl.flock(LOCK_EX)``
    around the read-modify-write window, plus tmp+fsync+rename for
    the write itself. Mirrors :class:`ReviewedStore` /
    :class:`SpawnedStore` in this module — see those for the
    reference implementation. After ``os.replace`` a defensive
    size-shrunk guard catches the edge case where an unidentified
    writer truncated the file outside our lock (the original
    2026-05-03 truncation bug's hypothetical cause-(c)) and turns
    silent data loss into a loud ``RuntimeError`` naming both
    paths.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        # Capture the on-disk size BEFORE we read — the
        # size-shrunk guard compares this against the post-replace
        # size to catch a stale/empty read overwriting real content.
        actual_pre = path.stat().st_size if path.exists() else 0
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        existing = existing.rstrip("\n")
        if existing:
            new = existing + ENTRY_DELIMITER + content + "\n"
        else:
            new = content + "\n"
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(new)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        actual_post = path.stat().st_size
        if actual_post < actual_pre:
            raise RuntimeError(
                f"shadow-file truncation detected after append: "
                f"path={path} lock={lock_path} "
                f"size_pre={actual_pre} size_post={actual_post} "
                f"appended_bytes={len(content.encode('utf-8'))} — "
                f"a writer outside the lock truncated the file or "
                f"path.read_text returned stale empty state"
            )
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _format_lesson_entry(lesson: dict, meta: SessionMeta | None = None) -> str:
    """Render one verified lesson into a memory-store entry.

    Layout: a tagged header line so a future ``/learning audit`` can
    grep for ``[learned`` to enumerate curator-authored entries, then
    the lesson body, then class/tier (when present), an optional
    Coherence: line (v3a — only for non-COHERENT verdicts), then
    scope and verbatim evidence as provenance, then an optional
    Source: line (Step 15 — 8-char session-UUID prefix when ``meta``
    is provided). The whole block is one §-delimited entry on disk.

    v2 (Day 1) addition: class + tier + target lines surfaced for
    audit so the user reviewing MEMORY-SHADOW.md can see what the
    curator INTENDED to write — a PROCEDURAL/S3 entry sitting in the
    shadow file is a Day 1/Day 2 in-flight signal that the eventual
    skill write hasn't landed yet. v1-shape lessons (no class) still
    render legibly with the legacy three-line layout.

    v3a (Day 6) addition: when the coherence judge attached a
    non-COHERENT verdict (NEAR_MISS_REVIEW or INCOHERENT), surface a
    Coherence: line so the user reviewing the shadow file sees the
    flag inline. COHERENT verdicts are silent — no annotation, no
    visual noise — per §3.4 of the v3a research doc.

    Step 15 addition: when ``meta`` is provided, append a
    ``Source: <8-char-uuid>`` line so the dashboard can join run.json
    outcomes (which carry session_uuid) with shadow-file entries
    (which previously did not). ``meta=None`` opts out for legacy
    tests; production callers in ``_write_verified`` pass it.
    """
    today = _utc_now().strftime("%Y-%m-%d")
    lines: list[str] = [f"[learned {today}] {lesson['lesson']}"]
    class_ = lesson.get("class")
    tier = lesson.get("tier")
    target = lesson.get("target") if isinstance(lesson.get("target"), dict) else None
    if class_:
        lines.append(f"  Class: {class_}")
    if tier and target:
        skill_name = target.get("skill_name") or "?"
        if tier == "S1":
            lines.append(f"  Tier: S1 (would patch existing skill: {skill_name})")
        elif tier == "S2":
            path = target.get("support_file_path") or "?"
            lines.append(
                f"  Tier: S2 (would add support file under {skill_name}: {path})"
            )
        elif tier == "S3":
            lines.append(f"  Tier: S3 (would create new skill: {skill_name})")
        else:
            lines.append(f"  Tier: {tier}")
    elif tier:
        lines.append(f"  Tier: {tier}")
    coherence_line = _format_coherence_line(lesson.get("coherence"))
    if coherence_line:
        lines.append(coherence_line)
    lines.append(f"  Scope: {lesson['scope']}")
    lines.append(f"  Evidence: {lesson['evidence']}")
    if meta is not None and meta.session_uuid:
        lines.append(f"  Source: {meta.session_uuid[:8]}")
    return "\n".join(lines)


def _format_coherence_line(verdict: Any) -> str | None:
    """Render the v3a Coherence: annotation, or None when silent.

    Silent on COHERENT verdicts (no line emitted) so clean lessons
    don't clutter the shadow file. NEAR_MISS_REVIEW gets the soft
    label; INCOHERENT gets the hard FLAGGED label with the reason
    name so the user can grep for specific failure modes
    (``grep 'FLAGGED (mismatched-attribution)' MEMORY-SHADOW.md``).

    Accepts ``Any`` rather than ``CoherenceVerdict | None`` because
    legacy callers (tests that don't set ``lesson["coherence"]``)
    pass ``None`` and we want to no-op cleanly.
    """
    if verdict is None:
        return None
    v = getattr(verdict, "verdict", None)
    if not v or v == "COHERENT":
        return None
    explanation = (getattr(verdict, "explanation", None) or "").strip()
    reason = getattr(verdict, "reason", None)
    if v == "INCOHERENT":
        reason_part = reason or "?"
        return f"  Coherence: FLAGGED ({reason_part}) — {explanation}"
    if v == "NEAR_MISS_REVIEW":
        prefix = f"({reason}) " if reason else ""
        return f"  Coherence: NEAR_MISS {prefix}— {explanation}"
    # Defensive — unknown verdict (shouldn't happen since the judge's
    # verifier already enum-checks).
    return f"  Coherence: {v} — {explanation}"


def _summarize_outcome(output: ReviewOutput, *, written: int) -> str:
    """Build the one-line outcome string saved to reviewed.json.

    Caller has already filtered out ``output.error`` (raised as
    ``RuntimeError`` by ``_real_review``), so this only handles the
    "review ran cleanly" cases: nothing-to-save, all-rejected, or
    one-or-more verified-and-written.
    """
    if output.nothing_to_save:
        return "nothing to save"
    if not output.verified_lessons and not output.parsed_lessons:
        return "nothing to save (empty parse)"
    if not output.verified_lessons:
        first_reason = output.rejected[0][1] if output.rejected else "?"
        return f"rejected: {len(output.rejected)} candidate(s); first: {first_reason}"
    suffix = ""
    if output.rejected:
        suffix = f"; {len(output.rejected)} rejected"
    return f"wrote {written} entry/entries (transcript {output.transcript_chars}c){suffix}"


_LESSON_PREVIEW_CHARS = 80


def _attach_coherence_verdict(
    workspace: Path,
    lesson: dict,
    messages: list,
    meta: SessionMeta,
    summary: WriteSummary,
) -> None:
    """Run the v3a coherence judge on one verified lesson.

    Side effects:
      - Stores the ``CoherenceVerdict`` on ``lesson["coherence"]`` so
        ``_format_lesson_entry`` can emit the inline annotation.
      - Bumps ``summary.coherence_flagged`` (INCOHERENT) or
        ``summary.coherence_near_miss`` (NEAR_MISS_REVIEW).
      - Bumps ``summary.coherence_by_reason[reason]`` when the
        verdict carries a reason.
      - Appends a per-flag detail record to ``summary.coherence_flags``
        for the REPORT.md narrative.

    COHERENT verdicts are silent — attached to the lesson dict but
    not surfaced in counts or annotations. The dict carries them so
    a future audit could grep ``lesson["coherence"]`` to confirm
    the judge ran (vs. opted out) but the user-facing channels stay
    clean.

    Never raises — ``run_coherence_judge`` already collapses every
    failure path (timeout, parse failure, spawn error) to
    NEAR_MISS_REVIEW with reason=other, so any failure of the judge
    itself surfaces as a soft flag the user reviews.
    """
    verdict = run_coherence_judge(workspace, lesson, messages)
    lesson["coherence"] = verdict
    if verdict.verdict == "COHERENT":
        return
    if verdict.verdict == "INCOHERENT":
        summary.coherence_flagged += 1
    elif verdict.verdict == "NEAR_MISS_REVIEW":
        summary.coherence_near_miss += 1
    if verdict.reason:
        summary.coherence_by_reason[verdict.reason] = (
            summary.coherence_by_reason.get(verdict.reason, 0) + 1
        )
    lesson_text = str(lesson.get("lesson") or "")
    preview = (
        lesson_text
        if len(lesson_text) <= _LESSON_PREVIEW_CHARS
        else lesson_text[: _LESSON_PREVIEW_CHARS - 3] + "..."
    )
    summary.coherence_flags.append({
        "session_uuid": meta.session_uuid,
        "lesson_preview": preview,
        "verdict": verdict.verdict,
        "reason": verdict.reason,
        "explanation": verdict.explanation or "",
    })


def _write_verified(
    workspace: Path,
    output: ReviewOutput,
    *,
    meta: SessionMeta,
    shadow: bool,
    messages: list[Any] | None = None,
) -> WriteSummary:
    """Persist verified lessons. Returns a WriteSummary breakdown
    (Day 5: detailed counts per class / tier / outcome — see the
    WriteSummary dataclass).

    v2 routing (full):

    - **PROCEDURAL** lessons go to ``<workspace>/skills/.shadow/``
      via the appropriate ``stage_skill_*`` call. The ``shadow`` flag
      is intentionally IGNORED for skill writes — the staging tree
      IS the shadow for skills. Audit trail in MEMORY-SHADOW.md.

    - **IDENTITY** lessons go through the USER candidate queue
      (Day 3). First observation → queue entry, no USER.md write.
      Second observation in a DIFFERENT session within the 30-day
      window → promotion to USER-SHADOW.md (or USER.md in live
      mode). The dispatcher handles claim aliasing — if the LLM
      sets ``target.user_claim_alias``, the new occurrence attaches
      to the named existing claim instead of creating a fresh one.

    - **SITUATIONAL** lessons go to MEMORY-SHADOW.md in shadow mode
      or MEMORY.md in live mode (via ``MemoryStore.add`` for the
      cap + threat-scanner gates).

    - **VOLATILE** lessons were rejected at validate time and never
      reach this function.

    v3a (Day 6): when ``messages`` is provided, run the coherence
    judge on each verified lesson BEFORE the per-class branch.
    The verdict gets attached to the lesson dict as
    ``lesson["coherence"]`` so the downstream renderer
    (``_format_lesson_entry``) can emit the inline annotation.
    Aggregate counts and per-flag detail land on the WriteSummary.

    ``messages=None`` (or empty) opts out of the coherence judge —
    legacy callers (most existing tests) pass nothing and get the
    pre-v3a behavior. Production ``_real_review`` always passes the
    full transcript.

    The ``meta`` argument carries the session UUID, needed by the
    IDENTITY queue path so each observation is recorded against
    the right session and the cross-session threshold can fire AND
    by the v3a flag-record so REPORT.md can attribute each flag
    to its source session.
    """
    summary = WriteSummary()
    for lesson in output.verified_lessons:
        # v3a coherence judge (Day 6) — runs first so the verdict
        # is attached to the lesson dict before the per-class branch
        # renders the entry. Per the v3a research doc §3.2, the
        # judge is advisory-only: a flag annotates the audit entry
        # but does NOT block the per-class write. Skipped when
        # ``messages`` is None/empty (test seam — production always
        # passes the parsed session transcript via _real_review).
        if messages:
            _attach_coherence_verdict(workspace, lesson, messages, meta, summary)
        class_ = lesson.get("class")
        if class_ in {"PROCEDURAL", "IDENTITY", "SITUATIONAL", "VOLATILE"}:
            summary.by_class[class_] = summary.by_class.get(class_, 0) + 1
        if class_ == "PROCEDURAL":
            tier = lesson.get("tier") or "?"
            stage_result = _stage_procedural_lesson(workspace, lesson)
            if stage_result.ok:
                summary.written += 1
                summary.by_tier[tier] = summary.by_tier.get(tier, 0) + 1
                entry = _format_lesson_entry(lesson, meta)
                if stage_result.staged_path is not None:
                    entry = f"{entry}\n  Staged: {stage_result.staged_path}"
                _append_shadow_entry(workspace, entry)
            else:
                summary.stage_refused += 1
                log.warning(
                    "Skill staging refused for lesson: %s",
                    stage_result.message,
                )
                entry = _format_lesson_entry(lesson, meta)
                entry = f"{entry}\n  Stage refused: {stage_result.message}"
                _append_shadow_entry(workspace, entry)
        elif class_ == "IDENTITY":
            queue_result = _route_identity(
                workspace, lesson, meta=meta, shadow=shadow
            )
            entry = _format_lesson_entry(lesson, meta)
            entry = f"{entry}\n  Queue: {queue_result.message}"
            _append_shadow_entry(workspace, entry)
            if queue_result.ok:
                summary.written += 1
                if queue_result.promoted:
                    summary.queue_promoted += 1
                    summary.by_tier["USER"] = summary.by_tier.get("USER", 0) + 1
                else:
                    summary.queue_added += 1
        elif class_ == "SITUATIONAL":
            entry = _format_lesson_entry(lesson, meta)
            if shadow:
                _append_shadow_entry(workspace, entry)
                summary.written += 1
                summary.by_tier["MEM"] = summary.by_tier.get("MEM", 0) + 1
            else:
                store = MemoryStore(memories_dir(workspace))
                result = store.add("memory", entry)
                if isinstance(result, MemorySuccess):
                    summary.written += 1
                    summary.by_tier["MEM"] = summary.by_tier.get("MEM", 0) + 1
                else:
                    log.warning(
                        "MemoryStore rejected curator-authored entry: %s",
                        getattr(result, "message", "?"),
                    )
        else:
            # Unknown class — defensive. Shouldn't happen since
            # _validate_lesson rejects unknown classes; if it ever
            # does, log loudly and route to the audit shadow file
            # so we don't silently drop the lesson.
            log.warning(
                "Verified lesson with unexpected class %r — writing "
                "to shadow as fallback",
                class_,
            )
            entry = _format_lesson_entry(lesson, meta)
            _append_shadow_entry(workspace, entry)
            summary.written += 1
    # Dedup skips happen INSIDE run_review (the verifier rejects
    # SITUATIONAL candidates whose evidence overlaps an existing
    # memory entry). Surface that count from output.rejected so the
    # tick report can show what the dedup gate caught this session.
    summary.dedup_skipped = sum(
        1 for _cand, reason in output.rejected if "deduped" in reason
    )
    return summary


@dataclass
class _IdentityRouteResult:
    """Outcome of one IDENTITY → queue/USER routing decision.

    ``message`` is the human-readable summary that lands in the
    audit ``Queue:`` line of MEMORY-SHADOW.md.
    """

    ok: bool
    message: str
    promoted: bool = False


def _check_claim_overlap(
    candidate_claim: str, existing_claims: list[str]
) -> str | None:
    """Bidirectional substring match between ``candidate_claim`` and
    each entry in ``existing_claims``. Returns the matched existing
    claim text, or None on miss.

    Mirrors the shape of ``_check_evidence_overlap`` in
    ``core/learning_review.py``: belt-and-suspenders dedup that runs
    in-process when the LLM didn't set ``target.user_claim_alias``.
    Two near-equivalent claims under different wording would
    otherwise accumulate as separate queue entries; the overlap gate
    folds the new occurrence into whichever existing claim it
    overlaps with so the threshold accumulates correctly.

    Bidirectional intent:
      (a) candidate is more specific than an existing claim
          ("User prefers terse responses to direct questions"
          contains "User prefers terse responses") → alias to the
          shorter existing claim,
      (b) candidate is less specific than an existing claim
          ("User prefers terse responses" is contained in
          "User prefers terse responses to direct questions") →
          alias to the longer existing claim.
    Either way the new occurrence joins an existing accumulator
    rather than splitting the queue.
    """
    needle = candidate_claim.strip()
    if not needle:
        return None
    for existing in existing_claims:
        existing_stripped = existing.strip()
        if not existing_stripped:
            continue
        if existing_stripped == needle:
            # Exact match is already handled by add_occurrence's
            # dict-key collision; surfacing it here lets callers log
            # it explicitly without a separate special case.
            return existing_stripped
        if needle in existing_stripped or existing_stripped in needle:
            return existing_stripped
    return None


def _route_identity(
    workspace: Path,
    lesson: dict,
    *,
    meta: SessionMeta,
    shadow: bool,
) -> _IdentityRouteResult:
    """Add the IDENTITY observation to the queue and promote if
    the cross-session threshold is met.

    Aliasing — three paths, in priority order:

      1. **LLM-emitted alias**: ``target.user_claim_alias`` set by
         the review fork. The verifier already checked it's a
         non-empty string; here we confirm the alias target really
         exists in the queue. If the LLM hallucinated, we fall
         through to path 2.

      2. **In-process overlap match (C2 fix)**: the LLM didn't
         emit an alias, but the proposed claim text has a
         bidirectional substring overlap with an existing queue
         claim. ``_check_claim_overlap`` finds the match;
         occurrences fold into the existing claim rather than
         splitting the queue across paraphrases. Mirrors
         ``_check_evidence_overlap`` for SITUATIONAL/MEMORY.md.

      3. **Fresh insert**: the proposed claim is genuinely new.
         Creates a new queue entry under its own text.
    """
    store = UserCandidateStore(user_candidates_path())
    target = lesson.get("target") if isinstance(lesson.get("target"), dict) else None
    alias_for: str | None = None
    if target is not None:
        candidate_alias = target.get("user_claim_alias")
        if isinstance(candidate_alias, str) and candidate_alias.strip():
            alias_for = candidate_alias.strip()

    if alias_for is not None:
        # Confirm the alias target really exists in the queue. If the
        # LLM hallucinated a claim text, fall back to fresh insertion
        # so the observation isn't lost.
        if store.get(alias_for) is None:
            log.warning(
                "IDENTITY alias target %r not found in queue; falling "
                "back to fresh claim insertion",
                alias_for,
            )
            alias_for = None

    proposed_claim = str(lesson.get("lesson", ""))
    overlap_match: str | None = None
    if alias_for is None and proposed_claim.strip():
        # C2 fix: in-process overlap gate. Snapshot the queue's
        # existing claim texts and check if the proposed claim
        # collapses into any of them. Skip when the LLM already
        # emitted an explicit alias — that path is canonical and
        # the overlap gate is the fallback for when the LLM didn't.
        existing_claims = [c.claim for c in store.list_all()]
        overlap_match = _check_claim_overlap(proposed_claim, existing_claims)
        if overlap_match is not None and overlap_match != proposed_claim:
            log.info(
                "IDENTITY in-process overlap match: %r → existing claim %r",
                proposed_claim[:80], overlap_match[:80],
            )
            alias_for = overlap_match

    claim_text = alias_for if alias_for is not None else proposed_claim
    if not claim_text.strip():
        return _IdentityRouteResult(
            False, "skipped: empty claim text after alias resolution"
        )

    evidence = str(lesson.get("evidence", ""))
    candidate = store.add_occurrence(
        claim_text,
        meta.session_uuid,
        evidence,
    )
    distinct = len(candidate.distinct_session_uuids_within(
        DEFAULT_WINDOW, now=_utc_now()
    ))
    if not store.eligible_for_promotion(claim_text):
        msg_parts: list[str] = []
        if overlap_match is not None and overlap_match != proposed_claim:
            # In-process overlap gate fired (C2 fix). Surface this
            # explicitly in the audit so the user can spot when the
            # LLM is producing paraphrases the gate is folding.
            msg_parts.append(
                f"overlap-aliased to existing claim "
                f"({distinct} session(s))"
            )
        elif alias_for is not None:
            msg_parts.append(f"aliased to existing claim ({distinct} session(s))")
        else:
            msg_parts.append(f"queued ({distinct}/{DEFAULT_PROMOTION_THRESHOLD} session(s))")
        return _IdentityRouteResult(True, ", ".join(msg_parts))

    # Threshold crossed → promote.
    promoted_msg, write_ok = _promote_user_claim(
        workspace, claim_text, candidate, shadow=shadow
    )
    if write_ok:
        # Only mark promoted when the write actually succeeded; a
        # cap rejection or other write refusal leaves the claim
        # eligible for retry on the next eligible session (e.g.
        # after the user manually clears space in USER.md).
        store.mark_promoted(claim_text, now=_utc_now())
        return _IdentityRouteResult(
            True,
            f"promoted to {'USER-SHADOW.md' if shadow else 'USER.md'}: {promoted_msg}",
            promoted=True,
        )
    return _IdentityRouteResult(
        False,
        f"promotion blocked: {promoted_msg}",
    )


def _promote_user_claim(
    workspace: Path,
    claim_text: str,
    candidate,  # UserCandidate; untyped to avoid circular import in signature
    *,
    shadow: bool,
) -> tuple[str, bool]:
    """Write a promoted IDENTITY claim to USER-SHADOW.md (or USER.md
    in live mode). Returns (status string, write_succeeded).

    Live-mode write goes through ``MemoryStore.add(target="user")``
    which enforces the USER.md char cap (default 1375) and runs the
    base threat scanner. Shadow-mode write appends to
    USER-SHADOW.md without the cap (the user reviews staged content
    before the manual flip).

    A False ``write_succeeded`` keeps the claim un-promoted in the
    queue so a future session can retry once the cap is cleared.
    Shadow-mode writes never fail under normal conditions, so this
    return path is exercised mainly in live mode.
    """
    today = _utc_now().strftime("%Y-%m-%d")
    body = (
        f"[learned {today}] {claim_text}\n"
        f"  Sessions: {len(candidate.distinct_session_uuids())} "
        f"({', '.join(sorted(candidate.distinct_session_uuids()))[:120]})"
    )
    if shadow:
        try:
            _append_user_shadow_entry(workspace, body)
        except OSError as exc:
            log.warning("USER-SHADOW.md write failed: %s", exc)
            return (f"USER-SHADOW.md write failed: {exc}", False)
        return (f"appended {len(body)} chars to USER-SHADOW.md", True)
    store = MemoryStore(memories_dir(workspace))
    result = store.add("user", body)
    if isinstance(result, MemorySuccess):
        return ("USER.md updated", True)
    log.warning(
        "MemoryStore rejected curator-authored USER.md entry: %s",
        getattr(result, "message", "?"),
    )
    return (f"USER.md write refused: {getattr(result, 'message', '?')}", False)


def _stage_procedural_lesson(
    workspace: Path, lesson: dict
) -> StageResult:
    """Dispatch a verified PROCEDURAL lesson to the right staging fn.

    The verifier already validated tier + target shape; this just
    picks the function and unpacks the target. A target that
    surprises the dispatcher (unknown tier, missing field) returns
    a StageResult.ok=False so the audit trail captures it cleanly.
    """
    tier = lesson.get("tier")
    target = lesson.get("target")
    if not isinstance(target, dict):
        return StageResult(False, f"PROCEDURAL lesson missing 'target' dict")
    skill_name = target.get("skill_name", "")
    if tier == "S1":
        return stage_skill_patch(
            workspace,
            skill_name,
            target.get("patch_old_string", ""),
            target.get("patch_new_string", ""),
        )
    if tier == "S2":
        return stage_support_file(
            workspace,
            skill_name,
            target.get("support_file_path", ""),
            target.get("support_file_content", ""),
        )
    if tier == "S3":
        return stage_new_skill(
            workspace,
            skill_name,
            target.get("new_skill_body", ""),
            target.get("new_skill_category"),
        )
    return StageResult(False, f"unknown tier {tier!r}")


def _real_review(
    workspace: Path, meta: SessionMeta
) -> tuple[str, WriteSummary]:
    """Production review path: parse the JSONL, run claude -p, verify,
    write. Returns ``(outcome_str, summary)``.

    Outcome strings (caller stores verbatim into reviewed.json):
      - "skip: no conversational messages"           — empty transcript
      - "skipped: transcript too large (N chars)"    — past hard threshold
      - "nothing to save"                            — LLM declined
      - "rejected: N candidate(s); first: <reason>"  — verifier dropped all
      - "wrote N entry/entries (transcript Nc)..."   — verified + written

    The summary carries the class/tier/dedup/queue counts (Day 5
    addition) for the per-tick REPORT.md and /learning audit. For
    no-op outcomes (skip, decline, nothing-to-save), summary is
    empty (all-zero WriteSummary).

    Raises ``RuntimeError`` on subprocess / parse / unknown failure
    so the controller's try/except engages the failure cooldown.
    All other outcomes — including the decline-too-large skip —
    advance ``last_reviewed_at`` so the session doesn't loop.
    """
    messages = list(iter_messages(meta.jsonl_path))
    if not messages:
        return ("skip: no conversational messages", WriteSummary())

    output = run_review(workspace, meta, messages)

    if output.error:
        raise RuntimeError(output.error)

    if output.declined_too_large:
        return (
            f"skipped: transcript too large ({output.transcript_chars} chars)",
            WriteSummary(),
        )

    if output.nothing_to_save:
        # Distinguish triage-skipped from a full-review nothing-to-save
        # so the audit surface (REPORT.md / /learning audit) can show
        # how often the cheap pass is filtering work that the expensive
        # pass would have dropped anyway.
        if output.triage_skipped:
            return ("nothing to save (triage)", WriteSummary())
        return ("nothing to save", WriteSummary())

    summary = _write_verified(
        workspace, output,
        meta=meta,
        shadow=learning_shadow_mode(),
        messages=messages,
    )
    return (
        _summarize_outcome(output, written=summary.written),
        summary,
    )


# --------------------------------------------------------------------
# Controller — daemon thread + telegram dispatcher
# --------------------------------------------------------------------


class LearningController:
    """Owns the daemon thread and exposes ``/learning`` slash commands.

    Mirrors ``CuratorController`` in ``core/curator.py`` so they read
    alike. The daemon thread ticks every
    ``learning_tick_interval_minutes()`` (default 5 min). Each tick:

      1. Build the eligibility map from reviewed.json's
         ``last_message_at_review_time`` snapshots.
      2. ``list_eligible_sessions`` filters by the message-vs-reviewed
         gate AND the idle-threshold gate AND a spawned-by-curator
         exclusion.
      3. For each candidate, check the failure cooldown (skip if a
         recent attempt failed and the cooldown window hasn't elapsed).
      4. Acquire a per-session busy guard (so a long review doesn't
         get re-entered on the next tick), run the (stubbed) review,
         record the outcome.

    The per-session busy lock is keyed on session UUID rather than
    being a single global mutex so backlogs of multiple eligible
    sessions can be processed serially without stalling future ticks.
    """

    def __init__(
        self,
        workspace: Path,
        notifier: Notifier | None = None,
        *,
        review_fn: ReviewFn | None = None,
    ) -> None:
        self._workspace = workspace
        self._notifier = notifier
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._busy_per_session: set[str] = set()
        self._busy_lock = threading.Lock()
        self._reviewed = ReviewedStore(learning_state_path())
        self._spawned_store = SpawnedStore(learning_spawned_path())
        # UUIDs the curator's own review forks spawned. Populated by
        # ``_review_one``'s scan-diff: before/after snapshots of the
        # projects directory expose the new JSONL each ``claude -p``
        # invocation creates, and those UUIDs feed into
        # ``list_eligible_sessions``'s exclusion set so the next tick
        # can't pick up the curator's own session.
        #
        # In-memory only; the disk-backed authoritative store is
        # ``self._spawned_store``. The eligibility filter unions both.
        # The set caches the same data the disk store holds so the
        # per-tick path doesn't reload spawned.json on every spawn —
        # the ``add_many`` call inside ``_review_one`` keeps both in
        # lockstep. On daemon restart the set starts empty and the
        # disk store carries the whole history forward.
        self._spawned_uuids: set[str] = set()
        # Day 3.5: tick counter drives periodic housekeeping
        # (currently: USER candidate queue expire_stale). Increments
        # on every tick; the housekeeping fires when it reaches the
        # interval. Persistence isn't needed — losing the count on
        # restart at worst delays one housekeeping pass by N ticks.
        self._tick_count: int = 0
        # Dependency injection for tests: pass ``review_fn`` to
        # short-circuit the real subprocess pipeline. Production
        # leaves it None and the controller calls ``_real_review``.
        self._review_fn = review_fn or _real_review
        # v3b Day 2: relationships curator hangs off the same
        # workspace + tick. Lazy-imported to avoid the optional-
        # dependency churn that would come from a top-level import.
        from core.relationships.curator import RelationshipsCurator
        self._relationships_curator = RelationshipsCurator(
            workspace=workspace,
        )

    # ---------- lifecycle ----------

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        if self._thread is not None:
            return
        if os.environ.get(RECURSION_ENV_VAR):
            log.info(
                "Learning curator running inside %s=1; not starting daemon",
                RECURSION_ENV_VAR,
            )
            return
        if not learning_enabled():
            log.info("Learning curator disabled via config; not starting daemon")
            return
        self._thread = threading.Thread(
            target=self._run_loop, name="vexis-learning", daemon=True
        )
        self._thread.start()
        log.info(
            "Learning curator daemon started (tick=%dm, idle=%dm, shadow=%s)",
            learning_tick_interval_minutes(),
            learning_idle_threshold_minutes(),
            learning_shadow_mode(),
        )

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
        self._thread = None
        log.info("Learning curator daemon stopped")

    # ---------- internals ----------

    def _run_loop(self) -> None:
        # Sleep first so a fresh-started daemon doesn't fire a tick
        # the same instant the user launched the bot. The interval
        # is short enough that "wait one tick" adds at most 5 min
        # of latency to a session that's already 25 min idle.
        while not self._stop.is_set():
            self._stop.wait(learning_tick_interval_minutes() * 60)
            if self._stop.is_set():
                break
            try:
                if not is_paused():
                    self._run_once()
            except Exception:
                log.exception("Learning curator tick raised")

    # Housekeeping cadence: how many ticks between expire_stale runs
    # on the USER candidate queue. At the default 5-min tick, 12 ticks
    # = once per hour — frequent enough that stale claims don't pile
    # up, infrequent enough that a buggy queue can't churn the file
    # every tick. Constant, not configurable — if a real production
    # need emerges to tune this, promote to yaml_config.py then.
    _HOUSEKEEPING_TICKS = 12

    def _run_once(self) -> TickResult:
        result = TickResult()
        started_at = _utc_now()
        idle_threshold = timedelta(minutes=learning_idle_threshold_minutes())
        cooldown = timedelta(hours=learning_failure_cooldown_hours())
        records = self._reviewed.load()

        # v3b Day 2: relationships restart-recovery is one-shot,
        # gated by an internal `_has_recovered` flag in the curator
        # itself. Runs BEFORE the lesson-curator work so any tokens
        # we re-mint are available to the promote pass below. Recovery
        # spawns claude -p per pending entry — bounded at ~5 calls
        # per restart per the research doc; safe under the per-tick
        # budget. Failures are logged and don't block the tick.
        try:
            asyncio.run(
                self._relationships_curator.recover_after_restart()
            )
        except Exception:
            log.exception("relationships.recover_after_restart raised")

        # Day 3.5 housekeeping: every N ticks, expire stale USER
        # candidate queue entries. Runs BEFORE the per-session reviews
        # so the rendered queue context the LLM sees doesn't include
        # claims that should already be aged out. Best-effort — a
        # failed expire doesn't block the tick (an unbounded queue is
        # also bounded by MAX_OCCURRENCES_PER_CLAIM at insert time).
        self._tick_count += 1
        if self._tick_count % self._HOUSEKEEPING_TICKS == 0:
            try:
                store = UserCandidateStore(user_candidates_path())
                removed = store.expire_stale(now=started_at)
                if removed:
                    log.info(
                        "Expired %d stale USER candidate(s) (>%d days "
                        "since last_seen)",
                        removed, DEFAULT_WINDOW.days,
                    )
            except Exception:
                log.exception("USER candidate expire_stale failed")

        # Eligibility map keyed by `last_message_at_review_time` (the
        # snapshot from the last successful review). A failed review
        # leaves this None so the same content is retried after the
        # cooldown gate elapses.
        eligibility_map = {
            uuid: rec.last_message_at_review_time
            for uuid, rec in records.items()
            if rec.last_message_at_review_time is not None
        }

        # Recursion guard: union the in-memory set with the persistent
        # disk store so daemon restarts don't drop our exclusion list.
        # Disk read is small (one parse per tick) and well under the
        # per-tick scan budget noted in core/transcripts.py.
        spawned_union = self._spawned_uuids | self._spawned_store.load_uuids()

        candidates = list_eligible_sessions(
            workspace=self._workspace,
            reviewed=eligibility_map,
            idle_threshold=idle_threshold,
            now=started_at,
            spawned_by_curator=spawned_union,
        )
        result.eligible = [m.session_uuid for m in candidates]

        spawn_count = 0
        for meta in candidates:
            if spawn_count >= MAX_SPAWNS_PER_TICK:
                result.skipped.append((meta.session_uuid, "tick-budget"))
                result.outcomes.append((meta.session_uuid, "tick-budget"))
                continue
            rec = records.get(meta.session_uuid)
            if self._in_cooldown(rec, started_at, cooldown):
                result.skipped.append((meta.session_uuid, "cooldown"))
                result.outcomes.append((meta.session_uuid, "cooldown"))
                continue
            with self._busy_lock:
                if meta.session_uuid in self._busy_per_session:
                    result.skipped.append((meta.session_uuid, "busy"))
                    result.outcomes.append((meta.session_uuid, "busy"))
                    continue
                self._busy_per_session.add(meta.session_uuid)
            try:
                spawn_count += 1
                outcome, session_summary = self._review_one(meta)
                self._reviewed.update(
                    meta.session_uuid,
                    success=True,
                    last_message_at_review_time=meta.last_message_timestamp,
                    outcome=outcome,
                    now=_utc_now(),
                )
                result.reviewed.append(meta.session_uuid)
                result.outcomes.append((meta.session_uuid, outcome))
                # Day 5: roll per-session counts into the tick total
                # so REPORT.md / /learning audit can surface them.
                result.summary.merge(session_summary)
            except Exception as exc:
                log.exception("Review failed for %s", meta.session_uuid)
                err_outcome = f"error: {exc}"
                # Anthropic-side quota errors aren't review failures —
                # they're a "you can't talk to the API right now" signal.
                # Recording them as failures would burn the per-session
                # 3-strikes budget and pin sessions until the user adds
                # new content, which is the wrong recovery. Instead: skip
                # the failure-bookkeeping write and abort the rest of
                # this tick (subsequent spawns would hit the same wall).
                if _is_rate_limit_error(err_outcome):
                    log.warning(
                        "Rate-limit hit for %s; aborting tick. (%s)",
                        meta.session_uuid, err_outcome,
                    )
                    result.skipped.append((meta.session_uuid, "rate-limited"))
                    result.outcomes.append((meta.session_uuid, "rate-limited"))
                    with self._busy_lock:
                        self._busy_per_session.discard(meta.session_uuid)
                    break
                self._reviewed.update(
                    meta.session_uuid,
                    success=False,
                    last_message_at_review_time=meta.last_message_timestamp,
                    outcome=err_outcome,
                    now=_utc_now(),
                )
                result.skipped.append((meta.session_uuid, err_outcome))
                result.outcomes.append((meta.session_uuid, err_outcome))
            finally:
                with self._busy_lock:
                    self._busy_per_session.discard(meta.session_uuid)

        finished_at = _utc_now()

        # Tick heartbeat — small, written every tick (even no-op
        # ticks) so /learning status can show "we are alive".
        state = _load_daemon_state()
        state["last_tick_at"] = _iso(finished_at)
        state["last_tick_eligible"] = len(result.eligible)
        state["last_tick_reviewed"] = len(result.reviewed)
        state["last_tick_skipped"] = len(result.skipped)
        _save_daemon_state(state)

        # v3b Day 2: relationships tick-promote pass. Runs AFTER
        # the lesson-curator work so any restart-recovered tokens
        # from this tick's startup pass are still in the registry.
        # Failures are logged and don't fail the tick.
        try:
            promote_results = self._relationships_curator.tick_promote_pending()
            if promote_results:
                promoted = sum(1 for r in promote_results if r.promoted)
                blocked = len(promote_results) - promoted
                log.info(
                    "relationships tick: %d attempted, %d promoted, %d blocked",
                    len(promote_results), promoted, blocked,
                )
        except Exception:
            log.exception("relationships.tick_promote_pending raised")

        # Per-tick REPORT.md + run.json — only when the tick did
        # something (eligible or skipped). No-op ticks would otherwise
        # spam the logs dir; the heartbeat above is enough for
        # liveness checks.
        if result.eligible or result.skipped:
            try:
                self._write_tick_report(started_at, finished_at, result)
            except Exception:
                log.exception("Could not write learning tick report")

        return result

    @property
    def relationships_curator(self):
        """v3b: accessor for the per-controller RelationshipsCurator.

        Returns the in-memory instance bound to this LearningController;
        Telegram and tests reach in here to invoke the turn-level
        ``process_user_turn`` entry point.
        """
        return self._relationships_curator

    def _write_tick_report(
        self,
        started_at: datetime,
        finished_at: datetime,
        result: TickResult,
    ) -> Path:
        """Persist the per-tick narrative + machine-readable record.

        Layout matches the existing curator's ``write_report`` shape
        (``<utc-iso>/REPORT.md`` + ``<utc-iso>/run.json``) so the
        future audit dashboard can iterate one logs root.
        """
        folder_name = _iso(started_at).replace(":", "")
        folder = learning_logs_dir() / folder_name
        folder.mkdir(parents=True, exist_ok=True)

        elapsed = (finished_at - started_at).total_seconds()
        s = result.summary
        md_lines: list[str] = [
            f"# Learning curator tick {folder_name}",
            "",
            f"- started_at: {_iso(started_at)}",
            f"- finished_at: {_iso(finished_at)}",
            f"- elapsed_seconds: {elapsed:.2f}",
            f"- eligible: {len(result.eligible)}",
            f"- reviewed (success): {len(result.reviewed)}",
            f"- skipped: {len(result.skipped)}",
            "",
            "## Write summary (Day 5)",
            "",
            f"- total written: {s.written}",
            f"- by class: {_format_count_dict(s.by_class) or '(none)'}",
            f"- by tier: {_format_count_dict(s.by_tier) or '(none)'}",
            f"- dedup-skipped (memory): {s.dedup_skipped}",
            f"- queue added (identity, no promotion): {s.queue_added}",
            f"- queue promoted (identity → USER): {s.queue_promoted}",
            f"- skill stage refused: {s.stage_refused}",
            "",
        ]
        if result.outcomes:
            md_lines.append("## Per-session outcomes")
            md_lines.append("")
            for uuid, outcome in result.outcomes:
                short = uuid[:8] if len(uuid) >= 8 else uuid
                md_lines.append(f"- `{short}` — {outcome}")
            md_lines.append("")
        # v3b Day 3a: relationships curator counters surfaced into
        # REPORT.md alongside lesson-curator stats. Present on every
        # tick so a steady "0 across the board" run is visible (a
        # zero-row reads as "the relationships path was healthy this
        # tick", not "the section is missing").
        rel_counters = self._relationships_curator.counters
        md_lines.append("## Relationships counters (since startup)")
        md_lines.append("")
        for name in (
            # 3a
            "add_staged",
            "delete_executed",
            "delete_missing",
            "cursor_collision",
            "hook_errors",
            # 3b
            "supersede_executed",
            "supersede_missing",
            "supersede_blocked_sensitive",
            "supersede_blocked_coherence",
            "ambiguous_emitted",
            "ambiguous_resolved",
            "ambiguous_dropped_unresolved",
            "ambiguous_dropped_unrelated",
            "disambiguation_back_edit",
            "restore_executed",
            "restore_missing",
            "restore_collision",
            # 4a (silent extraction)
            "extractor_runs",
            "extractor_errors",
            "extractor_facts_emitted",
            "extractor_facts_dropped_sensitive",
            "extractor_facts_dropped_dedup",
            "candidates_queued",
            "candidates_eligible",
            "candidates_approved",
            "candidates_rejected",
            "candidates_expired",
            "approve_blocked_sensitive",
            "approve_blocked_missing_qualifier",
        ):
            md_lines.append(f"- {name}: {rel_counters.get(name, 0)}")
        md_lines.append("")
        # v3a (Day 6): coherence flags section. Omitted entirely when
        # no flags fired this tick (per §3.4 — silent on COHERENT).
        if s.coherence_flags:
            md_lines.append("## Coherence flags")
            md_lines.append("")
            md_lines.append(
                f"- {s.coherence_flagged} INCOHERENT, "
                f"{s.coherence_near_miss} NEAR_MISS_REVIEW"
            )
            if s.coherence_by_reason:
                md_lines.append(
                    f"- by reason: "
                    f"{_format_count_dict(s.coherence_by_reason)}"
                )
            md_lines.append("")
            for flag in s.coherence_flags:
                short_uuid = (
                    flag["session_uuid"][:8]
                    if len(flag["session_uuid"]) >= 8
                    else flag["session_uuid"]
                )
                if flag["verdict"] == "INCOHERENT":
                    label = "FLAGGED"
                elif flag["verdict"] == "NEAR_MISS_REVIEW":
                    label = "NEAR_MISS"
                else:
                    label = flag["verdict"]
                reason_part = f"({flag['reason']}) " if flag["reason"] else ""
                preview = flag["lesson_preview"]
                md_lines.append(
                    f"- session `{short_uuid}`, lesson \"{preview}\":"
                )
                md_lines.append(
                    f"  {label} {reason_part}— {flag['explanation']}"
                )
            md_lines.append("")
        report_md = folder / "REPORT.md"
        report_md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

        run_json = folder / "run.json"
        payload = {
            "started_at": _iso(started_at),
            "finished_at": _iso(finished_at),
            "elapsed_seconds": elapsed,
            "eligible": result.eligible,
            "reviewed": result.reviewed,
            "skipped": [
                {"session_uuid": u, "reason": r}
                for u, r in result.skipped
            ],
            "outcomes": [
                {"session_uuid": u, "outcome": o}
                for u, o in result.outcomes
            ],
            "summary": s.to_dict(),
            "relationships_counters": dict(
                self._relationships_curator.counters
            ),
        }
        run_json.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False),
            encoding="utf-8",
        )
        return folder

    @staticmethod
    def _in_cooldown(
        rec: ReviewRecord | None,
        now: datetime,
        cooldown: timedelta,
    ) -> bool:
        """A session is in cooldown when its most recent attempt
        failed AND the cooldown window hasn't elapsed since that
        attempt. A successful review clears cooldown by definition
        (last_reviewed_at advances to last_review_attempt_at)."""
        if rec is None or rec.last_review_attempt_at is None:
            return False
        last_success = rec.last_reviewed_at
        if last_success is not None and last_success >= rec.last_review_attempt_at:
            return False
        return (now - rec.last_review_attempt_at) < cooldown

    def _review_one(self, meta: SessionMeta) -> tuple[str, WriteSummary]:
        """Single-session review. Delegates to the configured review
        function (default: ``_real_review``, which spawns ``claude -p``
        and runs the verifier). Tests inject a stub via ``review_fn``.

        Returns ``(outcome_str, summary)`` — the summary carries the
        per-session class/tier/dedup/queue counts (Day 5 addition)
        that ``_run_once`` aggregates into the tick-level total.

        The shadow-vs-live split lives inside ``_real_review`` (it
        calls ``learning_shadow_mode()`` to decide where verified
        lessons get written), so this dispatcher stays uniform.

        Also implements the **recursion guard scan-diff** (§4.6 of
        the research doc): each ``claude -p`` invocation by the
        review fork creates a new session JSONL in the same projects
        directory the curator scans for eligibility. We snapshot the
        directory's UUIDs before and after the review and add the
        diff to ``self._spawned_uuids`` — ``list_eligible_sessions``
        filters those out so the next tick can't pick up the
        curator's own review session as a review candidate.

        Backward compat: if a legacy review_fn returns just a string
        (pre-Day-5 interface), wrap it in an empty WriteSummary so
        old test stubs still work without modification.
        """
        projects_dir = claude_session_jsonl_dir(self._workspace)
        before = self._scan_projects_uuids(projects_dir)
        try:
            ret = self._review_fn(self._workspace, meta)
            if isinstance(ret, tuple):
                outcome, summary = ret
            else:
                # Legacy stub returning just a string — synthesize
                # an empty summary so the rest of the pipeline works.
                outcome, summary = ret, WriteSummary()
        finally:
            after = self._scan_projects_uuids(projects_dir)
            new_uuids = after - before
            if new_uuids:
                # Persist FIRST so a crash between the two updates
                # leaves the disk authoritative — better to over-filter
                # (a UUID known to disk but lost from memory still
                # exits via the union) than under-filter (a UUID in
                # memory but not on disk would vanish on restart).
                try:
                    self._spawned_store.add_many(
                        new_uuids, parent_session=meta.session_uuid,
                    )
                except OSError:
                    # Disk-write failure is logged but doesn't abort
                    # the review path — the in-memory set still
                    # protects this daemon's lifetime.
                    log.exception(
                        "Could not persist spawned UUIDs %s to %s",
                        sorted(new_uuids),
                        self._spawned_store._path,
                    )
                self._spawned_uuids.update(new_uuids)
                log.debug(
                    "Learning curator added spawned UUIDs to recursion "
                    "guard set: %s",
                    sorted(new_uuids),
                )
        # v3c Day 4a: silent extractor runs after the lesson reviewer
        # on the same session, sharing the loaded transcript. Failures
        # are isolated — extractor errors don't undermine the lesson
        # reviewer's success bookkeeping above. Sequential rather than
        # asyncio.gather because the lesson reviewer is sync and
        # restructuring the entire daemon to async for a marginal
        # latency win wasn't worth the blast radius (documented as
        # a deviation in the day-4a deliverables).
        try:
            self._run_silent_extractor(meta)
        except Exception:
            log.exception(
                "relationships extractor raised for %s",
                meta.session_uuid,
            )
            self._relationships_curator.increment_counter("extractor_errors")
        return outcome, summary

    def _run_silent_extractor(self, meta: SessionMeta) -> None:
        """v3c Day 4a: fire the relationships extractor against
        ``meta``'s session JSONL. No-op when the transcript is
        empty. Counter updates land on the curator so REPORT.md
        surfaces them under the existing relationships block."""
        from core.relationships.extractor import (
            extract_relationships,
        )
        messages = list(iter_messages(meta.jsonl_path))
        if not messages:
            return
        rel = self._relationships_curator
        rel.increment_counter("extractor_runs")
        try:
            result = asyncio.run(
                extract_relationships(
                    messages,
                    meta.session_uuid,
                    workspace=self._workspace,
                    candidate_store=rel.candidate_store,
                    relationships_store=rel.store,
                )
            )
        except Exception:
            log.exception(
                "extract_relationships raised for %s",
                meta.session_uuid,
            )
            rel.increment_counter("extractor_errors")
            return
        if result.error:
            rel.increment_counter("extractor_errors")
            return
        rel.increment_counter("extractor_facts_emitted", by=result.facts_emitted)
        rel.increment_counter(
            "extractor_facts_dropped_sensitive",
            by=result.facts_dropped_sensitive,
        )
        rel.increment_counter(
            "extractor_facts_dropped_dedup",
            by=result.facts_dropped_dedup,
        )
        rel.increment_counter("candidates_queued", by=result.facts_queued)
        if result.facts_queued:
            log.info(
                "relationships extractor queued %d fact(s) for sess %s",
                result.facts_queued, meta.session_uuid,
            )

    @staticmethod
    def _scan_projects_uuids(projects_dir: Path) -> set[str]:
        """Cheap UUID enumeration of the projects directory. Returns
        an empty set when the directory doesn't exist (fresh install)."""
        if not projects_dir.exists():
            return set()
        return {p.stem for p in projects_dir.glob("*.jsonl")}

    # ---------- /learning dispatch ----------

    async def handle_telegram(self, sub: str, args: list[str]) -> str:
        """Implement ``/learning`` subcommands: status, pause, resume,
        run, audit, coherence-audit."""
        sub = (sub or "status").lower()
        if sub == "status":
            return self._status_text()
        if sub == "pause":
            set_paused(True)
            return "Learning curator paused. /learning resume to start it again."
        if sub == "resume":
            set_paused(False)
            return "Learning curator resumed."
        if sub == "run":
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, self._run_once)
            return (
                f"Learning curator finished. "
                f"Eligible: {len(result.eligible)}, "
                f"reviewed: {len(result.reviewed)}, "
                f"skipped: {len(result.skipped)}."
            )
        if sub == "audit":
            return self._audit_text()
        if sub == "coherence-audit":
            # v3a Day 3 — re-run the judge over already-promoted
            # entries on demand. Async via the executor because the
            # judge spawns claude -p (potentially several seconds per
            # entry × N entries) and we don't want to block the
            # Telegram event loop.
            shadow_only = "--shadow-only" in args
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None, self._coherence_audit_text, shadow_only,
            )
        if sub == "relationships-dryrun":
            # v3b Day 1 — observation-only. Walks recent session
            # JSONLs, runs the trigger detector against each
            # role=user turn, prints what *would* trigger. No file
            # writes anywhere.
            last_n = _parse_last_n_sessions(args, default=30)
            return await self._relationships_dryrun_text(last_n)
        if sub == "relationships-restore":
            # v3b Day 3b — user-initiated reverse of a deletion.
            # Token-free: the user is the explicit caller (the
            # Telegram allow-list already auth-gated this surface).
            if not args:
                return (
                    "Usage: /learning relationships-restore <slug>"
                )
            slug = args[0].strip()
            if not slug:
                return (
                    "Usage: /learning relationships-restore <slug>"
                )
            try:
                result = self._relationships_curator.restore(slug)
            except Exception:
                log.exception("relationships.restore raised")
                return "⚠️ Restore failed; check the logs."
            return result.reply_text or "(no reply)"
        if sub == "relationships-pending":
            # v3c Day 4a — list pending candidates from the silent
            # extraction queue. Read-only.
            return self._relationships_pending_text()
        if sub == "relationships-approve":
            # v3c Day 4a — whole-person approve via slash command.
            # Per-fact toggle is dashboard-only.
            if not args:
                return (
                    "Usage: /learning relationships-approve <slug>"
                )
            slug = args[0].strip()
            if not slug:
                return (
                    "Usage: /learning relationships-approve <slug>"
                )
            try:
                result = self._relationships_curator.approve_candidate(slug)
            except Exception:
                log.exception("relationships.approve_candidate raised")
                return "⚠️ Approve failed; check the logs."
            reply_text = result.reply_text or "(no reply)"
            # v3c Day 4c: append the brain-cache invalidation hint
            # on success. Suppressible via
            # relationships.approval_hint_enabled (default true).
            if result.ok:
                from core.yaml_config import (
                    relationships_approval_hint_enabled,
                )
                if relationships_approval_hint_enabled():
                    reply_text = (
                        f"{reply_text} "
                        f"(Active in your next session — `/clear` "
                        f"to start fresh.)"
                    )
            return reply_text
        if sub == "relationships-reject":
            # v3c Day 4a — whole-slug reject via slash command.
            if not args:
                return (
                    "Usage: /learning relationships-reject <slug>"
                )
            slug = args[0].strip()
            if not slug:
                return (
                    "Usage: /learning relationships-reject <slug>"
                )
            try:
                result = self._relationships_curator.reject_candidate(slug)
            except Exception:
                log.exception("relationships.reject_candidate raised")
                return "⚠️ Reject failed; check the logs."
            return result.reply_text or "(no reply)"
        if sub == "relationships-digest":
            # v3c Day 4c — on-demand summary of pending candidates.
            # User-pull, no cron. Output mirrors the research doc §5.3
            # format with a glyph + ending CTA.
            return self._relationships_digest_text()
        return (
            "Usage: /learning [status|pause|resume|run|audit|"
            "coherence-audit [--shadow-only]|"
            "relationships-dryrun [--last-n-sessions N]|"
            "relationships-restore <slug>|"
            "relationships-pending|"
            "relationships-approve <slug>|"
            "relationships-reject <slug>|"
            "relationships-digest]"
        )

    def _relationships_digest_text(self) -> str:
        """Render the ``/learning relationships-digest`` reply.

        Output shape (per research doc §5.3):

            Pending relationships (3):
              ▲ sarah (coworker) — 2 sessions, 2 facts. Eligible.
              ▲ marco (?) — 1 session, 1 fact. Below threshold.
              ▲ mom (mom) — 1 session, 0 facts. (will drop on next sweep)

            Run `/learning relationships-approve <slug>` to approve from
            here, or use the dashboard for per-fact granularity.
        """
        try:
            views = self._relationships_curator.list_pending_candidates()
        except Exception:
            log.exception("relationships digest list raised")
            return "⚠️ Could not list pending candidates."
        if not views:
            return "No pending relationships."
        lines = [f"Pending relationships ({len(views)}):"]
        for v in views:
            qual = v.qualifier or "?"
            sess_word = "session" if v.session_count == 1 else "sessions"
            fact_word = "fact" if v.fact_count == 1 else "facts"
            if v.eligible:
                state = "Eligible."
            elif v.fact_count == 0:
                state = "(will drop on next sweep)"
            else:
                state = "Below threshold."
            lines.append(
                f"  ▲ {v.slug} ({qual}) — "
                f"{v.session_count} {sess_word}, "
                f"{v.fact_count} {fact_word}. {state}"
            )
        lines.append("")
        lines.append(
            "Run `/learning relationships-approve <slug>` to approve from "
            "here, or use the dashboard for per-fact granularity."
        )
        return "\n".join(lines)

    def _relationships_pending_text(self) -> str:
        """Render the ``/learning relationships-pending`` reply.

        Format from research doc §5.2:

            Pending relationships (3):
              sarah (coworker, 2 sess, 2 facts) — eligible
              marco (?, 1 sess, 1 fact) — below threshold
              mom (mom, 1 sess, 0 facts) — drop on next sweep
        """
        try:
            views = self._relationships_curator.list_pending_candidates()
        except Exception:
            log.exception("relationships pending list raised")
            return "⚠️ Could not list pending candidates."
        if not views:
            return "No pending relationships."
        lines = [f"Pending relationships ({len(views)}):"]
        for v in views:
            qual = v.qualifier or "?"
            sess = v.session_count
            facts = v.fact_count
            if v.eligible:
                state = "eligible"
            elif facts == 0:
                state = "drop on next sweep"
            else:
                state = "below threshold"
            lines.append(
                f"  {v.slug} ({qual}, {sess} sess, {facts} fact"
                f"{'s' if facts != 1 else ''}) — {state}"
            )
        return "\n".join(lines)

    async def _relationships_dryrun_text(self, last_n: int) -> str:
        """v3b Day 1 — observation-only.

        Walks the most recent ``last_n`` session JSONLs (by file
        mtime, descending), runs ``relationships_detect`` against
        every ``role=="user"`` turn, and returns a printable report
        of what *would* trigger. NO file writes anywhere — no
        consent token, no shadow file, no live file. Day 2 wires
        the detector into ``_dispatch_to_brain`` for real.

        Curator-owned JSONLs (review forks) are filtered out via
        the same content-prefix guard the daemon tick uses, so a
        prior review of "remember that …" inside a quoted lesson
        body doesn't show up as a fake trigger.
        """
        projects_dir = claude_session_jsonl_dir(self._workspace)
        if not projects_dir.exists():
            return (
                f"relationships-dryrun: no session JSONLs at "
                f"{projects_dir} (workspace has no Claude Code "
                f"history yet)."
            )

        all_jsonls = sorted(
            (p for p in projects_dir.glob("*.jsonl") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        recent = all_jsonls[: max(1, last_n)]

        lines: list[str] = []
        verdict_counts: Counter[str] = Counter()
        total_user_turns = 0
        triggered_turns = 0
        sessions_scanned = 0
        sessions_skipped_curator = 0

        for jsonl in recent:
            if _is_curator_owned(jsonl):
                sessions_skipped_curator += 1
                continue
            sessions_scanned += 1
            session_uuid = jsonl.stem
            short = session_uuid[:8]
            user_turn_index = 0
            for msg in iter_messages(jsonl):
                if msg.role != "user":
                    continue
                user_turn_index += 1
                total_user_turns += 1
                verdict = await relationships_detect(
                    msg.text,
                    role="user",
                    session_uuid=session_uuid,
                    turn_index=user_turn_index,
                    # Dryrun is observation-only: regex gate is enough
                    # to surface "this turn looks like a trigger." Full
                    # classifier pass would spawn one claude -p per
                    # candidate user turn — prohibitive for a CLI tool.
                    skip_classifier=True,
                )
                if verdict.verdict == "NONE":
                    continue
                triggered_turns += 1
                verdict_counts[verdict.verdict] += 1
                pattern = verdict.matched_pattern_id or "?"
                lines.append(
                    f"  Session {short} (T{user_turn_index}): "
                    f"MATCH {pattern} → {verdict.verdict}"
                )

        header = (
            f"relationships-dryrun (NO writes): "
            f"scanned {sessions_scanned} session(s) "
            f"(skipped {sessions_skipped_curator} curator-owned), "
            f"{total_user_turns} user turn(s)."
        )
        if not lines:
            body = "  (no triggers fired)"
        else:
            body = "\n".join(lines)

        if total_user_turns:
            pct = 100.0 * triggered_turns / total_user_turns
        else:
            pct = 0.0
        summary = (
            f"Summary: {triggered_turns}/{total_user_turns} "
            f"turns triggered ({pct:.2f}%). "
            f"{verdict_counts.get('ADD', 0)} ADD, "
            f"{verdict_counts.get('DELETE', 0)} DELETE, "
            f"{verdict_counts.get('SUPERSEDE', 0)} SUPERSEDE."
        )
        return f"{header}\n{body}\n{summary}"

    def run_now(self) -> TickResult:
        """Force a tick on demand. Synchronous; safe to call from
        a worker thread (e.g. dashboard force-run, smoke test)."""
        return self._run_once()

    def is_running(self) -> bool:
        with self._busy_lock:
            return bool(self._busy_per_session)

    def _audit_text(self) -> str:
        """User-facing audit surface — what entries the curator has
        promoted, plus the skip rate so we know whether the
        decline-too-large policy is starting to bite.

        Shadow mode is the v1 expectation: ``MEMORY-SHADOW.md`` lives
        alongside ``MEMORY.md`` and the audit lists every entry the
        curator has staged. The user reviews and either runs
        ``mv MEMORY-SHADOW.md MEMORY.md`` (manual flip), prunes
        unwanted entries by hand, or leaves it for the eval harness
        to gate. Once the curator flips to live mode, the audit also
        scopes MEMORY.md entries that start with the ``[learned``
        marker so the user can find curator-authored vs hand-written
        entries at a glance.
        """
        lines: list[str] = []

        shadow_path = _shadow_file(self._workspace)
        shadow_entries = _read_curator_entries(shadow_path)
        if shadow_path.exists():
            lines.append(
                f"Shadow entries ({len(shadow_entries)}) "
                f"@ {shadow_path.name}:"
            )
            if shadow_entries:
                for header, body in shadow_entries[:10]:
                    lines.append(f"  • {header}")
                if len(shadow_entries) > 10:
                    lines.append(
                        f"  …{len(shadow_entries) - 10} more (see {shadow_path})"
                    )
            else:
                lines.append("  (no entries yet)")
        else:
            lines.append("No MEMORY-SHADOW.md yet — curator hasn't promoted anything.")

        # Live entries (curator-authored, marked with the [learned tag).
        live_path = memories_dir(self._workspace) / "MEMORY.md"
        live_entries = _read_curator_entries(live_path)
        if live_entries:
            lines.append("")
            lines.append(
                f"Live entries ({len(live_entries)}) in MEMORY.md "
                f"with [learned ...] tag:"
            )
            for header, body in live_entries[:10]:
                lines.append(f"  • {header}")
            if len(live_entries) > 10:
                lines.append(f"  …{len(live_entries) - 10} more")

        # Skip-rate surface — Day 3 spec: "track skip rate so we can
        # decide later whether to add truncation/summarization. >10%
        # over a week = signal."
        records = self._reviewed.load()
        too_large = sum(
            1 for r in records.values()
            if r.outcome.startswith("skipped: transcript too large")
        )
        successful = sum(
            1 for r in records.values()
            if r.last_reviewed_at is not None
            and not r.outcome.startswith("error:")
        )
        total_reviewed = sum(
            1 for r in records.values() if r.last_reviewed_at is not None
        )
        if total_reviewed > 0:
            pct = (100 * too_large) // total_reviewed
            lines.append("")
            lines.append(
                f"Skip rate (transcript too large): {too_large}/{total_reviewed} "
                f"({pct}% of reviewed sessions). "
                f"Threshold for revisit: >10% sustained."
            )
            if pct > 10:
                lines.append(
                    "  ⚠ Above 10%; consider truncation or "
                    "summarization strategy."
                )

        # ----- Day 5: curator-authored skills surface -----
        # Walk the live tree AND the staging tree for skills carrying
        # ``origin: learning-curator`` (or migration variant) in
        # frontmatter. The user wants one place to see "what skills
        # has the curator produced and what's still staged".
        curator_skills_live, curator_skills_staged = self._scan_curator_skills()
        if curator_skills_live or curator_skills_staged:
            lines.append("")
            lines.append("Curator-authored skills:")
            if curator_skills_live:
                lines.append(f"  Live ({len(curator_skills_live)}):")
                for name, origin in curator_skills_live[:10]:
                    lines.append(f"    • {name}  [origin: {origin}]")
                if len(curator_skills_live) > 10:
                    lines.append(
                        f"    …{len(curator_skills_live) - 10} more"
                    )
            if curator_skills_staged:
                lines.append(
                    f"  Staged ({len(curator_skills_staged)}) "
                    f"— flip with `vexis-skill flip-shadow`:"
                )
                for name, origin in curator_skills_staged[:10]:
                    lines.append(f"    • {name}  [origin: {origin}]")
                if len(curator_skills_staged) > 10:
                    lines.append(
                        f"    …{len(curator_skills_staged) - 10} more"
                    )

        # ----- Day 5: USER candidate queue surface -----
        try:
            queue = UserCandidateStore(user_candidates_path())
            pending = queue.list_pending()
            promoted = queue.list_promoted()
        except OSError:
            pending = []
            promoted = []
        if pending or promoted:
            lines.append("")
            lines.append(
                f"USER candidate queue: {len(pending)} pending, "
                f"{len(promoted)} promoted"
            )
            if pending:
                now = _utc_now()
                for c in pending[:8]:
                    distinct = len(c.distinct_session_uuids_within(
                        DEFAULT_WINDOW, now=now,
                    ))
                    days_until_expiry = max(
                        0,
                        DEFAULT_WINDOW.days
                        - (now - c.last_seen).days,
                    )
                    short = c.claim if len(c.claim) <= 80 else c.claim[:77] + "..."
                    lines.append(
                        f"  • {short!r} "
                        f"({distinct}/{DEFAULT_PROMOTION_THRESHOLD} sessions, "
                        f"~{days_until_expiry}d until expiry)"
                    )
                if len(pending) > 8:
                    lines.append(f"  …{len(pending) - 8} more pending")

        # ----- Day 5: recent dedup-skipped writes -----
        # Mine the last N tick reports for dedup_skipped > 0 so the
        # user can spot when the dedup gate is firing often (could
        # indicate the LLM keeps proposing duplicates → prompt-tune).
        recent_dedup, ticks_scanned = self._recent_dedup_skips(window_ticks=24)
        if ticks_scanned > 0:
            lines.append("")
            lines.append(
                f"Dedup gate (last {ticks_scanned} tick reports): "
                f"{recent_dedup} candidate(s) skipped as duplicates."
            )

        # ----- Day 6 (v3a): recent coherence flags -----
        # Mine the last N tick reports for coherence_flagged /
        # coherence_near_miss totals + by-reason breakdown. Surfaces
        # the v3a flag rate so the user can spot a prompt-calibration
        # issue (>50% flag rate → prompt is over-eager; <5% over a
        # week with normal volume → prompt is under-eager).
        coh_flagged, coh_near_miss, coh_by_reason, coh_ticks = (
            self._recent_coherence_flags(window_ticks=24)
        )
        if coh_ticks > 0:
            lines.append("")
            if coh_flagged or coh_near_miss:
                lines.append(
                    f"Coherence flags (last {coh_ticks} tick reports): "
                    f"{coh_flagged} INCOHERENT, "
                    f"{coh_near_miss} NEAR_MISS_REVIEW."
                )
                if coh_by_reason:
                    for reason, count in sorted(coh_by_reason.items()):
                        lines.append(f"  • {reason}: {count}")
            else:
                lines.append(
                    f"Coherence flags (last {coh_ticks} tick reports): "
                    f"0 entries flagged."
                )

        # ----- Tier distribution (audit A2 deferred-fix instrument) -----
        # Aggregate by_tier counts from run.json across the last N
        # ticks. Surfaces "did S1 actually get picked, or is it stuck
        # at 0?" — the metric the v2-hermes-verification audit named
        # as the signal for whether the deferred two-pass-review fix
        # is needed. If S1 stays at 0 over a soak week, the LLM is
        # systematically falling back to S2/S3 because it can't see
        # SKILL.md bodies; we'd then lift the A2 fix into v3.
        tier_counts, tier_ticks = self._recent_tier_distribution(window_ticks=72)
        if tier_ticks > 0:
            lines.append("")
            ordered_keys = ("S1", "S2", "S3", "MEM", "USER")
            total = sum(tier_counts.values())
            if total == 0:
                lines.append(
                    f"Tier distribution (last {tier_ticks} tick reports): "
                    f"no writes yet."
                )
            else:
                parts = []
                for k in ordered_keys:
                    n = tier_counts.get(k, 0)
                    parts.append(f"{k}={n}")
                # Surface any tiers we didn't enumerate explicitly
                # (defensive — keeps the audit honest if a future
                # tier name lands without an audit update).
                for k in sorted(set(tier_counts) - set(ordered_keys)):
                    parts.append(f"{k}={tier_counts[k]}")
                lines.append(
                    f"Tier distribution (last {tier_ticks} tick reports, "
                    f"{total} writes): {', '.join(parts)}"
                )
                # Flag the audit-A2 signal when S1 stays at 0 with
                # non-trivial PROCEDURAL volume — the curator is
                # avoiding patches and minting S2/S3 instead.
                s1 = tier_counts.get("S1", 0)
                s_total = sum(tier_counts.get(k, 0) for k in ("S1", "S2", "S3"))
                if s1 == 0 and s_total >= 5:
                    lines.append(
                        f"  ⚠ S1 at 0 across {s_total} procedural writes — "
                        f"see v2-hermes-verification.md A2 (LLM cannot see "
                        f"SKILL.md bodies; consider two-pass review)."
                    )

        return "\n".join(lines) if lines else "Nothing to audit yet."

    def _scan_curator_skills(self) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        """Return ``(live, staged)`` lists of (name, origin) tuples
        for every SKILL.md whose YAML frontmatter carries an
        ``origin: learning-curator*`` value.

        Reads the live tree and the ``.shadow/`` staging tree. The
        ``origin`` value can be ``learning-curator`` (Day 2 spec) or
        ``learning-curator-migration`` (Day 4 migration script).
        """
        live: list[tuple[str, str]] = []
        staged: list[tuple[str, str]] = []
        live_root = skills_dir(self._workspace)
        # Live tree
        for meta in discover_skills(live_root):
            origin = meta.raw_frontmatter.get("origin")
            if isinstance(origin, str) and origin.startswith("learning-curator"):
                live.append((meta.name, origin))
        # Staging tree — walk .shadow/ manually since iter_skill_dirs
        # excludes dotfile dirs (the whole point of the staging
        # location). Each SKILL.md under .shadow/ is a candidate.
        shadow_root = live_root / ".shadow"
        if shadow_root.exists():
            for skill_md in shadow_root.rglob("SKILL.md"):
                try:
                    content = skill_md.read_text(encoding="utf-8")
                except OSError:
                    continue
                meta = parse_skill_md(content)
                if meta is None:
                    continue
                origin = meta.raw_frontmatter.get("origin")
                if isinstance(origin, str) and origin.startswith("learning-curator"):
                    staged.append((meta.name, origin))
        return sorted(live), sorted(staged)

    def _recent_dedup_skips(self, *, window_ticks: int) -> tuple[int, int]:
        """Sum ``summary.dedup_skipped`` across the most recent
        ``window_ticks`` tick-report run.json files. Returns
        ``(total_dedup_count, ticks_actually_scanned)``."""
        logs_root = learning_logs_dir()
        if not logs_root.exists():
            return 0, 0
        # Tick directories are named with sortable utc-iso (with
        # colons stripped); sort descending → most recent first.
        tick_dirs = sorted(
            (p for p in logs_root.iterdir() if p.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        )[:window_ticks]
        total = 0
        scanned = 0
        for d in tick_dirs:
            run_json = d / "run.json"
            try:
                payload = json.loads(run_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            scanned += 1
            summary = payload.get("summary") if isinstance(payload, dict) else None
            if isinstance(summary, dict):
                total += int(summary.get("dedup_skipped", 0) or 0)
        return total, scanned

    def _recent_coherence_flags(
        self, *, window_ticks: int
    ) -> tuple[int, int, dict[str, int], int]:
        """Aggregate ``summary.coherence`` counts across the most
        recent ``window_ticks`` tick-report run.json files. Returns
        ``(flagged_total, near_miss_total, by_reason_dict,
        ticks_actually_scanned)``.

        Mirrors ``_recent_dedup_skips`` / ``_recent_tier_distribution``
        — same iteration shape, same JSON-parse tolerance. The
        run.json schema already includes ``summary.coherence`` (per
        ``WriteSummary.to_dict``), so no schema change required.

        Used by ``_audit_text`` (v3a Day 6) to surface the rolling
        flag rate. The user reads this to spot prompt-calibration
        drift: >50% flag rate over a soak week → prompt is over-
        eager; <5% with normal volume → under-eager (revisit C2).
        """
        logs_root = learning_logs_dir()
        if not logs_root.exists():
            return 0, 0, {}, 0
        tick_dirs = sorted(
            (p for p in logs_root.iterdir() if p.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        )[:window_ticks]
        flagged_total = 0
        near_miss_total = 0
        by_reason: dict[str, int] = {}
        scanned = 0
        for d in tick_dirs:
            run_json = d / "run.json"
            try:
                payload = json.loads(run_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            scanned += 1
            summary = payload.get("summary") if isinstance(payload, dict) else None
            if not isinstance(summary, dict):
                continue
            coh = summary.get("coherence")
            if not isinstance(coh, dict):
                continue
            try:
                flagged_total += int(coh.get("flagged", 0) or 0)
            except (TypeError, ValueError):
                pass
            try:
                near_miss_total += int(coh.get("near_miss", 0) or 0)
            except (TypeError, ValueError):
                pass
            reasons = coh.get("by_reason")
            if isinstance(reasons, dict):
                for reason, count in reasons.items():
                    if not isinstance(reason, str):
                        continue
                    try:
                        n = int(count)
                    except (TypeError, ValueError):
                        continue
                    by_reason[reason] = by_reason.get(reason, 0) + n
        return flagged_total, near_miss_total, by_reason, scanned

    def _recent_tier_distribution(
        self, *, window_ticks: int
    ) -> tuple[dict[str, int], int]:
        """Aggregate ``summary.by_tier`` counts across the most recent
        ``window_ticks`` tick-report run.json files. Returns
        ``({tier: count, ...}, ticks_actually_scanned)``.

        Implementation mirrors ``_recent_dedup_skips`` — same iteration
        shape, same JSON-parse tolerance. The schema is already
        comprehensive (``summary.to_dict()`` includes ``by_tier``);
        no run.json schema change required.

        Used by ``_audit_text`` to surface the S1/S2/S3/MEM/USER
        distribution; specifically named in v2-hermes-verification.md
        as the metric for whether the deferred A2 fix becomes
        v3 follow-up (S1 stuck at 0 → two-pass-review needed).
        """
        logs_root = learning_logs_dir()
        if not logs_root.exists():
            return {}, 0
        tick_dirs = sorted(
            (p for p in logs_root.iterdir() if p.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        )[:window_ticks]
        totals: dict[str, int] = {}
        scanned = 0
        for d in tick_dirs:
            run_json = d / "run.json"
            try:
                payload = json.loads(run_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            scanned += 1
            summary = payload.get("summary") if isinstance(payload, dict) else None
            if not isinstance(summary, dict):
                continue
            by_tier = summary.get("by_tier")
            if not isinstance(by_tier, dict):
                continue
            for tier, count in by_tier.items():
                if not isinstance(tier, str):
                    continue
                try:
                    n = int(count)
                except (TypeError, ValueError):
                    continue
                totals[tier] = totals.get(tier, 0) + n
        return totals, scanned

    def _recent_class_distribution(
        self, *, window_ticks: int
    ) -> tuple[dict[str, int], int]:
        """Aggregate ``summary.by_class`` counts across the most recent
        ``window_ticks`` tick-report run.json files. Returns
        ``({class: count, ...}, ticks_actually_scanned)``.

        Twin of ``_recent_tier_distribution`` — same iteration shape,
        same JSON-parse tolerance, just keyed off ``summary.by_class``
        (PROCEDURAL / IDENTITY / SITUATIONAL) instead of
        ``summary.by_tier``. Used by the Step 15 dashboard to render
        the by-class half of the distribution panel.
        """
        logs_root = learning_logs_dir()
        if not logs_root.exists():
            return {}, 0
        tick_dirs = sorted(
            (p for p in logs_root.iterdir() if p.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        )[:window_ticks]
        totals: dict[str, int] = {}
        scanned = 0
        for d in tick_dirs:
            run_json = d / "run.json"
            try:
                payload = json.loads(run_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            scanned += 1
            summary = payload.get("summary") if isinstance(payload, dict) else None
            if not isinstance(summary, dict):
                continue
            by_class = summary.get("by_class")
            if not isinstance(by_class, dict):
                continue
            for cls, count in by_class.items():
                if not isinstance(cls, str):
                    continue
                try:
                    n = int(count)
                except (TypeError, ValueError):
                    continue
                totals[cls] = totals.get(cls, 0) + n
        return totals, scanned

    # v3a Day 3 — manual /learning coherence-audit command. Re-runs
    # the judge over already-promoted entries (shadow files by
    # default; live tree's [learned-tagged entries when not
    # --shadow-only) in degraded mode (no transcript window — the
    # source-session JSONL linkage isn't preserved on already-
    # promoted entries; see §5.5). Useful for retroactively checking
    # v1-era entries that were promoted before v3a existed and for
    # spot-checking shadow contents on demand.

    _COHERENCE_AUDIT_MAX_ENTRIES_PER_FILE = 30
    _COHERENCE_AUDIT_MAX_REPLY_LINES = 80

    def _coherence_audit_text(self, shadow_only: bool) -> str:
        """Re-run the coherence judge on already-promoted entries.

        ``shadow_only=False`` also walks live MEMORY.md /
        USER.md curator-authored entries (those marked with
        ``[learned``); ``shadow_only=True`` restricts to the shadow
        files. Caller (handle_telegram) already runs this in an
        executor so the per-entry judge calls don't block the
        event loop.

        Output is a chat-friendly summary capped at
        ``_COHERENCE_AUDIT_MAX_REPLY_LINES`` lines so it fits in
        a single Telegram message; the full per-entry detail also
        lands in a structured log entry under
        ``learning_logs_dir() / coherence-audit / <utc>.json``
        so the user can inspect later.
        """
        targets: list[tuple[str, Path]] = []
        memories_root = memories_dir(self._workspace)
        for name in (SHADOW_FILE_NAME, USER_SHADOW_FILE_NAME):
            p = memories_root / name
            if p.exists():
                targets.append((name, p))
        if not shadow_only:
            for name in ("MEMORY.md", "USER.md"):
                p = memories_root / name
                if p.exists():
                    targets.append((name, p))
        if not targets:
            return "No shadow or live curator-authored files to audit."

        results: list[dict] = []
        all_entries: list[tuple[str, dict]] = []  # (file_label, parsed_entry)
        for label, path in targets:
            entries = _parse_curator_entries(path)
            cap = self._COHERENCE_AUDIT_MAX_ENTRIES_PER_FILE
            for entry in entries[:cap]:
                all_entries.append((label, entry))
        if not all_entries:
            return (
                "Audited "
                + ", ".join(label for label, _ in targets)
                + " — no curator-authored entries found."
            )

        for label, entry in all_entries:
            verdict = run_coherence_judge(
                self._workspace,
                {
                    "class": entry.get("class") or "?",
                    "lesson": entry["lesson"],
                    "scope": entry.get("scope", ""),
                    "evidence": entry.get("evidence", ""),
                    "tier": entry.get("tier"),
                },
                [],  # degraded mode — no source transcript
            )
            results.append({
                "file": label,
                "lesson_preview": (
                    entry["lesson"][: _LESSON_PREVIEW_CHARS - 3] + "..."
                    if len(entry["lesson"]) > _LESSON_PREVIEW_CHARS
                    else entry["lesson"]
                ),
                "verdict": verdict.verdict,
                "reason": verdict.reason,
                "explanation": verdict.explanation or "",
                "degraded": verdict.degraded,
            })

        # Persist the structured detail so the user can read the
        # full output later if the chat reply is truncated.
        log_dir = learning_logs_dir() / "coherence-audit"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / (_iso(_utc_now()).replace(":", "") + ".json")
        log_path.write_text(
            json.dumps(
                {"audited_at": _iso(_utc_now()),
                 "shadow_only": shadow_only,
                 "results": results},
                indent=2, sort_keys=True, ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        flagged = [r for r in results if r["verdict"] == "INCOHERENT"]
        near_miss = [r for r in results if r["verdict"] == "NEAR_MISS_REVIEW"]
        coherent = [r for r in results if r["verdict"] == "COHERENT"]
        lines = [
            f"Coherence audit: {len(results)} entries judged "
            f"(degraded mode — no transcript window).",
            f"  COHERENT: {len(coherent)}",
            f"  NEAR_MISS_REVIEW: {len(near_miss)}",
            f"  INCOHERENT: {len(flagged)}",
            f"  Full log: {log_path}",
        ]
        if flagged or near_miss:
            lines.append("")
            lines.append("Flagged entries:")
            for r in (flagged + near_miss)[: self._COHERENCE_AUDIT_MAX_REPLY_LINES]:
                label = "FLAGGED" if r["verdict"] == "INCOHERENT" else "NEAR_MISS"
                reason = f"({r['reason']}) " if r["reason"] else ""
                lines.append(
                    f"  [{r['file']}] {label} {reason}— "
                    f"\"{r['lesson_preview']}\""
                )
        return "\n".join(lines)

    # ----- Step 15: dashboard surface ------------------------------

    _DASHBOARD_DISTRIBUTION_WINDOW_TICKS = 84   # ~7d at 5-min cadence
    _DASHBOARD_RATES_WINDOW_TICKS = 24          # ~2h at 5-min cadence
    _DASHBOARD_ACTIVITY_LIMIT = 20

    def dashboard_payload(self) -> dict:
        """Return the Learning-tab combined-state payload.

        Shape per ``.plans/dashboard-learning-tab.md`` §1. Everything
        is on-disk reads; no contention with the running tick because
        the only mutators (the tick itself, ``judge_entry``) write
        through atomic temp+rename. The dashboard's payload builder
        (``WebDashboard._learning_payload``) wraps this with the
        archive-curator row before serializing.
        """
        state = _load_daemon_state()
        last_tick = state.get("last_tick_at")
        paused = bool(state.get("paused"))

        # Pull the most-recent tick's run.json once — used both for
        # the "learning" curator row's summary and the coherence row.
        last_tick_summary, coherence_row_summary = (
            self._format_last_tick_summaries()
        )

        # Curators panel — learning + nested coherence row. Archive
        # row is added by the dashboard glue (it owns the archive
        # controller).
        interval_minutes = learning_tick_interval_minutes()
        next_eligible: str | None = None
        last_tick_dt = _parse_iso(last_tick)
        if last_tick_dt is not None:
            next_eligible = _iso(
                last_tick_dt + timedelta(minutes=interval_minutes)
            )
        curators = [
            {
                "name": "learning",
                "nested_under": None,
                "enabled": True,
                "paused": paused,
                "running": self.is_running(),
                "last_run_at": last_tick,
                "next_eligible_at": next_eligible,
                "summary": last_tick_summary,
                "interval_label": f"{interval_minutes}m",
            },
            {
                "name": "coherence",
                "nested_under": "learning",
                "enabled": True,
                "paused": False,
                "running": False,
                "last_run_at": last_tick,
                "next_eligible_at": None,
                "summary": coherence_row_summary,
                "interval_label": "inline",
            },
        ]

        # Shadow / staged-skill entries — used both directly (for the
        # coherence-flags panel) and as the join source for the
        # merged activity feed.
        memories_root = memories_dir(self._workspace)
        shadow_entries: list[dict] = []
        for source_label, filename in (
            ("memory-shadow", SHADOW_FILE_NAME),
            ("user-shadow", USER_SHADOW_FILE_NAME),
        ):
            path = memories_root / filename
            for parsed in _parse_curator_entries_annotated(path):
                shadow_entries.append({"source": source_label, **parsed})
        # Staged skills — each may carry a [learned ...] preamble in
        # the SKILL.md body. Walk and parse what's there.
        shadow_root = skills_dir(self._workspace) / ".shadow"
        if shadow_root.exists():
            for skill_md in shadow_root.rglob("SKILL.md"):
                try:
                    content = skill_md.read_text(encoding="utf-8")
                except OSError:
                    continue
                # Strip frontmatter so the parser sees the body's
                # [learned ...] preamble cleanly.
                body = content
                if body.startswith("---"):
                    end = body.find("\n---", 3)
                    if end > 0:
                        body = body[end + 4:]
                tmp_entries = _parse_curator_entries_annotated_text(body)
                if not tmp_entries:
                    continue
                # Skill name = parent directory under .shadow/.
                try:
                    skill_name = skill_md.parent.relative_to(shadow_root).parts[0]
                except (IndexError, ValueError):
                    skill_name = skill_md.parent.name
                for parsed in tmp_entries:
                    shadow_entries.append({
                        "source": f"skill-shadow:{skill_name}",
                        **parsed,
                    })

        # Build a session-prefix → entry index for the activity-feed
        # join. First entry per prefix wins (entries are appended
        # newest-tail in shadow files; iterating in document order
        # preserves "first observation of this session" semantics).
        by_session: dict[str, dict] = {}
        for e in shadow_entries:
            sp = e.get("source_session_prefix")
            if isinstance(sp, str) and sp and sp not in by_session:
                by_session[sp] = e

        # Recent activity feed — merged outcome × shadow-entry view.
        recent_activity = self._build_activity_feed(
            by_session, limit=self._DASHBOARD_ACTIVITY_LIMIT,
        )

        # Distribution (last 7d default)
        class_counts, class_ticks = self._recent_class_distribution(
            window_ticks=self._DASHBOARD_DISTRIBUTION_WINDOW_TICKS,
        )
        tier_counts, tier_ticks = self._recent_tier_distribution(
            window_ticks=self._DASHBOARD_DISTRIBUTION_WINDOW_TICKS,
        )
        distribution = {
            "window_ticks": max(class_ticks, tier_ticks),
            "by_class": class_counts,
            "by_tier": tier_counts,
            "a2_watch": (
                tier_counts.get("S1", 0) == 0
                and sum(tier_counts.get(k, 0) for k in ("S1", "S2", "S3")) >= 5
            ),
        }

        # Rates (last 2h default)
        dedup_total, dedup_ticks = self._recent_dedup_skips(
            window_ticks=self._DASHBOARD_RATES_WINDOW_TICKS,
        )
        coh_flagged, coh_near_miss, coh_by_reason, coh_ticks = (
            self._recent_coherence_flags(
                window_ticks=self._DASHBOARD_RATES_WINDOW_TICKS,
            )
        )
        rates = {
            "window_ticks_scanned": max(dedup_ticks, coh_ticks),
            "dedup_skipped": dedup_total,
            "coherence_flagged": coh_flagged,
            "coherence_near_miss": coh_near_miss,
            "coherence_by_reason": coh_by_reason,
        }

        # USER candidate queue
        try:
            queue = UserCandidateStore(user_candidates_path())
            pending = queue.list_pending()
            promoted = queue.list_promoted()
        except OSError:
            pending = []
            promoted = []
        now = _utc_now()
        user_candidates = {
            "pending": [
                {
                    "claim_preview": (
                        c.claim[:160] + "..." if len(c.claim) > 160 else c.claim
                    ),
                    "distinct_sessions": len(
                        c.distinct_session_uuids_within(DEFAULT_WINDOW, now=now)
                    ),
                    "threshold": DEFAULT_PROMOTION_THRESHOLD,
                    "first_seen": _iso(c.first_seen),
                    "last_seen": _iso(c.last_seen),
                    "days_until_expiry": max(
                        0,
                        DEFAULT_WINDOW.days - (now - c.last_seen).days,
                    ),
                }
                for c in pending
            ],
            "promoted_count": len(promoted),
        }

        # Pending-review (denormalized filter of shadow_entries)
        coherence_pending_review = [
            self._strip_dashboard_entry_for_payload(e)
            for e in shadow_entries
            if e.get("coherence_verdict") in ("INCOHERENT", "NEAR_MISS_REVIEW")
        ]

        # Curator-authored skills (live + staged)
        live_skills, staged_skills = self._scan_curator_skills()
        curator_skills = {
            "live": [{"name": n, "origin": o} for n, o in live_skills],
            "staged": [{"name": n, "origin": o} for n, o in staged_skills],
        }

        # Models
        models = {
            "brain": model_brain(),
            "learning_review": model_learning_review(),
            "coherence_judge": model_coherence_judge(),
            "migration_classifier": model_migration_classifier(),
        }

        return {
            "curators": curators,
            "recent_activity": recent_activity,
            "shadow_entries": [
                self._strip_dashboard_entry_for_payload(e)
                for e in shadow_entries
            ],
            "distribution": distribution,
            "rates": rates,
            "user_candidates": user_candidates,
            "coherence_pending_review": coherence_pending_review,
            "curator_skills": curator_skills,
            "models": models,
        }

    @staticmethod
    def _strip_dashboard_entry_for_payload(entry: dict) -> dict:
        """Trim the parsed-shadow dict for JSON serialization.

        Drops nothing currently — but reserves a single point of
        ``dict → wire-shape`` translation so future schema
        changes (e.g., truncating long lessons, hiding scope from
        the dashboard) land in one place.
        """
        return {
            "source": entry.get("source"),
            "lesson": entry.get("lesson"),
            "lesson_preview": _ellipsize(entry.get("lesson") or "", 200),
            "class": entry.get("class"),
            "tier": entry.get("tier"),
            "scope": entry.get("scope"),
            "evidence": entry.get("evidence"),
            "coherence_verdict": entry.get("coherence_verdict"),
            "coherence_reason": entry.get("coherence_reason"),
            "coherence_explanation": entry.get("coherence_explanation"),
            "outcome_marker": entry.get("outcome_marker"),
            "source_session_prefix": entry.get("source_session_prefix"),
            "entry_id": entry.get("entry_id"),
        }

    def _format_last_tick_summaries(self) -> tuple[str, str]:
        """Return ``(learning_row_summary, coherence_row_summary)``
        formatted from the most-recent tick's ``run.json``. Both
        strings are one-line; "—" if no tick has happened yet.
        """
        logs_root = learning_logs_dir()
        if not logs_root.exists():
            return ("no ticks yet", "no ticks yet")
        tick_dirs = sorted(
            (p for p in logs_root.iterdir() if p.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        )
        if not tick_dirs:
            return ("no ticks yet", "no ticks yet")
        run_json = tick_dirs[0] / "run.json"
        try:
            payload = json.loads(run_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ("last tick log unreadable", "last tick log unreadable")
        summary = payload.get("summary") if isinstance(payload, dict) else None
        if not isinstance(summary, dict):
            return ("last tick had no summary block", "no judged lessons")
        written = int(summary.get("written", 0) or 0)
        by_class = summary.get("by_class") or {}
        by_tier = summary.get("by_tier") or {}
        class_part = (
            ", ".join(f"{k}={v}" for k, v in sorted(by_class.items()))
            if by_class else "—"
        )
        tier_part = (
            ", ".join(f"{k}={v}" for k, v in sorted(by_tier.items()))
            if by_tier else "—"
        )
        learning_summary = (
            f"wrote {written} ({class_part}); {tier_part}"
            if written
            else f"reviewed {payload.get('reviewed', 0)}, "
                 f"nothing-to-save / rejected / cooldown"
        )
        coh = summary.get("coherence")
        if isinstance(coh, dict):
            judged = (
                int(coh.get("flagged", 0) or 0)
                + int(coh.get("near_miss", 0) or 0)
                + int(coh.get("coherent", 0) or 0)
            )
            coherence_summary = (
                f"{coh.get('flagged', 0)} flagged, "
                f"{coh.get('near_miss', 0)} near-miss "
                f"across {judged} lessons judged"
            )
        else:
            coherence_summary = "no judged lessons in last tick"
        return learning_summary, coherence_summary

    def _build_activity_feed(
        self, by_session: dict[str, dict], *, limit: int
    ) -> list[dict]:
        """Walk recent tick ``run.json`` files and produce the merged
        activity feed.

        For each outcome in ``run.json``, attempt a join by 8-char
        session-UUID prefix into ``by_session``. Hits get
        ``lesson_preview`` / ``class`` / ``tier`` / etc. populated
        from the shadow entry; misses (pre-instrumentation entries
        or non-write outcomes) leave those fields null.
        """
        logs_root = learning_logs_dir()
        if not logs_root.exists():
            return []
        # Look back farther than `limit` because some ticks have
        # zero outcomes — the limit is on rendered rows, not ticks
        # scanned.
        tick_dirs = sorted(
            (p for p in logs_root.iterdir() if p.is_dir()),
            key=lambda p: p.name,
            reverse=True,
        )[: max(limit * 4, 16)]
        rows: list[dict] = []
        for d in tick_dirs:
            run_json = d / "run.json"
            try:
                payload = json.loads(run_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            tick_at = payload.get("started_at") if isinstance(payload, dict) else None
            outcomes = payload.get("outcomes") if isinstance(payload, dict) else None
            if not isinstance(outcomes, list):
                continue
            for o in outcomes:
                if not isinstance(o, dict):
                    continue
                detail = str(o.get("outcome") or "")
                session_uuid = str(o.get("session_uuid") or "")
                outcome_kind = _classify_outcome(detail)
                # Skip cooldowns from the feed — they're not actions,
                # just "next tick we'll look again." 200 cooldowns per
                # tick swamp the panel otherwise.
                if outcome_kind == "cooldown":
                    continue
                prefix = session_uuid[:8] if session_uuid else ""
                joined = by_session.get(prefix) if prefix else None
                rows.append({
                    "tick_folder": d.name,
                    "tick_at": tick_at,
                    "session_uuid_prefix": prefix or None,
                    "outcome": outcome_kind,
                    "outcome_detail": detail,
                    "lesson_preview": (
                        _ellipsize(joined.get("lesson") or "", 200)
                        if joined else None
                    ),
                    "class": joined.get("class") if joined else None,
                    "tier": joined.get("tier") if joined else None,
                    "source": joined.get("source") if joined else None,
                    "coherence_verdict": (
                        joined.get("coherence_verdict") if joined else None
                    ),
                    "coherence_reason": (
                        joined.get("coherence_reason") if joined else None
                    ),
                    "outcome_marker": (
                        joined.get("outcome_marker") if joined else None
                    ),
                    "entry_id": joined.get("entry_id") if joined else None,
                })
                if len(rows) >= limit:
                    return rows
        return rows

    def judge_entry(self, entry: dict) -> dict:
        """Run ``run_coherence_judge`` on a single entry and return
        ``{verdict, reason, explanation, degraded, judged_at}``.

        Synchronous — the caller (the dashboard route handler) wraps
        this in ``asyncio.to_thread`` since ``run_coherence_judge``
        spawns ``claude -p`` and blocks for ~1–3 s.

        Persists a single-entry record to
        ``learning_logs_dir() / "coherence-audit" / <utc>.json`` in
        the same shape ``_coherence_audit_text`` uses, so the chat-
        triggered batch audits and the dashboard one-offs share
        history on disk.
        """
        verdict = run_coherence_judge(
            self._workspace,
            {
                "class": entry.get("class") or "?",
                "lesson": entry.get("lesson") or "",
                "scope": entry.get("scope") or "",
                "evidence": entry.get("evidence") or "",
                "tier": entry.get("tier"),
            },
            [],  # degraded mode — no source transcript
        )
        judged_at = _iso(_utc_now())
        result = {
            "verdict": verdict.verdict,
            "reason": verdict.reason,
            "explanation": verdict.explanation or None,
            "degraded": verdict.degraded,
            "judged_at": judged_at,
        }
        log_dir = learning_logs_dir() / "coherence-audit"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / (
                _iso(_utc_now()).replace(":", "") + "-dashboard.json"
            )
            log_path.write_text(
                json.dumps(
                    {
                        "audited_at": judged_at,
                        "shadow_only": True,
                        "trigger": "dashboard",
                        "entry_id": entry.get("entry_id"),
                        "results": [{
                            "file": entry.get("source") or "unknown",
                            "lesson_preview": _ellipsize(
                                entry.get("lesson") or "", _LESSON_PREVIEW_CHARS,
                            ),
                            **result,
                        }],
                    },
                    indent=2, sort_keys=True, ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            # Logging failure shouldn't block the judge result. Fall
            # through with the in-memory verdict.
            pass
        return result

    # ----- end Step 15 dashboard surface ---------------------------

    def _status_text(self) -> str:
        state = _load_daemon_state()
        records = self._reviewed.load()
        last_tick = state.get("last_tick_at") or "never"
        paused = bool(state.get("paused"))
        successes = [
            (uuid, rec)
            for uuid, rec in records.items()
            if rec.last_reviewed_at is not None
        ]
        epoch = datetime.min.replace(tzinfo=timezone.utc)
        successes.sort(
            key=lambda kv: kv[1].last_reviewed_at or epoch,
            reverse=True,
        )
        lines = [
            f"Learning curator: {'paused' if paused else 'enabled'}",
            f"Last tick: {last_tick}",
            f"Tracked sessions: {len(records)}",
            f"Successful reviews: {len(successes)}",
            f"Shadow mode: {'on' if learning_shadow_mode() else 'off'}",
            (
                f"Tick interval: {learning_tick_interval_minutes()}m, "
                f"idle threshold: {learning_idle_threshold_minutes()}m"
            ),
        ]
        for uuid, rec in successes[:5]:
            short = uuid[:8] if len(uuid) >= 8 else uuid
            lines.append(
                f"  {short} — last_reviewed="
                f"{_iso(rec.last_reviewed_at) if rec.last_reviewed_at else '?'} "
                f"outcome={rec.outcome}"
            )
        return "\n".join(lines)

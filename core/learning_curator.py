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
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from core.learning_review import (
    RECURSION_ENV_VAR,
    ReviewOutput,
    run_review,
)
from core.memory import MemoryStore, MemorySuccess
from core.notify import Notifier
from core.paths import (
    learning_logs_dir,
    learning_state_path,
    memories_dir,
)
from core.transcripts import (
    SessionMeta,
    claude_session_jsonl_dir,
    iter_messages,
    list_eligible_sessions,
)
from core.yaml_config import (
    learning_enabled,
    learning_failure_cooldown_hours,
    learning_idle_threshold_minutes,
    learning_shadow_mode,
    learning_tick_interval_minutes,
)

# Type alias for dependency-injected review functions (used by tests
# to swap the real subprocess pipeline for synthetic outcomes).
ReviewFn = Callable[[Path, SessionMeta], str]

log = logging.getLogger(__name__)

# Shadow file lives alongside MEMORY.md / USER.md inside the workspace
# so the user can review and `mv MEMORY-SHADOW.md MEMORY.md` once
# they're happy. The shadow file is NOT injected into the system
# prompt — that's the whole point of "shadow mode".
SHADOW_FILE_NAME = "MEMORY-SHADOW.md"

# Same delimiter MEMORY.md uses (core/memory.py:ENTRY_DELIMITER), so a
# manual cutover to live mode is a `mv` not a reformat.
ENTRY_DELIMITER = "\n§\n"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


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

    def to_dict(self) -> dict[str, Any]:
        def _fmt(dt: datetime | None) -> str | None:
            return _iso(dt) if dt is not None else None
        return {
            "last_reviewed_at": _fmt(self.last_reviewed_at),
            "last_review_attempt_at": _fmt(self.last_review_attempt_at),
            "last_message_at_review_time": _fmt(self.last_message_at_review_time),
            "outcome": self.outcome,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReviewRecord":
        return cls(
            last_reviewed_at=_parse_iso(data.get("last_reviewed_at")),
            last_review_attempt_at=_parse_iso(data.get("last_review_attempt_at")),
            last_message_at_review_time=_parse_iso(
                data.get("last_message_at_review_time")
            ),
            outcome=str(data.get("outcome") or ""),
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
        ``last_message_at_review_time`` (the eligibility-gate snapshot).
        On failure: advance only ``last_review_attempt_at`` so the
        cooldown gate kicks in and the eligibility gate stays open
        (the same content can be retried after the cooldown).
        """
        records = self.load()
        rec = records.get(session_uuid) or ReviewRecord()
        when = now or _utc_now()
        rec.last_review_attempt_at = when
        rec.outcome = outcome
        if success:
            rec.last_reviewed_at = when
            rec.last_message_at_review_time = last_message_at_review_time
        records[session_uuid] = rec
        self.save(records)


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


def _shadow_file(workspace: Path) -> Path:
    return memories_dir(workspace) / SHADOW_FILE_NAME


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


def _append_shadow_entry(workspace: Path, content: str) -> None:
    """Append one §-delimited entry to ``MEMORY-SHADOW.md``.

    No threat scanner / char cap / verifier here — that machinery
    lands in Day 2. The shadow file is not injected into the system
    prompt, so a stubbed entry can't reach the model. Atomic temp+rename
    keeps concurrent readers safe even though writes are infrequent.
    """
    path = _shadow_file(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
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


def _format_lesson_entry(lesson: dict) -> str:
    """Render one verified lesson into a memory-store entry.

    Layout: a tagged header line so a future ``/learning audit`` can
    grep for ``[learned`` to enumerate curator-authored entries, then
    the lesson body, then scope and verbatim evidence as provenance.
    The whole block is one §-delimited entry on disk.
    """
    today = _utc_now().strftime("%Y-%m-%d")
    return (
        f"[learned {today}] {lesson['lesson']}\n"
        f"  Scope: {lesson['scope']}\n"
        f"  Evidence: {lesson['evidence']}"
    )


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


def _write_verified(
    workspace: Path,
    output: ReviewOutput,
    *,
    shadow: bool,
) -> int:
    """Persist verified lessons. Returns the count actually written.

    Shadow mode: append §-delimited entries to MEMORY-SHADOW.md (no
    threat scanner — that file is not injected into the system
    prompt). Live mode: route through ``MemoryStore.add`` so the
    existing threat scanner runs and the live MEMORY.md char cap is
    enforced; a rejection from the store counts as "not written" but
    isn't surfaced as an error (the curator did its job; the store
    chose not to accept).
    """
    written = 0
    for lesson in output.verified_lessons:
        entry = _format_lesson_entry(lesson)
        if shadow:
            _append_shadow_entry(workspace, entry)
            written += 1
        else:
            store = MemoryStore(memories_dir(workspace))
            result = store.add("memory", entry)
            if isinstance(result, MemorySuccess):
                written += 1
            else:
                log.warning(
                    "MemoryStore rejected curator-authored entry: %s",
                    getattr(result, "message", "?"),
                )
    return written


def _real_review(workspace: Path, meta: SessionMeta) -> str:
    """Production review path: parse the JSONL, run claude -p, verify,
    write. Returns the outcome string for reviewed.json on success.

    Outcome strings (caller stores verbatim):
      - "skip: no conversational messages"           — empty transcript
      - "skipped: transcript too large (N chars)"    — past hard threshold
      - "nothing to save"                            — LLM declined
      - "rejected: N candidate(s); first: <reason>"  — verifier dropped all
      - "wrote N entry/entries (transcript Nc)..."   — verified + written

    Raises ``RuntimeError`` on subprocess / parse / unknown failure
    so the controller's try/except engages the failure cooldown.
    All other outcomes — including the decline-too-large skip —
    advance ``last_reviewed_at`` so the session doesn't loop.
    """
    messages = list(iter_messages(meta.jsonl_path))
    if not messages:
        return "skip: no conversational messages"

    output = run_review(workspace, meta, messages)

    if output.error:
        # Spawn failure / claude non-zero exit / parse failure —
        # all transient enough that retry-after-cooldown is right.
        raise RuntimeError(output.error)

    if output.declined_too_large:
        # Successful no-op: we chose not to call the LLM. Per Day 3
        # spec, advance last_reviewed_at so the session doesn't
        # loop. Skip rate is computed from these outcomes by the
        # /learning audit command.
        return f"skipped: transcript too large ({output.transcript_chars} chars)"

    if output.nothing_to_save:
        return "nothing to save"

    written = _write_verified(
        workspace,
        output,
        shadow=learning_shadow_mode(),
    )
    return _summarize_outcome(output, written=written)


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
        # UUIDs the curator's own review forks spawned. Populated by
        # ``_review_one``'s scan-diff (§4.6 of the research doc):
        # before/after snapshots of the projects directory expose the
        # new JSONL each ``claude -p`` invocation creates, and those
        # UUIDs feed into ``list_eligible_sessions``'s exclusion set
        # so the next tick can't pick up the curator's own session.
        # Survives daemon restart only as a soft state — restart loses
        # the set, but the env-var recursion guard plus the 25-min
        # idle gate plus the reviewed.json gate make a true loop
        # essentially impossible even with the set forgotten.
        self._spawned_uuids: set[str] = set()
        # Dependency injection for tests: pass ``review_fn`` to
        # short-circuit the real subprocess pipeline. Production
        # leaves it None and the controller calls ``_real_review``.
        self._review_fn = review_fn or _real_review

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

    def _run_once(self) -> TickResult:
        result = TickResult()
        started_at = _utc_now()
        idle_threshold = timedelta(minutes=learning_idle_threshold_minutes())
        cooldown = timedelta(hours=learning_failure_cooldown_hours())
        records = self._reviewed.load()

        # Eligibility map keyed by `last_message_at_review_time` (the
        # snapshot from the last successful review). A failed review
        # leaves this None so the same content is retried after the
        # cooldown gate elapses.
        eligibility_map = {
            uuid: rec.last_message_at_review_time
            for uuid, rec in records.items()
            if rec.last_message_at_review_time is not None
        }

        candidates = list_eligible_sessions(
            workspace=self._workspace,
            reviewed=eligibility_map,
            idle_threshold=idle_threshold,
            now=started_at,
            spawned_by_curator=self._spawned_uuids,
        )
        result.eligible = [m.session_uuid for m in candidates]

        for meta in candidates:
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
                outcome = self._review_one(meta)
                self._reviewed.update(
                    meta.session_uuid,
                    success=True,
                    last_message_at_review_time=meta.last_message_timestamp,
                    outcome=outcome,
                    now=_utc_now(),
                )
                result.reviewed.append(meta.session_uuid)
                result.outcomes.append((meta.session_uuid, outcome))
            except Exception as exc:
                log.exception("Review failed for %s", meta.session_uuid)
                err_outcome = f"error: {exc}"
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
        ]
        if result.outcomes:
            md_lines.append("## Per-session outcomes")
            md_lines.append("")
            for uuid, outcome in result.outcomes:
                short = uuid[:8] if len(uuid) >= 8 else uuid
                md_lines.append(f"- `{short}` — {outcome}")
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

    def _review_one(self, meta: SessionMeta) -> str:
        """Single-session review. Delegates to the configured review
        function (default: ``_real_review``, which spawns ``claude -p``
        and runs the verifier). Tests inject a stub via ``review_fn``.

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
        """
        projects_dir = claude_session_jsonl_dir(self._workspace)
        before = self._scan_projects_uuids(projects_dir)
        try:
            outcome = self._review_fn(self._workspace, meta)
        finally:
            after = self._scan_projects_uuids(projects_dir)
            new_uuids = after - before
            if new_uuids:
                self._spawned_uuids.update(new_uuids)
                log.debug(
                    "Learning curator added spawned UUIDs to recursion "
                    "guard set: %s",
                    sorted(new_uuids),
                )
        return outcome

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
        run, audit."""
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
        return "Usage: /learning [status|pause|resume|run|audit]"

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

        return "\n".join(lines) if lines else "Nothing to audit yet."

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

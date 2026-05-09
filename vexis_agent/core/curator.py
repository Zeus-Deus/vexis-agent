"""Background skill curator.

Runs periodically (every 7 days by default), in two phases:

  Phase 1 — deterministic transitions. Cheap, no LLM:
    * `last_used_at <= now - 90d` and not archived  → archive_skill
    * `last_used_at <= now - 30d` and active        → mark stale
    * `last_used_at >  now - 30d` and stale         → reactivate

  Phase 2 — LLM consolidation pass. Spawns a fresh `claude -p` with
  ``VEXIS_CURATOR=1`` set so the skill CLI's destructive verbs
  (``delete``, ``remove-file``) refuse outright. Goal is umbrella-
  building: the LLM looks at prefix clusters and merges narrow
  siblings into broader skills, archiving the originals. A pre-run
  tarball of the entire skills tree is written to
  ``.curator_backups/`` for one-shot rollback.

Trigger: a daemon thread ticks hourly and asks ``should_run_now``.
First observation seeds ``last_run_at = now`` and returns False, so
fresh installs don't trip a curator pass on day one.

Reports land in ``~/.vexis/logs/curator/<utc-iso>/``:
  * REPORT.md — human-readable narrative
  * run.json  — machine-readable counts + timeline (future dashboard)

The curator NEVER deletes. Maximum destructive action is archive,
which is recoverable via /curator restore. Pinned skills are skipped
in both phases.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tarfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vexis_agent.core.brain.base import (
    BrainAuthRequired,
    BrainError,
    BrainNotInstalled,
    BrainTimeoutError,
)

if TYPE_CHECKING:
    from vexis_agent.core.brain.base import Brain

from vexis_agent.core.notify import Notifier
from vexis_agent.core.paths import (
    curator_logs_dir,
    curator_state_path,
    skills_dir,
)
from vexis_agent.core.skills import (
    CURATOR_BACKUPS_DIR_NAME,
    PinStore,
    STATE_ACTIVE,
    STATE_ARCHIVED,
    STATE_STALE,
    UsageStore,
    archive_skill,
    archived_skill_names,
    list_active_reports,
    restore_skill,
)
from vexis_agent.core.yaml_config import (
    curator_archive_after_days,
    curator_enabled,
    curator_interval_hours,
    curator_stale_after_days,
    subsystem_reasoning,
    subsystem_tier,
)

log = logging.getLogger(__name__)

# How often the daemon thread ticks. Hourly is plenty — checking
# more often is wasted cycles, less often delays the first run.
TICK_INTERVAL_SECONDS = 60 * 60

# Hard cap on phase-2 wallclock. The LLM pass walks the skill tree
# and may make many tool calls; 30 minutes is conservative for our
# expected library size (~20 skills).
PHASE2_TIMEOUT_SECONDS = 30 * 60


# --------------------------------------------------------------------
# State file (~/.vexis/curator/state.json)
# --------------------------------------------------------------------


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


def load_state() -> dict[str, Any]:
    path = curator_state_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read curator state %s: %s", path, exc)
        return {}


def save_state(state: dict[str, Any]) -> None:
    path = curator_state_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        log.warning("Could not write curator state %s: %s", path, exc)
        tmp.unlink(missing_ok=True)


def is_paused() -> bool:
    return bool(load_state().get("paused"))


def set_paused(value: bool) -> None:
    state = load_state()
    state["paused"] = bool(value)
    save_state(state)


def should_run_now(now: datetime | None = None) -> bool:
    """Should a curator pass start now?

    First observation seeds ``last_run_at = now`` and returns False,
    deferring the first real run by one full interval. This avoids a
    fresh-install gateway tick from immediately marking new skills
    stale based on their (zero) usage data.
    """
    if not curator_enabled():
        return False
    if is_paused():
        return False
    state = load_state()
    last = _parse_iso(state.get("last_run_at"))
    if now is None:
        now = _utc_now()
    if last is None:
        state["last_run_at"] = _iso(now)
        state["seeded"] = True
        save_state(state)
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    interval = timedelta(hours=curator_interval_hours())
    return (now - last) >= interval


# --------------------------------------------------------------------
# Phase 1 — deterministic transitions
# --------------------------------------------------------------------


@dataclass
class Phase1Result:
    checked: int = 0
    marked_stale: int = 0
    reactivated: int = 0
    archived: int = 0
    archived_names: list[str] = field(default_factory=list)
    stale_names: list[str] = field(default_factory=list)
    reactivated_names: list[str] = field(default_factory=list)


def run_phase1(workspace: Path, now: datetime | None = None) -> Phase1Result:
    """Apply deterministic state transitions across active skills.

    Pinned skills are skipped. The anchor for staleness is
    ``last_used_at`` (live or set by previous phase 1) falling back to
    ``created_at``. Skills with neither (e.g. created in this same
    pass) are anchored at ``now`` so they don't immediately archive.
    """
    if now is None:
        now = _utc_now()
    root = skills_dir(workspace)
    pins = set(PinStore(root).list())
    usage = UsageStore(root)
    stale_cutoff = now - timedelta(days=curator_stale_after_days())
    archive_cutoff = now - timedelta(days=curator_archive_after_days())

    result = Phase1Result()
    # Snapshot reports before mutating — archiving renames directories
    # and would otherwise interfere with a single-pass iteration.
    reports = list_active_reports(root)
    for rep in reports:
        result.checked += 1
        if rep.pinned or rep.name in pins:
            continue
        anchor = (
            _parse_iso(rep.last_used_at)
            or _parse_iso(rep.created_at)
            or now
        )
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)

        if anchor <= archive_cutoff and rep.state != STATE_ARCHIVED:
            op = archive_skill(root, rep.name)
            if op.ok:
                result.archived += 1
                result.archived_names.append(rep.name)
            else:
                log.warning("Curator phase1 archive failed for %s: %s",
                            rep.name, op.message)
        elif anchor <= stale_cutoff and rep.state == STATE_ACTIVE:
            usage.set_state(rep.name, STATE_STALE)
            result.marked_stale += 1
            result.stale_names.append(rep.name)
        elif anchor > stale_cutoff and rep.state == STATE_STALE:
            usage.set_state(rep.name, STATE_ACTIVE)
            result.reactivated += 1
            result.reactivated_names.append(rep.name)
    return result


# --------------------------------------------------------------------
# Phase 2 — pre-run tarball + LLM pass
# --------------------------------------------------------------------


_CURATOR_REVIEW_PROMPT = """\
You are running as Vexis's background skill CURATOR. This is an
UMBRELLA-BUILDING consolidation pass, not a passive audit and not a
duplicate-finder.

The goal of the skill collection is a LIBRARY OF CLASS-LEVEL
INSTRUCTIONS AND EXPERIENTIAL KNOWLEDGE. A collection of dozens of
narrow skills where each one captures one session's specific bug is
a FAILURE of the library — not a feature. An agent searching skills
matches on descriptions, not on exact names; one broad umbrella
skill with labeled subsections beats five narrow siblings for
discoverability.

Hard rules — do not violate:
1. DO NOT touch pinned skills. They are filtered out below.
2. DO NOT delete any skill. Archiving (moving the skill's directory
   into the archive via `vexis-skill archive <name>`) is the
   maximum destructive action. Archives are recoverable; deletion
   is not. Note: the delete and remove-file actions have been
   removed from your tool surface — calling them returns an error.
3. DO NOT use usage counters as a reason to skip consolidation.
   `use=0` is absence of evidence either way, not evidence the
   skill is valuable.
4. DO NOT reject consolidation on the grounds that "each skill has
   a distinct trigger". The right bar is: "would a human maintainer
   write this as N separate skills, or as one skill with N labeled
   subsections?" When the answer is the latter, MERGE.

How to work:
1. Scan the full candidate list. Identify PREFIX CLUSTERS (skills
   sharing a first word or domain keyword).
2. For each cluster of 2+ members, ask "what is the UMBRELLA CLASS
   these all serve?"
3. Three ways to consolidate:
   a. MERGE INTO EXISTING UMBRELLA — patch the broader skill to add
      sections from narrow siblings, then archive the siblings.
   b. CREATE A NEW UMBRELLA SKILL.md — new skill with labeled
      subsections, then archive the originals.
   c. DEMOTE TO REFERENCES/TEMPLATES — move session-specific detail
      to a support file under an existing umbrella.
4. Iterate. After one consolidation round, scan the remaining set
   for the next umbrella opportunity. Don't stop after 3 merges.

If you ended this pass with fewer than 3 consolidations and the
candidate list has more than 10 skills, you stopped too early.
Re-scan.

Your toolset (invoke via Bash, all under ~/projects/vexis-agent/scripts/):
  - vexis-skill list                       — read the current landscape
  - vexis-skill view <name>                — read a SKILL.md body
  - vexis-skill view <name> --file <path>  — read a supporting file
  - vexis-skill patch <name> --old-string ... --new-string ...
                                            — add sections to an umbrella
  - vexis-skill create <name> --content-file <path> [--category ...]
                                            — create a new umbrella skill
  - vexis-skill write-file <name> --file ... --content-file ...
                                            — add a reference/template/script
  - vexis-skill archive <name>             — archive an obsoleted skill

NOT available to you (will refuse): vexis-skill delete, vexis-skill remove-file.

"keep" is a legitimate decision ONLY when the skill is already a
class-level umbrella and none of the proposed merges would improve
discoverability.

When you are done, write a short summary of what you consolidated
and why. The next message you send IS your final output — keep it
under ~30 lines and start it with the literal line:

CURATOR-SUMMARY:
"""


def _make_prerun_tarball(skills_root: Path, started_at: datetime) -> Path:
    """Tar.gz the entire skills tree (sans backups dir) before phase 2."""
    backups_root = skills_root / CURATOR_BACKUPS_DIR_NAME
    backups_root.mkdir(parents=True, exist_ok=True)
    name = f"{_iso(started_at).replace(':', '')}.tar.gz"
    dest = backups_root / name

    def _exclude(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
        rel = Path(tarinfo.name).parts
        if rel and rel[0] == CURATOR_BACKUPS_DIR_NAME:
            return None
        return tarinfo

    with tarfile.open(dest, "w:gz") as tf:
        tf.add(str(skills_root), arcname=skills_root.name, filter=_exclude)
    return dest


@dataclass
class Phase2Result:
    ran: bool = False
    backup_path: str | None = None
    iterations: int = 0
    final_message: str = ""
    archived_names: list[str] = field(default_factory=list)
    created_names: list[str] = field(default_factory=list)
    error: str | None = None


def _candidate_text(workspace: Path) -> tuple[str, list[str]]:
    """Render the candidate-skill list the curator's LLM sees, and return
    the list of candidate names so we can compare pre/post."""
    root = skills_dir(workspace)
    pins = set(PinStore(root).list())
    reports = list_active_reports(root)
    lines: list[str] = []
    names: list[str] = []
    for rep in reports:
        if rep.name in pins:
            continue
        lines.append(
            f"- {rep.name} (state={rep.state}, "
            f"last_used={rep.last_used_at or 'never'}): {rep.description}"
        )
        names.append(rep.name)
    if not lines:
        return "(no candidates)", []
    return "\n".join(lines), names


def run_phase2(
    workspace: Path,
    brain: "Brain",
    *,
    now: datetime | None = None,
) -> Phase2Result:
    """Run the LLM consolidation pass via ``Brain.spawn_aux``.

    Phase B: ``brain`` is the aux-spawn surface. Unlike judges and
    extractors, the curator's consolidation pass MUST be able to
    write files (it moves / merges / archives skill SKILL.md trees),
    so it passes ``allow_tools=True`` so the spawned brain sees
    ``--permission-mode bypassPermissions``.

    Sync wrapper around the async ``spawn_aux`` via ``asyncio.run``;
    the curator daemon thread (only caller) has no event loop.
    """
    if now is None:
        now = _utc_now()
    root = skills_dir(workspace)
    candidate_text, before_names = _candidate_text(workspace)
    if not before_names:
        return Phase2Result(ran=False, final_message="No candidates; nothing to do.")

    backup_path = _make_prerun_tarball(root, now)
    if backup_path.stat().st_size < 32:
        return Phase2Result(
            ran=False,
            error=f"backup tarball at {backup_path} is suspiciously small; aborting",
        )

    prompt = (
        f"{_CURATOR_REVIEW_PROMPT}\n\n## Candidate skills\n\n{candidate_text}"
    )

    try:
        result = asyncio.run(
            brain.spawn_aux(
                prompt,
                model_tier=subsystem_tier("curator"),
                reasoning_level=subsystem_reasoning("curator"),
                timeout_seconds=PHASE2_TIMEOUT_SECONDS,
                env_overrides={"VEXIS_CURATOR": "1"},
                allow_tools=True,  # consolidation writes files
                cwd=workspace,
                subsystem="curator",
            )
        )
    except BrainTimeoutError:
        return Phase2Result(
            ran=True,
            backup_path=str(backup_path),
            error=f"phase2 timed out after {PHASE2_TIMEOUT_SECONDS}s",
        )
    except (BrainNotInstalled, BrainAuthRequired) as exc:
        return Phase2Result(
            ran=False, backup_path=str(backup_path), error=f"spawn failed: {exc}"
        )
    except BrainError as exc:
        return Phase2Result(
            ran=False, backup_path=str(backup_path), error=f"spawn failed: {exc}"
        )

    stdout = result.stdout
    if result.returncode != 0:
        return Phase2Result(
            ran=True,
            backup_path=str(backup_path),
            error=f"claude -p exited {result.returncode}: {(result.stderr or stdout).strip()}",
        )

    after_text, after_names = _candidate_text(workspace)
    archived_now = sorted(set(before_names) - set(after_names))
    created_now = sorted(set(after_names) - set(before_names))

    final_message = stdout.strip()
    # If the LLM followed instructions, find the CURATOR-SUMMARY line
    # and use the trailing block as the human summary. Otherwise
    # fall back to the entire stdout — better noisy than silent.
    marker = "CURATOR-SUMMARY:"
    if marker in final_message:
        final_message = final_message.split(marker, 1)[1].strip()

    return Phase2Result(
        ran=True,
        backup_path=str(backup_path),
        iterations=stdout.count("tool_use") if stdout else 0,
        final_message=final_message,
        archived_names=archived_now,
        created_names=created_now,
    )


# --------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------


def write_report(
    started_at: datetime,
    finished_at: datetime,
    phase1: Phase1Result,
    phase2: Phase2Result,
) -> Path:
    """Write REPORT.md + run.json to ~/.vexis/logs/curator/<utc-iso>/."""
    folder_name = _iso(started_at).replace(":", "")
    folder = curator_logs_dir() / folder_name
    folder.mkdir(parents=True, exist_ok=True)

    report_md = folder / "REPORT.md"
    run_json = folder / "run.json"

    md_lines: list[str] = [
        f"# Curator run {folder_name}",
        "",
        f"- started_at: {_iso(started_at)}",
        f"- finished_at: {_iso(finished_at)}",
        f"- phase1: checked={phase1.checked}, "
        f"marked_stale={phase1.marked_stale}, "
        f"reactivated={phase1.reactivated}, "
        f"archived={phase1.archived}",
        "",
    ]
    if phase1.archived_names:
        md_lines.append(f"## Archived (phase 1): {', '.join(phase1.archived_names)}")
    if phase1.stale_names:
        md_lines.append(f"## Marked stale: {', '.join(phase1.stale_names)}")
    if phase1.reactivated_names:
        md_lines.append(
            f"## Reactivated: {', '.join(phase1.reactivated_names)}"
        )
    md_lines.append("")
    if phase2.ran:
        md_lines.append("## Phase 2 (LLM consolidation)")
        md_lines.append("")
        md_lines.append(f"- backup: {phase2.backup_path}")
        if phase2.archived_names:
            md_lines.append(
                f"- archived this pass: {', '.join(phase2.archived_names)}"
            )
        if phase2.created_names:
            md_lines.append(
                f"- newly created umbrellas: {', '.join(phase2.created_names)}"
            )
        if phase2.error:
            md_lines.append(f"- error: {phase2.error}")
        md_lines.append("")
        md_lines.append("### LLM summary")
        md_lines.append("")
        md_lines.append(phase2.final_message or "(no summary returned)")
    else:
        md_lines.append("## Phase 2: skipped")
        if phase2.final_message:
            md_lines.append(phase2.final_message)
        if phase2.error:
            md_lines.append(f"- error: {phase2.error}")

    report_md.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    run = {
        "started_at": _iso(started_at),
        "finished_at": _iso(finished_at),
        "phase1": asdict(phase1),
        "phase2": asdict(phase2),
    }
    run_json.write_text(
        json.dumps(run, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )
    return folder


# --------------------------------------------------------------------
# Public entry point: full pass
# --------------------------------------------------------------------


@dataclass
class RunSummary:
    folder: Path
    phase1: Phase1Result
    phase2: Phase2Result


def run_curator(
    workspace: Path,
    brain: "Brain",
    *,
    skip_phase2: bool = False,
    now: datetime | None = None,
) -> RunSummary:
    """Execute one full curator pass: phase 1 → phase 2 → write report."""
    started_at = now or _utc_now()
    p1 = run_phase1(workspace, now=started_at)
    if skip_phase2:
        p2 = Phase2Result(
            ran=False, final_message="phase 2 skipped (skip_phase2=True)"
        )
    else:
        p2 = run_phase2(workspace, brain, now=started_at)
    finished_at = _utc_now()
    folder = write_report(started_at, finished_at, p1, p2)

    state = load_state()
    state["last_run_at"] = _iso(finished_at)
    state["last_run_summary"] = (
        f"phase1: archived={p1.archived}, stale={p1.marked_stale}, "
        f"reactivated={p1.reactivated}; phase2 ran={p2.ran}"
    )
    save_state(state)

    return RunSummary(folder=folder, phase1=p1, phase2=p2)


# --------------------------------------------------------------------
# Daemon thread + telegram controller
# --------------------------------------------------------------------


class CuratorController:
    """Owns the daemon thread and exposes the /curator slash commands.

    The daemon thread ticks once an hour and asks ``should_run_now``;
    when true it kicks off ``run_curator`` in a child thread so the
    tick loop stays responsive. /curator commands are async-friendly
    (they're awaited by the Telegram transport) but their underlying
    work is sync, so we offload to a thread pool when needed.
    """

    def __init__(
        self,
        workspace: Path,
        notifier: Notifier | None = None,
        *,
        brain: "Brain | None" = None,
    ) -> None:
        # Phase B: brain is the aux-spawn surface for the
        # consolidation pass. Production main.py threads the real
        # brain in; tests can leave it None and a BrainNull is
        # synthesised at the call site below — those tests must
        # mock out run_phase2 entirely (they do — see
        # tests/test_curator.py:_FakeProc usage), so the BrainNull
        # is never actually exercised in test paths.
        from vexis_agent.core.brain.null import BrainNull

        self._workspace = workspace
        self._notifier = notifier
        self._brain: "Brain" = brain or BrainNull()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._busy = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    # ---------- lifecycle ----------

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self._thread is not None:
            return
        self._loop = loop
        self._thread = threading.Thread(
            target=self._run_loop, name="vexis-curator", daemon=True
        )
        self._thread.start()
        log.info("Curator daemon thread started")

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)
        self._thread = None
        log.info("Curator daemon thread stopped")

    # ---------- internals ----------

    def _run_loop(self) -> None:
        # Sleep first; the daemon just started and we don't want to
        # fire phase 2 the same second the user launched the bot.
        # The hourly cadence is short enough that "wait one tick"
        # adds at most an hour of latency to any genuinely-due pass.
        while not self._stop.is_set():
            try:
                if should_run_now():
                    self._run_once()
            except Exception:
                log.exception("Curator tick raised")
            self._stop.wait(TICK_INTERVAL_SECONDS)

    def _run_once(self) -> RunSummary | None:
        if not self._busy.acquire(blocking=False):
            log.warning("Curator already running; skipping this tick")
            return None
        try:
            # Trim backups before each run. Phase 2 will write a fresh
            # tarball, so keeping 11 here yields a steady-state of 12
            # tarballs after a successful pass — matches the user's
            # "keep last 12" intent without an off-by-one ceiling.
            try:
                pruned = self.prune_backups(keep=11)
                if pruned:
                    log.info("Pruned %d old curator backup tarball(s)", pruned)
            except Exception:
                # Pruning is housekeeping — never block the actual run.
                log.exception("Backup pruning failed; continuing with curator pass")
            log.info("Curator pass beginning")
            summary = run_curator(self._workspace, self._brain)
            log.info(
                "Curator pass complete: phase1=%s phase2=%s folder=%s",
                summary.phase1.archived,
                summary.phase2.ran,
                summary.folder,
            )
            return summary
        finally:
            self._busy.release()

    # ---------- /curator dispatch ----------

    async def handle_telegram(self, sub: str, args: list[str]) -> str:
        """Implement /curator subcommands: status, pause, resume, run, restore."""
        sub = (sub or "status").lower()
        if sub == "status":
            return self._status_text()
        if sub == "pause":
            set_paused(True)
            return "Curator paused. /curator resume to start it again."
        if sub == "resume":
            set_paused(False)
            return "Curator resumed."
        if sub == "run":
            if self._busy.locked():
                return "Curator is already running. Try /curator status."
            return await self._run_async()
        if sub == "restore":
            if not args:
                names = archived_skill_names(skills_dir(self._workspace))
                if not names:
                    return "No archived skills."
                return "Archived skills:\n" + "\n".join(f"  {n}" for n in names)
            op = restore_skill(skills_dir(self._workspace), args[0])
            return op.message
        return (
            "Usage: /curator [status|pause|resume|run|restore <name>]"
        )

    async def _run_async(self) -> str:
        """Kick off run_curator on a worker thread without blocking the
        Telegram event loop."""
        summary = await self.run_now()
        if summary is None:
            return "Curator is already running. Try /curator status."
        return (
            f"Curator finished. Report: {summary.folder}. "
            f"Phase 1: archived={summary.phase1.archived}, "
            f"stale={summary.phase1.marked_stale}, "
            f"reactivated={summary.phase1.reactivated}. "
            f"Phase 2 ran={summary.phase2.ran}."
        )

    async def run_now(self) -> RunSummary | None:
        """Force a curator pass on demand, returning the structured summary.

        Returns ``None`` if a pass was already in flight (the busy lock
        is held). Used by both the Telegram ``/curator run`` handler and
        the web dashboard's force-run button so they share one busy
        guard and produce one report per pass.
        """
        if self._busy.locked():
            return None
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._run_once)

    def is_running(self) -> bool:
        """True if a curator pass is currently in flight (daemon-thread
        tick or a forced run from Telegram/dashboard)."""
        return self._busy.locked()

    def _status_text(self) -> str:
        state = load_state()
        last = state.get("last_run_at") or "never"
        paused = bool(state.get("paused"))
        interval = curator_interval_hours()
        last_dt = _parse_iso(last)
        if last_dt is not None:
            next_eligible = last_dt + timedelta(hours=interval)
            next_str = _iso(next_eligible)
        else:
            next_str = "as soon as the seed elapses"
        archived = archived_skill_names(skills_dir(self._workspace))
        reports = list_active_reports(skills_dir(self._workspace))
        # Top 5 LRU skills (least recently used = oldest last_used_at).
        # None values sort last so we surface "never used" too.
        ordered = sorted(
            reports,
            key=lambda r: (r.last_used_at or "0"),
        )
        top = ordered[:5]
        lines = [
            f"Curator: {'paused' if paused else 'enabled'}",
            f"Last run: {last}",
            f"Next eligible: {next_str}",
            f"Archived skills: {len(archived)}",
            f"Active skills: {len(reports)}",
        ]
        if top:
            lines.append("LRU active:")
            for r in top:
                lu = r.last_used_at or "never"
                lines.append(f"  {r.name} — last_used={lu} state={r.state}")
        return "\n".join(lines)

    # ---------- pruning ----------

    def prune_backups(self, keep: int = 8) -> int:
        """Optional: trim the backup tarball directory. Returns count
        removed. Not called automatically — exposed for future cleanup.
        """
        backups = skills_dir(self._workspace) / CURATOR_BACKUPS_DIR_NAME
        if not backups.exists():
            return 0
        items = sorted(
            (p for p in backups.iterdir() if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        removed = 0
        for old in items[keep:]:
            try:
                old.unlink()
                removed += 1
            except OSError:
                log.warning("Could not prune %s", old)
        return removed

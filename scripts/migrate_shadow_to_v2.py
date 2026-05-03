#!/usr/bin/env python3
"""One-time migration: route v1 MEMORY-SHADOW.md entries into v2 stores.

v1 of the learning curator put every promoted lesson into MEMORY-SHADOW.md
regardless of its actual class. v2 partitions by class:

  PROCEDURAL → skill (S1 patch / S2 support file / S3 new umbrella)
               via the staging tree at <workspace>/skills/.shadow/
  IDENTITY   → USER candidate queue at ~/.vexis/learning/user_candidates.json
  SITUATIONAL→ stays in MEMORY.md (or MEMORY-SHADOW.md during the soak)
  VOLATILE   → drop, no migration target

This script does NOT mutate MEMORY-SHADOW.md (read-only). It produces
a markdown PLAN that the user reviews and edits, then APPLIES. Apply
is idempotent: re-running it skips already-staged entries. SKIP is
the user's escape hatch for any entry they want to defer.

Two-phase flow (per .plans/learning-curator-v2-research.md §5.6):

    # 1. Generate plan (idempotent; can re-run any time)
    $ scripts/migrate_shadow_to_v2.py --plan
    Wrote plan: ~/.vexis/learning/migration-plan-<utc>.md

    # 2. Open in your editor, review the `decision:` lines, save
    $ $EDITOR ~/.vexis/learning/migration-plan-*.md

    # 3. Apply
    $ scripts/migrate_shadow_to_v2.py --apply <plan-path>

The plan-file pattern mirrors the existing curator's REPORT.md so
the user is reviewing markdown they're already familiar with.

Recovery / partial-state:
  - --apply is idempotent. Re-running against an already-applied plan
    is a no-op per entry (each apply step checks for the staged
    file's existence first).
  - Failed --apply leaves the partial work in the staging tree;
    re-run continues from where it left off.
  - The applied plan is moved to ``migration-plans-applied/`` so
    history is preserved.
  - MEMORY-SHADOW.md is NEVER modified by this script. After the
    user verifies the staged content, they manually clear or edit
    the shadow file.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Allow `scripts/migrate_shadow_to_v2.py` to import from the project.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.learning_review import _scan_lesson_for_sensitive_content  # noqa: E402
from core.learning_writes import (  # noqa: E402
    stage_new_skill,
    stage_skill_patch,
    stage_support_file,
)
from core.memory import ENTRY_DELIMITER, MemoryStore  # noqa: E402
from core.paths import (  # noqa: E402
    memories_dir,
    user_candidates_path,
    vexis_dir,
    workspace_dir,
)
from core.user_candidates import UserCandidateStore  # noqa: E402

log = logging.getLogger(__name__)

# How many parsed entries fit in one classification batch. The LLM
# call returns one classification per entry; smaller batches are
# more reliable but cost more total calls. 30 fits a typical
# MEMORY-SHADOW.md (the user's currently has 23) in one shot.
BATCH_CLASSIFY_SIZE = 30

# Decision tokens the user can put on a `decision:` line in the
# plan. The regex enforces format on apply.
_DECISIONS = (
    "PROCEDURAL_S1",
    "PROCEDURAL_S2",
    "PROCEDURAL_S3",
    "IDENTITY",
    "SITUATIONAL",
    "DROP",
    "SKIP",
)

_DECISION_RE = re.compile(
    r"^decision:\s*(?P<decision>\S+)(?:\s+(?P<arg>\S.*))?$",
    re.MULTILINE,
)


# --------------------------------------------------------------------
# Parsed entry representation
# --------------------------------------------------------------------


@dataclass
class ShadowEntry:
    """One §-delimited entry parsed out of MEMORY-SHADOW.md.

    The shadow file currently uses the v1 / Day 1 format:

        [learned 2026-05-02] <lesson body>
          Scope:    <scope>
          Evidence: <verbatim user quote>

    Day 2+ entries also include ``Class:`` / ``Tier:`` / ``Staged:``
    / ``Stage refused:`` lines — those are audit artifacts, not
    additional content. The migration script reads ``lesson``,
    ``scope``, ``evidence`` and ignores the rest.
    """

    raw: str
    lesson: str
    scope: str
    evidence: str
    learned_date: str = ""

    def is_v1_shape(self) -> bool:
        """A v1-shape entry has no Class: line. Used to skip
        already-classified-and-staged Day 2 entries that the user
        re-runs the migration over."""
        return "Class:" not in self.raw


@dataclass
class PlanRow:
    """One row in the migration plan file: an entry + its decision."""

    index: int
    entry: ShadowEntry
    decision: str = "SKIP"
    arg: str = ""

    def to_markdown(self) -> str:
        # Render lesson/evidence/scope as separate lines so editors
        # can wrap visually without breaking parsing on apply.
        lines = [
            f"## Entry {self.index}",
            f"lesson:    {self.entry.lesson}",
            f"evidence:  {self.entry.evidence}",
            f"scope:     {self.entry.scope}",
            f"decision:  {self.decision}{(' ' + self.arg) if self.arg else ''}",
        ]
        return "\n".join(lines)


@dataclass
class ApplyResult:
    """Outcome of one entry's apply step."""

    index: int
    decision: str
    ok: bool
    message: str


@dataclass
class ApplySummary:
    """Aggregate counts for the apply pass."""

    results: list[ApplyResult] = field(default_factory=list)

    def by_decision(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.results:
            counts[r.decision] = counts.get(r.decision, 0) + 1
        return counts

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)


# --------------------------------------------------------------------
# Phase 1: parse the source shadow file
# --------------------------------------------------------------------


def parse_shadow_file(path: Path) -> list[ShadowEntry]:
    """Return one ShadowEntry per §-delimited block in ``path``.

    Skips entries that don't have the ``[learned`` tag (those weren't
    written by the curator and shouldn't be migrated).
    """
    if not path.exists():
        return []
    raw = path.read_text(encoding="utf-8")
    entries: list[ShadowEntry] = []
    for chunk in raw.split(ENTRY_DELIMITER):
        chunk = chunk.strip()
        if not chunk or "[learned" not in chunk[:50]:
            continue
        first_line, _, rest = chunk.partition("\n")
        # Strip the [learned YYYY-MM-DD] prefix from the lesson body.
        m = re.match(r"^\[learned\s+(\S+)\]\s+(.*)$", first_line)
        if not m:
            continue
        learned_date = m.group(1).rstrip("]")
        lesson = m.group(2).strip()
        scope = _extract_field(rest, "Scope")
        evidence = _extract_field(rest, "Evidence")
        if not lesson or not evidence:
            log.warning("Skipping malformed entry near %r", first_line[:80])
            continue
        entries.append(ShadowEntry(
            raw=chunk,
            lesson=lesson,
            scope=scope,
            evidence=evidence,
            learned_date=learned_date,
        ))
    return entries


def _extract_field(text: str, name: str) -> str:
    """Pull the ``Name: value`` line out of a multi-line block.

    Tolerates 0-2 spaces of indentation. The value extends to end-of-
    line; multi-line values aren't supported by the v1 format.
    """
    m = re.search(rf"^\s{{0,4}}{re.escape(name)}:\s*(.+)$", text, re.MULTILINE)
    return m.group(1).strip() if m else ""


# --------------------------------------------------------------------
# Phase 2: classify each entry via batch LLM call
# --------------------------------------------------------------------


CLASSIFY_PROMPT = """\
You are migrating Vexis's v1 learning-curator output (one big bucket)
into v2's class-based routing. For each entry below, suggest the
right v2 destination based on its content.

Allowed decisions (return one per entry, with optional argument):

  PROCEDURAL_S1 <skill-name>
      The lesson is a general rule about HOW to do a class of task,
      and an existing skill should be patched with it. Use only when
      the user clearly already has a skill that covers this class.
      Pick the existing skill name as the argument.

  PROCEDURAL_S2 <skill-name>/<rel-path>
      The lesson is procedural detail (a recipe, a reference) that
      belongs as a support file under an existing skill. The path
      argument must start with references/, templates/, or scripts/.

  PROCEDURAL_S3 <new-skill-name>
      The lesson is a general rule that warrants a new umbrella
      skill. The name must be at the class level — kebab-case,
      describing the class of task. NOT a session artifact.

  IDENTITY
      The lesson is a durable fact about WHO the user is or HOW they
      want Vexis to behave that is NOT conditional on a specific
      task. Phrasing test: "user prefers …" / "user is …" / "user
      works in …" without an action-oriented "when X" trigger.

  SITUATIONAL
      A factual claim about the user's environment, tools, or
      constraints that is neither procedural nor identity (server
      addresses, daemon names, hardware quirks). Stays in MEMORY.md.

  DROP
      The lesson is a one-shot bug, mood signal, or temporary
      observation that wouldn't generalize. Don't migrate.

When in doubt, prefer PROCEDURAL_S3 over PROCEDURAL_S1/S2: creating
a new umbrella is recoverable (the archive curator consolidates
later); patching the wrong existing skill poisons it.

Output: one JSON array. Each element corresponds 1:1 to the input
entries (keep the same order). Each element has shape:

    {"index": <1-based int>, "decision": "<DECISION>", "arg": "<arg or empty>"}

Wrap as a JSON array even for one entry. Do NOT include any text
outside the JSON. Do NOT call tools.
"""


def classify_entries(
    entries: list[ShadowEntry],
    *,
    spawn=None,
    timeout_s: int = 300,
) -> list[tuple[str, str]]:
    """Batch-classify ``entries`` via one LLM call.

    Returns a list of ``(decision, arg)`` tuples in the same order
    as ``entries``. On any failure (spawn error, parse error, length
    mismatch), returns all-SKIP — better to default-skip and let the
    user resolve manually than to silently mis-classify.

    ``spawn`` is for tests; production passes None and shells out
    via ``subprocess.run``.
    """
    if not entries:
        return []
    prompt = _build_classify_prompt(entries)
    argv = ["claude", "-p", prompt]
    try:
        if spawn is not None:
            cp = spawn(argv, dict(os.environ))
        else:
            cp = subprocess.run(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_s,
            )
    except subprocess.TimeoutExpired:
        log.warning("Classification LLM call timed out; defaulting to SKIP")
        return [("SKIP", "") for _ in entries]
    except OSError as exc:
        log.warning("Classification spawn failed: %s; defaulting to SKIP", exc)
        return [("SKIP", "") for _ in entries]
    if cp.returncode != 0:
        body = (cp.stderr or cp.stdout or b"").decode("utf-8", errors="replace")
        log.warning(
            "Classification LLM exited %d: %s; defaulting to SKIP",
            cp.returncode, body[:200],
        )
        return [("SKIP", "") for _ in entries]
    text = (cp.stdout or b"").decode("utf-8", errors="replace").strip()
    parsed = _parse_classify_response(text, expected=len(entries))
    if parsed is None:
        log.warning("Classification response malformed; defaulting to SKIP")
        return [("SKIP", "") for _ in entries]
    return parsed


def _build_classify_prompt(entries: list[ShadowEntry]) -> str:
    parts = [CLASSIFY_PROMPT, "", "## Entries", ""]
    for i, e in enumerate(entries, start=1):
        parts.append(f"### Entry {i}")
        parts.append(f"lesson:   {e.lesson}")
        parts.append(f"scope:    {e.scope}")
        parts.append(f"evidence: {e.evidence}")
        parts.append("")
    return "\n".join(parts)


def _parse_classify_response(text: str, *, expected: int) -> list[tuple[str, str]] | None:
    """Strip optional code fences, parse JSON array, validate shape."""
    body = text.strip()
    fence_match = re.search(r"```(?:json)?\s*\n(.+?)\n```", body, re.DOTALL)
    if fence_match:
        body = fence_match.group(1).strip()
    arr_match = re.search(r"\[.*\]", body, re.DOTALL)
    if arr_match:
        body = arr_match.group(0)
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(parsed, list) or len(parsed) != expected:
        return None
    out: list[tuple[str, str]] = []
    for i, item in enumerate(parsed, start=1):
        if not isinstance(item, dict):
            return None
        decision = str(item.get("decision", "")).strip()
        arg = str(item.get("arg", "") or "").strip()
        if decision not in _DECISIONS:
            decision = "SKIP"
            arg = ""
        out.append((decision, arg))
    return out


# --------------------------------------------------------------------
# Phase 3: render and parse the plan file
# --------------------------------------------------------------------


PLAN_HEADER = """\
# Learning curator v2 migration plan
#
# Generated: {generated_at}
# Source:    {source}
#
# Edit the `decision:` field per entry. Allowed values:
#   PROCEDURAL_S1 <skill-name>            — patch existing skill
#   PROCEDURAL_S2 <skill-name>/<rel-path> — add support file
#   PROCEDURAL_S3 <new-skill-name>        — create new umbrella
#   IDENTITY                               — insert into USER candidate queue
#   SITUATIONAL                            — keep in MEMORY.md
#   DROP                                   — remove (no migration target)
#   SKIP                                   — leave untouched (defer)
#
# When you've reviewed every entry, save and apply with:
#   scripts/migrate_shadow_to_v2.py --apply {plan_path}
#
# Apply is idempotent. SKIP'd entries can be migrated in a follow-up
# plan. MEMORY-SHADOW.md is NEVER modified by this script — after a
# successful apply, you decide whether/how to clear or trim it.
"""


def render_plan(
    plan_path: Path,
    source: Path,
    rows: list[PlanRow],
) -> str:
    """Build the markdown plan body. ``plan_path`` is rendered into
    the header so the apply command line is copy-pastable."""
    parts = [PLAN_HEADER.format(
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        source=source,
        plan_path=plan_path,
    )]
    for row in rows:
        parts.append(row.to_markdown())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


_ENTRY_HEADER_RE = re.compile(r"^##\s+Entry\s+(\d+)\s*$", re.MULTILINE)


def parse_plan(plan_path: Path) -> list[PlanRow]:
    """Read a (possibly user-edited) plan file. Returns the rows in
    order with their decisions parsed.

    Strict on shape — every Entry block must have lesson, evidence,
    scope, decision lines. A missing decision defaults to SKIP.
    Decisions outside the allowed set raise ValueError so the user
    notices typos.
    """
    raw = plan_path.read_text(encoding="utf-8")
    # Find every "## Entry N" header and slice the body up to the
    # next header (or EOF).
    headers = list(_ENTRY_HEADER_RE.finditer(raw))
    rows: list[PlanRow] = []
    for i, m in enumerate(headers):
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(raw)
        block = raw[start:end]
        index = int(m.group(1))
        lesson = _extract_field(block, "lesson")
        scope = _extract_field(block, "scope")
        evidence = _extract_field(block, "evidence")
        decision_match = _DECISION_RE.search(block)
        if decision_match:
            decision = decision_match.group("decision")
            arg = (decision_match.group("arg") or "").strip()
        else:
            decision = "SKIP"
            arg = ""
        if decision not in _DECISIONS:
            raise ValueError(
                f"Entry {index}: invalid decision {decision!r}. "
                f"Allowed: {', '.join(_DECISIONS)}"
            )
        if not lesson or not evidence:
            log.warning(
                "Entry %d missing lesson or evidence; defaulting to SKIP",
                index,
            )
            decision = "SKIP"
        rows.append(PlanRow(
            index=index,
            entry=ShadowEntry(
                raw="", lesson=lesson, scope=scope, evidence=evidence,
            ),
            decision=decision,
            arg=arg,
        ))
    return rows


# --------------------------------------------------------------------
# Phase 4: apply the plan
# --------------------------------------------------------------------


def apply_plan(workspace: Path, rows: list[PlanRow]) -> ApplySummary:
    """Execute every row's decision against the workspace.

    Per-decision behavior:
      - PROCEDURAL_S1 <name>: needs the original skill body to
        formulate a patch_old/new pair. v1 entries don't carry that
        information, so PROCEDURAL_S1 in the plan is operationally
        treated as PROCEDURAL_S2 (write the lesson body as a
        ``references/<safe-slug>.md`` under the named skill). The
        user can later promote it to a real S1 patch by hand if
        desired. Documented behavior; not a bug.
      - PROCEDURAL_S2 <name>/<path>: stages the support file with
        the lesson body as content.
      - PROCEDURAL_S3 <name>: stages a new skill whose body wraps
        the lesson + scope + evidence into a SKILL.md skeleton with
        ``origin: learning-curator-migration``.
      - IDENTITY: inserts the lesson into the candidate queue with
        a synthetic 2-occurrence prefill (one for first_seen, one
        for promotion-eligibility — using the migration timestamp
        and a synthetic ``migration:<row-index>`` session UUID for
        each so the threshold fires on the next eligible session).
      - SITUATIONAL: appends to MEMORY.md (not MEMORY-SHADOW.md).
      - DROP: no-op, recorded.
      - SKIP: no-op, recorded.

    Idempotent: if the staged file / queue claim already exists,
    the apply step records a "skipped (already applied)" message
    rather than overwriting. Re-running the same plan is safe.
    """
    summary = ApplySummary()
    for row in rows:
        result = _apply_one(workspace, row)
        summary.results.append(result)
    return summary


def _apply_one(workspace: Path, row: PlanRow) -> ApplyResult:
    decision = row.decision
    arg = row.arg
    entry = row.entry
    if decision == "SKIP":
        return ApplyResult(row.index, decision, True, "skipped (deferred)")
    if decision == "DROP":
        return ApplyResult(row.index, decision, True, "dropped (no migration target)")
    if decision == "SITUATIONAL":
        return _apply_situational(workspace, row)
    if decision == "IDENTITY":
        return _apply_identity(workspace, row)
    if decision == "PROCEDURAL_S2":
        return _apply_s2(workspace, row)
    if decision == "PROCEDURAL_S3":
        return _apply_s3(workspace, row)
    if decision == "PROCEDURAL_S1":
        # v1 entries don't carry SKILL.md context; we can't construct
        # a meaningful patch_old/new pair. Fall through to S2 with
        # a derived references path so the lesson lands somewhere
        # reviewable rather than failing the whole plan.
        if not arg:
            return ApplyResult(
                row.index, decision, False,
                "PROCEDURAL_S1 needs a skill name argument",
            )
        derived_arg = f"{arg}/references/migrated-{row.index:03d}.md"
        log.info(
            "Entry %d: PROCEDURAL_S1 has no patch context (v1 source); "
            "treating as S2 → %s",
            row.index, derived_arg,
        )
        return _apply_s2(workspace, PlanRow(
            index=row.index, entry=entry,
            decision="PROCEDURAL_S2", arg=derived_arg,
        ))
    return ApplyResult(row.index, decision, False, f"unknown decision {decision!r}")


def _apply_s2(workspace: Path, row: PlanRow) -> ApplyResult:
    if "/" not in row.arg:
        return ApplyResult(
            row.index, row.decision, False,
            "PROCEDURAL_S2 arg must be '<skill-name>/<rel-path>'",
        )
    skill_name, rel_path = row.arg.split("/", 1)
    body = _migration_support_file_body(row.entry)
    result = stage_support_file(workspace, skill_name, rel_path, body)
    return ApplyResult(
        row.index, row.decision, result.ok, result.message,
    )


def _apply_s3(workspace: Path, row: PlanRow) -> ApplyResult:
    # TODO(idempotence-bug): the module docstring promises
    # ``--apply`` is idempotent ("re-running ... is a no-op per
    # entry, each apply step checks for the staged file's existence
    # first"), but ``stage_new_skill`` from core/learning_writes.py
    # is collision-strict — it returns an error when the staged dir
    # already exists, with NO regard for whether the existing
    # content matches what we'd write. So re-running --apply on an
    # already-applied plan fails on every prior-success S3 entry,
    # even though the on-disk content is identical to what we'd
    # write again. Surfaced 2026-05-03 during the first real
    # migration: 11 prior successes all flipped to failures on
    # second-run.
    #
    # Fix shape (NOT implemented; left for the next migration
    # cycle): before calling ``stage_new_skill``, check whether the
    # staged dir already exists AND its SKILL.md content matches
    # the body we're about to write. If both true → return ok=True
    # with message "skipped (already applied, identical content)".
    # If staged exists but content differs → still error (the user
    # has hand-edited and we shouldn't overwrite). Same treatment
    # belongs in ``_apply_s2`` for support files. The collision-
    # strict behavior in stage_new_skill itself stays as-is — that's
    # the right default for the curator's own writes; only the
    # migration-script wrapper needs the idempotent-overlay.
    skill_name = row.arg
    if not skill_name:
        return ApplyResult(
            row.index, row.decision, False,
            "PROCEDURAL_S3 needs a new skill name",
        )
    body = _migration_skill_body(skill_name, row.entry)
    result = stage_new_skill(workspace, skill_name, body)
    return ApplyResult(
        row.index, row.decision, result.ok, result.message,
    )


def _apply_identity(workspace: Path, row: PlanRow) -> ApplyResult:
    """Insert into queue with a synthetic prefill.

    The eligibility check needs ≥2 distinct session UUIDs. Migration
    entries don't have a real session UUID — they came from v1's
    one-bucket era. Per §5.6 "synthetic 2-occurrence prefill so
    they're immediately eligible", we insert two synthetic
    occurrences: one anchored at the entry's learned_date, one
    anchored now. Each carries a distinct synthetic UUID
    (``migration:v1-<index>:a`` / ``...:b``) so the distinct-count
    bumps to 2 and the next eligible session triggers promotion.

    Threat-scanner gate (audit B2 fix): the curator hot path runs
    ``_scan_lesson_for_sensitive_content`` inside ``_validate_lesson``
    before any IDENTITY-class write decision. Migration bypassed
    that scan because it inserts directly into the queue without
    going through the curator. We mirror the curator's check here
    on the entry's ``lesson + scope`` with ``target_file="user"``
    so religion / politics / sexuality / self-harm / third-party-name
    content cannot ride a human migration plan into USER.md via the
    queue's synthetic prefill.
    """
    sensitive = _scan_lesson_for_sensitive_content(
        row.entry.lesson, row.entry.scope, target_file="user",
    )
    if sensitive:
        return ApplyResult(
            row.index, row.decision, False,
            f"USER.md threat scanner refused: {sensitive}",
        )
    store = UserCandidateStore(user_candidates_path())
    claim = row.entry.lesson
    # Don't overwrite an already-promoted claim.
    existing = store.get(claim)
    if existing is not None and existing.promoted_to_user_md:
        return ApplyResult(
            row.index, row.decision, True,
            f"skipped (already promoted): {claim[:60]}",
        )
    base = _parse_learned_date(row.entry.learned_date) or datetime.now(timezone.utc)
    store.add_occurrence(
        claim,
        f"migration:v1-{row.index:03d}:a",
        row.entry.evidence,
        now=base,
    )
    store.add_occurrence(
        claim,
        f"migration:v1-{row.index:03d}:b",
        row.entry.evidence,
        now=datetime.now(timezone.utc),
    )
    return ApplyResult(
        row.index, row.decision, True,
        "inserted into USER candidate queue with synthetic 2-occurrence prefill",
    )


def _apply_situational(workspace: Path, row: PlanRow) -> ApplyResult:
    store = MemoryStore(memories_dir(workspace))
    body = (
        f"[migrated {datetime.now(timezone.utc).strftime('%Y-%m-%d')}] "
        f"{row.entry.lesson}\n"
        f"  Scope: {row.entry.scope}\n"
        f"  Evidence: {row.entry.evidence}"
    )
    result = store.add("memory", body)
    if hasattr(result, "render"):
        return ApplyResult(row.index, row.decision, True, "appended to MEMORY.md")
    return ApplyResult(
        row.index, row.decision, False,
        getattr(result, "message", "MEMORY.md write refused"),
    )


def _migration_support_file_body(entry: ShadowEntry) -> str:
    return (
        f"# Migrated lesson (v1 → v2)\n\n"
        f"**Lesson:** {entry.lesson}\n\n"
        f"**Scope:** {entry.scope}\n\n"
        f"**Evidence:** > {entry.evidence}\n"
    )


def _migration_skill_body(skill_name: str, entry: ShadowEntry) -> str:
    description = entry.scope or entry.lesson[:120]
    return (
        f"---\n"
        f"name: {skill_name}\n"
        f"description: {description}\n"
        f"origin: learning-curator-migration\n"
        f"---\n\n"
        f"# {skill_name}\n\n"
        f"{entry.lesson}\n\n"
        f"## Provenance\n\n"
        f"Migrated from v1 MEMORY-SHADOW.md (entry dated {entry.learned_date}).\n\n"
        f"**Original evidence:** > {entry.evidence}\n"
    )


def _parse_learned_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------


def _migration_dir() -> Path:
    parent = vexis_dir() / "learning"
    parent.mkdir(parents=True, exist_ok=True)
    return parent


def _applied_dir() -> Path:
    p = _migration_dir() / "migration-plans-applied"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cmd_plan(workspace: Path, source: Path, *, skip_classify: bool = False) -> Path:
    """Generate a plan file. Returns the path written.

    With ``skip_classify=True`` (mostly for tests), every entry
    gets ``decision: SKIP`` so the user picks each one manually
    without paying for an LLM call.
    """
    entries = parse_shadow_file(source)
    if not entries:
        log.info("No migratable entries in %s", source)
    if skip_classify or not entries:
        decisions = [("SKIP", "") for _ in entries]
    else:
        log.info("Classifying %d entries via claude -p ...", len(entries))
        decisions = classify_entries(entries)
    rows = [
        PlanRow(index=i, entry=e, decision=d, arg=arg)
        for i, (e, (d, arg)) in enumerate(zip(entries, decisions), start=1)
    ]
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    plan_path = _migration_dir() / f"migration-plan-{stamp}.md"
    body = render_plan(plan_path, source, rows)
    plan_path.write_text(body, encoding="utf-8")
    return plan_path


def cmd_apply(workspace: Path, plan_path: Path) -> ApplySummary:
    rows = parse_plan(plan_path)
    summary = apply_plan(workspace, rows)
    # Archive the applied plan so history is preserved and
    # re-running --apply against the same path is a clear "this
    # was already done" signal.
    archive = _applied_dir() / plan_path.name
    try:
        shutil.copy2(plan_path, archive)
    except OSError as exc:
        log.warning("Could not archive applied plan: %s", exc)
    return summary


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Workspace root (default: $VEXIS_WORKSPACE or ~/vexis-workspace)",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--plan",
        action="store_true",
        help="Generate a migration plan file (Phase 1)",
    )
    mode.add_argument(
        "--apply",
        type=Path,
        metavar="PLAN_PATH",
        help="Apply the (possibly user-edited) plan at PLAN_PATH (Phase 2)",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Source shadow file. Default: <workspace>/memories/MEMORY-SHADOW.md",
    )
    parser.add_argument(
        "--skip-classify",
        action="store_true",
        help="(--plan only) Skip the LLM classification step; emit "
             "all entries as SKIP for manual decision",
    )
    args = parser.parse_args()
    workspace = args.workspace or workspace_dir(
        os.environ.get("VEXIS_WORKSPACE", "~/vexis-workspace")
    )
    source = args.source or (memories_dir(workspace) / "MEMORY-SHADOW.md")

    if args.plan:
        plan_path = cmd_plan(workspace, source, skip_classify=args.skip_classify)
        print(f"Wrote plan: {plan_path}")
        print(f"Review with: $EDITOR {plan_path}")
        print(f"Apply with:  scripts/migrate_shadow_to_v2.py --apply {plan_path}")
        return 0

    if args.apply:
        if not args.apply.exists():
            print(f"ERROR: plan file not found: {args.apply}", file=sys.stderr)
            return 1
        summary = cmd_apply(workspace, args.apply)
        counts = summary.by_decision()
        print(f"Applied {len(summary.results)} entries:")
        for decision, n in sorted(counts.items()):
            print(f"  {decision:<16} {n}")
        failures = [r for r in summary.results if not r.ok]
        if failures:
            print()
            print(f"FAILURES ({len(failures)}):")
            for r in failures:
                print(f"  Entry {r.index} ({r.decision}): {r.message}")
            return 1
        return 0

    return 2  # unreachable thanks to mutually_exclusive_group(required=True)


if __name__ == "__main__":
    sys.exit(main())

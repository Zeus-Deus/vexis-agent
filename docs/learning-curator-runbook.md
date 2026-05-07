# Learning curator — operational runbook

Operational reference for the v2 learning curator: recursion
guard, shadow-mode flip, two-tier review, coherence curator
(v3a), migration from v1, audit surfaces, eval gate. Standing
design facts live in `CLAUDE.md` and
`.plans/learning-curator-v2-research.md`; this file holds the
runtime / one-time recipes.

## Recursion guard

Each `claude -p` review fork writes a NEW session JSONL into
the same projects directory the curator scans for
eligibility — so without protections the curator would review
its own reviews on the next tick. Four mechanisms keep that
from happening:

1. **Persistent spawned-UUIDs registry** at
   `~/.vexis/learning/spawned.json`. Every review fork's
   session UUID is appended before the spawn returns;
   `list_eligible_sessions` unions this with the in-memory set
   so a daemon restart doesn't drop the exclusion list.

2. **Content-prefix filter.** `list_eligible_sessions` opens
   each candidate JSONL and skips any whose first user message
   starts with `CURATOR_REVIEW_PROMPT_PREFIX`. This is the
   load-bearing filter — env vars set on the spawn (e.g.
   `VEXIS_CURATOR=1`, `VEXIS_GOAL_JUDGE=1`) are forensic-only
   markers for audit logs; no curator code path reads them for
   filtering. The unit test `test_curator_prompt_invariant`
   asserts that the rendered prompt actually starts with the
   constant, so future prompt edits surface a test failure
   rather than a silent filter regression.

3. **Max-attempts cap** at `MAX_REVIEW_FAILURES=3`. After three
   consecutive failures the curator pins the session's
   `last_message_at_review_time` so the eligibility gate
   filters it until the user adds new content (which advances
   the JSONL's `last_message_timestamp` past the pinned
   snapshot, reopening eligibility). Bounds runaway retry
   loops on transcripts the verifier consistently rejects.

4. **Single-instance PID lock** at `~/.vexis/daemon.pid`
   (acquired in `main.acquire_daemon_lock` before any work).
   Two concurrent daemons can't fan out into each other's
   spawns; the second startup exits 2 with a clear pointer to
   the live PID.

### Cleanup

`scripts/clean_curator_jsonls.py` (dry-run by default,
`--apply` to act) moves curator-owned JSONLs out of a
workspace's projects directory and into a timestamped archive
under `~/.vexis/learning/curator-jsonl-archive/`. Use this if
you're cleaning up after a historical recursion event — the
four mechanisms above prevent recurrence on a fresh install.

Postmortem on the May 2026 fanout that motivated the four
mechanisms (in-memory-only registry didn't survive daemon
restart; 2,165 of 2,207 JSONLs in the workspace projects dir
were curator-owned reviews of past curator reviews):
`.plans/learning-curator-recursion-fix.md`.

## Shadow mode

`learning_shadow_mode=true` (the default) means:

- **Procedural writes** always go through `<workspace>/skills/.shadow/`
  regardless of the flag. The staging tree IS the shadow for skills
  — `iter_skill_dirs` excludes dotfile dirs so staged content is
  invisible to the system-prompt skill index.
- **MEMORY.md** writes route to `MEMORY-SHADOW.md` instead.
- **USER.md** writes (after the cross-session threshold fires) route
  to `USER-SHADOW.md` instead.

Disable shadow mode in `~/.vexis/config.yaml`:

```yaml
learning:
  shadow_mode: false
```

## Two-tier review (haiku triage)

Every eligible session first gets a cheap haiku triage call ("does
this session contain anything memorable? YES/NO"). Only YES sessions
escalate to the full sonnet review. About 85% of sessions return
"nothing to save" — triage skips the expensive sonnet call entirely
on those.

Knobs in `~/.vexis/config.yaml`:

```yaml
learning:
  triage_enabled: true   # default; set false to restore single-pass

models:
  learning_triage: haiku  # default; any model name resolve_model_flag accepts
  learning_review: sonnet # unchanged; runs only on YES verdicts
```

Failure modes:

- **Garbage / unparseable triage output** → fail open, sonnet runs.
  Logged at WARNING under `learning_review`.
- **Triage timeout (90 s) or spawn error** → fail open.
- **Rate-limit hit on triage** → propagated as a review error so the
  curator's tick-abort path triggers. Sonnet is NOT called.

The audit trail records `triage_skipped` and `triage_result`
(`YES` / `NO` / `FAIL_OPEN` / `ERROR` / `DISABLED`) per session so
parse-failure rates and quality drift are visible in `run.json`.

## Coherence curator (v3a)

Third curator. Runs inline inside the learning curator's tick:
for every verified lesson, a `claude -p` "judge" call decides
whether the lesson body is properly grounded in the cited
evidence string. **Advisory-only — never blocks a write.**

Three verdicts:

- **COHERENT** — silent; no annotation written.
- **NEAR_MISS_REVIEW** — soft annotation in the shadow file,
  so borderline cases stay auditable.
- **INCOHERENT** — hard `Coherence: FLAGGED (<reason>)`
  annotation in the shadow file.

### Five surfaces

1. **Inline `Coherence:` line** in `MEMORY-SHADOW.md` /
   `USER-SHADOW.md` / staged `SKILL.md` entries. The
   annotation travels with the lesson.
2. **`## Coherence flags` section in per-tick `REPORT.md`** —
   omitted when empty so clean ticks don't carry boilerplate.
3. **`Coherence flags (last N tick reports):` row in
   `/learning audit`** — Telegram surface for at-a-glance
   tracking.
4. **`summary.coherence = {flagged, near_miss, by_reason}` in
   `run.json`** — machine-readable for the dashboard's
   Learning tab.
5. **`/learning coherence-audit [--shadow-only]`** — re-judge
   already-promoted entries on demand. Degraded mode (no
   transcript context); useful for periodic sweeps over the
   live MEMORY / USER / SKILLS to catch drift the original
   judge missed.

Configuration knobs in `~/.vexis/config.yaml`:

```yaml
models:
  coherence_judge: sonnet  # default; tier 'small' on the
                           # new schema (legacy raw-string
                           # passthrough on claude-code)
```

Full design + prompt: `.plans/coherence-curator-research.md`.

## Soak windows (recommended; from `.plans/learning-curator-v2-research.md` §3.4)

| Target | Window | Why |
|---|---|---|
| Skills + MEMORY.md | 1 week | Tests the writing pipeline (threat scanner, dedup, staging). One work-week is enough variation. |
| USER.md | 2 weeks | Tests identity stability across day-of-week / mood variance. Two work-week samples are needed to surface contradictory claims. |

## Manual flip flow

After the soak passes for a given target:

```sh
# Skills: per-skill or all-at-once into the live tree
vexis-skill flip-shadow [--all | --skill NAME] [--dry-run]

# MEMORY (after Week 1 soak)
mv ~/vexis-workspace/memories/MEMORY-SHADOW.md \
   ~/vexis-workspace/memories/MEMORY.md

# USER (after Week 2 soak)
mv ~/vexis-workspace/memories/USER-SHADOW.md \
   ~/vexis-workspace/memories/USER.md
```

Then disable shadow mode (above).

## v1 → v2 migration (one-time)

Three phases. The script is non-destructive (`MEMORY-SHADOW.md` is
read-only), idempotent (re-running `--apply` skips already-staged
entries), and resumable (a partial apply continues on retry).

```sh
# Phase 1: generate plan; classifies entries via batch claude -p
scripts/migrate_shadow_to_v2.py --plan
# → wrote ~/.vexis/learning/migration-plan-<utc>.md

# Phase 2: review / edit `decision:` lines per entry
$EDITOR ~/.vexis/learning/migration-plan-<utc>.md

# Phase 3: apply
scripts/migrate_shadow_to_v2.py --apply ~/.vexis/learning/migration-plan-<utc>.md
```

Allowed `decision:` values: `PROCEDURAL_S1 <skill>`,
`PROCEDURAL_S2 <skill>/<rel-path>`, `PROCEDURAL_S3 <new-skill>`,
`IDENTITY`, `SITUATIONAL`, `DROP`, `SKIP`. SKIP is the escape hatch
for any entry the user wants to defer; SKIP entries reappear in the
next plan generation.

Applied plans archive to `~/.vexis/learning/migration-plans-applied/`.

## Audit & reporting

- **Telegram**: `/learning audit` surfaces curator-authored skills
  (live + staged), USER candidate queue with pending claims +
  days-until-expiry, recent dedup-skipped count, and shadow/live
  entry breakdown.
- **Per-tick reports** at `~/.vexis/logs/learning/<utc>/`:
  - `REPORT.md` — human-readable narrative including the new
    Day-5 "Write summary" section (classification counts, per-tier
    write counts, dedup-skipped, queue-added, queue-promoted,
    stage-refused).
  - `run.json` — same counts in machine-readable form under the
    `summary` key, for later dashboard work.

## Eval gate

Before flipping shadow → live for ANY target, run:

```sh
scripts/eval_learning.py
```

Pass bar (per `.plans/learning-curator-v2-research.md` §4.3):

| Grade | What | Bar |
|---|---|---|
| G1 | Promoted at all (or correctly skipped on dedup) | strict (denominator) |
| G2 | Verbatim evidence verifies | strict |
| G3 | Same-class probe APPLIES | ≥6/8 of probed |
| G4 | Different-class probe doesn't misfire | strict |
| G5 | Routes to correct class+tier | strict |
| G6 | Skill update vs create correctness | strict |
| G7 | Memory dedup works (semantic + exact-evidence variants) | strict |
| G8 | USER.md threshold respected | strict |

Cost: ~30-40 LLM calls per run (~3 minutes). Reports land at
`~/.vexis/logs/learning-eval/<utc>/REPORT.md`.

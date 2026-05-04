# Learning curator — operational runbook

Operational reference for the v2 learning curator: shadow-mode flip,
migration from v1, audit surfaces, eval gate. Standing design facts
live in `CLAUDE.md` and `.plans/learning-curator-v2-research.md`;
this file holds the runtime / one-time recipes.

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

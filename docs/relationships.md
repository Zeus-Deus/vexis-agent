# Relationships

Vexis remembers third-party people you mention in conversation —
your mom, your coworker, your partner — and stores facts about
them in `RELATIONSHIPS.md` so the brain can refer back across
sessions. Capture is silent and capture-first; *promotion* to
the brain-readable file requires your approval.

## How it works

1. **Silent capture.** A learning-curator tick (every ~5 minutes
   over recently-idle sessions) runs a haiku extractor over the
   transcript. Third-party facts get queued at
   `<workspace>/.vexis/relationships-candidates.json`. The brain
   never sees this file.
2. **Eligibility gate.** A candidate becomes "eligible for
   approval" once it crosses a recurrence threshold:
   - **Strong cues** (mom, dad, partner, sibling, child) →
     eligible after 1 session. The relationship is structural;
     one mention is real.
   - **Soft / weak cues** (friend, coworker, no qualifier) →
     eligible after ≥2 distinct sessions within 30 days. Filters
     one-off mentions of people who aren't really part of your
     life.
3. **Approval.** You promote candidates to live `RELATIONSHIPS.md`
   via one of three surfaces (below). The brain then sees the
   facts on its **next** session spawn.

## Approval surfaces

### Dashboard (Learning tab → Relationships panel)

The richest surface. Shows every pending candidate with per-fact
toggles, edit-in-place text, and a "show rejected" reveal. Use
this when you want to:

- Approve a subset of facts (not all) under a person.
- Edit a fact's wording before saving.
- Pick a qualifier to disambiguate two same-name people.
- See full source-turn pointers for each observation.

A modal handles the "two people, same first name, neither has a
qualifier yet" case: it asks you to assign a qualifier to the
existing entry first, then lands the new one.

### Telegram slash commands

Quick, whole-person approve from your phone:

- `/learning relationships-pending` — list pending candidates +
  eligibility states.
- `/learning relationships-approve <slug>` — approve all current
  facts under one slug.
- `/learning relationships-reject <slug>` — tombstone (silent
  drop on future re-extractions).
- `/learning relationships-digest` — formatted summary, useful
  if you want a daily/weekly self-review without the dashboard.

Per-fact granularity is dashboard-only — Telegram callbacks can't
carry slug + fact-id pairs cleanly.

### Restore (after a rejection)

If you rejected someone you later want to track again, the
dashboard's "show rejected" toggle reveals tombstoned slugs. A
restore action clears the tombstone; eligibility re-evaluates
from the existing observations.

## The `/clear` semantics

When you approve a candidate, the fact lands in
`RELATIONSHIPS.md` immediately, but the **running brain session
has a cached system prompt** — it won't see the new fact until
the cache rotates. Cache rotates when:

- You run `/clear` in Telegram (most direct).
- You run `/new` to start a fresh named session.
- You run `/switch <name>` to a different session.
- The brain spawns a brand-new session (e.g., daemon restart).

Vexis appends a one-line reminder ("Active in your next session
— `/clear` to start fresh.") after each successful approve. If
you've internalised the model, suppress the reminder via
`relationships.approval_hint_enabled: false` in
`~/.vexis/config.yaml`.

## Privacy stance

The third party is the person you're storing facts about, and
they haven't consented. Vexis takes that seriously:

- **Silent extraction is *capture*-first, not *promotion*-first.**
  Nothing reaches the brain until you click approve.
- **Sensitive content drops at extract-time.** The standard
  threat-scanner stack (medical / legal / financial / religion /
  politics / sexuality / self-harm / mental-health) runs over
  every extracted fact before it's queued. Hits drop silently.
  At approval time, the scanner runs again as a second pass.
- **Total deletion is one click / one slash command.** Reject a
  slug and Vexis stops capturing that person; delete a live
  entry via the dashboard or `/learning relationships-restore`'s
  inverse path. The archive is local-only.
- **No export by default.** No upload, no analytics, no
  cross-system consolidation. The candidate JSON, the live MD,
  and the archive MD all live under `<workspace>/`.
- **No archive-curator consolidation.** Vexis does not
  cross-reference relationship facts to build a network or
  infer relationships you didn't state. One person, one block
  of facts you approved.

## Configuration cheat sheet

`~/.vexis/config.yaml`:

```yaml
relationships:
  # Default off; legacy v3b "remember that..." path. Enable for
  # phrasings that save immediately without the candidate queue.
  explicit_consent_enabled: false

  # Default true; appends a /clear reminder after approve.
  approval_hint_enabled: true

models:
  # Default sonnet (v3c Day 5 release gate). Override to haiku
  # if cost matters more than reliability — but the Day 5 eval
  # showed haiku hovering around 83% positive on the test
  # corpus, below the 85% release threshold. Sonnet hit 100%.
  relationships_extractor: sonnet
```

## Eval gate

The extractor has an integration eval at
`tests/relationships/test_extractor_eval.py`. Run when prompts
or fixtures change:

```
pytest tests/relationships/ -m eval
```

Thresholds:

- **0 sensitive leaks** (hard gate; v3c does not ship if any).
- **≥85% positive accuracy.**
- **≥95% negative accuracy.**

v3c shipped at sonnet (100% positive on Day 5 release-gate
eval). If you've overridden to haiku and the eval drops,
flip back via `models.relationships_extractor: sonnet`. Note
that LLM evals have run-to-run variance; one failed run on a
borderline-passing model isn't conclusive evidence — re-run a
couple of times before concluding.

## Diagnostic surfaces

- **REPORT.md per tick:** the learning-curator tick log surfaces
  `extractor_runs / extractor_errors / candidates_queued /
  candidates_eligible / candidates_approved /
  candidates_rejected / approve_blocked_*` counters.
- **Daemon log:** every dashboard mutation (approve / reject /
  edit / resolve_qualifier) emits a structured INFO line with
  action + slug + fact_ids + token fingerprint + timestamp.
- **Audit-driven re-judging:** the dashboard's "show rejected"
  toggle is your one-stop "did I tombstone someone I want
  back?" surface.

Full system design: `.plans/relationships-v3c-research.md`.

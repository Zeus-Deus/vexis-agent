# Skill version history

Skills are procedural knowledge that gets edited over time — by Vexis
mid-session, by the background curator, or by you in the dashboard.
Version history makes every one of those edits recoverable: each
change snapshots the *previous* `SKILL.md` so you can see what a
skill used to say, diff it against the current version, and roll
back if an edit made things worse.

This is the safety net for the riskiest write path — Vexis patching a
live skill while it works. Before this existed, an unpinned skill
could be quietly rewritten with no way to see the prior text.

## What gets captured

Every edit to a **workspace** skill's `SKILL.md` snapshots the
pre-edit content. Three write paths feed it:

| Editor | When | Actor label |
|---|---|---|
| Vexis, in-session | `vexis-skill edit` / `patch` during a task | `agent` |
| Curator | learning-curator flip + consolidation pass | `curator` |
| You | dashboard Skills tab → Edit / Restore | `dashboard` |

Not captured: skill **creation** (there's no prior version to
supersede — history starts at the first edit), supporting files
under `references/` / `templates/` / `scripts/` (only `SKILL.md` is
versioned), and bundled / installed skills (read-only — they never
take edits).

## Where it lives

```
<workspace>/skills/.history/<skill-name>/<recorded-at>__<actor>.md
```

`recorded-at` is a compact UTC stamp (microsecond precision, so two
edits in the same second don't collide) and doubles as the version
id in API routes. The file body is the verbatim `SKILL.md` as it was
*before* the edit. A dotfile directory, so `iter_skill_dirs` skips it
— history snapshots never leak into the system-prompt skill index.

Retention: the newest 20 snapshots per skill are kept; older ones are
pruned on each write. Snapshotting is best-effort — a failed snapshot
never blocks the skill edit itself (same two-tier rule as `.usage.json`
telemetry).

## Dashboard

Expand any workspace skill in the **Skills** tab and click
**History**. The modal shows:

- **Timeline** (left) — every past version, newest first, each tagged
  with who made the edit that replaced it. A `Current` marker anchors
  the head of the timeline.
- **Changes** tab — a unified diff of the selected version against
  the current live skill, so you see exactly what changed since.
- **Full version** tab — the selected version's `SKILL.md` rendered
  as markdown.
- **Restore this version** — overwrites the live skill with the
  selected version. The restore is itself an edit, so the
  pre-restore state is snapshotted first — every revert is
  reversible. Restoring a pinned skill temporarily unpins it and
  re-pins immediately after.

## API

All routes are auth-gated and workspace-skill only.

| Route | Returns |
|---|---|
| `GET /api/v1/skills/{name}/history` | Version list (id, actor, timestamp, size). |
| `GET /api/v1/skills/{name}/history/{version_id}` | Full content, parsed body/description, and a unified diff vs. the current skill. |
| `POST /api/v1/skills/{name}/history/{version_id}/restore` | Reverts the skill to that version. Body: `{force_unpin?: bool}`. |

## Code

- `core/skill_history.py` — storage: `record_version`, `list_versions`,
  `read_version`. Pure filesystem, no dependency on `core/skills.py`
  (one-way import so there's no cycle).
- `core/skills.py` — `edit_skill` / `patch_skill` snapshot the
  pre-edit content; both take an `actor` keyword.
- `core/learning_writes.py` — `_flip_one` snapshots before a curator
  S1 flip overwrites a live skill.
- `tools/skills_cli.py` — `_actor()` resolves `curator` vs `agent`
  from the `VEXIS_CURATOR` env marker.
- `core/web_server.py` — the three history routes above.

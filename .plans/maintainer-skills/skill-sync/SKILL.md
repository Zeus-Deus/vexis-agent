> **Maintainer-only skill — do not bundle.** Pushes to the
> maintainer's vexis-agent-site repo. Useless to anyone else.
>
> **Install on the home agent:**
> 1. Dashboard → Skills → New Skill
> 2. Name: `skill-sync`
> 3. Body: paste everything from the `---` line below to EOF
> 4. Save → click row → Pin
>
> Schedule separately (whenever you want) via Telegram:
> `/schedule <when> run the skill-sync skill`

---
name: skill-sync
description: Refresh the public Skills catalog on vexis-agent-site by scraping the upstream Hermes Agent repo, normalising each SKILL.md's frontmatter into a JSON manifest, and committing + pushing the result if anything changed. Load this when asked to refresh the catalog or when the public Skills page looks stale.
---

# skill-sync

## What it does

Scrape `github.com/NousResearch/hermes-agent`, walk every `SKILL.md`
under `skills/` and `optional-skills/`, build a JSON manifest of
`{name, description, category, tier, platforms, installUrl}`,
write it to `~/projects/vexis-agent-site/src/content/community-skills.json`,
and commit + push if the file changed. Report back a one-line summary.

## Algorithm

1. **Clone upstream into a temp dir:**
   ```
   git clone --depth 1 --branch main \
     https://github.com/NousResearch/hermes-agent.git "$WORKDIR/upstream"
   ```
   Bail with a clear error if `upstream/skills/` is missing — that's
   an upstream layout change that needs human attention.

2. **Walk + parse.** For every `SKILL.md` under `upstream/skills/`
   and `upstream/optional-skills/`:
   - Read the YAML frontmatter (between the first two `---` lines).
   - Extract `name` and `description` (strings, both required).
   - Skip silently on missing fields, malformed YAML, or unreadable file.
   - Derive `category` from the path: `skills/<category>/<name>/SKILL.md`
     → `<category>` (lowercase, hyphenated as upstream has it).
   - Derive `tier`: `skills/` → `"optional"`; `optional-skills/` →
     `"community"`. (Vexis's `"bundled"` tier is reserved for
     vexis-authored skills, NOT for upstream-built-in ones.)
   - Derive `platforms` from the frontmatter's `platforms` field;
     fall back to `["linux", "macos", "windows"]` if missing.
   - Build `installUrl` as the GitHub blob URL:
     `https://github.com/NousResearch/hermes-agent/blob/main/<rel-path>`.
     (Vexis's install path rewrites blob → raw automatically.)

3. **Sort deterministically:** by `(tier, category, name)` so the
   JSON diff is stable across runs.

4. **Compose the manifest:**
   ```json
   {
     "source": "Hermes Agent",
     "source_repo": "NousResearch/hermes-agent",
     "generated_at": "<ISO-8601 UTC>",
     "skills": [ ... ]
   }
   ```

5. **Write only if something actually changed.** Read the existing
   `community-skills.json` (if any). Compare the new `skills` array
   against the old one. If identical, write the file with the OLD
   `generated_at` so `git diff --quiet` is true and you exit clean
   without committing. If different, stamp the current UTC time.

6. **Commit + push if `git diff` is non-empty.** Commit message:
   `skill-sync: refresh catalog (<count> skills)`. Then
   `git push origin main` — that triggers the GHA site deploy.
   On `git push` rejection, `git pull --rebase` once and retry; bail
   on second failure.

7. **Report back one line** to chat: `<count> skills, <added>/<removed>/<changed>, deploy in flight` — or `catalog already up to date (<count> skills)` on the no-op path.

## Output schema (target file)

`~/projects/vexis-agent-site/src/content/community-skills.json`:

```json
{
  "source": "Hermes Agent",
  "source_repo": "NousResearch/hermes-agent",
  "generated_at": "2026-01-01T00:00:00Z",
  "skills": [
    {
      "name": "apple-notes",
      "description": "Manage Apple Notes via memo CLI: create, search, edit.",
      "category": "apple",
      "tier": "optional",
      "platforms": ["macos"],
      "installUrl": "https://github.com/NousResearch/hermes-agent/blob/main/skills/apple/apple-notes/SKILL.md"
    }
  ]
}
```

Cap each `description` at 280 chars (the card layout truncates anyway).

## Rules

- **Idempotent.** Running back-to-back must produce zero git diff.
- **Per-skill failures are silent skips.** Don't bail the whole
  run on one bad SKILL.md.
- **Layout breakage is a hard fail.** Zero skills found, missing
  `skills/` dir, or upstream clone failure → exit non-zero and
  report the actual error.
- **Never delete `community-skills.json`** even if the walk returns
  zero skills — that's a bug, not "nothing to do".
- **Only touch `community-skills.json`** in the site repo. Never
  edit `Skills.tsx` or any other site file.

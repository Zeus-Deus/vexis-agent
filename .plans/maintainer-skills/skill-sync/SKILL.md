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

Scrape `github.com/NousResearch/hermes-agent` for skills from THREE
source families and unify them into one JSON manifest:

- `skills/` — Hermes "built-in" SKILL.md files
- `optional-skills/` — Hermes "optional" SKILL.md files
- `skills/index-cache/` — pre-aggregated metadata for upstream
  registries (Anthropic, Claude Marketplace; SKIP `lobehub_index.json`
  because LobeHub items are agent personas, not installable skills,
  and SKIP empty caches like `openai_skills_skills_.json`)

Write the unified manifest to
`~/projects/vexis-agent-site/src/content/community-skills.json` and
commit + push if the file changed. Report back a one-line summary.

## Algorithm

1. **Cheap-check first — skip if upstream skill paths haven't changed.**
   Hermes ships frequent commits to non-skill files; pulling on every
   schedule fire would waste tokens 90%+ of the time. Hit GitHub's
   tree API to get just the SHAs of the directories we care about:
   ```bash
   curl -s https://api.github.com/repos/NousResearch/hermes-agent/git/trees/main \
     | jq '.tree[] | select(.path == "skills" or .path == "optional-skills") | {path, sha}'
   ```
   Returns two `{path, sha}` rows. Compare against the
   `source_tree_shas` field in the existing
   `~/projects/vexis-agent-site/src/content/community-skills.json`:
   - If the file doesn't exist OR `source_tree_shas` is missing →
     proceed (first-ever run, no baseline to compare).
   - If both stored SHAs match the live ones → exit clean. Report:
     `skill-sync: no upstream skill changes (skills@<short-sha>,
     optional@<short-sha>), exit clean`.
   - If either SHA differs → proceed to step 2.

   Why this catches everything: index-cache changes (Anthropic /
   Marketplace adding entries) live INSIDE `skills/`, so its tree SHA
   moves whenever any descendant file changes.

   No GitHub auth needed (public repo, 1 call/day, well under the
   60/hr unauthenticated limit).

2. **Clone upstream into a temp dir:**
   ```
   git clone --depth 1 --branch main \
     https://github.com/NousResearch/hermes-agent.git "$WORKDIR/upstream"
   ```
   Bail with a clear error if `upstream/skills/` is missing — that's
   an upstream layout change that needs human attention.

3. **Walk + parse SKILL.md files** under `upstream/skills/` and
   `upstream/optional-skills/`. For each:
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
   - Set `source` to `"Hermes Agent"`.

4. **Walk + parse index-cache JSONs** under
   `upstream/skills/index-cache/`. SKIP `lobehub_index.json` (different
   schema — agent personas, not installable). SKIP files whose JSON
   parses to an empty array. For the remaining files, the filename
   prefix maps to a source label:

   ```
   anthropics_skills_…              → "Anthropic"
   claude_marketplace_…             → "Claude Marketplace"
   openai_skills_…                  → "OpenAI"
   (any other prefix)               → titlecased prefix
   ```

   Each entry in those arrays has shape
   `{name, description, repo, path, tags, ...}`. For each:
   - Skip silently on missing `name`, `description`, `repo`, or `path`.
   - Set `category` to `"general"` if no useful path-derived
     category exists. (Anthropic skills live at `skills/<name>/` with
     no subcategory; just use `"general"`.)
   - Set `tier` to `"community"`.
   - Set `platforms` to `["linux", "macos", "windows"]` (the cache
     entries don't carry per-platform info).
   - Build `installUrl` from the entry's own repo+path:
     `https://github.com/<repo>/blob/main/<path>/SKILL.md`.
   - Set `source` to the source label derived above.

5. **Sort deterministically:** by `(tier, source, category, name)` so
   the JSON diff is stable across runs.

6. **Compose the manifest** with a `generated_at` timestamp, the
   `source_tree_shas` block (the SHAs you fetched in step 1), and the
   sorted `skills` array (see Output schema below).

7. **Write only if something actually changed.** Read the existing
   `community-skills.json` (if any). Compare the new `skills` array
   against the old one. If identical, write the file with the OLD
   `generated_at` so `git diff --quiet` is true and you exit clean
   without committing. If different, stamp the current UTC time.
   The `source_tree_shas` block is always updated to the live values
   (otherwise the cheap-check in step 1 would never short-circuit on
   the next fire).

8. **Commit + push if `git diff` is non-empty.** Commit message:
   `skill-sync: refresh catalog (<count> skills)`. Then
   `git push origin main` — that triggers the GHA site deploy.
   On `git push` rejection, `git pull --rebase` once and retry; bail
   on second failure.

9. **Report back one line** to chat with the totals: total count, the
   per-source breakdown, and the diff vs the prior run
   (added/removed/changed). On the step-1 no-op path: `no upstream
   skill changes (<short-shas>), exit clean`. On the step-7 no-op
   path: `catalog already up to date (<count> skills)`.

## Output schema (target file)

`~/projects/vexis-agent-site/src/content/community-skills.json`:

```json
{
  "generated_at": "2026-01-01T00:00:00Z",
  "source_tree_shas": {
    "skills": "abc123def4567890…",
    "optional-skills": "789def0123456abc…"
  },
  "skills": [
    {
      "name": "apple-notes",
      "description": "Manage Apple Notes via memo CLI: create, search, edit.",
      "category": "apple",
      "tier": "optional",
      "platforms": ["macos"],
      "installUrl": "https://github.com/NousResearch/hermes-agent/blob/main/skills/apple/apple-notes/SKILL.md",
      "source": "Hermes Agent"
    },
    {
      "name": "algorithmic-art",
      "description": "Creating algorithmic art using p5.js …",
      "category": "general",
      "tier": "community",
      "platforms": ["linux", "macos", "windows"],
      "installUrl": "https://github.com/anthropics/skills/blob/main/skills/algorithmic-art/SKILL.md",
      "source": "Anthropic"
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

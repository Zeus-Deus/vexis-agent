---
name: skill-sync
description: Nightly job that scrapes upstream skill catalogs (Hermes Agent built-in + optional skills), normalises their metadata, writes a JSON catalog into the vexis-agent-site repo, commits + pushes (which triggers the GHA deploy), and reports a one-line summary back to chat. Load this when the user asks to refresh the public skills catalog, when scheduling a recurring catalog refresh, or when investigating why the public Skills page is stale.
---

# skill-sync — refresh the public skills catalog

## What this does

The vexis-agent-site Skills page (`https://vexis-agent.com/skills`)
shows a catalog of skills users can install. The catalog has two
parts:

- **Bundled** — skills that ship with vexis core (kanban-*, etc.).
  Hardcoded in `src/pages/Skills.tsx`. NEVER touch these.
- **Community / upstream** — a JSON file at
  `src/content/community-skills.json` that this skill rewrites on
  every run. The site bundles that JSON at build time.

The flow is:

1. Clone the upstream source repo (Hermes Agent by default)
2. Walk `skills/**/SKILL.md` and `optional-skills/**/SKILL.md`
3. Parse each frontmatter, derive metadata, build install URL
4. Compose a deterministic JSON manifest (sorted, stable)
5. Write to `~/projects/vexis-agent-site/src/content/community-skills.json`
6. If git diff is non-empty: commit + push → GHA deploys the site
7. If diff is empty: log "no changes" and exit clean
8. Report a one-line summary back to chat (counts, source, dur)

The public catalog **never holds the SKILL.md content itself** —
only metadata + a raw GitHub URL pointing back to upstream. Users
copy the install command from the card; vexis-agent on their
machine then fetches the upstream raw file at install time.

## When to use

- **Scheduled** — nightly via `/schedule every day at 2:30am run
  the skill-sync skill`. This is the normal mode.
- **Manual** — when the user asks to refresh the catalog now, or
  after a change to this skill itself.
- **Smoke-test** — once after a vexis-agent-site repo move or a
  change to the JSON schema.

Do NOT run this skill from a kanban worker that's also doing other
unrelated work — keep it isolated so failures don't poison
unrelated state.

## Prerequisites

- `git` available on PATH
- `gh` CLI authenticated (used as a fallback if cloning fails;
  primary path uses plain `git clone`)
- The vexis-agent-site repo cloned at
  `~/projects/vexis-agent-site` with a working `git push`
  (deploy key already in `~/.ssh/`, branch `main` set up to track
  origin)
- ~50MB of free disk space for the temp clone of upstream
- Network access to `github.com`

## Configuration

Defaults shown; override only if the user has explicitly asked.

| Knob | Default | Notes |
|---|---|---|
| Source repo | `https://github.com/NousResearch/hermes-agent` | The upstream Hermes Agent source — has both built-in and optional skills. |
| Source branch | `main` | |
| Built-in path | `skills/` | First tier on the catalog (rendered as `optional` in vexis-agent-site terminology because vexis "bundled" is reserved for vexis's own kanban-* skills). |
| Optional path | `optional-skills/` | Second tier; rendered as `community`. |
| Site repo | `~/projects/vexis-agent-site` | |
| Output JSON | `src/content/community-skills.json` (relative to site repo) | |
| Commit message prefix | `skill-sync:` | |
| Max skills per source | 1000 | Safety cap; flag if exceeded. |

## Algorithm — concrete steps

Run each step in order. Bail early on prerequisite failures (don't
proceed to commit if the parse step found zero skills — that's
almost certainly a broken upstream change, not "nothing to do").

### Step 1 — set up the temp workspace

```bash
WORKDIR=$(mktemp -d -t skill-sync.XXXXXX)
trap "rm -rf $WORKDIR" EXIT
SITE_REPO="$HOME/projects/vexis-agent-site"
test -d "$SITE_REPO/.git" || { echo "site repo not at $SITE_REPO"; exit 2; }
```

### Step 2 — clone upstream, depth 1

```bash
cd "$WORKDIR"
git clone --depth 1 --branch main \
  https://github.com/NousResearch/hermes-agent.git upstream
test -d upstream/skills || { echo "upstream layout changed: no skills/ dir"; exit 3; }
```

### Step 3 — walk + parse

For each `SKILL.md` under `upstream/skills/` and
`upstream/optional-skills/`, parse the YAML frontmatter and build
an entry:

```python
# (run via python -c "..." or write a temp .py script)
import json, sys, yaml
from pathlib import Path

UPSTREAM_RAW = "https://raw.githubusercontent.com/NousResearch/hermes-agent/main"
SOURCE_LABEL = "Hermes Agent"
SOURCE_REPO = "NousResearch/hermes-agent"

def walk(root: Path, tier: str, subtree: str) -> list[dict]:
    entries: list[dict] = []
    for skill_md in sorted(root.rglob("SKILL.md")):
        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue
        if not text.startswith("---"):
            continue
        try:
            _, fm_yaml, _body = text.split("---", 2)
        except ValueError:
            continue
        try:
            fm = yaml.safe_load(fm_yaml) or {}
        except yaml.YAMLError:
            continue
        name = (fm.get("name") or "").strip()
        desc = (fm.get("description") or "").strip()
        if not name or not desc:
            continue
        rel = skill_md.relative_to(root.parent)  # e.g. skills/apple/apple-notes/SKILL.md
        category = rel.parts[1] if len(rel.parts) > 2 else subtree
        platforms = fm.get("platforms") or ["linux", "macos", "windows"]
        if isinstance(platforms, str):
            platforms = [platforms]
        # Use the GitHub blob URL (vexis-skill install rewrites it
        # to raw automatically — see core/skill_install.py).
        install_url = (
            f"https://github.com/{SOURCE_REPO}/blob/main/{rel.as_posix()}"
        )
        entries.append({
            "name": name,
            "description": desc[:280],  # cap to fit the card
            "category": category,
            "tier": tier,
            "platforms": platforms,
            "installUrl": install_url,
            "source": SOURCE_LABEL,
        })
    return entries

upstream = Path(sys.argv[1])
items = []
items += walk(upstream / "skills", tier="optional", subtree="general")
items += walk(upstream / "optional-skills", tier="community", subtree="general")
# Stable order: by tier, then category, then name.
items.sort(key=lambda x: (x["tier"], x["category"], x["name"]))
print(json.dumps({
    "source": SOURCE_LABEL,
    "source_repo": SOURCE_REPO,
    "generated_at": "GENERATED_AT_PLACEHOLDER",
    "skills": items,
}, indent=2, sort_keys=False))
```

Run it:

```bash
python3 /tmp/sync.py "$WORKDIR/upstream" > "$WORKDIR/out.json"
SKILL_COUNT=$(jq '.skills | length' "$WORKDIR/out.json")
test "$SKILL_COUNT" -gt 10 || { echo "suspicious low count $SKILL_COUNT, aborting"; exit 4; }
```

### Step 4 — stamp the timestamp deterministically

The `generated_at` field is the only thing that changes when no
skills change. To keep the diff clean, replace it with the value
from the existing file IF the rest is identical; otherwise stamp
now.

```bash
EXISTING="$SITE_REPO/src/content/community-skills.json"
if [ -f "$EXISTING" ]; then
  EXISTING_TS=$(jq -r '.generated_at' "$EXISTING" 2>/dev/null || echo "")
  EXISTING_SKILLS=$(jq '.skills' "$EXISTING")
  NEW_SKILLS=$(jq '.skills' "$WORKDIR/out.json")
  if [ "$EXISTING_SKILLS" = "$NEW_SKILLS" ]; then
    # No change — keep the old timestamp so git sees no diff.
    sed -i "s|GENERATED_AT_PLACEHOLDER|$EXISTING_TS|" "$WORKDIR/out.json"
  else
    sed -i "s|GENERATED_AT_PLACEHOLDER|$(date -u +%Y-%m-%dT%H:%M:%SZ)|" "$WORKDIR/out.json"
  fi
else
  sed -i "s|GENERATED_AT_PLACEHOLDER|$(date -u +%Y-%m-%dT%H:%M:%SZ)|" "$WORKDIR/out.json"
fi
```

### Step 5 — write to site repo, commit if diff

```bash
mkdir -p "$SITE_REPO/src/content"
cp "$WORKDIR/out.json" "$SITE_REPO/src/content/community-skills.json"
cd "$SITE_REPO"

if git diff --quiet src/content/community-skills.json; then
  echo "skill-sync: no changes ($SKILL_COUNT skills, identical to last run)"
  exit 0
fi

DIFF_SUMMARY=$(git diff --stat src/content/community-skills.json | tail -1)
git add src/content/community-skills.json
git commit -m "skill-sync: refresh catalog ($SKILL_COUNT skills)

Source: NousResearch/hermes-agent@main
$DIFF_SUMMARY"
git push origin main
```

### Step 6 — report back

After successful push (or no-op), report a single line to chat:

> skill-sync: 167 skills from Hermes Agent (12 added, 3 removed, 0 changed). Site deploy in flight.

If it was a no-op:

> skill-sync: catalog already up to date (167 skills, last refreshed 2026-05-12T02:30:01Z).

If it failed at any step, report the step and the error message.

## Idempotency

The whole flow is idempotent in the sense that running it twice
back-to-back makes no difference. Specifically:

- The output JSON is sorted deterministically (tier → category → name)
- The `generated_at` timestamp is preserved when the skill list is
  unchanged, so git sees zero diff
- `git diff --quiet` is the gate before commit + push
- Failed clones don't touch the site repo (the temp dir is
  separate, and `cp` only happens after parse succeeds)

## Per-source error isolation

If a single SKILL.md is malformed (bad YAML, missing `name`), it's
silently skipped. Don't bail the whole run on one bad file.

If the `skills/` walk fails entirely (e.g. upstream renamed the
dir), exit non-zero with a clear message — that's a real upstream
breakage that needs human attention.

If `git push` fails (e.g. someone pushed to the site repo at the
same time), retry once after `git pull --rebase`. Bail on second
failure with the conflict details.

## What this skill must NOT do

- Do not push commits to `vexis-agent` (the main package) — only
  to `vexis-agent-site` (the public website).
- Do not modify `src/pages/Skills.tsx` — only the JSON file.
- Do not delete the JSON file even if the upstream walk returns
  zero skills (treat that as an error and exit non-zero).
- Do not auth as the user; use the deploy key already in
  `~/.ssh/` for the site repo.

## Adding a new upstream source

To scrape a second upstream (e.g. a future community-curated
repo), edit this skill's `walk(...)` calls in step 3 to add a new
source. Each entry's `source` field shows up on the card so users
know where it came from.

Note: vexis-agent-site is single-tenant (only the user maintains
it), so changing this skill takes effect on the next scheduled
run after a `vexis-agent` redeploy.

## Scheduling

Recommended schedule:

> /schedule every day at 2:30am run the skill-sync skill

Off-peak both for the user (asleep) and for GitHub (low API load).
The whole run takes ~20–60 seconds depending on upstream repo
size and network.

If multiple skill-sync runs would overlap (shouldn't happen with
nightly, but defensive), the kanban dispatcher's per-task circuit
breaker catches it.

## Failure modes seen in the wild

| Symptom | Cause | Fix |
|---|---|---|
| `fatal: could not read Username` on `git push` | Site repo not configured for SSH push | `cd ~/projects/vexis-agent-site && git remote set-url origin git@github.com:Zeus-Deus/vexis-agent-site.git` |
| `Permission denied (publickey)` | Deploy key not loaded | `ssh-add ~/.ssh/<keyname>` or check `~/.ssh/config` |
| Suspicious low count abort | Upstream layout change | Inspect `~/projects/vexis-agent-site/.. && ls upstream/skills/` to see what moved |
| GHA deploy fails after push | Site CI red | Check `gh run list -R Zeus-Deus/vexis-agent-site` for the actual error |

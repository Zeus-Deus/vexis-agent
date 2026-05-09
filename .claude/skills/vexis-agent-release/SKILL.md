---
name: vexis-agent-release
description: Use when releasing a new version of vexis-agent. Handles the full release ritual — version bump in pyproject.toml + vexis_agent/__init__.py, release-note generation from git log since the last tag, version-bump commit, signed tag, push of main + the tag. Trigger on phrases like "release vexis-agent", "tag a vexis-agent release", "publish vexis-agent", "ship a vexis-agent version", "make a vexis-agent release", or any mention of vexis-agent version bumping combined with tagging or publishing.
---

# vexis-agent release pipeline

vexis-agent's distribution path is curl-bash → pipx install from a git ref. There's no PyPI publish, no wheel artifact storage, no AUR package — the *release* is a git tag. `install.sh` defaults to the latest tag (with main as fallback), so users who run `curl … | bash` only ever land on tagged code unless they explicitly opt out via `VEXIS_REPO`.

That makes the release ritual short, but it has to be exact. Use this skill whenever the maintainer asks for a release.

## Ground rules

1. **Never bump versions on a feature branch.** Releases happen on `main`. If `git rev-parse --abbrev-ref HEAD` is anything else, refuse and tell the user to merge the feature branch first.
2. **Never push without confirming.** Show the planned tag, version, and notes preview to the user before any `git push`. Ask explicitly.
3. **Never sign the tag without asking** — the maintainer may not have GPG configured. Default to an annotated (unsigned) tag unless the user says otherwise.
4. **Don't skip pre-commit / pre-push hooks** unless the user explicitly says to.
5. **Commit messages follow MEMORY.md rules**: no AI attribution, no `Co-Authored-By` trailers, no self-mention ("I", "Claude", "the assistant"), no session-context references ("audited during ..." etc.).
6. **Don't auto-bump.** The user picks patch / minor / major. If they don't say, ask.

## Release flow

Run these in order. Each step is gated on the previous one passing.

### Step 1 — preflight

```bash
# Branch + cleanliness check.
git rev-parse --abbrev-ref HEAD                  # must be 'main'
git status --porcelain                           # must be empty
git fetch --tags origin                          # know about all remote tags
```

If branch isn't main → `Switch to main and merge first; refuse.`
If working tree dirty → list the files; refuse.

### Step 2 — current version + last tag

```bash
# Read the source-of-truth version from pyproject.toml.
grep '^version = ' pyproject.toml
# And the in-package mirror.
grep '__version__' vexis_agent/__init__.py
# Last release tag (or none if first release).
git tag --list 'v*' --sort=-v:refname | head -1
```

The two version strings must match. If they diverge (someone bumped one but not the other), surface and stop.

### Step 3 — pick the next version

Ask the user for the bump kind unless they specified:

- **patch** (`v0.1.0` → `v0.1.1`) — bug fixes, doc updates, packaging tweaks.
- **minor** (`v0.1.0` → `v0.2.0`) — new commands, new features, non-breaking refactors.
- **major** (`v0.1.0` → `v1.0.0`) — breaking changes (config schema renames, removed CLI commands, daemon protocol changes).

For the very first release on a fresh repo, default to `v0.1.0`.

### Step 4 — generate release notes

```bash
# Commits since the last tag, oneline, with author stripped.
git log "${LAST_TAG}..HEAD" --no-merges --pretty=format:'- %s' \
  | grep -vE '^- (chore|docs)\(release' \
  | head -50
```

If `LAST_TAG` is empty (first release), use `--all` instead:

```bash
git log --no-merges --pretty=format:'- %s' | head -50
```

Render the notes as a markdown body:

```markdown
## v0.X.Y — YYYY-MM-DD

### Highlights
- (Pull 2-4 bullets the user explicitly cares about — typically the
  feat() commits the maintainer flagged in chat. Don't auto-pick;
  ask the user which to highlight.)

### Changes
- <every commit since the last tag, deduped, no merge commits>

### Install / upgrade

    curl -fsSL https://raw.githubusercontent.com/Zeus-Deus/vexis-agent/main/install.sh | bash

Existing installs:

    vexis-agent update     # picks up the new tag
```

Show the rendered notes to the user. Wait for "looks good" before continuing.

### Step 5 — bump version

Two files. Both must change atomically:

```toml
# pyproject.toml
version = "0.X.Y"
```

```python
# vexis_agent/__init__.py
__version__ = "0.X.Y"
```

### Step 6 — commit + tag

```bash
git add pyproject.toml vexis_agent/__init__.py
git commit -m "chore(release): v0.X.Y"
git tag -a "v0.X.Y" -m "$(cat <<'EOF'
v0.X.Y

<paste the rendered release notes here>
EOF
)"
```

The tag message becomes the GitHub release body when the user later promotes the tag to a release on GitHub. Keep it complete, not abbreviated.

### Step 7 — push

Show the planned push to the user one more time:

```
will push:
  origin main             ← chore(release): v0.X.Y
  origin tag v0.X.Y       ← annotated
```

Ask: `push these now? [y/N]`

```bash
git push origin main
git push origin "v0.X.Y"
```

Do NOT use `--force` ever. If the push is rejected, surface and stop — the maintainer might have local commits that need investigating.

### Step 8 — verify the release works

After the push:

1. Wait ~30 seconds (GitHub indexing).
2. Run `pip wheel --no-deps --no-cache-dir "git+https://github.com/Zeus-Deus/vexis-agent.git@v0.X.Y"` in `/tmp` to confirm the tag resolves and builds.
3. Tell the user: tag is live, end-users running `curl … | bash` now get this version.

## Failure modes

- **Tag already exists** (re-tagging the same name) → refuse. Tags are immutable in spirit even though git lets you delete them; deleting a tag breaks every user who installed via that tag. Bump again.
- **`git push` rejected** → don't `--force`. Pull, inspect, reconcile. Likely the user committed something on main since the last fetch.
- **Pre-commit hook fails the version-bump commit** → fix the underlying issue, re-stage, re-commit (NEW commit, not `--amend`).
- **`pip wheel` from the tag fails post-push** → critical. The tag is live but broken. Either delete the tag (and announce in chat) or land a fix-up `v0.X.(Y+1)` immediately.

## Pointers

- `install.sh` (repo root) — implements the "default to latest tag, fallback to main" logic. Read it before changing release semantics.
- `pyproject.toml` — `[project]` `version` field.
- `vexis_agent/__init__.py` — `__version__` mirror.
- `.plans/packaging-implementation-plan.md` §11 — branch + PR strategy that this skill plugs into.

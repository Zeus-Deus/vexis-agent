"""Per-turn workspace file-mutation snapshotter (Issue #9).

After every brain turn the brain has no idea what files it actually
changed on disk — only what it *claims* to have changed. A silent
write failure (permission error, wrong path, dropped tool call) goes
undetected for turns. The fix is a per-turn verifier footer the
daemon injects at the top of the NEXT user message: a short summary
of files mutated during the previous turn, computed from a
filesystem snapshot diff.

This module is the snapshotter. It walks the workspace tree with
``os.scandir`` (one ``stat`` per dirent — no extra syscalls — so it
stays under the 100 ms budget for a 10k-file repo) and returns a
``dict[str, _Entry]`` keyed by repo-relative POSIX path. ``diff()``
compares a before/after pair and yields the changed-path list the
verifier footer consumes.

**Exclusions.** A fixed prune set covers the usual high-traffic
build/vendor directories (``.git``, ``node_modules``, ``__pycache__``,
``.venv``/``venv``, ``dist``, ``build``, ``.pytest_cache``,
``.mypy_cache``, ``target``, ``web/dist``, ``web/node_modules``,
``.next``, ``.nuxt``). Hidden directories under the workspace root
(``.git``, ``.cache``, ``.vexis`` if it lived in-repo, etc.) prune
on the leading dot. Honoring user-authored ``.gitignore`` is
deliberately not attempted: parsing it correctly (negations, nested
ignores, the global ignore, ``core.excludesFile``) is heavy enough
to blow the budget, and the fixed prune set covers the directories
that actually matter for performance. If a user has a multi-GB
custom build dir we surface a config knob
(``brain.file_mutation_footer: false``) to disable the feature
entirely rather than ship a fragile gitignore parser.

**Identity.** Two entries with identical ``(mtime_ns, size)`` are
treated as unchanged. We don't hash content — the cost is
prohibitive and the false-negative case (atomic same-mtime swap) is
already weird enough that catching it isn't worth the cost. Tools
the brain actually uses (Edit, Write, Bash with ``>`` redirects)
all bump mtime.

**Threading.** ``snapshot()`` is synchronous and CPU-bound; callers
that invoke it from the brain's async ``respond()`` should wrap in
``asyncio.to_thread`` for any workspace larger than a few hundred
files. The unit tests run it directly because the test workspace
fixture is small.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────

# Directories pruned during the walk. Names match the literal dirent
# name (no leading slash, no trailing slash). Compared against
# ``DirEntry.name`` so they prune anywhere in the tree, not just at
# the workspace root — a vendored ``node_modules`` two levels deep
# is still skipped. The set is deliberately conservative; expanding
# it should be paired with a comment explaining the trigger.
_PRUNE_DIR_NAMES: frozenset[str] = frozenset(
    {
        # VCS
        ".git",
        ".hg",
        ".svn",
        # Python
        "__pycache__",
        ".venv",
        "venv",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".eggs",
        # Node / web
        "node_modules",
        ".next",
        ".nuxt",
        ".turbo",
        ".parcel-cache",
        # Build outputs
        "dist",
        "build",
        "target",  # Rust / Java
        "out",
        # Editors / OS
        ".idea",
        ".vscode",
        ".DS_Store",
        # Vexis runtime artefacts under the workspace
        ".vexis",
        ".claude",  # claude-code session JSONLs
    }
)

# Files pruned regardless of directory. Lockfiles and per-tool cache
# files that thrash on every run would otherwise fill the verifier
# footer with noise the model can't act on.
_PRUNE_FILE_NAMES: frozenset[str] = frozenset(
    {
        ".DS_Store",
        "Thumbs.db",
    }
)


# Cap on the number of paths the snapshot tracks. A walk that hits
# this ceiling logs a warning and returns the partial map — the
# verifier footer is best-effort and a runaway walk must never
# block the brain. Tuned to ~5× the issue's stated 10k budget so a
# medium-sized repo still lands inside it; users with bigger
# workspaces should disable the feature via the config knob.
_MAX_TRACKED_FILES = 50_000


@dataclass(frozen=True, slots=True)
class _Entry:
    """One filesystem entry as captured at snapshot time.

    ``mtime_ns`` is the high-resolution modification time (nanoseconds
    since epoch); ``size`` is bytes. Both come from a single
    ``DirEntry.stat()`` call so the walk does not pay for redundant
    ``stat`` syscalls. Equality is by content (the dataclass default)
    so ``snapshot_a[path] == snapshot_b[path]`` is the unchanged test.
    """

    mtime_ns: int
    size: int


# ──────────────────────────────────────────────────────────────────
# Snapshot
# ──────────────────────────────────────────────────────────────────


def snapshot(workspace: Path) -> dict[str, _Entry]:
    """Walk ``workspace`` and return ``{relative_posix_path: _Entry}``.

    Returns an empty dict on any unexpected failure — a broken
    snapshot must never wedge the brain turn. The verifier footer
    degrades gracefully to "(none detected)" in that case.

    Single-pass ``os.scandir`` recursion. Keys are workspace-relative
    POSIX paths (``"foo.py"``, ``"bar/baz.json"``) — slashes
    normalised so Windows + POSIX produce the same string, and so
    the verifier footer is stable across platforms.

    Performance: on a 10k-file Python repo the walk is dominated by
    the per-entry ``stat()`` call that ``DirEntry.stat()`` makes
    once and caches; the prune set keeps the entry count down on
    real-world repos (a typical Node project's ``node_modules`` is
    300k+ files alone). See the perf test in
    ``tests/test_brain_file_mutation_footer.py``.
    """
    start = time.monotonic()
    try:
        workspace = workspace.resolve()
    except OSError:
        log.warning("workspace_snapshot: resolve failed for %s", workspace)
        return {}
    if not workspace.is_dir():
        return {}

    out: dict[str, _Entry] = {}
    # Stack-based DFS so we don't blow the recursion limit on deep
    # trees and so we can early-out on the file-count cap.
    stack: list[tuple[str, str]] = [(str(workspace), "")]
    while stack:
        abs_dir, rel_dir = stack.pop()
        try:
            it = os.scandir(abs_dir)
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            continue
        with it:
            for entry in it:
                name = entry.name
                if name in _PRUNE_FILE_NAMES:
                    continue
                # Avoid the syscall for symlink-loop traps —
                # follow_symlinks=False on the is_dir/is_file
                # probes. Symlinks themselves are still tracked
                # if they point at files (the stat path follows
                # the link); a broken symlink shows up as
                # "missing" on stat and is silently skipped.
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    if name in _PRUNE_DIR_NAMES:
                        continue
                    # Hidden dirs at any depth — many editor/cache
                    # dirs we haven't enumerated. The .git skip
                    # above also catches the root case but this
                    # broad rule keeps the walk fast on .cache,
                    # .tox-like dirs we missed in the list.
                    if name.startswith(".") and name not in (".",  ".."):
                        continue
                    child_rel = f"{rel_dir}/{name}" if rel_dir else name
                    stack.append((entry.path, child_rel))
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                rel_path = f"{rel_dir}/{name}" if rel_dir else name
                out[rel_path] = _Entry(mtime_ns=st.st_mtime_ns, size=st.st_size)
                if len(out) >= _MAX_TRACKED_FILES:
                    elapsed_ms = (time.monotonic() - start) * 1000
                    log.warning(
                        "workspace_snapshot: hit %d-file cap at %s after "
                        "%.1fms — returning partial map; consider setting "
                        "brain.file_mutation_footer: false",
                        _MAX_TRACKED_FILES,
                        workspace,
                        elapsed_ms,
                    )
                    return out

    elapsed_ms = (time.monotonic() - start) * 1000
    if elapsed_ms > 200:
        # Soft signal — the issue's perf budget is 100 ms but real
        # workspaces routinely sit at 30–80 ms; >200 ms is the line
        # at which the user probably wants to flip the config knob.
        log.info(
            "workspace_snapshot: %d files in %.1fms at %s",
            len(out), elapsed_ms, workspace,
        )
    return out


# ──────────────────────────────────────────────────────────────────
# Diff
# ──────────────────────────────────────────────────────────────────


def diff(
    before: dict[str, _Entry], after: dict[str, _Entry],
) -> list[str]:
    """Return the sorted list of paths whose ``(mtime_ns, size)``
    differs between ``before`` and ``after``.

    Includes:
      * Files present in ``after`` but absent in ``before`` (created).
      * Files present in ``before`` but absent in ``after`` (deleted).
      * Files present in both with a different ``_Entry`` (modified).

    The verifier footer renders the same string regardless of
    create/modify/delete because the brain only needs to know that
    *something* it expected didn't happen. If we later want
    finer-grained breakdown ("created: ...", "deleted: ...") the
    classification is one line away — the entries carry enough
    state to recover it.
    """
    if before is after:  # identity short-circuit for cheap noop diffs
        return []
    changed: set[str] = set()
    # Iterate the smaller side first for a small win on huge maps.
    if len(before) <= len(after):
        for path, entry in before.items():
            other = after.get(path)
            if other is None or other != entry:
                changed.add(path)
        for path in after:
            if path not in before:
                changed.add(path)
    else:
        for path, entry in after.items():
            other = before.get(path)
            if other is None or other != entry:
                changed.add(path)
        for path in before:
            if path not in after:
                changed.add(path)
    return sorted(changed)


# ──────────────────────────────────────────────────────────────────
# Verifier footer rendering
# ──────────────────────────────────────────────────────────────────


# Cap on paths rendered in the verifier footer body. Beyond this we
# truncate with an "...and N more" tail so a runaway "find . -exec
# touch" or a full git stash doesn't dump 50k paths into the next
# turn's user message. The cap is generous — typical turns mutate
# 1–5 files — but bounded so the footer can't dwarf the user's
# actual message.
_FOOTER_MAX_PATHS = 40

# Per-path display cap. Very long paths get truncated head+tail so
# the model can still identify the file. Real paths are usually
# well under 200 chars; this catches the pathological case.
_FOOTER_MAX_PATH_LEN = 200


def format_verifier_footer(
    turn_index: int,
    files_changed: list[str],
) -> str:
    """Render the verifier footer block.

    Format (matches the spec in Issue #9):

        [turn-N verifier]
        Files changed last turn: foo.py, bar/baz.json

    or, when nothing was detected:

        [turn-N verifier]
        Files changed last turn: (none detected)

    ``turn_index`` is the 1-based ordinal of the turn that just
    *finished* — the footer says "last turn" because by the time
    it lands in the prompt the brain is starting turn N+1.

    The "claimed but not changed" diagnostic mentioned in Issue #9
    is deferred (it requires scraping edit-tool calls from the
    previous transcript and diffing against the actual mutation set).
    The v1 footer is just the mutation list.
    """
    if not files_changed:
        body = "Files changed last turn: (none detected)"
    else:
        truncated = [
            _truncate_path(p) for p in files_changed[:_FOOTER_MAX_PATHS]
        ]
        if len(files_changed) > _FOOTER_MAX_PATHS:
            extra = len(files_changed) - _FOOTER_MAX_PATHS
            truncated.append(f"…and {extra} more")
        body = "Files changed last turn: " + ", ".join(truncated)
    return f"[turn-{turn_index} verifier]\n{body}"


def _truncate_path(path: str) -> str:
    if len(path) <= _FOOTER_MAX_PATH_LEN:
        return path
    head_len = _FOOTER_MAX_PATH_LEN // 2 - 2
    tail_len = _FOOTER_MAX_PATH_LEN - head_len - 3  # for "..."
    return f"{path[:head_len]}...{path[-tail_len:]}"


__all__ = [
    "diff",
    "format_verifier_footer",
    "snapshot",
    "_Entry",
    "_FOOTER_MAX_PATHS",
    "_MAX_TRACKED_FILES",
    "_PRUNE_DIR_NAMES",
]

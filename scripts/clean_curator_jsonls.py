"""One-time cleanup: move curator-spawned JSONLs out of the workspace
projects directory.

Background: until the recursion-fix landed, the learning curator's
in-memory ``_spawned_uuids`` set was the only signal preventing it from
reviewing its own ``claude -p`` review forks. The set was lost on
daemon restart, so over time the workspace projects directory
accumulated curator-owned JSONLs that the daemon then re-reviewed —
2,165 of 2,207 in the May 2026 audit. This script identifies those
JSONLs by their first user message (which always opens with the
curator's review prompt) and moves them into
``~/.vexis/learning/curator-jsonl-archive/<utc>/`` so the live
projects dir is clean. They're moved, not deleted, in case forensics
on what the curator wrote during the live-mode window matters later.

Run mode:

  python scripts/clean_curator_jsonls.py
      Dry run. Prints a count + first 10 UUIDs + total bytes that would
      move. Exits 0 in all cases (dry-run is informational).

  python scripts/clean_curator_jsonls.py --apply
      Moves matched JSONLs into the timestamped archive subdirectory.
      Idempotent: a second run finds no remaining curator JSONLs and
      reports a count of 0.

  python scripts/clean_curator_jsonls.py --workspace /tmp/vexis-eval-XYZ
      Targets a different workspace's projects directory. Useful for
      cleaning up eval workspaces (under ``/tmp/vexis-eval-*``).
      Default workspace is ``~/vexis-workspace``.

This is intentionally NOT a periodic job. Once the recursion guard
fix is live, the workspace projects directory shouldn't grow new
fanout — only real user sessions and immediately-recognized curator
forks (which the persistent registry filters out before the next tick).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running as a plain script from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.learning_review import CURATOR_REVIEW_PROMPT_PREFIX  # noqa: E402
from core.paths import learning_curator_archive_dir  # noqa: E402
from core.transcripts import (  # noqa: E402
    claude_session_jsonl_dir,
    iter_messages,
)


def _is_curator_owned(jsonl_path: Path) -> bool:
    """Match :func:`core.transcripts._is_curator_owned`. Re-defined here
    rather than imported because the module-private underscore-name is
    not part of the public API; the script is the secondary consumer."""
    for msg in iter_messages(jsonl_path):
        if msg.role != "user":
            continue
        return msg.text.startswith(CURATOR_REVIEW_PROMPT_PREFIX)
    return False


def _human_bytes(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024  # type: ignore[assignment]
    return f"{n} B"


def _scan(projects_dir: Path) -> tuple[list[Path], list[Path]]:
    """Return ``(curator_owned, real_user)`` Path lists. Misreads
    (file deleted between glob and open, unreadable file) get treated
    as real-user — better to leave a mystery file in place than to
    archive something we couldn't classify."""
    curator: list[Path] = []
    other: list[Path] = []
    for jsonl in sorted(projects_dir.glob("*.jsonl")):
        if not jsonl.is_file():
            continue
        try:
            owned = _is_curator_owned(jsonl)
        except OSError:
            owned = False
        if owned:
            curator.append(jsonl)
        else:
            other.append(jsonl)
    return curator, other


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Move curator-spawned JSONLs out of the workspace projects "
            "directory. Dry-run by default; pass --apply to actually move."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help=(
            "Move matched JSONLs into "
            "~/.vexis/learning/curator-jsonl-archive/<utc>/ (default: "
            "dry-run, prints count + samples and exits)."
        ),
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.home() / "vexis-workspace",
        help=(
            "Workspace whose projects directory to clean. Default: "
            "~/vexis-workspace. Pass an eval workspace path to clean "
            "those (e.g. /tmp/vexis-eval-XXXX)."
        ),
    )
    args = parser.parse_args(argv)

    workspace: Path = args.workspace.expanduser().resolve()
    projects_dir = claude_session_jsonl_dir(workspace)
    if not projects_dir.exists():
        print(
            f"Projects directory does not exist: {projects_dir}\n"
            f"  (encoded from workspace: {workspace})"
        )
        return 0

    print(f"Workspace:       {workspace}")
    print(f"Projects dir:    {projects_dir}")
    curator, other = _scan(projects_dir)
    total_bytes = sum(p.stat().st_size for p in curator)
    print(f"Curator-owned:   {len(curator)} JSONL(s), {_human_bytes(total_bytes)}")
    print(f"Real-user / other: {len(other)} JSONL(s)")

    if not curator:
        print("Nothing to do.")
        return 0

    print()
    print("First curator-owned UUIDs:")
    for jsonl in curator[:10]:
        print(f"  {jsonl.stem}")
    if len(curator) > 10:
        print(f"  ... and {len(curator) - 10} more")

    if not args.apply:
        print()
        print("Dry run — no files moved. Re-run with --apply to archive.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    dest = learning_curator_archive_dir() / stamp
    dest.mkdir(parents=True, exist_ok=False)
    print()
    print(f"Moving {len(curator)} file(s) to {dest}")
    moved = 0
    for jsonl in curator:
        target = dest / jsonl.name
        try:
            jsonl.rename(target)
        except OSError as exc:
            print(f"  failed: {jsonl.name}: {exc}", file=sys.stderr)
            continue
        moved += 1
    remaining_curator, remaining_other = _scan(projects_dir)
    print(f"Moved:           {moved}/{len(curator)}")
    print(f"Remaining curator: {len(remaining_curator)} (expected 0)")
    print(f"Remaining other:   {len(remaining_other)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

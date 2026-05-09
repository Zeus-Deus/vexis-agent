"""CLI for the memory subsystem.

Mirrors the conceptual ``memory`` tool: one verb, three actions, two
targets. The CLI is what Claude Code shells out to from inside a
brain turn — argv arrives via Bash, JSON goes back via stdout. All
errors print to stderr and exit non-zero so the model sees a clean
"this failed because X" rather than a JSON dump it has to interpret.

Usage:
    vexis-mem add memory "Codemux infra at 203.0.113.42"
    vexis-mem add user   "Prefers concise replies"
    vexis-mem replace memory --old "Codemux infra" --new "Codemux infra at 78.47..."
    vexis-mem remove user --old "Prefers concise"
    vexis-mem list memory      # JSON, for debug
    vexis-mem render memory    # the rendered block (what the brain sees)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from vexis_agent.core.memory import (
    MemoryError_,
    MemoryStore,
    MemorySuccess,
    Target,
)
from vexis_agent.core.paths import memories_dir, workspace_dir


def _store() -> MemoryStore:
    workspace = workspace_dir(os.environ.get("VEXIS_WORKSPACE", "~/vexis-workspace"))
    return MemoryStore(memories_dir(workspace))


def _emit(result: MemorySuccess | MemoryError_) -> int:
    if isinstance(result, MemorySuccess):
        # Stable JSON shape: success/message/render. The model reads
        # render to confirm what landed; success is the imperative.
        print(
            json.dumps(
                {
                    "success": True,
                    "message": result.message,
                    "render": result.render,
                },
                ensure_ascii=False,
            )
        )
        return 0
    payload: dict = {"success": False, "error": result.message}
    if result.extra:
        payload.update(result.extra)
    print(json.dumps(payload, ensure_ascii=False))
    return 1


def _cmd_add(target: Target, content: str) -> int:
    return _emit(_store().add(target, content))


def _cmd_replace(target: Target, old_text: str, new_text: str) -> int:
    return _emit(_store().replace(target, old_text, new_text))


def _cmd_remove(target: Target, old_text: str) -> int:
    return _emit(_store().remove(target, old_text))


def _cmd_list(target: Target) -> int:
    entries = _store().list_entries(target)
    print(json.dumps({"target": target, "entries": entries}, ensure_ascii=False))
    return 0


def _cmd_render(target: Target) -> int:
    block = _store().render(target)
    if block:
        print(block)
    return 0


def _add_target_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("target", choices=["memory", "user"])


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="vexis-mem",
        description=(
            "Mutate the agent's persistent memory at "
            f"{Path('~/vexis-workspace/memories/').as_posix()}."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="Append a new entry.")
    _add_target_arg(p_add)
    p_add.add_argument("content")

    p_rep = sub.add_parser("replace", help="Replace an entry by substring.")
    _add_target_arg(p_rep)
    p_rep.add_argument("--old", required=True, help="Substring of the entry to replace.")
    p_rep.add_argument("--new", required=True, help="New entry content.")

    p_rem = sub.add_parser("remove", help="Remove an entry by substring.")
    _add_target_arg(p_rem)
    p_rem.add_argument("--old", required=True, help="Substring of the entry to remove.")

    p_ls = sub.add_parser("list", help="JSON list of entries (debug).")
    _add_target_arg(p_ls)

    p_rd = sub.add_parser("render", help="Print the system-prompt block.")
    _add_target_arg(p_rd)

    args = parser.parse_args()
    if args.cmd == "add":
        return _cmd_add(args.target, args.content)
    if args.cmd == "replace":
        return _cmd_replace(args.target, args.old, args.new)
    if args.cmd == "remove":
        return _cmd_remove(args.target, args.old)
    if args.cmd == "list":
        return _cmd_list(args.target)
    if args.cmd == "render":
        return _cmd_render(args.target)
    return 2


if __name__ == "__main__":
    sys.exit(main())

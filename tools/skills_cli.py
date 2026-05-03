"""CLI for the skills subsystem.

What the brain shells out to from inside a turn, AND what the curator
shells out to during its consolidation pass.

Curator enforcement
-------------------
The curator's spawned ``claude -p`` runs with ``VEXIS_CURATOR=1`` set
in its environment. This CLI checks that env var on entry and refuses
the destructive verbs (``delete``, ``remove-file``) outright. This is
the code-enforced safety layer the spec requires — the LLM cannot
ratchet itself into deletion even if it gets confused about the rules.
``patch`` / ``edit`` / ``write-file`` / ``create`` / archive moves are
still allowed because that's how consolidation happens.

Subcommands map 1:1 to the spec's "skill_manage" actions plus
``view`` / ``list``:

    vexis-skill list
    vexis-skill view <name> [--file <relpath>]
    vexis-skill create <name> --content-file <path> [--category <cat>]
    vexis-skill edit <name> --content-file <path>
    vexis-skill patch <name> --old-string <s> --new-string <s> [--replace-all]
    vexis-skill delete <name>
    vexis-skill write-file <name> --file <relpath> --content-file <path>
    vexis-skill remove-file <name> --file <relpath>
    vexis-skill archive <name>
    vexis-skill restore <name>
    vexis-skill render-index           # debug: prints the index block
    vexis-skill flip-shadow [--all|--skill NAME] [--dry-run]
    vexis-skill list-staged            # show what's staged in .shadow/
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from core.learning_writes import (
    flip_shadow_to_live,
    list_staged_skills,
)
from core.paths import skills_dir, workspace_dir
from core.skills import (
    OpResult,
    archive_skill,
    build_skills_index_block,
    create_skill,
    delete_skill,
    edit_skill,
    list_active_reports,
    patch_skill,
    remove_supporting_file,
    restore_skill,
    view_skill,
    write_supporting_file,
)

CURATOR_ENV_VAR = "VEXIS_CURATOR"
CURATOR_BLOCKED_VERBS: frozenset[str] = frozenset({"delete", "remove-file"})


def _is_curator_context() -> bool:
    return os.environ.get(CURATOR_ENV_VAR, "").strip() not in ("", "0", "false")


def _root() -> Path:
    workspace = workspace_dir(os.environ.get("VEXIS_WORKSPACE", "~/vexis-workspace"))
    return skills_dir(workspace)


def _emit(result: OpResult) -> int:
    payload: dict = {"success": result.ok, "message": result.message}
    if result.extra:
        payload.update(result.extra)
    if result.ok:
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    print(json.dumps(payload, ensure_ascii=False))
    return 1


def _read_content_arg(path: str) -> str:
    """Slurp a file path that the caller used to pass content. Using
    --content-file instead of arg avoids argv length limits and keeps
    multi-line markdown intact through the shell."""
    return Path(path).expanduser().read_text(encoding="utf-8")


def _cmd_list() -> int:
    reports = list_active_reports(_root())
    payload = {
        "skills": [
            {
                "name": r.name,
                "description": r.description,
                "state": r.state,
                "pinned": r.pinned,
                "last_used_at": r.last_used_at,
                "created_at": r.created_at,
            }
            for r in reports
        ]
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _cmd_view(name: str, file_path: str | None) -> int:
    return _emit(view_skill(_root(), name, file_path))


def _cmd_create(name: str, content_path: str, category: str | None) -> int:
    return _emit(create_skill(_root(), name, _read_content_arg(content_path), category))


def _cmd_edit(name: str, content_path: str) -> int:
    return _emit(edit_skill(_root(), name, _read_content_arg(content_path)))


def _cmd_patch(name: str, old: str, new: str, replace_all: bool) -> int:
    return _emit(patch_skill(_root(), name, old, new, replace_all=replace_all))


def _cmd_delete(name: str) -> int:
    return _emit(delete_skill(_root(), name))


def _cmd_write_file(name: str, file_path: str, content_path: str) -> int:
    return _emit(
        write_supporting_file(
            _root(), name, file_path, _read_content_arg(content_path)
        )
    )


def _cmd_remove_file(name: str, file_path: str) -> int:
    return _emit(remove_supporting_file(_root(), name, file_path))


def _cmd_archive(name: str) -> int:
    return _emit(archive_skill(_root(), name))


def _cmd_restore(name: str) -> int:
    return _emit(restore_skill(_root(), name))


def _cmd_render_index() -> int:
    block = build_skills_index_block(_root())
    if block:
        print(block)
    return 0


def _workspace() -> Path:
    """The current workspace, used by flip-shadow operations.

    skills_dir(workspace) is what _root() already returns, but the
    learning_writes module wants the workspace itself so it can
    locate the staging tree alongside the live skill tree.
    """
    return workspace_dir(os.environ.get("VEXIS_WORKSPACE", "~/vexis-workspace"))


def _cmd_flip_shadow(only_skill: str | None, dry_run: bool) -> int:
    """Move staged skills from .shadow/ into the live tree.

    With ``--dry-run``, lists what would be flipped without touching
    the live tree (useful before a real flip when the user is
    reviewing the staging area).
    """
    workspace = _workspace()
    if dry_run:
        staged = list_staged_skills(workspace)
        if only_skill is not None:
            staged = [s for s in staged if s.name == only_skill]
        payload = {
            "dry_run": True,
            "would_flip": [
                {
                    "name": s.name,
                    "is_new_skill": s.live_dir is None,
                    "staged_dir": str(s.staged_dir),
                    "support_files": [str(p) for p in s.support_files],
                    "has_skill_md": s.has_skill_md,
                }
                for s in staged
            ],
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 0
    results = flip_shadow_to_live(workspace, only_skill=only_skill)
    if not results:
        print(json.dumps(
            {"success": True, "message": "No staged skills to flip.", "flips": []},
            ensure_ascii=False,
        ))
        return 0
    payload = {
        "success": all(r.ok for r in results),
        "flips": [
            {
                "name": r.skill_name,
                "ok": r.ok,
                "message": r.message,
                "files_copied": r.files_copied,
                "is_new_skill": r.is_new_skill,
            }
            for r in results
        ],
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["success"] else 1


def _cmd_list_staged() -> int:
    """Inspect the staging tree without flipping. Pairs with
    flip-shadow's --dry-run mode but suitable for separate audit
    callers (e.g. /learning audit Telegram surface)."""
    workspace = _workspace()
    staged = list_staged_skills(workspace)
    payload = {
        "staged": [
            {
                "name": s.name,
                "is_new_skill": s.live_dir is None,
                "staged_dir": str(s.staged_dir),
                "live_dir": str(s.live_dir) if s.live_dir else None,
                "has_skill_md": s.has_skill_md,
                "support_files": [str(p) for p in s.support_files],
            }
            for s in staged
        ]
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="vexis-skill",
        description="Manage skills under ~/vexis-workspace/skills/.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    p_view = sub.add_parser("view")
    p_view.add_argument("name")
    p_view.add_argument(
        "--file",
        dest="file_path",
        default=None,
        help="Optional relative path under references/, templates/, or scripts/.",
    )

    p_create = sub.add_parser("create")
    p_create.add_argument("name")
    p_create.add_argument("--content-file", required=True)
    p_create.add_argument("--category", default=None)

    p_edit = sub.add_parser("edit")
    p_edit.add_argument("name")
    p_edit.add_argument("--content-file", required=True)

    p_patch = sub.add_parser("patch")
    p_patch.add_argument("name")
    p_patch.add_argument("--old-string", dest="old_string", required=True)
    p_patch.add_argument("--new-string", dest="new_string", required=True)
    p_patch.add_argument(
        "--replace-all", dest="replace_all", action="store_true", default=False
    )

    p_delete = sub.add_parser("delete")
    p_delete.add_argument("name")

    p_wf = sub.add_parser("write-file")
    p_wf.add_argument("name")
    p_wf.add_argument("--file", dest="file_path", required=True)
    p_wf.add_argument("--content-file", required=True)

    p_rf = sub.add_parser("remove-file")
    p_rf.add_argument("name")
    p_rf.add_argument("--file", dest="file_path", required=True)

    p_arc = sub.add_parser("archive")
    p_arc.add_argument("name")

    p_rst = sub.add_parser("restore")
    p_rst.add_argument("name")

    sub.add_parser("render-index")

    p_flip = sub.add_parser(
        "flip-shadow",
        help="Move staged skills from .shadow/ into the live tree",
    )
    flip_target = p_flip.add_mutually_exclusive_group()
    flip_target.add_argument(
        "--all",
        dest="flip_all",
        action="store_true",
        default=False,
        help="Flip every staged skill (default if --skill not given)",
    )
    flip_target.add_argument(
        "--skill",
        dest="flip_skill",
        default=None,
        help="Flip only the named staged skill",
    )
    p_flip.add_argument(
        "--dry-run",
        dest="flip_dry_run",
        action="store_true",
        default=False,
        help="Show what would be flipped without touching the live tree",
    )

    sub.add_parser(
        "list-staged",
        help="Inspect the staging tree contents (no live-tree changes)",
    )

    args = parser.parse_args()

    if _is_curator_context() and args.cmd in CURATOR_BLOCKED_VERBS:
        # Hard refusal — the curator should never have been given this
        # verb. Exit non-zero with a clear message so a confused LLM
        # at least knows what it can't do.
        print(
            json.dumps(
                {
                    "success": False,
                    "error": (
                        f"action '{args.cmd}' is forbidden inside the curator. "
                        f"Archiving is the maximum destructive action."
                    ),
                },
                ensure_ascii=False,
            )
        )
        return 1

    if args.cmd == "list":
        return _cmd_list()
    if args.cmd == "view":
        return _cmd_view(args.name, args.file_path)
    if args.cmd == "create":
        return _cmd_create(args.name, args.content_file, args.category)
    if args.cmd == "edit":
        return _cmd_edit(args.name, args.content_file)
    if args.cmd == "patch":
        return _cmd_patch(args.name, args.old_string, args.new_string, args.replace_all)
    if args.cmd == "delete":
        return _cmd_delete(args.name)
    if args.cmd == "write-file":
        return _cmd_write_file(args.name, args.file_path, args.content_file)
    if args.cmd == "remove-file":
        return _cmd_remove_file(args.name, args.file_path)
    if args.cmd == "archive":
        return _cmd_archive(args.name)
    if args.cmd == "restore":
        return _cmd_restore(args.name)
    if args.cmd == "render-index":
        return _cmd_render_index()
    if args.cmd == "flip-shadow":
        return _cmd_flip_shadow(args.flip_skill, args.flip_dry_run)
    if args.cmd == "list-staged":
        return _cmd_list_staged()
    return 2


if __name__ == "__main__":
    sys.exit(main())

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
    vexis-skill render-index    # debug: prints the index block
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

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
    return 2


if __name__ == "__main__":
    sys.exit(main())

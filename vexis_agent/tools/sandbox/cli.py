"""``vexis-sandbox`` console script.

Talks directly to Docker (no daemon round-trip) because a sandbox is
per-task, not a shared singleton like the browser session. Output style
matches ``vexis-browse`` / ``vexis-bg``: each subcommand emits a single
JSON object on stdout (so the agent can parse it) and uses stderr for
human-friendly hints.

The agent CLI calls ``vexis-sandbox exec <task-id> -- <cmd...>`` to do
its build/test work; the ``--`` separator is enforced for ``exec`` so
the agent's command flags aren't accidentally parsed by argparse.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .backend import BackendError
from .sandbox import (
    InvalidTaskId,
    Sandbox,
    SandboxAlreadyRunning,
    SandboxError,
    SandboxNotFound,
    SandboxStartFailed,
    default_image,
)


def _emit(payload: dict[str, Any]) -> None:
    """One JSON object per subcommand. ``ensure_ascii=False`` keeps the
    output readable when stderr/stdout contains non-ASCII characters."""
    print(json.dumps(payload, ensure_ascii=False))


def _fail(msg: str, *, exit_code: int = 1) -> int:
    _emit({"ok": False, "error": msg})
    return exit_code


def _cmd_start(args: argparse.Namespace) -> int:
    try:
        sb = Sandbox(args.task_id)
        meta = sb.start(image=args.image, mounts=args.mount or None)
    except InvalidTaskId as exc:
        return _fail(str(exc), exit_code=2)
    except SandboxAlreadyRunning as exc:
        return _fail(str(exc), exit_code=2)
    except (SandboxStartFailed, SandboxError, BackendError) as exc:
        return _fail(str(exc))
    _emit({"ok": True, "result": meta.to_dict()})
    return 0


def _cmd_exec(args: argparse.Namespace) -> int:
    if not args.cmd:
        return _fail("exec: no command provided (use `-- cmd args...`)", exit_code=2)
    try:
        sb = Sandbox(args.task_id)
        res = sb.exec(
            list(args.cmd),
            cwd=args.cwd,
            timeout=args.timeout,
            auto_start=not args.no_start,
        )
    except InvalidTaskId as exc:
        return _fail(str(exc), exit_code=2)
    except SandboxNotFound as exc:
        return _fail(str(exc), exit_code=2)
    except (SandboxStartFailed, SandboxError, BackendError) as exc:
        return _fail(str(exc))

    if args.json:
        _emit({"ok": res.ok, "result": res.to_dict()})
        # `exec --json` deliberately exits 0 on a captured non-zero
        # command exit — the JSON is the result, and the agent caller
        # decides what to do with a non-zero exit_code. This mirrors
        # how `vexis-browse` returns ok-with-payload for action calls.
        return 0

    # Pass-through mode (no --json): mirror the underlying cmd's
    # stdout/stderr/exit so callers can pipe vexis-sandbox exec into
    # tools that don't speak JSON.
    if res.stdout:
        sys.stdout.write(res.stdout)
    if res.stderr:
        sys.stderr.write(res.stderr)
    return res.exit_code


def _cmd_cp(args: argparse.Namespace) -> int:
    try:
        sb = Sandbox(args.task_id)
        # Direction is decided by which arg looks like "<container>:path".
        # We use the prefix ``container:`` (no slashes before the colon)
        # to identify in-sandbox paths.
        src_in = ":" in args.src and not args.src.startswith("/")
        dst_in = ":" in args.dst and not args.dst.startswith("/")
        if src_in and not dst_in:
            # container → host (strip the "<container>:" prefix)
            _container, _colon, src = args.src.partition(":")
            res = sb.cp_from(src, args.dst)
        elif dst_in and not src_in:
            _container, _colon, dst = args.dst.partition(":")
            res = sb.cp_to(args.src, dst)
        else:
            return _fail(
                "cp: exactly one of <src>/<dst> must be a sandbox path "
                "of the form 'container:/path' — the other must be a host path.",
                exit_code=2,
            )
    except InvalidTaskId as exc:
        return _fail(str(exc), exit_code=2)
    except SandboxNotFound as exc:
        return _fail(str(exc), exit_code=2)
    except (SandboxError, BackendError) as exc:
        return _fail(str(exc))
    if not res.ok:
        return _fail(res.stderr.strip() or "docker cp failed")
    _emit({"ok": True, "result": {"src": args.src, "dst": args.dst}})
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    try:
        sb = Sandbox(args.task_id)
        removed = sb.stop()
    except InvalidTaskId as exc:
        return _fail(str(exc), exit_code=2)
    except (SandboxError, BackendError) as exc:
        return _fail(str(exc))
    _emit({"ok": True, "result": {"task_id": args.task_id, "stopped": removed}})
    return 0


def _cmd_list(_args: argparse.Namespace) -> int:
    try:
        rows = Sandbox.list_all()
    except (SandboxError, BackendError) as exc:
        return _fail(str(exc))
    _emit({"ok": True, "result": rows})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vexis-sandbox",
        description=(
            "Per-task Docker sandboxes for Vexis build-and-test loops. "
            "Each task-id maps to a named container; state persists across "
            "exec calls within the same task-id, and tasks are isolated."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start", help="Start (or reuse) the sandbox for this task-id.")
    p_start.add_argument("task_id")
    p_start.add_argument(
        "--image",
        default=None,
        help=f"Docker image to run (default: {default_image()}).",
    )
    p_start.add_argument(
        "--mount",
        action="append",
        default=None,
        help=(
            "Extra mount in 'host:container' form; repeatable. The default "
            "workspace + scratch mounts are always added on top."
        ),
    )
    p_start.set_defaults(func=_cmd_start)

    p_exec = sub.add_parser(
        "exec",
        help="Run a command inside the sandbox. Lazy-starts if needed.",
        epilog=(
            "examples:\n"
            "  vexis-sandbox exec t1 -- cargo test --release\n"
            "  vexis-sandbox exec --json t1 -- pytest\n"
            "\n"
            "Flags must come BEFORE the task-id (same convention as "
            "`docker exec`); anything after the task-id is treated as the "
            "command to run inside the sandbox."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_exec.add_argument("task_id")
    p_exec.add_argument("--cwd", default=None, help="Working dir inside the container.")
    p_exec.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Kill the exec after this many seconds.",
    )
    p_exec.add_argument(
        "--json",
        action="store_true",
        help=(
            "Emit a single JSON object with {ok,result:{exit_code,stdout,stderr}} "
            "instead of streaming the command's stdout/stderr/exit through."
        ),
    )
    p_exec.add_argument(
        "--no-start",
        action="store_true",
        help="Fail instead of lazy-starting an absent sandbox.",
    )
    # ``cmd`` is filled by ``main`` after the ``--`` split — argparse
    # only sees ``cmd=[]`` by default. Using a custom split rather than
    # REMAINDER keeps the user's mental model clean: flags go anywhere
    # before ``--`` regardless of whether they're before/after the
    # task-id, and everything after ``--`` is the command verbatim.
    p_exec.add_argument(
        "cmd",
        nargs="*",
        default=[],
        help="Command to run inside the sandbox; must follow `--`.",
    )
    p_exec.set_defaults(func=_cmd_exec)

    p_cp = sub.add_parser(
        "cp",
        help=(
            "Copy files between host and sandbox. Exactly one of src/dst "
            "must use the form 'container:/abs/path'."
        ),
    )
    p_cp.add_argument("task_id")
    p_cp.add_argument("src")
    p_cp.add_argument("dst")
    p_cp.set_defaults(func=_cmd_cp)

    p_stop = sub.add_parser("stop", help="Stop and remove the sandbox container.")
    p_stop.add_argument("task_id")
    p_stop.set_defaults(func=_cmd_stop)

    p_list = sub.add_parser("list", help="List all sandbox containers on this host.")
    p_list.set_defaults(func=_cmd_list)

    return parser


def _split_on_double_dash(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split argv on the first ``--`` token. The left side goes to
    argparse; the right side is the verbatim command for ``exec``.

    Argparse's REMAINDER would otherwise greedily consume any flag
    appearing after the first positional argument, which prevents users
    from writing ``vexis-sandbox exec t1 --cwd /x -- cmd``."""
    if "--" not in argv:
        return list(argv), []
    idx = argv.index("--")
    return list(argv[:idx]), list(argv[idx + 1 :])


def main(argv_in: list[str] | None = None) -> int:
    raw = list(sys.argv[1:]) if argv_in is None else list(argv_in)
    left, after_sep = _split_on_double_dash(raw)
    parser = build_parser()
    args = parser.parse_args(left)
    # The `exec` subcommand is the only one that takes a trailing
    # command; for everything else, a stray `--` is ignored on purpose.
    if getattr(args, "cmd", None) is not None and after_sep:
        args.cmd = after_sep
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover - exercised via console_script
    sys.exit(main())

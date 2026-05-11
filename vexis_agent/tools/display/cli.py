"""``vexis-display`` console script.

Same shape as ``vexis-sandbox``: per-task lifecycle, JSON on stdout,
human hints on stderr. The agent calls ``vexis-display env <task-id>``
to discover ``DISPLAY=`` / ``WAYLAND_DISPLAY=`` and exports those when
running GUI commands via ``vexis-sandbox exec``.

The display lives entirely inside the sandbox container, so its
lifecycle is bounded by the sandbox's — stopping the sandbox kills the
display implicitly. ``stop`` is provided for the case where the caller
wants to swap backends without tearing down the whole sandbox.
"""

from __future__ import annotations

import argparse
import json
import sys

from vexis_agent.tools.sandbox import SandboxError
from .display import (
    DEFAULT_DISPLAY_NUMBER,
    DEFAULT_RESOLUTION,
    DisplayError,
    DisplayNotFound,
    DisplayStartFailed,
    HeadlessDisplay,
    UnsupportedBackend,
    SUPPORTED_BACKENDS,
)


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _fail(msg: str, *, exit_code: int = 1) -> int:
    _emit({"ok": False, "error": msg})
    return exit_code


def _cmd_start(args: argparse.Namespace) -> int:
    try:
        display = HeadlessDisplay(args.task_id)
        meta = display.start(
            backend=args.backend,
            resolution=args.resolution,
            display_number=args.display_number,
        )
    except UnsupportedBackend as exc:
        return _fail(str(exc), exit_code=2)
    except (DisplayStartFailed, DisplayError, SandboxError) as exc:
        return _fail(str(exc))
    _emit({"ok": True, "result": meta.to_dict()})
    return 0


def _cmd_env(args: argparse.Namespace) -> int:
    try:
        env = HeadlessDisplay(args.task_id).env()
    except DisplayNotFound as exc:
        return _fail(str(exc), exit_code=2)
    except (DisplayError, SandboxError) as exc:
        return _fail(str(exc))
    if args.shell:
        # Print shell-source-able assignment lines for `eval $(...)` use.
        for key, val in env.items():
            print(f"export {key}={val}")
        return 0
    _emit({"ok": True, "result": env})
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    try:
        removed = HeadlessDisplay(args.task_id).stop()
    except (DisplayError, SandboxError) as exc:
        return _fail(str(exc))
    _emit({"ok": True, "result": {"task_id": args.task_id, "stopped": removed}})
    return 0


def _cmd_list(_args: argparse.Namespace) -> int:
    rows = HeadlessDisplay.list_all()
    _emit({"ok": True, "result": rows})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vexis-display",
        description=(
            "Headless display per vexis-sandbox task. The display lives "
            "inside the sandbox container, so the host's Wayland/X11 "
            "session is never touched."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser(
        "start", help="Start a headless display inside this task's sandbox."
    )
    p_start.add_argument("task_id")
    p_start.add_argument(
        "--backend",
        default="auto",
        choices=SUPPORTED_BACKENDS,
        help="Display backend (default: auto → xvfb).",
    )
    p_start.add_argument(
        "--resolution",
        default=DEFAULT_RESOLUTION,
        help=f"WIDTHxHEIGHT (default: {DEFAULT_RESOLUTION}).",
    )
    p_start.add_argument(
        "--display-number",
        type=int,
        default=DEFAULT_DISPLAY_NUMBER,
        help=(
            "X display number (default: 99). Bump this if you're "
            "running multiple displays in the same container."
        ),
    )
    p_start.set_defaults(func=_cmd_start)

    p_env = sub.add_parser(
        "env",
        help="Print DISPLAY/WAYLAND_DISPLAY for this task.",
    )
    p_env.add_argument("task_id")
    p_env.add_argument(
        "--shell",
        action="store_true",
        help="Print `export KEY=VALUE` lines instead of JSON.",
    )
    p_env.set_defaults(func=_cmd_env)

    p_stop = sub.add_parser("stop", help="Stop the display for this task.")
    p_stop.add_argument("task_id")
    p_stop.set_defaults(func=_cmd_stop)

    p_list = sub.add_parser("list", help="List recorded displays.")
    p_list.set_defaults(func=_cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

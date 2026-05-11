"""``vexis-ui`` console script.

Wraps :class:`vexis_agent.tools.ui.UIDriver` for shell callers. The
task-id is required for every subcommand (no implicit "the focused
sandbox" mode — explicit is better for the agent's plan-step trace).

Output style mirrors ``vexis-sandbox`` and ``vexis-browse``: one JSON
object on stdout per subcommand.
"""

from __future__ import annotations

import argparse
import json
import sys

from .ui import ATSPIError, UIDriver


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _fail(msg: str, *, exit_code: int = 1) -> int:
    _emit({"ok": False, "error": msg})
    return exit_code


def _driver(task_id: str) -> UIDriver:
    return UIDriver(task_id)


def _cmd_snapshot(args: argparse.Namespace) -> int:
    try:
        snap = _driver(args.task_id).snapshot()
    except ATSPIError as exc:
        return _fail(str(exc))
    _emit({"ok": True, "result": snap.to_dict()})
    return 0


def _cmd_click(args: argparse.Namespace) -> int:
    try:
        result = _driver(args.task_id).click(args.index)
    except ATSPIError as exc:
        return _fail(str(exc))
    _emit({"ok": True, "result": result})
    return 0


def _cmd_type(args: argparse.Namespace) -> int:
    try:
        result = _driver(args.task_id).type_text(args.index, args.text)
    except ATSPIError as exc:
        return _fail(str(exc))
    _emit({"ok": True, "result": result})
    return 0


def _cmd_press(args: argparse.Namespace) -> int:
    try:
        result = _driver(args.task_id).press(args.chord)
    except ATSPIError as exc:
        return _fail(str(exc))
    _emit({"ok": True, "result": result})
    return 0


def _cmd_focus(args: argparse.Namespace) -> int:
    try:
        result = _driver(args.task_id).focus(args.selector)
    except ATSPIError as exc:
        return _fail(str(exc))
    _emit({"ok": True, "result": result})
    return 0


def _cmd_vision(args: argparse.Namespace) -> int:
    try:
        result = _driver(args.task_id).vision_snapshot(args.out)
    except ATSPIError as exc:
        return _fail(str(exc))
    _emit({"ok": True, "result": result})
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vexis-ui",
        description=(
            "Native-app driver via AT-SPI for a vexis-sandbox task. "
            "Snapshot the focused window, fire click/type/press by "
            "index, or fall back to a screenshot."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_snap = sub.add_parser("snapshot", help="Indexed DSL of the focused window.")
    p_snap.add_argument("task_id")
    p_snap.set_defaults(func=_cmd_snapshot)

    p_click = sub.add_parser("click", help="Click element by snapshot index.")
    p_click.add_argument("task_id")
    p_click.add_argument("index", type=int)
    p_click.set_defaults(func=_cmd_click)

    p_type = sub.add_parser("type", help="Type text into element by snapshot index.")
    p_type.add_argument("task_id")
    p_type.add_argument("index", type=int)
    p_type.add_argument("text")
    p_type.set_defaults(func=_cmd_type)

    p_press = sub.add_parser(
        "press",
        help="Send a key chord (e.g. 'Return', 'ctrl+s') via xdotool/ydotool.",
    )
    p_press.add_argument("task_id")
    p_press.add_argument("chord")
    p_press.set_defaults(func=_cmd_press)

    p_focus = sub.add_parser(
        "focus", help="Focus a window by name/role/class substring match."
    )
    p_focus.add_argument("task_id")
    p_focus.add_argument("selector")
    p_focus.set_defaults(func=_cmd_focus)

    p_vision = sub.add_parser(
        "vision-snapshot",
        help="Save a PNG screenshot via grim/import; fallback when AT-SPI is empty.",
    )
    p_vision.add_argument("task_id")
    p_vision.add_argument(
        "--out",
        default=None,
        help="Output path inside the sandbox (default: /tmp/vexis-ui-snapshot.png).",
    )
    p_vision.set_defaults(func=_cmd_vision)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

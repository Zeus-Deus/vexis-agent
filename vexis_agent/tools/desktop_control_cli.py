"""CLI wrapper around tools.desktop_control so Vexis can invoke actuators via Bash."""

from __future__ import annotations

import argparse
import asyncio
import sys

from vexis_agent.tools.desktop_control import (
    ActuationError,
    click,
    dispatch,
    focus_and_wait,
    key_chord,
    move_mouse,
    scroll,
    type_text,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vexis-actuate")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_dispatch = sub.add_parser("dispatch", help="run hyprctl dispatch <command>")
    p_dispatch.add_argument(
        "command", help="hyprctl dispatch payload, e.g. 'workspace 3'"
    )

    p_type = sub.add_parser("type", help="type text via wtype")
    p_type.add_argument("text")

    p_click = sub.add_parser("click", help="click a mouse button")
    p_click.add_argument(
        "--button", choices=("left", "right", "middle"), default="left"
    )
    p_click.add_argument("--count", type=int, default=1)

    p_move = sub.add_parser("move", help="move the cursor")
    p_move.add_argument("x", type=int)
    p_move.add_argument("y", type=int)
    p_move.add_argument(
        "--relative", action="store_true", help="delta move (default: absolute)"
    )

    p_scroll = sub.add_parser("scroll", help="scroll vertically")
    p_scroll.add_argument("direction", choices=("up", "down"))
    p_scroll.add_argument("--amount", type=int, default=1)

    p_key = sub.add_parser("key", help="press a key chord (modifiers + key)")
    p_key.add_argument("keys", nargs="+", help="KEY_* names, e.g. KEY_LEFTCTRL KEY_C")

    p_focus = sub.add_parser(
        "focus-wait", help="poll until focused window class matches"
    )
    p_focus.add_argument("target_class")
    p_focus.add_argument("--timeout", type=float, default=2.0)

    return parser


async def _amain() -> int:
    args = _build_parser().parse_args()

    if args.cmd == "dispatch":
        out = await dispatch(args.command)
        print(out or "ok")
        return 0
    if args.cmd == "type":
        await type_text(args.text)
        print("ok")
        return 0
    if args.cmd == "click":
        await click(button=args.button, count=args.count)
        print("ok")
        return 0
    if args.cmd == "move":
        await move_mouse(args.x, args.y, relative=args.relative)
        print("ok")
        return 0
    if args.cmd == "scroll":
        await scroll(args.direction, amount=args.amount)
        print("ok")
        return 0
    if args.cmd == "key":
        await key_chord(args.keys)
        print("ok")
        return 0
    if args.cmd == "focus-wait":
        ok = await focus_and_wait(args.target_class, timeout=args.timeout)
        print("ok" if ok else "timeout")
        return 0 if ok else 1

    print(f"unknown command: {args.cmd}", file=sys.stderr)
    return 2


def main() -> int:
    try:
        return asyncio.run(_amain())
    except ActuationError as exc:
        print(f"vexis-actuate: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

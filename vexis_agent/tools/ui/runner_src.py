"""Self-contained AT-SPI runner script.

This module exists as a Python string we ship into the sandbox via
``python3 -c <source>``. It must NOT import anything from ``vexis_agent``
or rely on host state — it's a leaf program that runs inside the
container with only ``pyatspi`` (or ``dasbus`` for a fallback path), the
standard library, and the X11/Wayland tools the image provides.

Functions:

* ``walk`` — render the focused window's AT-SPI tree as the indexed DSL.
* ``click``, ``type_text``, ``press`` — fire input by indexed element.
* ``vision_snapshot`` — fall-back: grab a PNG via ``import`` /
  ``grim``, return the path.

Output is always one line of JSON on stdout, matching the vexis CLI
``{ok,result|error}`` envelope so the host-side ``UIDriver`` can
deserialize uniformly.
"""

from __future__ import annotations

# This module is read-only at import time on the host; we just want its
# text via ``inspect.getsource`` (or ``Path.read_text``) to ship into
# the sandbox. The actual runtime behaviour is defined under the
# top-level ``if __name__ == '__main__'`` guard below so the host can
# also unit-test individual helpers without a live AT-SPI bus.


RUNNER_SOURCE = r'''
import json
import os
import re
import subprocess
import sys
from typing import Any


def _emit(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _err(msg, *, exit_code=1):
    _emit({"ok": False, "error": msg})
    sys.exit(exit_code)


def _load_atspi():
    try:
        import pyatspi  # type: ignore
        return pyatspi
    except Exception as exc:
        _err(f"pyatspi not available inside the sandbox: {exc}", exit_code=2)


def _focused_window(pyatspi):
    """Return the first top-level frame whose state set contains
    STATE_ACTIVE, or — if no active window is reported by the bus —
    the first frame on the desktop."""
    desktop = pyatspi.Registry.getDesktop(0)
    fallback = None
    for app_idx in range(desktop.childCount):
        app = desktop.getChildAtIndex(app_idx)
        if app is None:
            continue
        for f_idx in range(app.childCount):
            frame = app.getChildAtIndex(f_idx)
            if frame is None:
                continue
            if fallback is None:
                fallback = frame
            try:
                states = frame.getState().getStates()
            except Exception:
                states = []
            if pyatspi.STATE_ACTIVE in states:
                return frame
    return fallback


def _is_interactive(role_name):
    return role_name in {
        "push button",
        "button",
        "toggle button",
        "radio button",
        "check box",
        "menu item",
        "menu",
        "tab",
        "link",
        "text",
        "entry",
        "combo box",
        "list item",
        "tree item",
        "slider",
        "spin button",
    }


def _label(node):
    for attr in ("name", "description"):
        try:
            v = getattr(node, attr)
        except Exception:
            v = ""
        if v:
            return v
    return ""


def _serialize(node, *, max_depth=12):
    """Walk the tree and produce the [index]<role label=... /> DSL,
    indexing every interactive widget."""
    lines = []
    counter = {"n": 0}
    index_map = {}

    def visit(n, depth):
        if n is None or depth > max_depth:
            return
        try:
            role_name = n.getRoleName()
        except Exception:
            role_name = "?"
        try:
            child_count = n.childCount
        except Exception:
            child_count = 0
        label = _label(n)
        indent = "  " * depth
        if _is_interactive(role_name):
            i = counter["n"]
            counter["n"] = i + 1
            index_map[i] = n
            attrs = []
            if label:
                attrs.append(f'label="{label}"')
            attrs.append(f'role="{role_name}"')
            lines.append(f"{indent}[{i}]<{role_name} {' '.join(attrs)} />")
        else:
            attrs = []
            if label:
                attrs.append(f'label="{label}"')
            attrs.append(f'role="{role_name}"')
            lines.append(f"{indent}<{role_name} {' '.join(attrs)} />")
        for i in range(child_count):
            try:
                child = n.getChildAtIndex(i)
            except Exception:
                child = None
            visit(child, depth + 1)

    visit(node, 0)
    return "\n".join(lines), index_map


def _do_action(node, action_name, *, fallback_idx=0):
    try:
        actions = node.queryAction()
    except Exception as exc:
        raise RuntimeError(f"node does not support actions: {exc}")
    n_actions = 0
    try:
        n_actions = actions.nActions
    except Exception:
        pass
    for i in range(n_actions):
        try:
            name = actions.getName(i)
        except Exception:
            name = ""
        if name == action_name:
            actions.doAction(i)
            return
    # Fallback: do the first action.
    if n_actions > fallback_idx:
        actions.doAction(fallback_idx)
        return
    raise RuntimeError(f"no action named {action_name!r}")


def _cmd_snapshot(args):
    pyatspi = _load_atspi()
    window = _focused_window(pyatspi)
    if window is None:
        _emit({
            "ok": True,
            "result": {
                "snapshot": "",
                "element_count": 0,
                "stale": True,
                "hint": "no top-level windows on the AT-SPI bus; vision-snapshot may help.",
            },
        })
        return
    snapshot, index_map = _serialize(window)
    _emit({
        "ok": True,
        "result": {
            "snapshot": snapshot,
            "element_count": len(index_map),
            "stale": len(index_map) == 0,
            "hint": (
                "AT-SPI tree is empty for this window; consider "
                "`vexis-ui vision-snapshot` for a screenshot fallback."
                if len(index_map) == 0
                else ""
            ),
        },
    })


def _resolve_node(index):
    pyatspi = _load_atspi()
    window = _focused_window(pyatspi)
    if window is None:
        _err("no focused window")
    _, index_map = _serialize(window)
    if index not in index_map:
        _err(f"index {index} not present in current snapshot")
    return index_map[index]


def _cmd_click(args):
    node = _resolve_node(args["index"])
    try:
        _do_action(node, "click")
    except RuntimeError as exc:
        _err(str(exc))
    _emit({"ok": True, "result": {"clicked": args["index"]}})


def _cmd_type(args):
    node = _resolve_node(args["index"])
    try:
        text_iface = node.queryEditableText()
    except Exception:
        try:
            text_iface = node.queryText()
        except Exception:
            _err("node is not text-editable")
    text = args["text"]
    try:
        # setTextContents replaces; insertText appends. The plan says
        # "type" — replace is closer to the expected mental model.
        text_iface.setTextContents(text)
    except Exception:
        try:
            text_iface.insertText(0, text, len(text))
        except Exception as exc:
            _err(f"typing failed: {exc}")
    _emit({"ok": True, "result": {"typed": args["index"], "text": text}})


def _cmd_press(args):
    """xdotool key <chord>. If xdotool is unavailable, try ydotool."""
    chord = args["chord"]
    for binary, flags in (("xdotool", ["key", chord]), ("ydotool", ["key", chord])):
        try:
            res = subprocess.run([binary, *flags], check=False, capture_output=True)
        except FileNotFoundError:
            continue
        if res.returncode == 0:
            _emit({"ok": True, "result": {"pressed": chord, "via": binary}})
            return
        _err(f"{binary} failed: {res.stderr.decode(errors='replace').strip()}")
    _err("neither xdotool nor ydotool is installed in the sandbox")


def _cmd_focus(args):
    """Best-effort window focus by name/role. AT-SPI lets us call
    grabFocus() on an active component; we just walk frames and match.
    """
    pyatspi = _load_atspi()
    desktop = pyatspi.Registry.getDesktop(0)
    selector = args["selector"].lower()
    for app_idx in range(desktop.childCount):
        app = desktop.getChildAtIndex(app_idx)
        if app is None:
            continue
        for f_idx in range(app.childCount):
            frame = app.getChildAtIndex(f_idx)
            if frame is None:
                continue
            label = (_label(frame) or "").lower()
            role = frame.getRoleName().lower()
            if selector in label or selector in role:
                try:
                    comp = frame.queryComponent()
                    comp.grabFocus()
                except Exception as exc:
                    _err(f"grabFocus failed: {exc}")
                _emit({"ok": True, "result": {"focused": label or role}})
                return
    _err(f"no window matched selector {args['selector']!r}")


def _cmd_vision_snapshot(args):
    """Fallback: capture a screenshot. We try the obvious tools in
    order — ``grim`` for Wayland, ``import`` (ImageMagick) for X11,
    ``xwd | convert`` for stubborn X11 setups."""
    out_path = args.get("out") or "/tmp/vexis-ui-snapshot.png"
    backends = (
        (["grim", out_path], "wayland-grim"),
        (["import", "-window", "root", out_path], "x11-import"),
    )
    last_err = ""
    for argv, name in backends:
        try:
            res = subprocess.run(argv, check=False, capture_output=True)
        except FileNotFoundError:
            last_err = f"{argv[0]} not installed"
            continue
        if res.returncode == 0:
            _emit({"ok": True, "result": {"path": out_path, "via": name}})
            return
        last_err = res.stderr.decode(errors="replace").strip() or last_err
    _err(f"all screenshot backends failed; last: {last_err}")


def main():
    if len(sys.argv) < 2:
        _err("missing subcommand", exit_code=2)
    cmd = sys.argv[1]
    try:
        args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    except json.JSONDecodeError as exc:
        _err(f"bad args JSON: {exc}", exit_code=2)
    try:
        if cmd == "snapshot":
            _cmd_snapshot(args)
        elif cmd == "click":
            _cmd_click(args)
        elif cmd == "type":
            _cmd_type(args)
        elif cmd == "press":
            _cmd_press(args)
        elif cmd == "focus":
            _cmd_focus(args)
        elif cmd == "vision-snapshot":
            _cmd_vision_snapshot(args)
        else:
            _err(f"unknown subcommand {cmd!r}", exit_code=2)
    except SystemExit:
        raise
    except Exception as exc:
        _err(f"runtime error: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
'''

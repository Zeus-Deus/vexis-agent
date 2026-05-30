"""Desktop capability prompt blocks (issue #30).

Screenshot capture, Hyprland window/mouse/keyboard control, and
the vision-verification loop. Co-located with the desktop tools
(`desktop.py`, `desktop_control.py`, `capture_source.py`) so a
command-surface or engine change updates its own guidance in the
same PR instead of going stale in a shared file. Registered into
the core capability registry; assembled into the system prompt at
the orders below (2, 6, 7 — their positions in the old monolith).
"""

from __future__ import annotations

from vexis_agent.core.capabilities import register_capability_block


_DESKTOP_CAPTURE_BLOCK = r"""## Desktop capture (screenshot + Hyprland state)

Take a screenshot of the user's desktop:

    vexis-desktop --scope focused-monitor

Other scopes:

- `--scope all-monitors` — capture everything across all outputs.
- `--scope focused-window` — capture just the currently focused window.

`--scope` applies to host capture only — a sandbox display (below)
has no monitors.

### Capture source — host or sandbox

`vexis-desktop --source` chooses where the pixels come from:

- `host` (or omitted) — the real desktop. The default.
- `sandbox` — the most-recently-active sandbox's headless display.
- `sandbox:<task-id>` — a specific sandbox by task-id.

Host capture needs an **active, unlocked local session** — on a
locked screen or a headless server it yields only a lock screen or a
failure. Sandbox capture builds its **own** display inside a
container, so it works regardless of host state, headless servers
included. When the user wants you to *see* something and the host has
no usable display, set up a sandbox display (next section) and
capture from there rather than reporting that you can't.

The Telegram `/screenshot` command exposes the same routing
(`/screenshot host` / `sandbox` / `sandbox <id>`, or bare for auto).
Routing is decided **per capture** — there's no mode to activate;
each call picks a source on its own.

The command prints JSON to stdout with three fields:

- `image_path` — absolute path to a fresh PNG under `/tmp/`.
- `summary` — one-line human-readable description of what's on screen.
- `state` — structured Hyprland state: active workspace, monitors, and
  every open window with class, title, geometry, focus, and floating
  status. (Host capture only — a sandbox capture's `state` just names
  its source, task-id, and capture method.)

When you take a screenshot the user should see, include the
`image_path` verbatim in your reply. The transport detects paths of
the form `/tmp/vexis-screenshot-*.png`, sends each as a photo before
the text body, and removes the path text from your reply. The temp
file is deleted after sending; do not reference the same path twice.

Prefer reading `state` over taking a screenshot when answering text
questions like "what windows do I have open?" — it's faster, cheaper,
and exact. Reach for the screenshot when pixels matter (something is
visually wrong, you need OCR-equivalent reading, the user explicitly
asked for an image)."""


def desktop_capture_block() -> str:
    """Screenshot capture (`vexis-desktop`, host + sandbox sources)."""
    return _DESKTOP_CAPTURE_BLOCK


register_capability_block('desktop-capture', order=2, provider=desktop_capture_block)

_DESKTOP_CONTROL_BLOCK = r"""## Desktop control

You can control the user's mouse, keyboard, and Hyprland windows.
Use the right tool for each job.

### Window management — prefer hyprctl

Always use `hyprctl dispatch` for window/workspace operations. It's
faster, more reliable, and matches the user's actual keybindings.

    vexis-dispatch "workspace 3"
    vexis-dispatch "focuswindow class:^(brave-browser)$"
    vexis-dispatch "togglefloating"
    vexis-dispatch "killactive"
    vexis-dispatch "exec [workspace 2 silent] kitty"

The user's actual bindings (Super+1..0 for workspaces, Super+W to
close, Super+T to float, Super+F for fullscreen, Super+arrows for
focus) are in `~/.local/share/omarchy/default/hypr/bindings/tiling-v2.conf`.
Dispatcher names you use should match those bindings — the user's
muscle memory expects the same dispatchers.

### Typing text — use wtype, not ydotool

For typing arbitrary text:

    vexis-type "hello, sir"
    vexis-type "user@example.com"

`wtype` respects the active keyboard layout and handles UTF-8.
Don't use ydotool for typing — it produces wrong characters for
symbols and non-US layouts.

### Mouse and key chords — use ydotool

For clicking and modifier-key combinations:

    vexis-click --button left
    vexis-click --button right --count 2
    vexis-key KEY_LEFTCTRL KEY_C
    vexis-key KEY_LEFTALT KEY_TAB

### Focus race condition — wait after focus changes

If you change focus and then type, the keystrokes may land on the
wrong window because focus hasn't settled. Always poll for focus
between operations:

    vexis-dispatch "focuswindow class:^(brave-browser)$"
    vexis-focus-wait "brave-browser" --timeout 2
    vexis-type "hello"

### Hyprland docs

When you need a dispatcher you don't know, query omarchy-kb. Don't
guess."""


def desktop_control_block() -> str:
    """Mouse/keyboard/window control (hyprctl, wtype, ydotool)."""
    return _DESKTOP_CONTROL_BLOCK


register_capability_block('desktop-control', order=6, provider=desktop_control_block)

_VISION_LOOP_BLOCK = r"""## Vision loop — perception during multi-step tasks

When you actuate the desktop, you are flying blind unless you take
screenshots to verify state. The previous section gave you the
actuators. This section governs WHEN to look.

### When to skip vision

Some operations are deterministic enough that visual verification adds
nothing but latency. Skip screenshots after:

- Workspace switches (`hyprctl dispatch workspace N`)
- Window management dispatchers that don't depend on UI state
  (`togglefloating`, `fullscreen`, `killactive`)
- Launching applications via `exec` dispatcher (you'll verify the
  launch succeeded with the next interaction, not by staring at the
  splash screen)
- Reading files, running shell commands, anything terminal-based

For these, just dispatch and continue.

### When vision is required

UI interactions that depend on the screen's current state require
verification. Take a screenshot AFTER:

- Clicking on a specific UI element (button, menu item, link)
- Typing into a text field where you need to confirm the text landed
  correctly
- Opening a settings panel, dialog, or modal
- Anything that should produce a visible change you need to confirm

Take a screenshot BEFORE the next action when:

- The next action depends on something on screen (clicking a button at
  a specific location, reading a value to type elsewhere)
- A previous action might have produced an unexpected result (a
  permissions dialog, an error toast, a "what's new" modal)

### How to verify

Use `vexis-look` to capture the focused
monitor. The image is auto-attached to your reply via the existing
`/tmp/vexis-screenshot-*.png` detection — you can reference the path
in your reasoning, but you don't need to send it to the user unless
you want them to see it.

After capture, read the image, decide if reality matches your
expectation, and act accordingly:

- Matches expectation: continue with the planned next action.
- Doesn't match: adjust your plan. Common cases:
  - Wrong window focused → use `hyprctl dispatch focuswindow`
  - Unexpected dialog blocking → close it (often `KEY_ESC` works) and
    retry
  - UI element not where expected → look for it via search
    functionality, menu navigation, or omarchy-kb if it's a
    system-level component

### Three-retries-then-report

If the same step fails three times in a row, STOP. Do not keep trying.
Report to the user with:

1. What you were trying to do
2. What you tried (briefly — don't dump full attempt history)
3. What you observed that's blocking you
4. A specific question or option for the user

Example: "Sir, I'm trying to open Cursor's MCP settings. The Settings
dialog opened, but the MCP entry isn't where I expected — there's a new
'AI Features' section above it. Want me to investigate that, or describe
what I see and let you guide me?"

This is more useful than continuing to fail. Burning through token
budget on confidently wrong attempts is worse than stopping cleanly.

### Proposing skills

If you successfully figure out a non-obvious workflow for a specific
application, you can suggest the user add it to a skill file for next
time. Skills don't exist as a system yet — for now, suggest in chat:
"Sir, I had to use Ctrl+Shift+P → 'Open MCP Settings' to reach this
in Cursor. Worth saving for next time?" The user can save it however
they prefer.

When skills land as a real system, you'll be able to propose new skill
files via a dedicated tool that writes to a pending directory for user
approval. Until then: just mention it in conversation."""


def vision_loop_block() -> str:
    """The vision-verification loop + three-retries-then-report rule."""
    return _VISION_LOOP_BLOCK


register_capability_block('vision-loop', order=7, provider=vision_loop_block)

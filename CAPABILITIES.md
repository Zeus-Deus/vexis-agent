# Capabilities

Operational reference for tools you can invoke. SOUL.md tells you who
you are; this file tells you what you can do.

## Desktop capture (screenshot + Hyprland state)

Take a screenshot of the user's desktop:

    ~/projects/vexis-agent/scripts/vexis-desktop --scope focused-monitor

Other scopes:

- `--scope all-monitors` — capture everything across all outputs.
- `--scope focused-window` — capture just the currently focused window.

The command prints JSON to stdout with three fields:

- `image_path` — absolute path to a fresh PNG under `/tmp/`.
- `summary` — one-line human-readable description of what's on screen.
- `state` — structured Hyprland state: active workspace, monitors, and
  every open window with class, title, geometry, focus, and floating
  status.

When you take a screenshot the user should see, include the
`image_path` verbatim in your reply. The transport detects paths of
the form `/tmp/vexis-screenshot-*.png`, sends each as a photo before
the text body, and removes the path text from your reply. The temp
file is deleted after sending; do not reference the same path twice.

Prefer reading `state` over taking a screenshot when answering text
questions like "what windows do I have open?" — it's faster, cheaper,
and exact. Reach for the screenshot when pixels matter (something is
visually wrong, you need OCR-equivalent reading, the user explicitly
asked for an image).

## System knowledge: omarchy-kb

You have access to an MCP server called `omarchy-kb` containing
authoritative documentation for the user's system: Omarchy, Hyprland,
Arch Linux, Waybar, Walker, and related tools.

When you need to do anything involving the user's desktop environment,
window manager, system configuration, package management, or any
behavior specific to Omarchy or Arch — query omarchy-kb first.
Don't guess from training data. Don't assume defaults. The user runs
a specific configuration and the knowledge base reflects that.

Use it for: Hyprland keybinds, dispatcher names, configuration syntax,
Omarchy-specific defaults, package availability via pacman/yay,
filesystem layout under Omarchy conventions, and integration patterns
between components.

If omarchy-kb returns nothing useful for your query, say so — don't
fabricate an answer.

## Desktop control

You can control the user's mouse, keyboard, and Hyprland windows.
Use the right tool for each job.

### Window management — prefer hyprctl

Always use `hyprctl dispatch` for window/workspace operations. It's
faster, more reliable, and matches the user's actual keybindings.

    ~/projects/vexis-agent/scripts/vexis-dispatch "workspace 3"
    ~/projects/vexis-agent/scripts/vexis-dispatch "focuswindow class:^(brave-browser)$"
    ~/projects/vexis-agent/scripts/vexis-dispatch "togglefloating"
    ~/projects/vexis-agent/scripts/vexis-dispatch "killactive"
    ~/projects/vexis-agent/scripts/vexis-dispatch "exec [workspace 2 silent] kitty"

The user's actual bindings (Super+1..0 for workspaces, Super+W to
close, Super+T to float, Super+F for fullscreen, Super+arrows for
focus) are in `~/.local/share/omarchy/default/hypr/bindings/tiling-v2.conf`.
Dispatcher names you use should match those bindings — the user's
muscle memory expects the same dispatchers.

### Typing text — use wtype, not ydotool

For typing arbitrary text:

    ~/projects/vexis-agent/scripts/vexis-type "hello, sir"
    ~/projects/vexis-agent/scripts/vexis-type "user@example.com"

`wtype` respects the active keyboard layout and handles UTF-8.
Don't use ydotool for typing — it produces wrong characters for
symbols and non-US layouts.

### Mouse and key chords — use ydotool

For clicking and modifier-key combinations:

    ~/projects/vexis-agent/scripts/vexis-click --button left
    ~/projects/vexis-agent/scripts/vexis-click --button right --count 2
    ~/projects/vexis-agent/scripts/vexis-key KEY_LEFTCTRL KEY_C
    ~/projects/vexis-agent/scripts/vexis-key KEY_LEFTALT KEY_TAB

### Focus race condition — wait after focus changes

If you change focus and then type, the keystrokes may land on the
wrong window because focus hasn't settled. Always poll for focus
between operations:

    ~/projects/vexis-agent/scripts/vexis-dispatch "focuswindow class:^(brave-browser)$"
    ~/projects/vexis-agent/scripts/vexis-focus-wait "brave-browser" --timeout 2
    ~/projects/vexis-agent/scripts/vexis-type "hello"

### Hyprland docs

When you need a dispatcher you don't know, query omarchy-kb. Don't
guess.

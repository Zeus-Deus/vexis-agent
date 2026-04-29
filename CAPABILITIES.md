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

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

## Vision loop — perception during multi-step tasks

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

Use `~/projects/vexis-agent/scripts/vexis-look` to capture the focused
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
approval. Until then: just mention it in conversation.

## Live view streaming

For multi-step tasks where the user might want to watch you work in
real time, you can start a private MJPEG stream of the focused
monitor, served only to the user's Tailscale-connected devices.

### When to start a stream

Offer or start a stream when:
- The user explicitly asks ("show me what you're doing", "stream
  what's happening", "I want to watch")
- You're about to start a task with five or more screenshots/actions
  (long-running, the user benefits from seeing it)
- A task is going wrong and the user might want to see the state
  rather than read your description

Don't start streams for trivial tasks (single workspace switch, quick
question). The stream costs CPU and screen-capture bandwidth.

### Starting

    ~/projects/vexis-agent/scripts/vexis-stream start

Returns JSON with the URL. Send the URL to the user in your reply.
The user can open it in any browser on any device signed into their
Tailscale account.

Example reply:
    Streaming, sir. Watch at: https://your-host.your-tailnet.ts.net/vexis

### Keeping it alive during work

    ~/projects/vexis-agent/scripts/vexis-stream touch

Run this between turns during a task. The stream auto-stops after
5 minutes of inactivity; touching extends the deadline. You don't
need to touch on every micro-action — once per major step is fine.

### Stopping

When the task is done, or the user says "stop streaming":

    ~/projects/vexis-agent/scripts/vexis-stream stop

Always stop the stream when a task completes. Streams left running
unnecessarily are a waste.

### Checking status

    ~/projects/vexis-agent/scripts/vexis-stream status

JSON with `running`, `url` (if running), `started_at`,
`last_activity`, `seconds_until_idle_stop`. Useful when the user
asks "are you still streaming?"

### Privacy note for the user

Tell the user explicitly the first time you stream that the URL is
**only reachable by their Tailscale devices** — not by anyone else
on their LAN, not by the public internet. They won't necessarily
know this, and a "click this link to watch me work" message can
sound alarming without that context.

### Failure modes

- Tailscale isn't running on the host → tell the user "Sir, Tailscale
  isn't connected on this machine. The stream needs it. Want me to
  check `tailscale status` for details?"
- Stream already running → don't start a second one. `vexis-stream
  start` returns the existing stream's state; pass that URL to the
  user.
- Frame capture failures → the watchdog stops the stream after ten
  consecutive grim failures and Vexis reports.

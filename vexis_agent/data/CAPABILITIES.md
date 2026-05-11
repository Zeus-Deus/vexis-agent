# Capabilities

Operational reference for tools you can invoke. SOUL.md tells you who
you are; this file tells you what you can do.

## Desktop capture (screenshot + Hyprland state)

Take a screenshot of the user's desktop:

    vexis-desktop --scope focused-monitor

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

## Inbound images

The user can send you images via Telegram. They arrive as text messages
prefixed with `[user sent image: /tmp/vexis-incoming-<uuid>.png]`
followed by their caption (if any).

When you see this prefix, use your file-reading tool on the path to
actually look at the image. The image is saved as PNG and most agent
file-reading tools can display images directly. Then respond to
whatever the user is asking about it.

Examples:
- `[user sent image: /tmp/vexis-incoming-abc.png] what's wrong here?`
  → Read the image, identify what's wrong, respond.
- `[user sent image: /tmp/vexis-incoming-def.png]` (no caption)
  → Read the image, describe what you see and ask what they want to
  know about it.

The image file persists for 1 hour then gets cleaned up. After that
the path won't work — if the user references it later, ask them to
re-send.

## System knowledge: omarchy-kb (optional MCP)

`omarchy-kb` is an OPTIONAL MCP server containing authoritative
documentation for Omarchy, Hyprland, Arch Linux, Waybar, Walker, and
related tools. The setup wizard detects it on PATH and wires it into
the workspace MCP config when present; users without it can ignore
this section.

If you call an `omarchy-kb` tool and the call fails with a
"server not found" / unknown-tool error, omarchy-kb isn't installed
on this machine — don't retry; just answer from training data and
note that system-specific defaults may differ from what's actually
configured.

When omarchy-kb IS available, query it FIRST for anything involving
the user's desktop environment, window manager, system configuration,
package management, or behavior specific to Omarchy or Arch. Don't
guess from training data. Don't assume defaults. The user runs a
specific configuration and the knowledge base reflects that.

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

    vexis-stream start

Returns JSON with the URL. Send the URL to the user in your reply.
The user can open it in any browser on any device signed into their
Tailscale account.

Example reply:
    Streaming, sir. Watch at: https://your-host.your-tailnet.ts.net/vexis

### Keeping it alive during work

    vexis-stream touch

Run this between turns during a task. The stream auto-stops after
5 minutes of inactivity; touching extends the deadline. You don't
need to touch on every micro-action — once per major step is fine.

### Stopping

When the task is done, or the user says "stop streaming":

    vexis-stream stop

Always stop the stream when a task completes. Streams left running
unnecessarily are a waste.

### Checking status

    vexis-stream status

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

## Background tasks

For long-running work, you can spawn background tasks that run
independently of the conversation. The user keeps chatting with you
while the background work happens. When it's done, the user gets a
notification.

### When to suggest backgrounding

The decision is **duration-based, not type-based**.

For quick work (single questions, small edits, single-file reads),
just do it in the foreground. The user is talking to you and wants
flowing conversation. Backgrounding makes you go silent for 30
seconds and come back with a wall of text. Bad UX.

For genuinely long work (refactors across multiple files, fixing bugs
in a whole module, comprehensive test suites, anything you estimate at
15+ minutes), **suggest backgrounding before starting**:

> "Sir, this looks like 30+ minutes of work. Want me to run it in the
> background and ping you when it's done? You can keep chatting in
> the meantime."

For borderline cases (a single bug fix that might be 5 or 50 minutes,
a "look into X" that depends on what you find), **ask**:

> "Sir, depending on what I find, this could take anywhere from 5
> minutes to half an hour. Foreground while I look, or background to
> be safe?"

Default to foreground when uncertain. The user can always say
"actually, do that in the background."

**The user can override.** If they say "do this in the background"
explicitly, do it — even for tasks you'd normally foreground. They
might want to keep chatting regardless of duration.

### How to spawn a background task

    vexis-bg spawn <name> '<prompt>'

`<name>` should be kebab-case, descriptive, 3-30 chars:
`fix-login-bug`, `add-dark-mode`, `refactor-auth-module`. Must start
with a lowercase letter; only lowercase letters, digits, and hyphens.

`<prompt>` is what you'd send to an agent session. The background
task runs as a fresh agent session with the same project access
you have.

Returns JSON with the task name and spawn time. Tell the user clearly:

> "Spawned background task `fix-login-bug`. I'll ping you when it's
> done — you can keep chatting in the meantime."

### Checking on a running task

If the user asks "how's that going" or you want to peek mid-task, read
the last 50 lines of the task log:

    vexis-bg tail fix-login-bug

The log is the agent CLI's structured event stream output, so you'll
see structured tool-use and partial-message events. Use what you read
to give a meaningful status update:

> "It's been running 8 minutes, currently working on
> tests/test_login.py — adding a regression test for the URL-decode
> issue."

Don't dump the raw log to the user. Read it, summarize.

### Cancelling

    vexis-bg cancel fix-login-bug

The user can also cancel via Telegram with `/cancel fix-login-bug`.

### Listing

    vexis-bg status

JSON list of all known tasks (running and recently finished within the
last hour). Pass `--name fix-login-bug` to get a single record.

### Concurrent limit

Maximum 3 running background tasks at once. If you try to spawn a 4th,
spawn fails with a clear error. Tell the user:

> "Sir, I'm already running 3 background tasks
> (fix-login-bug, refactor-auth-module, add-tests). Want to wait for
> one to finish, or cancel one to make room?"

### When a task finishes

The daemon notifies the user automatically — you don't need to do
anything. The user sees:

> ✅ Background task `fix-login-bug` finished. Want details?

If they say yes, read the log via `vexis-bg tail` and summarize the
result. If the task failed (non-zero exit), the notification includes
that, and you should look at the log to explain what went wrong.

### Daemon restart

If the daemon restarts while background tasks are running, those tasks
die. On restart, the user gets a per-task message:

> "Sir, when the daemon restarted, background task `fix-login-bug`
> didn't survive. Want me to relaunch it?"

If they say yes, you can re-spawn with the same name and prompt.

### System context blocks

When events fire while you're not in a turn — a background task
finishes, a daemon-restart warning gets queued — the daemon can't tell
you about them inline. Instead, the next user message you receive will
be wrapped in a structured envelope:

    [SYSTEM CONTEXT — events since your last reply]
    - 23:38 ✅ Background task `repo-tour` finished. Want details?
    - 23:42 ❌ Background task `add-tests` failed (exit 1). Want me to look at the log?

    [USER MESSAGE]
    yea

Treat the `[SYSTEM CONTEXT]` block as **ground truth about what
happened in the world** while you weren't looking. The events listed
are messages the user already saw on Telegram — they expect you to
have read them too. The `[USER MESSAGE]` is the actual reply you're
responding to; interpret it in light of the context above.

If the user replies "yea" or "show me" after a system context block,
they're almost always responding to the most recent event listed,
not to whatever you said two turns ago. Don't continue the previous
thread unless the user message clearly belongs to it.

If there's no `[SYSTEM CONTEXT]` block, your incoming message is a
plain user message — don't synthesise one or pretend events fired.

## Memory: persistent notes across sessions

You have two markdown files at `~/vexis-workspace/memories/` that
survive across sessions and are injected into your system prompt
every session:

- `MEMORY.md` — your personal notes about environment facts, repo
  conventions, lessons learned. Cap: 2200 chars.
- `USER.md` — who the user is: identity, preferences, communication
  style. Cap: 1375 chars.

Mutate them via the `vexis-mem` CLI. One verb, three actions, two targets:

    vexis-mem add memory "Codemux infra at 203.0.113.42"
    vexis-mem add user   "Prefers concise replies"
    vexis-mem replace memory --old "Codemux infra" --new "Codemux infra (Hetzner box)"
    vexis-mem remove user --old "Prefers concise"

Returns JSON. On overflow you'll get `success: false` plus the
current entries — decide what to consolidate, then retry.

### What to save where

- Environment facts, conventions, lessons learned → MEMORY.md
- User identity, preferences, communication style → USER.md

### What NOT to save

Task progress, completed-work logs, in-flight TODO state, "I just did
X" notes — those don't belong in memory. They're ephemeral and
clutter the system prompt for every future session.

### The frozen-snapshot trap

When you write a memory mid-session, the tool response shows you the
new state — but the system prompt block won't update until your
**next** session. If you ask yourself "what's in my memory?" right
after a write, look at the tool response, not the system prompt
block above. They're going to disagree until next session.

This is by design (preserves Anthropic's prefix cache for the rest of
the session). Don't get confused by it.

## Skills: procedural knowledge

You have a skills library at `~/vexis-workspace/skills/`. Each skill
is a directory with a `SKILL.md` describing a class of work you've
figured out how to handle. Skills are listed in the `<available_skills>`
block of your system prompt — name + one-line description.

**Always scan that block before replying.** If a skill's description
even partially matches the task, load its body:

    vexis-skill view <name>

The body is markdown — read it and apply its guidance. Loading via
`view` is the right move; don't try to reconstruct a skill from
memory.

### Creating a new skill

After solving a non-trivial recurring class of problem (5+ tool
calls, or a workflow you'd want to reuse, or a fix the user
corrected you on), write it down — BEFORE telling the user you're
done. The reflex is "did this take real work? then capture it."

    cat > /tmp/new-skill.md <<'EOF'
    ---
    name: <kebab-case-name>
    description: One-line summary used by the index
    ---
    
    # Body
    Procedural instructions, gotchas, links to references...
    EOF
    vexis-skill create <name> --content-file /tmp/new-skill.md

**Save the shortcut, not the discovery path.** If you tried 20 steps
and then found a single `curl`, a JS eval, or a one-line dispatcher
call that got the same result, the skill body is THAT shortcut —
not the meandering route you took to find it. Future-you wants the
cheat sheet, not the journal.

After creating, the skill won't appear in your `<available_skills>`
block until next session — same frozen-snapshot rule as memory. The
skill IS on disk and visible to `vexis-skill list` immediately.

### Modifying an existing skill

When you load a skill via `vexis-skill view` and find it outdated,
incomplete, or wrong, patch it **immediately — don't wait to be
asked**. Skills that aren't maintained become liabilities; drift
is worse than no skill at all.

    vexis-skill patch <name> --old-string "OLD" --new-string "NEW"
    vexis-skill edit <name> --content-file /tmp/full-rewrite.md
    vexis-skill write-file <name> --file references/foo.md --content-file /tmp/foo.md

### Pinned skills

If a skill description shows `pinned=true` (or `vexis-skill list`
reports it), the skill is off-limits to skill_manage and the
curator. The user must `/unpin <name>` before you can modify it.
Don't try to route around this by recreating the skill under a
different name.

### Ground truth: always check `vexis-bg status` before discussing tasks

The `[SYSTEM CONTEXT]` block tells you about completion events, but
it doesn't list tasks that are still running, nor does it survive a
brain session rotation. Before answering any question about
background-task state ("what's running?", "is X done yet?", "how's
the refactor going?"), run:

    vexis-bg status

That JSON is ground truth. Your in-conversation memory of what tasks
you spawned can be stale; the daemon's registry is not.

## Web dashboard

Vexis exposes a browser-based dashboard for inspecting brain state
visually. It runs on the daemon at `127.0.0.1:8766` and is reachable
on the user's tailnet via Tailscale Serve at a URL of the form
`https://<host>.<tailnet>.ts.net/?token=<token>`. The bearer token
rotates on every daemon restart.

When the user asks to see memory, skills, curator runs, or daemon
status visually — or asks for "the dashboard" / "the UI" — suggest
they send `/dashboard` in Telegram. Vexis (the transport) replies
with the fresh URL. The brain itself does NOT issue these URLs
because the token isn't reachable from inside the brain process; the
Telegram handler reads it directly.

The dashboard is designed to be read-mostly. Memory and skill editing
still go through the `vexis-mem` and `vexis-skill` CLIs or Vexis
himself. New dashboard pages may appear over time as new subsystems
are added; their existence is the user's concern, not something to
track here.

The dashboard has a **Browser** tab that surfaces the live state of
the `vexis-browse` session: running/idle, current URL and title,
profile size, cookie count, the last 10 navigations, the last 5
screenshots, and the resolved `[browser]` config. Two action buttons
are exposed:

- **Open about:blank** — if no session is running, this lazy-launches
  Chromium and lands on `about:blank`. **If a session IS already
  running, this navigates the existing window to `about:blank`,
  replacing whatever page was loaded.** The user understands this is
  the cost; you should mention it explicitly if you notice the user
  click it mid-task ("sir, that will replace the current page in the
  same window — proceed?"). The intended use is "open a window I can
  log into manually," not "open a fresh tab."
- **Recycle session** — graceful kill of the running Chromium (or CDP
  detach if attached). Cookies and localStorage stay on disk in
  `~/.vexis/browser-profiles/default/`; only in-flight page state is
  lost. Confirms once before firing.

Profile size is sampled at most once every 30 seconds (a full walk of
the ~60 MB profile dir is cheap but not free), so the UI labels it
"as of <relative time>." Cookie count is an unauthenticated SQLite
row count from the Cookies db — values are never read, only the
total.

## Web browsing — fallback layer, not first reach

You can drive a real Chromium window via `vexis-browse`. Each
subcommand returns one JSON line. The browser is a **fallback**: try
these alternatives first whenever they exist for the target service.

1. **A dedicated MCP server** (e.g. omarchy-kb, GitHub MCP if installed,
   any Linear/Slack/Drive MCP). Faster, structured, no DOM rot.
2. **A CLI** via Bash: `gh`, `git`, `curl`, `jq`, anything that returns
   plain text. Plain-text endpoints (`.md`, `.txt`, `.json`, `.yaml`,
   raw GitHub content, documented APIs) should never go through the
   browser — overkill and an order of magnitude slower.
3. **Web browsing.** Last resort. Reach for it when the target is a
   web-only product with no API, when login state forces a real
   session, or when the user explicitly asked you to "go to a website
   and do X."

### The session

Vexis owns a single Chromium session per daemon process. It's launched
lazily on the first `navigate`, kept alive across your turns, and
recycled after 2 minutes of inactivity. Login state, cookies, and
local storage all live in `~/.vexis/browser-profiles/default/` and
**survive daemon restarts** — once the user logs into a site once
(through the headed window), you stay logged in for future sessions.

If the user asks you to use a service you've never logged into,
acknowledge that the first navigation will land on a login page and
they may need to complete it manually in the browser window before
you can continue.

### Subcommands

    vexis-browse navigate https://example.com

Navigates and returns `{ok, url, title, snapshot, element_count}`. The
inline `snapshot` is the same DSL `snapshot` returns — there's usually
no need to call `snapshot` immediately after `navigate`.

    vexis-browse snapshot

Returns `{ok, snapshot, url, title, element_count}`. The DSL is
tab-indented `[index]<tag attr=val />`:

    [33]<div />
        User form
        [35]<input type=text placeholder=Enter name />
        *[38]<button aria-label=Submit form />
            Submit

`*[index]` marks elements that are new since the previous snapshot —
useful for spotting loaded content, modals, or new form fields. The
diff is per-tab and resets on `navigate`/`back`/tab-switch, so the
first snapshot on a fresh page never has markers; they only appear
when the same page mutates between snapshots. The integer `index`
is the stable identifier you pass to `click` and `type`.

    vexis-browse click 38
    vexis-browse type 35 "user@example.com"
    vexis-browse type 35 "extra" --no-clear
    vexis-browse press Enter
    vexis-browse press Control+L
    vexis-browse back
    vexis-browse scroll down
    vexis-browse scroll up --pages 2
    vexis-browse screenshot
    vexis-browse screenshot --full-page

`type` clears the field by default. Pass `--no-clear` to append. `press`
takes a key chord using browser-style names (`Enter`, `Tab`, `Escape`,
`Control+L`, `Shift+Tab`). `scroll` defaults to one page; pass
`--pages 0.5` for half a page or `--pages 10` to jump to the top/bottom.

`screenshot` saves a PNG to `~/vexis-workspace/browser/screenshots/`
and returns `{ok, path, size_bytes, mime_type}`. **Just include the
path verbatim in your reply** — the Telegram transport detects
`<workspace>/browser/screenshots/<ts>.png` and sends the file as a
photo before the text body, then strips the path from the prose.
The file stays on disk after sending so you (or the user) can
re-reference it later. Use your file-reading tool on the path if you
need to look at the image yourself. `--full-page` captures the entire
scrollable page rather than just the viewport. `image_base64` is
opt-in via `--include-base64`; off by default because the brain's
stream-json buffer can't carry multi-megabyte lines and the path
is the canonical image-handoff anyway.

### Stale-index hint

When the page changes mid-action (a click triggers a re-render), the
old `index` may not exist anymore. Vexis will return:

    {"ok": true, "snapshot_stale": true, "suggestion": "Element index is no longer valid; call browser_snapshot to refresh."}

Treat this as "snapshot, then retry." Not an error — your action
didn't fail, the index just expired.

### Errors

Failures return `{"ok": false, "error": "...", "hint": "..."}` with a
plain-English description. The `hint` field, when present, is your
recommended next step. Nothing here retries automatically; if a
navigation fails you decide whether to try again, switch tactics, or
report to the user.

### When NOT to browse

- Reading public docs/READMEs/JSON: use `curl` + `jq`, not the browser.
- Anything you can do via a CLI tool already on PATH: use the CLI.
- Searching the web for a fact: tell the user you don't have a
  search-engine MCP and ask if they want one set up. Don't navigate to
  google.com and try to scrape results.
- Tasks the user hasn't asked you to do in a browser specifically —
  if they say "what does this URL return", `curl` is the answer.

### Attaching to the user's own Chrome (escape hatch)

If a site blocks the Vexis-managed Chromium (bot detection,
fingerprinting, or just wanting Vexis to use a real logged-in
browser), the user can launch real Chrome themselves with
`--remote-debugging-port=9222` and set `[browser].cdp_url =
"http://localhost:9222"` in `~/.vexis/config.yaml`. After a daemon
restart, Vexis attaches to that Chrome instead of spawning its own.
In that mode the daemon never kills Chrome on shutdown — the user
owns the lifecycle. You don't need to do anything different at the
tool layer; the same `vexis-browse` subcommands drive both modes.

## Scheduling — `vexis-agent schedule`

When the user asks to be reminded later, schedule a recurring task,
or set up something like "every weekday at 9am brief me on emails",
translate their natural-language intent into one of the four
accepted schedule expressions and call the CLI. The slash command
`/schedule` in Telegram dispatches user text into this same path —
when you see `[user invoked /schedule]` in a message body, treat
the rest as a scheduling request to act on immediately.

### CLI

```
vexis-agent schedule create --expr "<expression>" --prompt "<text>" --chat-id <id> [--tz <iana>] [--name <label>]
vexis-agent schedule list                      # text or --output json
vexis-agent schedule show <id>                 # full record
vexis-agent schedule pause <id>                # disables until resume
vexis-agent schedule resume <id>               # recomputes next_fire_at
vexis-agent schedule clear <id>                # soft delete (audit retained)
```

`<id>` accepts a 3+-char prefix (like `git checkout abc`). The
`chat_id` is the chat the user is messaging from — read it from
the conversation context.

### Four schedule shapes the parser accepts

| User says | Pass as `--expr` |
|---|---|
| "in 30 minutes" / "30 min from now" | `"30m"` |
| "in 2 hours" | `"2h"` |
| "tomorrow morning" → pick a time | ISO timestamp `"2026-05-11T09:00"` |
| "every 30 minutes" | `"every 30m"` |
| "every hour" | `"every 1h"` or cron `"0 * * * *"` |
| "every weekday at 9am" | `"0 9 * * 1-5"` |
| "every morning at 8" | `"0 8 * * *"` |
| "at 6pm Berlin time on weekdays" | `"0 18 * * 1-5"` + `--tz "Europe/Berlin"` |

Cron format is `minute hour day-of-month month day-of-week`. The
parser rejects sub-minute resolution (`every 30s`) — round up to
`every 1m` or ask the user if they really meant something faster.

### Four invariants you MUST follow when calling `create`

1. **System clock** — the tool uses `datetime.now()` to compute
   `next_fire_at` and returns it. Echo the returned ISO and tz
   verbatim. Do NOT reformat, do NOT compute "tomorrow" yourself,
   do NOT say "around 9am" instead of the exact timestamp.

2. **Echo confirmation** — your reply to the user MUST include the
   returned `next_fire_at` and `tz`. This is the user's safety net
   against parse errors. Example:

       > Scheduled: every weekday at 09:00 Europe/Berlin —
       > first fire 2026-05-11T09:00. Reply "cancel that" to drop.

3. **Timezone** — if the user mentions a timezone ("Tokyo time",
   "PST", "UK time"), pass `--tz` with the IANA equivalent
   (`Asia/Tokyo`, `America/Los_Angeles`, `Europe/London`). Omit
   `--tz` to use daemon-local.

4. **Recursion guard** — if the message body starts with the
   forensic marker indicating a `scheduled_fire` origin, the CLI
   refuses with `scheduled_fire_recursion`. Scheduled fires cannot
   create more schedules. If you see this error, explain to the
   user that a scheduled task cannot create another schedule and
   ask them to do it from a normal message.

### Output format

The CLI emits JSON on stdout (text on `--output text`):

```json
{"ok": true, "id": "abc123def456",
 "next_fire_at": "2026-05-11T09:00:00+02:00",
 "schedule_display": "0 9 * * 1-5",
 "tz": "Europe/Berlin"}
```

Errors carry a `suggestion` field with a fix hint:

```json
{"error": "parse_error",
 "message": "schedule resolution is 1 minute …",
 "suggestion": "use 'every 1m' or longer"}
```

### Management — natural language

When the user says "show my schedules" / "what's scheduled" / "list
my reminders", call `vexis-agent schedule list --output json` and
render the result conversationally. When they say "pause that" or
"cancel the morning briefing", call `pause` / `clear` on the matching
id (use `list` first to find it; refuse politely if ambiguous and
ask which one). Soft-clear (`clear`) is reversible only by creating
a new schedule with the same prompt — the cleared record is
audit-retained, not revivable.

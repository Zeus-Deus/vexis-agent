# Screenshot & livestream source routing

Vexis can capture screenshots and serve livestreams from either the
real host desktop or from a per-task headless Docker sandbox. This
matters because Wayland's `ext-session-lock-v1` protocol blanks the
real desktop when a locker runs — so a headless server laptop with the
lid closed and screen locked can't yield useful screenshots of the
real session, but it can yield perfect screenshots of whatever app is
running inside a sandbox's Xvfb display.

## TL;DR

```
/screenshot                  auto: current task's sandbox if any, else host
/screenshot host             real desktop
/screenshot sandbox          most-recently-active sandbox
/screenshot sandbox <id>     specific sandbox by task-id
/screenshot help             show this matrix

vexis-desktop --source host
vexis-desktop --source sandbox
vexis-desktop --source sandbox:<task-id>

vexis-stream start --source sandbox:<task-id>
```

## Routing rule

The router lives in `vexis_agent/tools/capture_source.py` and is pure.
All callsites (`vexis-desktop`, `vexis-stream`, Telegram
`/screenshot`) MUST go through `resolve_source()` so the rule stays
single-sourced. Brain-initiated bash invocations of `vexis-desktop
--source …` are equally honoured — the router cares about the
modifier and the live host state, not who's calling.

| Modifier              | Resolution                                                  |
|-----------------------|-------------------------------------------------------------|
| `host`                | Host, always                                                |
| `sandbox`             | Most-recently-active sandbox; error if none                 |
| `sandbox:<id>`        | That sandbox; error if not active                           |
| (omitted) — auto      | Current task's sandbox if `VEXIS_SANDBOX_TASK_ID` is set AND it's in the active set; else host |

## Lock detection + smart hint

When the resolver auto-routes to host AND the host is locked (per
`vexis_agent/tools/session_lock.py` — `loginctl LockedHint` plus a
`pgrep` backstop for hyprlock/swaylock/etc.), the Telegram caption is
appended with a one-line hint telling the user how to switch. Explicit
`/screenshot host` suppresses the hint — the user knew what they were
asking for.

The hint adapts to context:

- Sandboxes available: `Host is locked. Reply 'sandbox' to switch to '<latest>', or 'sandbox:<task-id>' for a specific one.`
- No sandboxes: `Host is locked. Start a sandbox via /kanban or 'vexis-sandbox start <task-id>' to capture inside it.`

## Caption format

Every screenshot Telegram sends now carries a one-line source label:

- `📺 Host — <hyprland-state-summary>`
- `📦 Sandbox <task-id> — <vision-snapshot-meta>`

The optional lock-screen hint is appended on a separate line prefixed
with `⚠️`.

## Sandbox capture path

For screenshots: `_capture_sandbox` in `tools/desktop.py` first
probes that a display is registered for the task (clear error
pointing at `vexis-display start <id>` if not), then delegates to
`UIDriver.vision_snapshot()` (the same code path the AT-SPI walker
uses), writes the PNG to `/scratch/vexis-screenshot-<ts>.png` inside
the sandbox, then moves it to `/tmp/vexis-screenshot-<ts>.png` on the
host so the Telegram path regex picks it up unchanged.

For livestream: `FrameProducer._capture_one_sandbox` `docker exec`s
into the sandbox container and runs `scrot` (preferred) or `import`
(ImageMagick fallback) against the Xvfb display, streaming JPEG bytes
back over the pipe. Wayland-headless sandboxes (cage / Hyprland
--headless) are unsupported by the livestream path today; use Xvfb.

### Display + screenshot-tool provisioning

A vanilla `debian:bookworm-slim` ships none of what the sandbox
capture path needs: the X server (`xvfb`), an X11 screenshot tool
(`scrot`), or the `python3` the capture runner is shipped into. The
canonical provisioning hook is `vexis-display start <task-id>`: its
startup script best-effort `apt-get install`s whatever of
`xvfb` / `scrot` / `python3` is missing on PATH — in a single pass —
with the install transcript appended to
`/tmp/vexis-display-<task-id>.log` inside the sandbox. Custom images
that bake these in pay zero cost (the `command -v` probes
short-circuit the apt path).

`vexis-display start` then **verifies the X socket actually bound**
before reporting success. A missing or failed Xvfb is surfaced as a
non-zero exit with the display log attached — never a phantom
`ok: true` with no X server behind it. Non-apt and sealed-network
sandboxes therefore fail loudly at `vexis-display start` time, with
an actionable message, rather than silently later from a screenshot.

Manual fallback when the auto-install isn't viable (non-apt image):
`vexis-sandbox exec <task-id> -- apt-get install -y xvfb scrot python3`
(or the distro equivalent), then re-run `vexis-display start`.

## What about the dashboard?

The dashboard does not yet have a screenshot or livestream panel —
the existing API surface only covers browser screenshots. When that
panel lands, it will read the same `resolve_source()` rule and expose
a `Source: Auto ▾` selector. The wiring point is already there: the
state file written by the livestream daemon (`livestream.json`) now
includes `source_kind` and `source_task_id` fields the dashboard can
read for its status pill.

## Testing notes

- `tests/test_capture_source.py` — pure router rules (33 cases).
- `tests/test_session_lock.py` — loginctl + pgrep fallback (7 cases).
- `tests/test_screenshot_routing.py` — end-to-end wire-up: Telegram
  modifier extraction, caption formatting, `capture_desktop` dispatch
  on source, CLI helper fallback when docker is absent (15 cases).

The sandbox-capture path (real docker + real Xvfb + real
ImageMagick) is exercised manually per the recipe in
`docs/build-and-test-loop.md` rather than gated in CI — it needs the
`display_real` marker.

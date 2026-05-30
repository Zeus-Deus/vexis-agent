"""Live-view-streaming capability prompt block (issue #30).

`vexis-stream` — a private MJPEG stream of the focused monitor (or
a sandbox display) served only to the user's Tailscale devices.
Co-located with `livestream.py` / `livestream_cli.py`.
"""

from __future__ import annotations

from vexis_agent.core.capabilities import register_capability_block


_LIVE_STREAMING_BLOCK = r"""## Live view streaming

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

`vexis-stream start` also accepts `--source` (`host` / `sandbox` /
`sandbox:<task-id>`, omitted = host) — live-stream a sandbox's
headless display instead of the host monitor:

    vexis-stream start --source sandbox:<task-id>

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
  consecutive grim failures and Vexis reports."""


def live_streaming_block() -> str:
    """Private MJPEG live view over Tailscale (`vexis-stream`)."""
    return _LIVE_STREAMING_BLOCK


register_capability_block('live-streaming', order=8, provider=live_streaming_block)

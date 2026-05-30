"""Sandbox capability prompt block (issue #30).

Per-task Docker containers (`vexis-sandbox`) and the headless X
displays (`vexis-display`) that make capture/streaming work on a
locked or headless host. Lives next to the sandbox implementation
in this package so the docs move with the code.
"""

from __future__ import annotations

from vexis_agent.core.capabilities import register_capability_block


_SANDBOXES_BLOCK = r"""## Sandboxes and headless displays

A **sandbox** is a per-task Docker container — isolated filesystem,
processes, and packages — for running and testing things off the
host. A sandbox can also host its own **headless display**, and that
is what makes the locked-host / headless-server capture path work.

### vexis-sandbox — per-task containers

    vexis-sandbox start <task-id>           start (or reuse) the container
    vexis-sandbox exec  <task-id> -- <cmd>  run a command inside it
    vexis-sandbox cp    <task-id> <s> <d>   copy files host<->container
    vexis-sandbox stop  <task-id>           stop and remove it
    vexis-sandbox list                      list all sandboxes

`<task-id>` is a short kebab-case name (3-30 chars, starts with a
letter). State persists across `exec` calls under the same task-id;
different task-ids are isolated. Each subcommand prints one JSON
line; `exec` lazy-starts the container if it isn't running.

### vexis-display — a headless display in a sandbox

    vexis-display start <task-id>   start a headless X display
    vexis-display env   <task-id>   print DISPLAY= for GUI commands
    vexis-display stop  <task-id>   stop the display
    vexis-display list              list recorded displays

`vexis-display start` brings up an `Xvfb` virtual screen *inside* the
sandbox — the host's display is never touched. On apt-based images it
auto-installs what the capture path needs (`xvfb`, `scrot`,
`python3`) and verifies the display came up before returning; if it
can't, it fails with a clear error rather than faking success. The
display dies with the sandbox.

### Recipe: see a GUI app or browser on a headless host

No manual setup — this works on a server with no physical display, or
when the host screen is locked:

1. `vexis-sandbox start <task-id>`
2. `vexis-display start <task-id>`
3. Run the app on that display, e.g.
   `vexis-sandbox exec <task-id> -- env DISPLAY=:99 <gui-command>`
   (`:99` is the default; confirm with `vexis-display env`). Any X11
   app works — a browser, an editor, a GUI tool.
4. `vexis-desktop --source sandbox:<task-id>` — screenshot it. The PNG
   comes back exactly like a host capture; include `image_path` in
   your reply to send it to the user.

For the user, `/screenshot sandbox <id>` does step 4 from Telegram."""


def sandboxes_block() -> str:
    """Per-task containers (`vexis-sandbox`) + headless displays (`vexis-display`)."""
    return _SANDBOXES_BLOCK


register_capability_block('sandboxes', order=3, provider=sandboxes_block)

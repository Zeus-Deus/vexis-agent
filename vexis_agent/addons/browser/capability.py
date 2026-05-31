"""Web-browsing capability prompt block — owned by the browser add-on.

`vexis-browse` drives a real stealth Camoufox session. This block
documents the browser NEXT TO its integration, so the next engine swap
(Camoufox -> whatever) updates this guidance in the same PR that
changes the engine — exactly the drift modularisation exists to kill.

It used to be a core builtin (``vexis_agent/tools/browser/capability.py``,
order 13). With the browser extracted into this add-on, the block moved
here and is registered via ``ctx.register_capability_block`` from
``register(ctx)`` — so the "Web browsing" section appears in the
assembled system prompt ONLY when the browser add-on is loaded, instead
of leaking into core for every install. Order 13 is preserved so it
sorts into its historical position.
"""

from __future__ import annotations

from vexis_agent.core.addons.context import PluginContext


_WEB_BROWSING_BLOCK = r"""## Web browsing — `vexis-browse`

You drive a real browser via `vexis-browse`. Each subcommand returns
one JSON line. The engine is a stealth Camoufox (a hardened Firefox):
it's built to walk through the bot-detection, fingerprinting, and
Cloudflare walls a vanilla browser bounces off, and it solves
Cloudflare challenges automatically on `navigate`. There is no second
"stealth mode" to switch into — this *is* the browser.

Still pick the right tool for the job: a documented API, an MCP server,
or a CLI (`gh`, `curl` + `jq`, ...) is faster and more robust than
scraping a page, so prefer those for plain-text/JSON endpoints. But
when the target is a web-only product, login state forces a real
session, or the user asked you to go to a site and do something —
reach for the browser without hesitation. It's first-class.

### The session

Vexis owns a single Camoufox session per daemon process. It's launched
lazily on the first `navigate`, kept alive across your turns, and
recycled after 2 minutes of inactivity. Login state, cookies, and
local storage all live in `~/.vexis/browser-profiles/default/` and
**survive daemon restarts** — once a site is logged in, you stay
logged in for future sessions.

The session is **headless by default**, so there's no window for the
user to look at. When a site needs a login you don't already have:

- If the credentials are in a vault you can reach (e.g. Bitwarden via
  its CLI), fill the form yourself.
- Otherwise `screenshot` the login page, send the PNG to the user via
  Telegram, and ask for exactly what you need (a code, a password).
  Never tell the user to "unlock the laptop" — they may be nowhere
  near it, and a headless browser doesn't need their screen.

### On a locked or headless host — this just works

Headless Camoufox renders to an off-screen surface, so `navigate`,
`click`, `type`, `snapshot`, and `screenshot` all work identically
whether the host is unlocked, locked, or a lid-closed server with no
display at all. **Never ask the user to unlock the laptop so you can
"see the screen" — you don't need their screen.** `vexis-browse
screenshot` captures the page straight from the renderer; send that
PNG to Telegram.

The one thing headless can't do is let a human click *inside* the
rendered page (an image captcha, a shape-select challenge). If you
hit that and they're at the machine, they can set
`[browser].headless: false` in `~/.vexis/config.yaml` and restart for
a visible window; otherwise run a browser inside a sandbox display
(see "Sandboxes and headless displays") and stream it with
`vexis-stream` so they can watch and act.

### Subcommands

    vexis-browse navigate https://example.com

Navigates and returns `{ok, url, title, snapshot, element_count}`. The
inline `snapshot` is the same DSL `snapshot` returns — there's usually
no need to call `snapshot` immediately after `navigate`.

    vexis-browse snapshot

Returns `{ok, snapshot, url, title, element_count}`. The DSL is one
line per interactive element, `[index]<tag attr="val">text</tag>`:

    [33]<input type="text" placeholder="Enter name" />
    [38]<button aria-label="Submit form">Submit</button>
    [39]<a href="/help">Help</a>

The integer `index` is the identifier you pass to `click` and `type`.
Each snapshot re-numbers the page from scratch, so always act on the
indices from your most recent snapshot.

    vexis-browse click 38
    vexis-browse type 33 "user@example.com"
    vexis-browse type 33 "extra" --no-clear
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
report to the user."""


def web_browsing_block() -> str:
    """Driving a real stealth browser (`vexis-browse`, Camoufox)."""
    return _WEB_BROWSING_BLOCK


def register_capability(ctx: PluginContext) -> None:
    """Register the web-browsing capability block on ``ctx``.

    Slots into the shared "Capabilities" order space at 13 — same
    position the block held when it was a core builtin
    (``vexis_agent/tools/browser/capability.py``). Duplicate
    name/order against core or another add-on raises
    ``AddonConflictError``.
    """
    ctx.register_capability_block(
        "web-browsing",
        web_browsing_block,
        order=13,
    )

"""Web-browsing capability prompt block — owned by the browser add-on.

The browser ships as the `vexis-browser` MCP server driving a real
stealth Camoufox session. This block documents it NEXT TO its
integration, so the next engine swap updates this guidance in the same
PR that changes the engine — exactly the drift modularisation exists to
kill.

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


_WEB_BROWSING_BLOCK = r"""## Web browsing — `vexis-browser` MCP tools

You drive a real browser through the **`vexis-browser` MCP server**.
Your tool list carries its tools — `browser_navigate`,
`browser_snapshot`, `browser_click`, `browser_read`, `browser_type`,
`browser_press`, `browser_back`, `browser_scroll`, `browser_screenshot`
(your agent CLI may show them namespaced, e.g.
`mcp__vexis-browser__browser_navigate`). Call them like any other tool;
each returns a JSON object. The engine is a stealth Camoufox (a
hardened Firefox): it's built to walk through the bot-detection,
fingerprinting, and Cloudflare walls a vanilla browser bounces off, and
it solves Cloudflare challenges automatically on navigate. There is no
second "stealth mode" to switch into — this *is* the browser.

There's also an equivalent `vexis-browse` shell CLI (same verbs:
`vexis-browse navigate <url>`, `vexis-browse snapshot`, ...) for use
from Bash or scripts. Prefer the MCP tools in normal operation; both
drive the exact same session.

Still pick the right tool for the job: a documented API, another MCP
server, or a CLI (`gh`, `curl` + `jq`, ...) is faster and more robust
than scraping a page, so prefer those for plain-text/JSON endpoints.
But when the target is a web-only product, login state forces a real
session, or the user asked you to go to a site and do something —
reach for the browser without hesitation. It's first-class.

### The session

Vexis owns a single Camoufox session per daemon process, shared by
every `vexis-browser` call. It's launched lazily on the first navigate,
kept alive across your turns, and recycled after 2 minutes of
inactivity. Login state, cookies, and local storage all live in
`~/.vexis/browser-profiles/default/` and **survive daemon restarts** —
once a site is logged in, you stay logged in for future sessions. If a
lot of time passed since your last navigate the page may have been
recycled; just navigate again (you're still logged in).

The session is **headless by default**, so there's no window for the
user to look at. When a site needs a login you don't already have:

- If the credentials are in a vault you can reach (e.g. Bitwarden via
  its CLI), fill the form yourself.
- Otherwise `browser_screenshot` the login page, send the PNG to the
  user via Telegram, and ask for exactly what you need (a code, a
  password). Never tell the user to "unlock the laptop" — they may be
  nowhere near it, and a headless browser doesn't need their screen.

### On a locked or headless host — this just works

Headless Camoufox renders to an off-screen surface, so navigate, click,
type, snapshot, and screenshot all work identically whether the host is
unlocked, locked, or a lid-closed server with no display at all.
**Never ask the user to unlock the laptop so you can "see the screen" —
you don't need their screen.** `browser_screenshot` captures the page
straight from the renderer; send that PNG to Telegram.

The one thing headless can't do is let a human click *inside* the
rendered page (an image captcha, a shape-select challenge). If you
hit that and they're at the machine, they can set
`addons.browser.headless: false` in `~/.vexis/config.yaml` (the legacy
`[browser].headless` still works) and restart for a visible window;
otherwise run a browser inside a sandbox display (see "Sandboxes and
headless displays") and stream it with `vexis-stream` so they can watch
and act.

### The tools

`browser_navigate(url)` — navigates and returns
`{ok, url, title, snapshot, element_count}`. The inline `snapshot` is
the same DSL `browser_snapshot` returns, so there's usually no need to
snapshot right after navigating.

`browser_snapshot()` — returns `{ok, snapshot, url, title,
element_count}`. The DSL is one line per interactive element,
`[index]<tag attr="val">text</tag>`:

    [33]<input type="text" placeholder="Enter name" />
    [38]<button aria-label="Submit form">Submit</button>
    [39]<a href="/help">Help</a>

The integer `index` is the identifier you pass to `browser_click` and
`browser_type`. Each snapshot re-numbers the page from scratch, so
always act on the indices from your most recent snapshot.

`browser_click(index, js=false)` — click an element. Set `js=true` to
fire the element's own `click()` from JavaScript when a normal click
hangs on a full-screen cookie/consent overlay.

`browser_read(selector=null)` — return the rendered text of the page
(or a CSS selector); fast, lossless escape hatch for div/table-heavy
pages the snapshot DSL leaves nearly empty.

`browser_type(index, text, clear=true)` — type into a field; clears it
first by default, pass `clear=false` to append.

`browser_press(key)` — a key chord using browser-style names (`Enter`,
`Tab`, `Escape`, `Control+L`, `Shift+Tab`).

`browser_back()` — navigate back in history.

`browser_scroll(direction, pages=1.0)` — `direction` is `"up"` or
`"down"`; `pages=0.5` is half a page, `pages=10` jumps to top/bottom.

`browser_screenshot(full_page=false, include_base64=false)` — saves a
PNG to `~/vexis-workspace/browser/screenshots/` and returns
`{ok, path, size_bytes, mime_type}`. **Just include the path verbatim
in your reply** — the Telegram transport detects
`<workspace>/browser/screenshots/<ts>.png` and sends the file as a
photo before the text body, then strips the path from the prose. The
file stays on disk afterward so you (or the user) can re-reference it;
use your file-reading tool on the path to look at the image yourself.
`full_page=true` captures the entire scrollable page; `include_base64`
is off by default because the brain's stream buffer can't carry
multi-megabyte lines and the path is the canonical image-handoff.

### Stale-index hint

When the page changes mid-action (a click triggers a re-render), the
old `index` may not exist anymore. You'll get:

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
    """Driving a real stealth browser (the `vexis-browser` MCP server, Camoufox)."""
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

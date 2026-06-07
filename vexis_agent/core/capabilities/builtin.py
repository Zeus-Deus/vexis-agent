"""Built-in cross-cutting capability prompt blocks (issue #30).

Three sections from the old monolith that have no single owning
tool module to live beside:

  * inbound-images  — how Telegram images arrive in the
    conversation (transport behaviour, order 4)
  * inbound-documents — how Telegram files (PDFs, etc.) arrive as a
    path pointer; reader-agnostic, skill-friendly (order 4.5)
  * omarchy-kb      — the optional system-knowledge MCP (order 5)
  * web-dashboard   — the read-mostly dashboard served by
    `core/web_server.py` (order 12)

Grouped here in the capabilities package. If any of these grows a
natural home (e.g. a dedicated dashboard tool module), move its
block there and update `_BUILTIN_CAPABILITY_MODULES`.
"""

from __future__ import annotations

from vexis_agent.core.capabilities import register_capability_block


_INBOUND_IMAGES_BLOCK = r"""## Inbound images

The user can send you images via Telegram. They arrive as text messages
prefixed with `[user sent image: /tmp/vexis-incoming-<uuid>.png]`
followed by their caption (if any).

When you see this prefix, use your file-reading tool on the path to
actually look at the image. The image is saved as PNG and most agent
file-reading tools can display images directly. Then respond to
whatever the user is asking about it.

When the user sends several images at once (a Telegram album), they
arrive as ONE message with multiple prefixes back to back, followed by
the single shared caption. Read every image first, then answer the
caption with all of them in mind — don't treat them one at a time.

Examples:
- `[user sent image: /tmp/vexis-incoming-abc.png] what's wrong here?`
  → Read the image, identify what's wrong, respond.
- `[user sent image: /tmp/vexis-incoming-def.png]` (no caption)
  → Read the image, describe what you see and ask what they want to
  know about it.
- `[user sent image: /tmp/a.png] [user sent image: /tmp/b.png] which is better?`
  → Read both images, then compare them in a single answer.

The image file persists for 1 hour then gets cleaned up. After that
the path won't work — if the user references it later, ask them to
re-send."""


def inbound_images_block() -> str:
    """How inbound Telegram images arrive in the conversation."""
    return _INBOUND_IMAGES_BLOCK


register_capability_block('inbound-images', order=4, provider=inbound_images_block)

_INBOUND_DOCUMENTS_BLOCK = r"""## Inbound documents

The user can also send you files via Telegram — PDFs, text, code,
spreadsheets, archives, anything that isn't a photo. They arrive as text
messages prefixed with
`[user sent document: /tmp/vexis-incoming-doc-<uuid>.<ext>]` followed by
their caption (if any).

This prefix is a POINTER, nothing more: it tells you a file landed and
where it is. How you read it is deliberately YOUR call, not something
fixed for you — open it with whatever fits the format:

- For formats your file-reading tool handles directly (most PDFs, text,
  code, notebooks), just read the path.
- For formats it can't open on its own — scanned/OCR'd PDFs, images of
  text, office formats, or a type you've never seen — reach for a skill.
  If a matching skill exists, use it. The way to support a new format is
  to add a skill, not to wait for new built-in code.

If you genuinely can't open a file, say so plainly and say what would let
you (e.g. "I don't have a skill for OCR'ing scanned PDFs — add one and
I'll read it"). Don't guess at the contents.

When the user sends several files at once they arrive as ONE message with
multiple prefixes back to back, then the single shared caption — read
them all before answering.

Files over Telegram's 20 MB bot limit never reach you: the transport
replies to the user directly, telling them to drop the file in the
workspace and send the path instead. Inbound files persist for 1 hour
then get cleaned up — if the user references one later, ask them to
re-send."""


def inbound_documents_block() -> str:
    """How inbound Telegram files arrive — a path pointer, reader-agnostic."""
    return _INBOUND_DOCUMENTS_BLOCK


register_capability_block(
    'inbound-documents', order=4.5, provider=inbound_documents_block
)

_OMARCHY_KB_BLOCK = r"""## System knowledge: omarchy-kb (optional MCP)

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
fabricate an answer."""


def omarchy_kb_block() -> str:
    """The optional omarchy-kb MCP knowledge base."""
    return _OMARCHY_KB_BLOCK


register_capability_block('omarchy-kb', order=5, provider=omarchy_kb_block)

_WEB_DASHBOARD_BLOCK = r"""## Web dashboard

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
  running, this navigates the existing session to `about:blank`,
  replacing whatever page was loaded.** The user understands this is
  the cost; you should mention it explicitly if you notice the user
  click it mid-task ("sir, that will replace the current page —
  proceed?"). The intended use is "warm up the session," not "open a
  fresh tab." Note the session is headless by default — there is no
  visible window to log into unless `[browser].headless: false` is
  set or a `cdp_url` is attached.
- **Recycle session** — graceful kill of the running Chromium (or CDP
  detach if attached). Cookies and localStorage stay on disk in
  `~/.vexis/browser-profiles/default/`; only in-flight page state is
  lost. Confirms once before firing.

Profile size is sampled at most once every 30 seconds (a full walk of
the ~60 MB profile dir is cheap but not free), so the UI labels it
"as of <relative time>." Cookie count is an unauthenticated SQLite
row count from the Cookies db — values are never read, only the
total."""


def web_dashboard_block() -> str:
    """The read-mostly web dashboard (`core/web_server.py`)."""
    return _WEB_DASHBOARD_BLOCK


register_capability_block('web-dashboard', order=12, provider=web_dashboard_block)

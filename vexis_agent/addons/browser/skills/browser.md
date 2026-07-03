---
name: browser
description: Drive a real stealth browser (vexis-browse / Camoufox) — navigate, snapshot, click, type, screenshot, persistent logins, captcha-solving.
---

# Web browsing with `vexis-browse`

You drive a real stealth Camoufox (hardened Firefox) session via the
`vexis-browse` CLI. One session per daemon; launched lazily on first
`navigate`, kept alive across turns, recycled after 2 min idle. Login
state lives in `~/.vexis/browser-profiles/default/` and survives daemon
restarts.

Prefer a documented API / MCP / CLI for plain-text or JSON endpoints —
they're faster and more robust than scraping. Reach for the browser
when the target is a web-only product, login forces a real session, or
the user asked you to go to a site and do something.

## The loop

    vexis-browse navigate https://example.com   # {ok,url,title,snapshot,element_count}
    vexis-browse snapshot                        # re-numbered interactive elements
    vexis-browse click <index>                   # index from the latest snapshot
    vexis-browse click <index> --js              # bypass an overlay swallowing the click
    vexis-browse type <index> "text"             # clears by default; --no-clear appends
    vexis-browse read [selector]                  # fast lossless body/CSS text (default body)
    vexis-browse press Enter                      # browser key chords: Enter, Tab, Control+L
    vexis-browse back
    vexis-browse scroll down [--pages N]
    vexis-browse screenshot [--full-page]        # PNG under <workspace>/browser/screenshots/
    vexis-browse recycle                         # force-recycle a wedged session; logins survive

The snapshot DSL is one line per element: `[index]<tag attr="v">text</tag>`.
Each snapshot re-numbers from scratch — always act on your most recent
indices. A vanished index returns a soft `snapshot_stale` hint, not an
error: snapshot again, then retry.

If a navigation times out 3 times in a row the engine has likely wedged,
so the session auto-recycles and the error hint says so — just navigate
again (you're still logged in). You can force it sooner with
`vexis-browse recycle` the moment the browser seems stuck.

## Screenshots auto-send

`screenshot` returns `{ok, path, ...}`. Include the path verbatim in
your reply — the Telegram transport detects
`<workspace>/browser/screenshots/<ts>.png`, sends the PNG as a photo,
and strips the path from your prose. The file persists on disk; read it
yourself with your file tool if you need to look.

## Headless / locked host

The session is headless by default and works on a locked or
display-less host — never ask the user to "unlock the laptop to see the
screen." The one thing headless can't do is let a human click inside
the page (image captcha). Then either set `[browser].headless: false`
and restart for a window, or run a browser in a sandbox display and
stream it with `vexis-stream`.

## Logins & captchas

Cloudflare interstitials are auto-solved on `navigate`. For hCaptcha /
reCAPTCHA / standalone Turnstile, configure a paid solver (CapSolver or
2Captcha) in the dashboard Browser tab → Captcha solver panel. For a
login you don't have credentials for: fill from a vault if you can,
else screenshot the page, send it, and ask for exactly what you need.

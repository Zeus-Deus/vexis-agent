# Browser: batched navigate+read, parallel tabs, cheaper nav wait

Three independent levers on the Camoufox browser add-on that cut the
round-trips a multi-page browsing task pays. All three are additive and
default-off in behaviour: an existing single-page flow is byte-for-byte
unchanged unless it opts into a new argument.

Implements issue #57.

## TL;DR

- **Batched read** — `then_read` on `browser_navigate` and `browser_click`
  runs a page read in the SAME lock hold as the action, so navigate→read
  (or click→read) is one round-trip. A failed bonus read never fails the
  action.
- **Parallel tabs** — a `tab` name on any page-taking op targets a named
  page on the one shared session. Ops on different tabs run concurrently;
  the agent fans out over K pages instead of navigating them serially.
- **Cheaper nav wait** — `wait_until` on `browser_navigate` lets a
  data/catalog page skip the bounded load+networkidle settle. Default
  (`settle`) is today's path exactly.

## Lever 1 — `then_read`

`browser_navigate(url, then_read=…)` and `browser_click(index, then_read=…)`
take a CSS selector (`"body"` = the whole body, same semantics as
`browser_read`). The read runs inside the same `action`/tab lock hold as
the navigate/click — one socket round-trip, one lock acquisition.

On success the parent result gains:

```json
"read": {"ok": true, "text": "…", "selector": "body", "chars": 1234}
```

If the parent action succeeded but the read part failed (bad selector,
detached node), the parent stays `ok: true` and carries
`"read": {"ok": false, "error": "…"}` — a failed bonus read must never
fail a navigation that loaded. When the parent action itself fails there
is no `read` key at all.

For `click`, a bounded best-effort `wait_for_load_state("domcontentloaded")`
runs before the read (short navigation budget, timeout swallowed) so a
nav-triggering click reads the new document, while a same-page click still
reads immediately.

The extraction is single-sourced in `BrowserTools._read_text`, shared by
`read`, the navigate batch, and the click batch, so the semantics can't
drift.

CLI: `--then-read [SELECTOR]` (`nargs="?"`, defaults the value to `body`
when the flag is bare, absent when the flag is omitted):

```
vexis-browse navigate https://site/list --then-read
vexis-browse navigate https://site/list --then-read "#results"
vexis-browse click 12 --then-read
```

## Lever 2 — named parallel tabs

`SessionManager` grows a named-tab registry on top of the one Camoufox
session: `_tabs: dict[str, Page]` + `_tab_locks: dict[str, asyncio.Lock]`.
The persistent MAIN page is unchanged — it's the unnamed tab, driven under
`action_lock` exactly as before.

- `tab` absent → the main page.
- `tab="a"` → the named tab `a`. `browser_navigate(tab="a")` creates it on
  first use (`session.context.new_page()`, sharing cookies/login); every
  other op requires it to already exist and errors otherwise:

  ```json
  {"ok": false, "error": "no tab named 'a'",
   "hint": "open tabs: […] — open one by navigating with a new tab name"}
  ```

Each tab carries its own lock, so ops on **different** tabs run
concurrently (the control socket already services connections
concurrently — the per-tab lock is what unlocks real parallelism), while
two ops on the **same** tab serialize. The `current_url`/`current_title`
the dashboard shows track the MAIN page only; named-tab activity never
clobbers them, though a named-tab navigation is still recorded in the
recent-navigations history with an extra `"tab"` key.

Fan-out pattern (MCP): fire several `browser_navigate` calls with distinct
`tab` names in ONE batch of parallel tool calls, then `browser_read(tab=…)`
each. CLI:

```
vexis-browse open https://site/a --tab a --wait-until domcontentloaded
vexis-browse open https://site/b --tab b --wait-until domcontentloaded
vexis-browse read --tab a
vexis-browse read --tab b
vexis-browse tabs                # {ok, tabs: [{name, url}]}
vexis-browse tab-close a         # {ok, closed: "a"}
```

`open` is the CLI's tab-navigation verb (it sends `browser_navigate` with
`tab`); `--tab` is required on it. Every other verb — read / snapshot /
click / type / press / back / scroll / screenshot — takes an optional
`--tab NAME`.

### Tab names, cap, and lifecycle

- **Name validation**: `^[A-Za-z0-9_-]{1,64}$`. A bad name is a
  `BadRequest` (parity with a bad element index).
- **Cap**: `max_tabs` (default 8), read per tab-open so an edit hot-reloads
  like every other browser knob. Over the cap returns an error payload with
  a close-a-tab hint. Config: `addons.browser.max_tabs` (legacy
  `[browser].max_tabs`); reader `yaml_config.browser_max_tabs()` →
  `profile.max_tabs()`.
- **Failed open leaves nothing behind**: if the navigation that opens a tab
  fails (bad URL, nav timeout), the just-created page is discarded — it never
  shows up in `browser_tabs` and never consumes a `max_tabs` slot, as if the
  open never happened. Re-navigating an EXISTING tab that fails keeps the tab
  (the caller had it before and may retry).
- **Lifecycle**: `stop()`, `recycle()`, and the idle sweep ALL clear the
  registry and locks. The pages die with the Camoufox context, so the
  teardown just drops the refs (a graceful `stop()` also tries to close each
  named page politely first). After a recycle the old tab names error
  "no tab named" and the agent re-opens — correct behaviour.
- **Wedge streak (#55)**: named-tab navigations feed the SAME
  consecutive-timeout streak as the main page, so tabs can't mask a wedged
  engine. `record_navigation_timeout` is called while the caller holds a
  page lock (`action_lock` **or** a tab lock); the recycle it may trigger
  takes `_start_lock` — safe because nothing holds `_start_lock` while
  waiting on any page lock.

## Lever 3 — `wait_until`

`browser_navigate(url, wait_until=…)`, one of
`"domcontentloaded" | "load" | "settle"`. Absent or `"settle"` is today's
behaviour byte-for-byte: `goto(wait_until="domcontentloaded")` + the
bounded `wait_stable` settle + the Cloudflare gate + the captcha layer.

- `"domcontentloaded"` — `goto(url, wait_until="domcontentloaded")`, then
  SKIP `wait_stable`. The Cloudflare gate still runs (it's wait-free on
  unchallenged pages since #45) and so does the captcha layer.
- `"load"` — `goto(url, wait_until="load")`, skip `wait_stable`.
- Unknown value → an error payload daemon-side; the CLI constrains it with
  argparse `choices`, and the MCP tool validates it too (returns an error
  payload, never raises).

The default stays `settle` — the fast modes are for catalog/data pages you
navigate only to read. CLI: `--wait-until {domcontentloaded,load,settle}`.

## Surfaces

| Surface | Where |
|---|---|
| Engine (session/registry) | `vexis_agent/tools/browser/session.py` |
| Ops + batch/tab plumbing | `vexis_agent/tools/browser/tools.py` |
| Tab error types + payloads | `vexis_agent/tools/browser/errors.py` |
| `max_tabs` knob | `core/yaml_config.py`, `tools/browser/profile.py`, `addons/browser/addon.yaml` |
| Control-socket dispatch | `vexis_agent/addons/browser/dispatch.py` |
| CLI (`vexis-browse`) | `vexis_agent/tools/browser_cli.py` |
| MCP (`vexis-browser`) | `vexis_agent/tools/browser/mcp_server.py` |
| Prompt block / skill | `addons/browser/capability.py`, `addons/browser/skills/browser.md` |

The two new ops bring the browser to **twelve** control-socket ops
(`browser_tabs`, `browser_tab_close` join the ten). See
`docs/browser-captcha.md` for the captcha layer and the wedge-recycle
behaviour these levers interact with.

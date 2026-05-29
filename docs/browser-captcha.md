# Browser captcha solver

Pluggable third-party captcha solving for the Camoufox browser. scrapling's
stealth engine already walks **Cloudflare interstitials** for free
(`solve_cloudflare`, on by default); this layer adds the captcha families it
doesn't cover — **hCaptcha, reCAPTCHA v2/v3, and standalone Turnstile widgets**
— via a paid provider the user supplies a key for.

Implements issue #25.

## TL;DR

- Off by default (`captcha_solver: none`). Configure a provider + key in the
  dashboard **Browser tab → Captcha solver** panel, or in
  `~/.vexis/config.yaml`.
- When configured, solving fires **automatically** on `navigate` whenever a
  captcha is detected — no separate tool call.
- When a captcha is hit and **no** solver is configured, the navigate result
  carries a `hint` pointing at the dashboard panel. Navigation still returns
  `ok: True` (the page loaded) — captcha state rides in a structured `captcha`
  field, mirroring the `snapshot_stale` soft-hint pattern.

## Config

`~/.vexis/config.yaml`:

```yaml
browser:
  captcha_solver: capsolver        # none | capsolver | twocaptcha
  captcha_solver_api_key: "..."    # plaintext in this gitignored home file
```

The key lives plaintext in the user's home config (not a committed secret,
consistent with the model-UX write path). It is **never** returned to the
dashboard — only a masked form (`•••• 1234`).

Readers: `yaml_config.browser_captcha_solver()` /
`browser_captcha_solver_api_key()` (defaults `none` / `None`; an unrecognized
provider falls back to `none`). Thin pass-throughs in
`tools/browser/profile.py`.

## Providers

Both speak the same JSON envelope (`createTask` → poll `getTaskResult` →
solution token; `getBalance`), so they share `JsonTaskSolver` in
`tools/browser/captcha/base.py`. Each provider file is ~10 lines declaring
only its base URL + task-type names:

| Provider   | Base URL                  | File             |
|------------|---------------------------|------------------|
| CapSolver  | `https://api.capsolver.com` | `capsolver.py` |
| 2Captcha   | `https://api.2captcha.com`  | `twocaptcha.py` (modern JSON API, not legacy `in.php`) |

We use the **ProxyLess** task variants — the Camoufox session already carries
the user's real IP/fingerprint, so a solver-side proxy would mismatch it.

The HTTP layer is an injectable `Transport` (default `aiohttp`); every test
passes a fake transport, so the whole layer is verified with no network and no
paid key.

## Detection trigger

DOM-selector based (deterministic / testable), run after the page is stable and
after scrapling's Cloudflare pass. `detect.detect_captcha(html)` is a pure
function matching the well-known widget markup and pulling `data-sitekey` (or
the reCAPTCHA `api.js?render=<key>` query for v3). A keyless-but-present widget
is still "detected" → surfaced as an unsolvable hint.

**Cloudflare interstitials are deliberately skipped.** The full-page Cloudflare
challenge embeds Turnstile too, but scrapling's native solver already walks it
(it runs first in `navigate`). Re-handling it via a paid provider is wasteful
and wrong — the interstitial uses Cloudflare's own managed/test sitekeys (e.g.
`3x00000000000000000000FF`) that a solver legitimately rejects, which would put
a spurious error on a navigation scrapling already rescued. So `detect_captcha`
returns `None` when it sees interstitial-only markers (`cdn-cgi/challenge-
platform` loader or scrapling's `cType:` marker). A site's *standalone*
`<div class="cf-turnstile">` widget — which loads the same `api.js` but carries
none of those markers — is still handled. (Verified live against nowsecure.nl.)

## Token injection

`inject.injection_js(challenge, token)` writes the token into the right hidden
field (`g-recaptcha-response` / `h-captcha-response` / `cf-turnstile-response`),
creating it if absent, and fires any registered `grecaptcha` callback. The
token is JSON-encoded into the script so quotes can't break the literal.

## Dashboard

Browser tab:

- **Status chip** (header): `captcha: <provider>` when configured,
  `captcha: not configured` otherwise, `captcha: low balance` after a Test that
  returns ≤ 0.
- **Captcha solver panel**: provider dropdown, masked password field
  (placeholder shows the current masked key; leave blank to keep it), **Save**,
  **Test**. Test calls the provider's `getBalance` and renders the balance or
  the provider's verbatim error.

Endpoints (`web_server.py`):

- `GET /api/v1/browser` — `config.captcha_solver`,
  `config.captcha_solver_key_masked`, and a top-level `captcha` status block.
- `POST /api/v1/browser/captcha/config` `{provider, api_key?}` — read-modify-
  write with comment-preserving backup + atomic rewrite. Returns masked only.
- `POST /api/v1/browser/captcha/test` — `{ok, provider, balance, low_balance}`
  or `{ok: False, error}` with the provider's response.

## Result shapes (navigate)

```jsonc
// captcha present, no solver
{ "ok": true, ...snapshot, "captcha": {"kind": "hcaptcha", "configured": false, "solved": false}, "hint": "...dashboard..." }
// solved
{ "ok": true, ...freshSnapshot, "captcha": {"kind": "turnstile", "configured": true, "provider": "capsolver", "solved": true} }
// solver configured but failed
{ "ok": true, ...snapshot, "captcha": {"kind": "recaptcha_v2", "configured": true, "provider": "twocaptcha", "solved": false, "error": "ERROR_ZERO_BALANCE"}, "hint": "...ERROR_ZERO_BALANCE" }
```

## Testing

- `tests/test_captcha.py` — detection, providers (balance/solve/poll/errors via
  fake transport), injection, factory, `apply_captcha` branches, masking.
- `tests/test_browser_captcha_config.py` — yaml_config defaults + writer
  round-trip; the two endpoints (key-masking invariant, balance, errors).
- **Live solving** needs a real provider key + a real captcha page and is not
  in CI. To exercise it manually: set a key in `~/.vexis/config.yaml`, run the
  daemon, navigate to a known hCaptcha/reCAPTCHA demo page, and watch the
  `captcha` field in the navigate result.

## Out of scope

Auto-billing / auto-topup (user maintains balance) and a vision-model fallback
(separate issue).

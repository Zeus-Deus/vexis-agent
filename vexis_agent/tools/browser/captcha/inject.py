"""Inject a solved captcha token back into the live page.

A solver returns an opaque token string; the page expects it in a specific
hidden field and usually wants the widget's success callback fired so its own
form-submit logic proceeds:

- reCAPTCHA v2/v3 -> ``textarea#g-recaptcha-response`` (+ ``grecaptcha`` callbacks)
- hCaptcha        -> ``textarea[name=h-captcha-response]`` (+ ``[name=g-recaptcha-response]``)
- Turnstile       -> ``input[name=cf-turnstile-response]``

``injection_js`` is a pure string builder so the generated JS is asserted in
tests without a browser. ``inject_token`` runs it via ``page.evaluate``.
Best-effort: many sites read the response field on submit and need no callback,
so we set the field unconditionally and fire callbacks only when present.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from vexis_agent.tools.browser.captcha.detect import CaptchaChallenge

log = logging.getLogger(__name__)


def injection_js(challenge: CaptchaChallenge, token: str) -> str:
    """Return JS that writes ``token`` into the right response field(s).

    The token is JSON-encoded into the script so quotes/backslashes can't
    break out of the string literal.
    """
    tok = json.dumps(token)
    if challenge.kind == "hcaptcha":
        names = ["h-captcha-response", "g-recaptcha-response"]
    elif challenge.kind == "turnstile":
        names = ["cf-turnstile-response", "g-recaptcha-response"]
    else:  # recaptcha_v2 / recaptcha_v3
        names = ["g-recaptcha-response"]
    names_js = json.dumps(names)
    # Set every candidate field (creating a hidden one if the page hasn't
    # rendered it yet), then attempt to invoke any registered grecaptcha
    # callback so the host page's flow continues.
    return f"""
(() => {{
  const token = {tok};
  const names = {names_js};
  for (const name of names) {{
    let els = document.getElementsByName(name);
    let el = els && els.length ? els[0] : document.getElementById(name);
    if (!el) {{
      el = document.createElement('textarea');
      el.name = name;
      el.id = name;
      el.style.display = 'none';
      document.body.appendChild(el);
    }}
    el.value = token;
  }}
  try {{
    if (window.___grecaptcha_cfg && window.___grecaptcha_cfg.clients) {{
      const walk = (o) => {{
        if (!o || typeof o !== 'object') return;
        for (const k of Object.keys(o)) {{
          const v = o[k];
          if (v && typeof v === 'object') {{
            if (typeof v.callback === 'function') {{ try {{ v.callback(token); }} catch (e) {{}} }}
            walk(v);
          }}
        }}
      }};
      Object.values(window.___grecaptcha_cfg.clients).forEach(walk);
    }}
  }} catch (e) {{}}
  return true;
}})()
""".strip()


async def inject_token(
    page: Any, challenge: CaptchaChallenge, token: str
) -> None:
    """Run ``injection_js`` against the live page. Best-effort; logs on failure."""
    try:
        await page.evaluate(injection_js(challenge, token))
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("[browser] captcha token injection failed: %s", exc)

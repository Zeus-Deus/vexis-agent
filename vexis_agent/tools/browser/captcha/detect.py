"""Captcha detection over page HTML.

scrapling's Camoufox engine solves Cloudflare Turnstile/Interstitial itself
(``engines/_browsers/_camoufox.py::_cloudflare_solver``) and that pass runs
first in ``BrowserTools.navigate``. What it does NOT touch is hCaptcha,
reCAPTCHA v2/v3, or standalone Turnstile widgets on non-Cloudflare sites —
those are the families a paid solver (CapSolver / 2Captcha) handles.

Detection is intentionally a pure function over the page's HTML string so it
runs in tests without a browser. The async ``detect_captcha_on_page`` wrapper
just feeds ``page.content()`` through it, with a small ``evaluate`` fallback
for sitekeys that live inside a shadow root / iframe attribute that the
serialized HTML still exposes as ``data-sitekey``.

Why DOM-selector detection rather than a load-timeout heuristic (the issue's
open question): it's deterministic and testable. A captcha is present iff its
well-known widget markup is on the page; the sitekey we must hand the solver
lives right there in ``data-sitekey`` (or the reCAPTCHA ``render=`` query).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

# Captcha families we can hand to a third-party solver. "turnstile" here means
# a standalone Cloudflare Turnstile widget embedded by a site (NOT the full
# Cloudflare interstitial — scrapling already walks through that one).
CaptchaKind = str  # "turnstile" | "hcaptcha" | "recaptcha_v2" | "recaptcha_v3"


@dataclass(frozen=True)
class CaptchaChallenge:
    """A detected captcha the solver layer can act on.

    ``sitekey`` may be ``None`` when the widget is present but its key could
    not be extracted from the markup — solvers require it, so callers treat a
    keyless challenge as "detected but unsolvable, surface a hint".
    """

    kind: CaptchaKind
    sitekey: str | None = None
    # reCAPTCHA v3 (and some enterprise flows) need the action name; harmless
    # to carry None for the others.
    action: str | None = None


# ``data-sitekey="..."`` / ``data-sitekey='...'`` — the canonical key carrier
# for all three widget families.
_SITEKEY_RE = re.compile(
    r"""data-sitekey\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE
)
# reCAPTCHA loaded via ``api.js?render=<sitekey>`` is the v3 signature.
_RECAPTCHA_RENDER_RE = re.compile(
    r"""recaptcha/api\.js\?[^'"]*\brender=([^'"&]+)""", re.IGNORECASE
)
_RECAPTCHA_ACTION_RE = re.compile(
    r"""grecaptcha\.execute\([^)]*action\s*:\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)

# Cloudflare's full-page interstitial embeds Turnstile too, but scrapling's
# native solver already walks it (see SessionManager.solve_cloudflare, which
# runs BEFORE this layer in navigate). Re-handling it via a paid provider is
# both wasteful and wrong: the interstitial uses Cloudflare's own managed/test
# sitekeys (e.g. 3x00000000000000000000FF), which a solver legitimately
# rejects — producing a spurious error on a navigation scrapling already
# rescued. So we detect the interstitial and bow out, leaving it to scrapling.
#
# Discriminators present ONLY on the full-page challenge, never on a site's
# standalone <div class="cf-turnstile"> widget:
#   - the challenge-platform loader script
#   - scrapling's own cType marker (matches _base.py::_detect_cloudflare)
_CF_INTERSTITIAL_MARKERS = (
    "cdn-cgi/challenge-platform",
    "ctype: 'non-interactive'",
    "ctype: 'managed'",
    "ctype: 'interactive'",
)


def _is_cloudflare_interstitial(lowered: str) -> bool:
    return any(marker in lowered for marker in _CF_INTERSTITIAL_MARKERS)


def _first_sitekey(html: str) -> str | None:
    m = _SITEKEY_RE.search(html)
    if m:
        return m.group(1).strip() or None
    return None


def detect_captcha(html: str) -> CaptchaChallenge | None:
    """Return the captcha challenge present in ``html``, or ``None``.

    Pure: same input always yields the same result, so it's unit-testable
    against HTML fixtures. Order matters — hCaptcha and Turnstile are checked
    before reCAPTCHA because a page can ship multiple vendor scripts and we
    prefer the widget that's actually rendered (``*-captcha`` class) over a
    stray loader.
    """
    if not isinstance(html, str) or not html:
        return None
    lowered = html.lower()

    # Cloudflare's own interstitial is scrapling's job, not the paid layer's.
    if _is_cloudflare_interstitial(lowered):
        return None

    # --- hCaptcha ----------------------------------------------------------
    if "h-captcha" in lowered or "hcaptcha.com" in lowered:
        return CaptchaChallenge(kind="hcaptcha", sitekey=_first_sitekey(html))

    # --- Cloudflare Turnstile (standalone widget) --------------------------
    if (
        "cf-turnstile" in lowered
        or "challenges.cloudflare.com/turnstile" in lowered
    ):
        return CaptchaChallenge(kind="turnstile", sitekey=_first_sitekey(html))

    # --- reCAPTCHA ---------------------------------------------------------
    render = _RECAPTCHA_RENDER_RE.search(html)
    if render:
        action_m = _RECAPTCHA_ACTION_RE.search(html)
        return CaptchaChallenge(
            kind="recaptcha_v3",
            sitekey=(render.group(1).strip() or None),
            action=(action_m.group(1).strip() if action_m else None),
        )
    if (
        "g-recaptcha" in lowered
        or "recaptcha/api.js" in lowered
        or "www.google.com/recaptcha" in lowered
    ):
        return CaptchaChallenge(
            kind="recaptcha_v2", sitekey=_first_sitekey(html)
        )

    return None


async def detect_captcha_on_page(page: Any) -> CaptchaChallenge | None:
    """Best-effort detection against a live Playwright page.

    Reads the serialized HTML and runs the pure detector. Never raises — a
    detection failure must not break a navigation that otherwise succeeded.
    """
    try:
        html = await page.content()
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("[browser] captcha detect: page.content() failed: %s", exc)
        return None
    return detect_captcha(html or "")

"""Pluggable captcha-solver layer for the Camoufox browser.

scrapling solves Cloudflare interstitials natively; this package adds paid
third-party solvers (CapSolver, 2Captcha) for the captcha families it can't —
hCaptcha, reCAPTCHA v2/v3, standalone Turnstile. See ``docs/browser-captcha.md``.

Layout:
- ``detect``    — pure HTML detection -> ``CaptchaChallenge``
- ``base``      — ``CaptchaSolver`` ABC + ``JsonTaskSolver`` + injectable transport
- ``capsolver`` / ``twocaptcha`` — provider task-type maps
- ``inject``    — write the solved token back into the page
- ``config``    — ``get_solver`` factory + ``apply_captcha`` navigate hook
"""

from __future__ import annotations

from vexis_agent.tools.browser.captcha.base import (
    CaptchaSolver,
    CaptchaSolverError,
    JsonTaskSolver,
)
from vexis_agent.tools.browser.captcha.config import (
    VALID_PROVIDERS,
    apply_captcha,
    get_solver,
    mask_key,
)
from vexis_agent.tools.browser.captcha.detect import (
    CaptchaChallenge,
    detect_captcha,
    detect_captcha_on_page,
)
from vexis_agent.tools.browser.captcha.inject import inject_token, injection_js

__all__ = [
    "CaptchaSolver",
    "CaptchaSolverError",
    "JsonTaskSolver",
    "CaptchaChallenge",
    "detect_captcha",
    "detect_captcha_on_page",
    "apply_captcha",
    "get_solver",
    "mask_key",
    "VALID_PROVIDERS",
    "inject_token",
    "injection_js",
]

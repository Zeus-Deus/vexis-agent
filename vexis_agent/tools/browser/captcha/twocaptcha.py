"""2Captcha provider — modern JSON API (https://2captcha.com/api-docs).

We use ``https://api.2captcha.com`` (createTask/getTaskResult/getBalance),
NOT the legacy ``in.php``/``res.php`` text API. The JSON API uses the exact
same envelope as CapSolver, so this provider is a thin ``JsonTaskSolver``
subclass — only the base URL and task-type spellings differ (2Captcha uses
``...Proxyless`` with a lowercase ``less`` and ``RecaptchaV2`` rather than
``ReCaptchaV2``).
"""

from __future__ import annotations

from vexis_agent.tools.browser.captcha.base import JsonTaskSolver


class TwoCaptcha(JsonTaskSolver):
    provider_name = "twocaptcha"
    base_url = "https://api.2captcha.com"
    task_types = {
        "turnstile": "TurnstileTaskProxyless",
        "hcaptcha": "HCaptchaTaskProxyless",
        "recaptcha_v2": "RecaptchaV2TaskProxyless",
        "recaptcha_v3": "RecaptchaV3TaskProxyless",
    }

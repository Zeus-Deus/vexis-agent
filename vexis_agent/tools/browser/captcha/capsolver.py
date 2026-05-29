"""CapSolver provider (https://docs.capsolver.com).

JSON API envelope (shared base ``JsonTaskSolver`` drives the calls):
- ``POST /createTask`` ``{clientKey, task}`` -> ``{errorId, taskId}``
- ``POST /getTaskResult`` ``{clientKey, taskId}`` ->
  ``{errorId, status: "processing"|"ready", solution: {...}}``
- ``POST /getBalance`` ``{clientKey}`` -> ``{errorId, balance}``

A non-zero ``errorId`` is an error; ``errorDescription``/``errorCode`` carry
the human detail surfaced verbatim per the issue.
"""

from __future__ import annotations

from vexis_agent.tools.browser.captcha.base import JsonTaskSolver


class CapSolver(JsonTaskSolver):
    provider_name = "capsolver"
    base_url = "https://api.capsolver.com"
    # Detected family -> CapSolver proxy-less task type.
    task_types = {
        "turnstile": "AntiTurnstileTaskProxyLess",
        "hcaptcha": "HCaptchaTaskProxyLess",
        "recaptcha_v2": "ReCaptchaV2TaskProxyLess",
        "recaptcha_v3": "ReCaptchaV3TaskProxyLess",
    }

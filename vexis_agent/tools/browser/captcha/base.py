"""Common solver interface + injectable HTTP transport.

Both shipping providers (CapSolver, 2Captcha) speak the same JSON envelope:
``createTask`` returns a task id, ``getTaskResult`` is polled until the
solution is ready, ``getBalance`` returns the account balance. That shared
shape lives in ``JsonTaskSolver`` so each provider declares only its base URL,
task-type names, and provider id — see ``capsolver.py`` / ``twocaptcha.py``,
which are ~10 lines each.

The HTTP layer is a small injectable ``Transport`` callable rather than a
hard ``aiohttp`` call inside each method. That's deliberate: every test in
``tests/test_captcha.py`` passes a fake transport, so the entire solver layer
— request building, response parsing, polling, error surfacing — is verified
with zero network and no paid API key. ``default_transport`` is the real
``aiohttp`` implementation used in production.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable

from vexis_agent.tools.browser.captcha.detect import CaptchaChallenge

log = logging.getLogger(__name__)

# (method, url, *, json=body) -> (http_status, parsed_json_or_text)
Transport = Callable[..., Awaitable[tuple[int, Any]]]

DEFAULT_POLL_INTERVAL_S = 5.0
DEFAULT_POLL_TIMEOUT_S = 120.0
# Outbound request timeout for a single createTask/getTaskResult/getBalance
# call. Distinct from poll_timeout, which bounds the whole solve loop.
DEFAULT_HTTP_TIMEOUT_S = 30.0


class CaptchaSolverError(Exception):
    """A provider call failed. Carries the provider name + verbatim detail.

    The issue requires surfacing "the provider's response" on failure, so the
    raw provider message rides in ``detail`` and the dashboard / navigate hint
    renders it as-is.
    """

    def __init__(self, provider: str, detail: str) -> None:
        self.provider = provider
        self.detail = detail
        super().__init__(f"{provider}: {detail}")


async def default_transport(
    method: str, url: str, *, json: dict | None = None
) -> tuple[int, Any]:
    """Real HTTP via aiohttp. Returns ``(status, parsed_json_or_text)``.

    Imported lazily so importing the captcha package never forces aiohttp at
    module-import time (mirrors the browser package's lazy-scrapling rule).
    """
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=DEFAULT_HTTP_TIMEOUT_S)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(method, url, json=json) as resp:
            try:
                body: Any = await resp.json(content_type=None)
            except Exception:
                body = await resp.text()
            return resp.status, body


class CaptchaSolver(ABC):
    """Provider-agnostic solver surface the rest of the app depends on."""

    def __init__(
        self,
        api_key: str,
        *,
        transport: Transport = default_transport,
        poll_interval: float = DEFAULT_POLL_INTERVAL_S,
        poll_timeout: float = DEFAULT_POLL_TIMEOUT_S,
    ) -> None:
        self._api_key = api_key
        self._transport = transport
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider id (``"capsolver"`` / ``"twocaptcha"``)."""

    @abstractmethod
    async def get_balance(self) -> float:
        """Account balance for the dashboard "Test" button. Raises
        ``CaptchaSolverError`` on a bad key / network / provider error."""

    @abstractmethod
    async def solve(self, challenge: CaptchaChallenge, page_url: str) -> str:
        """Solve ``challenge`` for ``page_url`` and return the token string."""

    async def _post(self, url: str, body: dict) -> dict:
        """POST JSON, returning the parsed dict. Raises ``CaptchaSolverError``
        on a transport/HTTP error or a non-dict body."""
        try:
            status, parsed = await self._transport("POST", url, json=body)
        except Exception as exc:  # network, DNS, timeout
            raise CaptchaSolverError(self.name, f"request failed: {exc}") from exc
        if not isinstance(parsed, dict):
            raise CaptchaSolverError(
                self.name, f"HTTP {status}: unexpected response {parsed!r}"
            )
        return parsed


class JsonTaskSolver(CaptchaSolver):
    """Shared implementation for the createTask/getTaskResult/getBalance JSON
    API that CapSolver and 2Captcha both expose.

    Subclasses set three class attributes:

    - ``provider_name`` — the id returned by ``name``
    - ``base_url`` — API root (no trailing slash)
    - ``task_types`` — ``{captcha_kind: provider_task_type}``

    Everything else (envelope, polling, token extraction, error mapping) is
    identical between the two vendors, so it lives here once.
    """

    provider_name: str = ""
    base_url: str = ""
    task_types: dict[str, str] = {}

    @property
    def name(self) -> str:
        return self.provider_name

    async def get_balance(self) -> float:
        data = await self._post(
            f"{self.base_url}/getBalance", {"clientKey": self._api_key}
        )
        self._raise_on_error(data)
        balance = data.get("balance")
        try:
            return float(balance)
        except (TypeError, ValueError):
            raise CaptchaSolverError(
                self.name, f"missing balance in response: {data!r}"
            )

    async def solve(self, challenge: CaptchaChallenge, page_url: str) -> str:
        created = await self._post(
            f"{self.base_url}/createTask",
            {"clientKey": self._api_key, "task": self._build_task(challenge, page_url)},
        )
        self._raise_on_error(created)
        task_id = created.get("taskId")
        if not task_id:
            raise CaptchaSolverError(
                self.name, f"createTask returned no taskId: {created!r}"
            )
        return await self._poll_result(task_id)

    # --- internals (shared) ------------------------------------------------

    def _build_task(self, challenge: CaptchaChallenge, page_url: str) -> dict:
        task_type = self.task_types.get(challenge.kind)
        if task_type is None:
            raise CaptchaSolverError(
                self.name, f"unsupported captcha kind {challenge.kind!r}"
            )
        if not challenge.sitekey:
            raise CaptchaSolverError(
                self.name, f"no sitekey for {challenge.kind} challenge"
            )
        # ProxyLess task variants: the Camoufox session already carries the
        # user's real IP/fingerprint, so a solver-side proxy would mismatch it.
        task: dict = {
            "type": task_type,
            "websiteURL": page_url,
            "websiteKey": challenge.sitekey,
        }
        if challenge.kind == "recaptcha_v3":
            task["pageAction"] = challenge.action or "verify"
        return task

    async def _poll_result(self, task_id: str) -> str:
        waited = 0.0
        while True:
            data = await self._post(
                f"{self.base_url}/getTaskResult",
                {"clientKey": self._api_key, "taskId": task_id},
            )
            self._raise_on_error(data)
            if data.get("status") == "ready":
                return self._extract_token(data.get("solution") or {})
            if waited >= self._poll_timeout:
                raise CaptchaSolverError(
                    self.name,
                    f"timed out after {self._poll_timeout:.0f}s waiting for solution",
                )
            await asyncio.sleep(self._poll_interval)
            waited += self._poll_interval

    def _extract_token(self, solution: dict) -> str:
        # reCAPTCHA solutions land under gRecaptchaResponse; Turnstile/hCaptcha
        # under token. Accept either so one extractor serves all families.
        token = solution.get("token") or solution.get("gRecaptchaResponse")
        if not token:
            raise CaptchaSolverError(
                self.name, f"no token in solution: {solution!r}"
            )
        return str(token)

    def _raise_on_error(self, data: dict) -> None:
        if data.get("errorId"):
            detail = (
                data.get("errorDescription")
                or data.get("errorCode")
                or repr(data)
            )
            raise CaptchaSolverError(self.name, str(detail))

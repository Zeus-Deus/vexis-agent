"""Pluggable captcha-solver tests (CapSolver / 2Captcha).

Everything here runs anywhere — no browser, no network, no paid API key. The
solver layer's HTTP is an injectable transport (``base.Transport``), so a
``FakeTransport`` scripts createTask/getTaskResult/getBalance responses and we
assert request-building, response-parsing, polling, and error surfacing. The
detector and injector are pure functions over strings. ``apply_captcha`` is
exercised with a fake page + fake solver across all four navigate branches.

Pin map (update when adding cases):
- detection: hcaptcha / turnstile / recaptcha v2 / recaptcha v3 / none + sitekey
- providers: balance ok+err, solve ready, poll not-ready→ready, errorId,
  missing taskId, missing token, unsupported kind, no sitekey, task-type maps
- inject: field names + token JSON-encoding per family
- factory: none / missing-key / each provider
- apply_captcha: no-challenge / hint / solved / solver-error
- mask_key
"""

from __future__ import annotations

import asyncio

import pytest

from vexis_agent.tools.browser.captcha import (
    CaptchaChallenge,
    CaptchaSolverError,
    apply_captcha,
    detect_captcha,
    get_solver,
    injection_js,
    mask_key,
)
from vexis_agent.tools.browser.captcha import config as config_mod
from vexis_agent.tools.browser.captcha.capsolver import CapSolver
from vexis_agent.tools.browser.captcha.twocaptcha import TwoCaptcha


def run(coro):
    return asyncio.run(coro)


# --- fakes -----------------------------------------------------------------


class FakeTransport:
    """Scripts responses by URL substring. A list value is popped per call so
    a poll can return ``processing`` then ``ready``."""

    def __init__(self, responses: dict):
        self.responses = {k: list(v) if isinstance(v, list) else v
                          for k, v in responses.items()}
        self.calls: list[tuple] = []

    async def __call__(self, method, url, *, json=None):
        self.calls.append((method, url, json))
        for key, val in self.responses.items():
            if key in url:
                if isinstance(val, list):
                    return val.pop(0)
                return val
        raise AssertionError(f"no fake response for {url}")


class FakePage:
    def __init__(self, html: str):
        self._html = html
        self.evaluated: list[str] = []

    async def content(self):
        return self._html

    async def evaluate(self, js):
        self.evaluated.append(js)
        return True


class FakeSolver:
    name = "fakeprov"

    def __init__(self, token=None, error=None):
        self._token = token
        self._error = error

    async def solve(self, challenge, page_url):
        if self._error:
            raise CaptchaSolverError(self.name, self._error)
        return self._token

    async def get_balance(self):
        return 5.0


# --- detection -------------------------------------------------------------


def test_detect_hcaptcha():
    c = detect_captcha('<div class="h-captcha" data-sitekey="hk_123"></div>')
    assert c == CaptchaChallenge(kind="hcaptcha", sitekey="hk_123")


def test_detect_turnstile():
    c = detect_captcha('<div class="cf-turnstile" data-sitekey="0xAAA"></div>')
    assert c.kind == "turnstile"
    assert c.sitekey == "0xAAA"


def test_detect_recaptcha_v2():
    html = (
        '<div class="g-recaptcha" data-sitekey="6Lc_v2_key"></div>'
        '<script src="https://www.google.com/recaptcha/api.js"></script>'
    )
    c = detect_captcha(html)
    assert c.kind == "recaptcha_v2"
    assert c.sitekey == "6Lc_v2_key"


def test_detect_recaptcha_v3_via_render_query_and_action():
    html = (
        '<script src="https://www.google.com/recaptcha/api.js?render=6Lc_v3_key"></script>'
        "<script>grecaptcha.execute('6Lc_v3_key', {action: 'login'})</script>"
    )
    c = detect_captcha(html)
    assert c.kind == "recaptcha_v3"
    assert c.sitekey == "6Lc_v3_key"
    assert c.action == "login"


def test_detect_none():
    assert detect_captcha("<html><body>hi</body></html>") is None
    assert detect_captcha("") is None


def test_detect_skips_cloudflare_interstitial():
    # Cloudflare's full-page challenge embeds Turnstile, but scrapling's native
    # solver owns it — the paid layer must bow out (regression: nowsecure.nl
    # produced a spurious "invalid websiteKey 3x...FF" error before this guard).
    challenge = (
        '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>'
        '<script>window._cf_chl_opt={cType: \'managed\'};</script>'
        '<div class="cf-turnstile" data-sitekey="3x00000000000000000000FF"></div>'
    )
    assert detect_captcha(challenge) is None
    # challenge-platform loader is also a sufficient discriminator on its own
    platform = (
        '<script src="https://challenges.cloudflare.com/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1"></script>'
        '<div class="cf-turnstile" data-sitekey="0xReal"></div>'
    )
    assert detect_captcha(platform) is None


def test_detect_standalone_turnstile_still_detected_with_cf_script():
    # A site's own Turnstile widget loads the same api.js but has NONE of the
    # interstitial markers — it must still be handled by the paid layer.
    standalone = (
        '<script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>'
        '<div class="cf-turnstile" data-sitekey="0x4AAAAAAA_real_site_key"></div>'
    )
    c = detect_captcha(standalone)
    assert c is not None
    assert c.kind == "turnstile"
    assert c.sitekey == "0x4AAAAAAA_real_site_key"


def test_detect_keyless_widget_still_detected():
    # widget present but sitekey unextractable -> detected, sitekey None
    c = detect_captcha('<div class="h-captcha"></div>')
    assert c.kind == "hcaptcha"
    assert c.sitekey is None


# --- mask_key --------------------------------------------------------------


def test_mask_key():
    assert mask_key("supersecretkey1234") == "•••• 1234"
    assert mask_key("abcd") == "••••"
    assert mask_key("") == "not set"
    assert mask_key(None) == "not set"


# --- providers: balance ----------------------------------------------------


def test_capsolver_balance_ok():
    t = FakeTransport({"/getBalance": (200, {"errorId": 0, "balance": 12.5})})
    solver = CapSolver("KEY", transport=t)
    assert run(solver.get_balance()) == 12.5
    # request shape
    method, url, body = t.calls[0]
    assert method == "POST"
    assert url == "https://api.capsolver.com/getBalance"
    assert body == {"clientKey": "KEY"}


def test_capsolver_balance_error_surfaces_provider_detail():
    t = FakeTransport({"/getBalance": (200, {
        "errorId": 1, "errorCode": "ERROR_KEY_DENIED_ACCESS",
        "errorDescription": "Account is blocked",
    })})
    solver = CapSolver("BADKEY", transport=t)
    with pytest.raises(CaptchaSolverError) as ei:
        run(solver.get_balance())
    assert ei.value.provider == "capsolver"
    assert "Account is blocked" in ei.value.detail


def test_balance_network_failure_is_wrapped():
    async def boom(*a, **k):
        raise ConnectionError("dns fail")
    solver = CapSolver("KEY", transport=boom)
    with pytest.raises(CaptchaSolverError) as ei:
        run(solver.get_balance())
    assert "request failed" in ei.value.detail


# --- providers: solve ------------------------------------------------------


def test_capsolver_solve_turnstile_ready_immediately():
    t = FakeTransport({
        "/createTask": (200, {"errorId": 0, "taskId": "T1"}),
        "/getTaskResult": (200, {
            "errorId": 0, "status": "ready", "solution": {"token": "TOK_TS"},
        }),
    })
    solver = CapSolver("KEY", transport=t, poll_interval=0)
    ch = CaptchaChallenge(kind="turnstile", sitekey="0xAAA")
    assert run(solver.solve(ch, "https://site.test/")) == "TOK_TS"
    # createTask body carries the right task type + site fields
    _, _, create_body = t.calls[0]
    assert create_body["task"]["type"] == "AntiTurnstileTaskProxyLess"
    assert create_body["task"]["websiteURL"] == "https://site.test/"
    assert create_body["task"]["websiteKey"] == "0xAAA"


def test_solve_polls_until_ready():
    t = FakeTransport({
        "/createTask": (200, {"errorId": 0, "taskId": "T2"}),
        "/getTaskResult": [
            (200, {"errorId": 0, "status": "processing"}),
            (200, {"errorId": 0, "status": "processing"}),
            (200, {"errorId": 0, "status": "ready",
                   "solution": {"gRecaptchaResponse": "TOK_RC"}}),
        ],
    })
    solver = CapSolver("KEY", transport=t, poll_interval=0)
    ch = CaptchaChallenge(kind="recaptcha_v2", sitekey="6L")
    assert run(solver.solve(ch, "https://x.test/")) == "TOK_RC"
    # createTask + 3 getTaskResult
    assert len(t.calls) == 4


def test_recaptcha_v3_includes_page_action():
    t = FakeTransport({
        "/createTask": (200, {"errorId": 0, "taskId": "T3"}),
        "/getTaskResult": (200, {"errorId": 0, "status": "ready",
                                 "solution": {"gRecaptchaResponse": "TOK"}}),
    })
    solver = CapSolver("KEY", transport=t, poll_interval=0)
    ch = CaptchaChallenge(kind="recaptcha_v3", sitekey="6L", action="checkout")
    run(solver.solve(ch, "https://x.test/"))
    _, _, body = t.calls[0]
    assert body["task"]["pageAction"] == "checkout"


def test_solve_createtask_error():
    t = FakeTransport({"/createTask": (200, {
        "errorId": 1, "errorDescription": "ERROR_ZERO_BALANCE",
    })})
    solver = CapSolver("KEY", transport=t, poll_interval=0)
    with pytest.raises(CaptchaSolverError) as ei:
        run(solver.solve(CaptchaChallenge("turnstile", "k"), "https://x/"))
    assert "ZERO_BALANCE" in ei.value.detail


def test_solve_missing_taskid():
    t = FakeTransport({"/createTask": (200, {"errorId": 0})})
    solver = CapSolver("KEY", transport=t, poll_interval=0)
    with pytest.raises(CaptchaSolverError) as ei:
        run(solver.solve(CaptchaChallenge("turnstile", "k"), "https://x/"))
    assert "no taskId" in ei.value.detail


def test_solve_missing_token_in_solution():
    t = FakeTransport({
        "/createTask": (200, {"errorId": 0, "taskId": "T"}),
        "/getTaskResult": (200, {"errorId": 0, "status": "ready", "solution": {}}),
    })
    solver = CapSolver("KEY", transport=t, poll_interval=0)
    with pytest.raises(CaptchaSolverError) as ei:
        run(solver.solve(CaptchaChallenge("turnstile", "k"), "https://x/"))
    assert "no token" in ei.value.detail


def test_solve_no_sitekey_is_error():
    solver = CapSolver("KEY", transport=FakeTransport({}), poll_interval=0)
    with pytest.raises(CaptchaSolverError) as ei:
        run(solver.solve(CaptchaChallenge("turnstile", None), "https://x/"))
    assert "no sitekey" in ei.value.detail


def test_solve_unsupported_kind_is_error():
    solver = CapSolver("KEY", transport=FakeTransport({}), poll_interval=0)
    with pytest.raises(CaptchaSolverError) as ei:
        run(solver.solve(CaptchaChallenge("funcaptcha", "k"), "https://x/"))
    assert "unsupported" in ei.value.detail


# --- 2captcha: shares the envelope, differs only in URL + task names --------


def test_twocaptcha_task_type_map_and_url():
    t = FakeTransport({
        "/createTask": (200, {"errorId": 0, "taskId": "X"}),
        "/getTaskResult": (200, {"errorId": 0, "status": "ready",
                                 "solution": {"token": "TT"}}),
    })
    solver = TwoCaptcha("KEY", transport=t, poll_interval=0)
    run(solver.solve(CaptchaChallenge("hcaptcha", "hk"), "https://y.test/"))
    _, url, body = t.calls[0]
    assert url == "https://api.2captcha.com/createTask"
    assert body["task"]["type"] == "HCaptchaTaskProxyless"


# --- injection -------------------------------------------------------------


def test_injection_js_recaptcha_field_and_token_encoding():
    js = injection_js(CaptchaChallenge("recaptcha_v2", "k"), 'tok"with')
    assert "g-recaptcha-response" in js
    # token JSON-encoded so the embedded quote can't break the literal
    assert '"tok\\"with"' in js


def test_injection_js_hcaptcha_and_turnstile_fields():
    h = injection_js(CaptchaChallenge("hcaptcha", "k"), "t")
    assert "h-captcha-response" in h
    ts = injection_js(CaptchaChallenge("turnstile", "k"), "t")
    assert "cf-turnstile-response" in ts


# --- get_solver factory ----------------------------------------------------


def test_get_solver_none_when_provider_none(monkeypatch):
    monkeypatch.setattr(config_mod.yaml_config, "browser_captcha_solver", lambda: "none")
    monkeypatch.setattr(config_mod.yaml_config, "browser_captcha_solver_api_key", lambda: "k")
    assert get_solver() is None


def test_get_solver_none_when_missing_key(monkeypatch):
    monkeypatch.setattr(config_mod.yaml_config, "browser_captcha_solver", lambda: "capsolver")
    monkeypatch.setattr(config_mod.yaml_config, "browser_captcha_solver_api_key", lambda: None)
    assert get_solver() is None


def test_get_solver_builds_each_provider(monkeypatch):
    monkeypatch.setattr(config_mod.yaml_config, "browser_captcha_solver_api_key", lambda: "k")
    monkeypatch.setattr(config_mod.yaml_config, "browser_captcha_solver", lambda: "capsolver")
    assert isinstance(get_solver(), CapSolver)
    monkeypatch.setattr(config_mod.yaml_config, "browser_captcha_solver", lambda: "twocaptcha")
    assert isinstance(get_solver(), TwoCaptcha)


# --- apply_captcha navigate hook -------------------------------------------


def test_apply_captcha_no_challenge_leaves_result_untouched():
    page = FakePage("<html>clean</html>")
    result = {"ok": True, "snapshot": "[]"}
    out = run(apply_captcha(page, "https://x/", result, solver_factory=lambda: None))
    assert "captcha" not in out
    assert "hint" not in out


def test_apply_captcha_hint_when_no_solver():
    page = FakePage('<div class="h-captcha" data-sitekey="k"></div>')
    result = {"ok": True, "snapshot": "[]"}
    out = run(apply_captcha(page, "https://x/", result, solver_factory=lambda: None))
    assert out["captcha"] == {"kind": "hcaptcha", "configured": False, "solved": False}
    assert "dashboard" in out["hint"].lower()


def test_apply_captcha_solves_and_injects(monkeypatch):
    async def fake_render(page):
        return {"snapshot": "[refreshed]", "element_count": 1,
                "url": "https://x/", "title": "T"}
    monkeypatch.setattr(config_mod.snapshot_mod, "render", fake_render)

    page = FakePage('<div class="cf-turnstile" data-sitekey="0xAAA"></div>')
    result = {"ok": True, "snapshot": "[stale]"}
    solver = FakeSolver(token="SOLVED_TOKEN")
    out = run(apply_captcha(page, "https://x/", result, solver_factory=lambda: solver))

    assert out["captcha"] == {
        "kind": "turnstile", "configured": True,
        "provider": "fakeprov", "solved": True,
    }
    # fresh snapshot merged in
    assert out["snapshot"] == "[refreshed]"
    # token injected via page.evaluate
    assert any("SOLVED_TOKEN" in js for js in page.evaluated)


def test_apply_captcha_solver_error_surfaces_provider_detail():
    page = FakePage('<div class="h-captcha" data-sitekey="k"></div>')
    result = {"ok": True, "snapshot": "[]"}
    solver = FakeSolver(error="ERROR_ZERO_BALANCE")
    out = run(apply_captcha(page, "https://x/", result, solver_factory=lambda: solver))
    assert out["captcha"]["solved"] is False
    assert out["captcha"]["error"] == "ERROR_ZERO_BALANCE"
    assert "ERROR_ZERO_BALANCE" in out["hint"]
    # navigation itself still succeeded
    assert out["ok"] is True

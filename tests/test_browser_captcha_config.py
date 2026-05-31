"""Captcha config: yaml_config readers + the two dashboard endpoints.

Two layers:
- pure ``yaml_config`` read defaults + writer round-trip (no HTTP)
- ``POST /api/v1/browser/captcha/{config,test}`` via a ``TestClient`` built
  the same way as ``tests/test_models_set_api.py`` — ``WebDashboard.__new__``
  with the minimal attr surface + ``_build_app()``.

The "test" endpoint's solver is monkeypatched so no paid API / network runs.
The key-masking invariant is asserted: the raw key is written to disk but the
endpoint response (and the GET payload) only ever returns the masked form.
"""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from vexis_agent.core import web_server as ws
from vexis_agent.core import yaml_config
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.web_server import DashboardConfig, WebDashboard
from vexis_agent.tools.browser.captcha.base import CaptchaSolverError

_TOKEN = "test-token-captcha-deadbeef"


# --- pure yaml_config layer ------------------------------------------------


def _point_config(monkeypatch, tmp_path: Path) -> Path:
    cfg_dir = tmp_path / "vexis"
    cfg_dir.mkdir(exist_ok=True)
    monkeypatch.setattr("vexis_agent.core.paths.vexis_dir", lambda: cfg_dir)
    monkeypatch.setattr("vexis_agent.core.yaml_config.vexis_dir", lambda: cfg_dir)
    return cfg_dir / "config.yaml"


def test_captcha_read_defaults(monkeypatch, tmp_path):
    _point_config(monkeypatch, tmp_path)
    assert yaml_config.browser_captcha_solver() == "none"
    assert yaml_config.browser_captcha_solver_api_key() is None


def test_captcha_read_values_and_invalid_falls_back(monkeypatch, tmp_path):
    cfg = _point_config(monkeypatch, tmp_path)
    cfg.write_text(
        "browser:\n  captcha_solver: capsolver\n  captcha_solver_api_key: secret_k\n",
        encoding="utf-8",
    )
    assert yaml_config.browser_captcha_solver() == "capsolver"
    assert yaml_config.browser_captcha_solver_api_key() == "secret_k"

    cfg.write_text("browser:\n  captcha_solver: bogus\n", encoding="utf-8")
    assert yaml_config.browser_captcha_solver() == "none"


# --- dashboard harness -----------------------------------------------------


class _FakeSessions:
    def get(self) -> str:
        return "test-sess"


def _build_dashboard(tmp_path: Path) -> WebDashboard:
    d = WebDashboard.__new__(WebDashboard)
    d._workspace = tmp_path
    d._token = _TOKEN
    d._learning = None
    d._relationships_mutation_window_seconds = 600
    d._relationships_mutation_limit = 100
    d._relationships_mutation_log = defaultdict(deque)
    d._config = DashboardConfig(
        host="127.0.0.1", port=0,
        web_dist=tmp_path / "no-frontend", manage_tailscale=False,
    )
    d._sessions = _FakeSessions()
    d._running_tasks = RunningTasks()
    d._background_tasks = None
    d._curator = None
    d._addon_runtime = None
    d._started_at = None
    d._tailscale_url = None
    d._tailscale_dns = None
    d._server = None
    d._serve_task = None
    d._profile_size_cache = None
    d._app = d._build_app()
    return d


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    _point_config(monkeypatch, tmp_path)
    return TestClient(_build_dashboard(tmp_path)._app)


def _hdr() -> dict:
    return {"Authorization": f"Bearer {_TOKEN}"}


# --- config endpoint -------------------------------------------------------


def test_config_requires_token(client):
    r = client.post("/api/v1/browser/captcha/config", json={"provider": "capsolver"})
    assert r.status_code == 401


def test_config_rejects_bad_provider(client):
    r = client.post(
        "/api/v1/browser/captcha/config",
        json={"provider": "nopesolver"}, headers=_hdr(),
    )
    assert r.status_code == 400


def test_config_writes_and_masks_key(client, tmp_path):
    r = client.post(
        "/api/v1/browser/captcha/config",
        json={"provider": "capsolver", "api_key": "supersecret1234"},
        headers=_hdr(),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["provider"] == "capsolver"
    # response carries only the masked form, never the raw key
    assert body["captcha_solver_key_masked"] == "•••• 1234"
    assert "supersecret1234" not in str(body)
    # but the raw key IS persisted to disk — under the canonical add-on
    # location ``addons.browser.*`` (the browser is a bundled add-on).
    cfg = yaml.safe_load((tmp_path / "vexis" / "config.yaml").read_text())
    assert cfg["addons"]["browser"]["captcha_solver_api_key"] == "supersecret1234"
    assert cfg["addons"]["browser"]["captcha_solver"] == "capsolver"


def test_config_blank_key_keeps_existing(client, tmp_path):
    cfg_path = tmp_path / "vexis" / "config.yaml"
    # Seed under the canonical add-on location; omitting api_key on a
    # provider switch must preserve the existing key.
    cfg_path.write_text(
        "addons:\n  browser:\n    captcha_solver: capsolver\n"
        "    captcha_solver_api_key: keepme1234\n",
        encoding="utf-8",
    )
    # switch provider, omit api_key -> key preserved
    r = client.post(
        "/api/v1/browser/captcha/config",
        json={"provider": "twocaptcha"}, headers=_hdr(),
    )
    assert r.status_code == 200
    cfg = yaml.safe_load(cfg_path.read_text())
    assert cfg["addons"]["browser"]["captcha_solver"] == "twocaptcha"
    assert cfg["addons"]["browser"]["captcha_solver_api_key"] == "keepme1234"


def test_config_migrates_legacy_browser_key_forward(client, tmp_path):
    """A legacy ``[browser].captcha_solver_api_key`` is migrated into the
    canonical ``addons.browser`` block on a key-omitting write, so the
    masked round-trip survives the extraction."""
    cfg_path = tmp_path / "vexis" / "config.yaml"
    cfg_path.write_text(
        "browser:\n  captcha_solver: capsolver\n"
        "  captcha_solver_api_key: legacy9999\n",
        encoding="utf-8",
    )
    r = client.post(
        "/api/v1/browser/captcha/config",
        json={"provider": "twocaptcha"}, headers=_hdr(),
    )
    assert r.status_code == 200
    cfg = yaml.safe_load(cfg_path.read_text())
    assert cfg["addons"]["browser"]["captcha_solver"] == "twocaptcha"
    assert cfg["addons"]["browser"]["captcha_solver_api_key"] == "legacy9999"


# --- test endpoint ---------------------------------------------------------


class _FakeSolver:
    name = "capsolver"

    def __init__(self, balance=None, error=None):
        self._balance = balance
        self._error = error

    async def get_balance(self):
        if self._error:
            raise CaptchaSolverError(self.name, self._error)
        return self._balance


def test_test_endpoint_no_solver_configured(client, monkeypatch):
    monkeypatch.setattr(ws, "captcha_get_solver", lambda: None)
    r = client.post("/api/v1/browser/captcha/test", headers=_hdr())
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "configured" in body["error"].lower()


def test_test_endpoint_reports_balance(client, monkeypatch):
    monkeypatch.setattr(ws, "captcha_get_solver", lambda: _FakeSolver(balance=7.5))
    r = client.post("/api/v1/browser/captcha/test", headers=_hdr())
    body = r.json()
    assert body == {
        "ok": True, "provider": "capsolver", "balance": 7.5, "low_balance": False,
    }


def test_test_endpoint_zero_balance_flagged_low(client, monkeypatch):
    monkeypatch.setattr(ws, "captcha_get_solver", lambda: _FakeSolver(balance=0))
    body = client.post("/api/v1/browser/captcha/test", headers=_hdr()).json()
    assert body["ok"] is True
    assert body["low_balance"] is True


def test_test_endpoint_surfaces_provider_error(client, monkeypatch):
    monkeypatch.setattr(
        ws, "captcha_get_solver",
        lambda: _FakeSolver(error="ERROR_KEY_DENIED_ACCESS"),
    )
    body = client.post("/api/v1/browser/captcha/test", headers=_hdr()).json()
    assert body["ok"] is False
    assert body["error"] == "ERROR_KEY_DENIED_ACCESS"
    assert body["provider"] == "capsolver"

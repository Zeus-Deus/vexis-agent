"""Tests for ``GET /api/v1/tailscale/status``.

Verifies the bearer-token gate, the happy-path JSON shape, and the
graceful-degradation contract: when the underlying CLI errors out,
the endpoint still returns 200 with ``error`` populated rather than
5xx-ing the dashboard page.
"""

from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from vexis_agent.core import tailscale
from vexis_agent.core.web_server import DashboardConfig, WebDashboard


_TOKEN = "test-token-cafef00d"


def _build_dashboard(tmp_path: Path) -> WebDashboard:
    """Same construction trick the relationships endpoint tests use:
    bypass the daemon wiring, set just the fields ``_build_app``
    touches, then build the FastAPI app."""
    dashboard = WebDashboard.__new__(WebDashboard)
    dashboard._workspace = tmp_path  # type: ignore[attr-defined]
    dashboard._token = _TOKEN  # type: ignore[attr-defined]
    dashboard._learning = None  # type: ignore[attr-defined]
    dashboard._relationships_mutation_window_seconds = 600  # type: ignore[attr-defined]
    dashboard._relationships_mutation_limit = 100  # type: ignore[attr-defined]
    dashboard._relationships_mutation_log = defaultdict(deque)  # type: ignore[attr-defined]
    dashboard._config = DashboardConfig(  # type: ignore[attr-defined]
        host="127.0.0.1",
        port=0,
        web_dist=tmp_path / "no-frontend",
        manage_tailscale=False,
    )
    dashboard._sessions = None  # type: ignore[attr-defined]
    dashboard._running_tasks = None  # type: ignore[attr-defined]
    dashboard._background_tasks = None  # type: ignore[attr-defined]
    dashboard._curator = None  # type: ignore[attr-defined]
    dashboard._browser = None  # type: ignore[attr-defined]
    dashboard._started_at = None  # type: ignore[attr-defined]
    dashboard._tailscale_url = None  # type: ignore[attr-defined]
    dashboard._tailscale_dns = None  # type: ignore[attr-defined]
    dashboard._server = None  # type: ignore[attr-defined]
    dashboard._serve_task = None  # type: ignore[attr-defined]
    dashboard._profile_size_cache = None  # type: ignore[attr-defined]
    dashboard._app = dashboard._build_app()  # type: ignore[attr-defined]
    return dashboard


@pytest.fixture(autouse=True)
def _drop_cache() -> None:
    tailscale.reset_cache()


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(_build_dashboard(tmp_path)._app)


def _hdr() -> dict:
    return {"Authorization": f"Bearer {_TOKEN}"}


def test_endpoint_rejects_missing_token(client: TestClient):
    resp = client.get("/api/v1/tailscale/status")
    assert resp.status_code == 401


def test_endpoint_rejects_wrong_token(client: TestClient):
    resp = client.get(
        "/api/v1/tailscale/status",
        headers={"Authorization": "Bearer not-the-token"},
    )
    assert resp.status_code == 401


def test_endpoint_happy_path(client: TestClient):
    fake_node = tailscale.NodeStatus(
        node=tailscale.NodeInfo(
            hostname="home-server.example.ts.net",
            ip="100.64.1.1",
            online=True,
        ),
    )
    fake_serve = tailscale.ServeStatus(
        serves=[
            tailscale.Serve(
                port=443, mount="/", target="http://localhost:8766",
                tls=True, funnel=False,
            ),
        ],
    )
    fake_funnel = tailscale.FunnelStatus(
        funnels=[
            tailscale.Funnel(
                port=443, mount="/demo", target="http://127.0.0.1:9000",
                tls=True,
            ),
        ],
    )
    fake_peers = tailscale.PeersStatus(
        peers=[
            tailscale.Peer(
                hostname="phone.example.ts.net",
                ip="100.64.1.2", online=True,
                last_seen=None, os="iOS",
            ),
        ],
    )
    with patch.object(tailscale, "get_node_info", return_value=fake_node), \
         patch.object(tailscale, "get_serve_status", return_value=fake_serve), \
         patch.object(tailscale, "get_funnel_status", return_value=fake_funnel), \
         patch.object(tailscale, "get_peers", return_value=fake_peers):
        resp = client.get("/api/v1/tailscale/status", headers=_hdr())
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    assert body["node"] == {
        "hostname": "home-server.example.ts.net",
        "ip": "100.64.1.1",
        "online": True,
    }
    assert body["serves"] == [
        {
            "port": 443, "mount": "/", "target": "http://localhost:8766",
            "tls": True, "funnel": False,
        },
    ]
    assert body["funnels"] == [
        {"port": 443, "mount": "/demo", "target": "http://127.0.0.1:9000", "tls": True},
    ]
    assert body["peers"] == [
        {
            "hostname": "phone.example.ts.net",
            "ip": "100.64.1.2", "online": True,
            "last_seen": None, "os": "iOS",
        },
    ]


def test_endpoint_returns_200_on_cli_error(client: TestClient):
    """When ``tailscale status`` errors out, the endpoint still
    returns 200 with the partial payload + populated ``error`` so
    the frontend can render the degraded banner instead of 5xx-ing."""
    err_node = tailscale.NodeStatus(error="tailscale CLI not found in PATH")
    err_serve = tailscale.ServeStatus(error="tailscale CLI not found in PATH")
    err_funnel = tailscale.FunnelStatus(error="tailscale CLI not found in PATH")
    err_peers = tailscale.PeersStatus(error="tailscale CLI not found in PATH")
    with patch.object(tailscale, "get_node_info", return_value=err_node), \
         patch.object(tailscale, "get_serve_status", return_value=err_serve), \
         patch.object(tailscale, "get_funnel_status", return_value=err_funnel), \
         patch.object(tailscale, "get_peers", return_value=err_peers):
        resp = client.get("/api/v1/tailscale/status", headers=_hdr())
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] == "tailscale CLI not found in PATH"
    assert body["node"] is None
    assert body["serves"] == []
    assert body["funnels"] == []
    assert body["peers"] == []


def test_endpoint_partial_failure_keeps_succeeding_sections(client: TestClient):
    """If serve fails but status works, peers + node still populate."""
    ok_node = tailscale.NodeStatus(
        node=tailscale.NodeInfo(
            hostname="home-server.example.ts.net",
            ip="100.64.1.1",
            online=True,
        ),
    )
    err_serve = tailscale.ServeStatus(error="tailscale exited 1: backend not running")
    ok_funnel = tailscale.FunnelStatus()
    ok_peers = tailscale.PeersStatus()
    with patch.object(tailscale, "get_node_info", return_value=ok_node), \
         patch.object(tailscale, "get_serve_status", return_value=err_serve), \
         patch.object(tailscale, "get_funnel_status", return_value=ok_funnel), \
         patch.object(tailscale, "get_peers", return_value=ok_peers):
        resp = client.get("/api/v1/tailscale/status", headers=_hdr())
    assert resp.status_code == 200
    body = resp.json()
    # node-level error is None (succeeded), so the surfaced top-level
    # error is the next one in the chain — the serve failure.
    assert body["error"] is not None
    assert "backend not running" in body["error"]
    assert body["node"] is not None
    assert body["node"]["hostname"] == "home-server.example.ts.net"

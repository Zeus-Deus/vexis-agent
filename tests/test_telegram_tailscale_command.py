"""Tests for the /tailscale Telegram command renderer.

The handler itself is a thin wrapper around ``core.tailscale`` plus
the pure renderer ``_format_tailscale_reply``. The renderer is what's
worth covering: happy path, the empty-state branches (no serves /
no funnels / solo tailnet), and the typed-error branches per section.
"""

from __future__ import annotations

from core import tailscale
from transports.telegram import _format_tailscale_reply


_NODE_OK = tailscale.NodeStatus(
    node=tailscale.NodeInfo(
        hostname="home-server.example.ts.net",
        ip="100.64.1.1",
        online=True,
    ),
)


def test_happy_path_renders_all_sections():
    serve = tailscale.ServeStatus(
        serves=[
            tailscale.Serve(
                port=443, mount="/", target="http://localhost:8766",
                tls=True, funnel=False,
            ),
        ],
    )
    funnel = tailscale.FunnelStatus(
        funnels=[
            tailscale.Funnel(
                port=443, mount="/demo", target="http://127.0.0.1:9000",
                tls=True,
            ),
        ],
    )
    peers = tailscale.PeersStatus(
        peers=[
            tailscale.Peer(
                hostname="phone.example.ts.net",
                ip="100.64.1.2",
                online=True,
                last_seen=None,
                os="iOS",
            ),
            tailscale.Peer(
                hostname="peer1.example.ts.net",
                ip="100.64.1.4",
                online=False,
                last_seen="2026-04-27T16:21:17Z",
                os="linux",
            ),
        ],
    )
    out = _format_tailscale_reply(_NODE_OK, serve, funnel, peers)
    assert out.startswith("Tailscale status")
    assert "Node: home-server.example.ts.net (100.64.1.1) — online" in out
    assert "Active serves (1):" in out
    assert ":443 / → http://localhost:8766 (HTTPS)" in out
    assert "Active funnels (1):" in out
    assert ":443 /demo → http://127.0.0.1:9000 (HTTPS)" in out
    # Only online peers show in the listing; the offline one isn't rendered.
    assert "Peers online (1 of 2):" in out
    assert "phone.example.ts.net (100.64.1.2)" in out
    assert "peer1.example.ts.net" not in out


def test_empty_state_renders_none_markers():
    out = _format_tailscale_reply(
        _NODE_OK,
        tailscale.ServeStatus(),
        tailscale.FunnelStatus(),
        tailscale.PeersStatus(),
    )
    assert "Active serves (0):" in out
    assert "Active funnels (0):" in out
    assert "Peers online (0 of 0):" in out
    # The marker word lives on its own indented line, three times.
    assert out.count("\n  none") == 3


def test_node_error_short_circuits_the_whole_reply():
    err_node = tailscale.NodeStatus(error="tailscale CLI not found in PATH")
    out = _format_tailscale_reply(
        err_node,
        tailscale.ServeStatus(),
        tailscale.FunnelStatus(),
        tailscale.PeersStatus(),
    )
    assert out == "Tailscale status unavailable: tailscale CLI not found in PATH"


def test_partial_serve_error_still_renders_other_sections():
    err_serve = tailscale.ServeStatus(
        error="tailscale exited 1: backend not running",
    )
    out = _format_tailscale_reply(
        _NODE_OK,
        err_serve,
        tailscale.FunnelStatus(),
        tailscale.PeersStatus(),
    )
    assert "Active serves: (error: tailscale exited 1: backend not running)" in out
    # Node + funnel + peer sections still render.
    assert "Node: home-server.example.ts.net" in out
    assert "Active funnels (0):" in out
    assert "Peers online (0 of 0):" in out


def test_solo_tailnet_renders_zero_zero():
    out = _format_tailscale_reply(
        _NODE_OK,
        tailscale.ServeStatus(),
        tailscale.FunnelStatus(),
        tailscale.PeersStatus(peers=[]),
    )
    assert "Peers online (0 of 0):" in out

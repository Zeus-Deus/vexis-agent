"""Tests for ``core.tailscale``.

Covers the full surface: subprocess success and every named failure
mode (missing binary, non-zero exit, malformed JSON, timeout), the
JSON shape captured during the audit (serves, funnels with
``AllowFunnel``, peers, node identity), and the 10-second TTL cache.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any
from unittest.mock import patch

import pytest

from vexis_agent.core import tailscale


# ---- realistic fixtures (sliced from real CLI output) ------------------


_SERVE_DOC: dict[str, Any] = {
    "TCP": {"443": {"HTTPS": True}},
    "Web": {
        "home-server.example.ts.net:443": {
            "Handlers": {
                "/": {"Proxy": "http://localhost:8766"},
                "/shot": {"Proxy": "http://127.0.0.1:8767"},
            },
        },
    },
}


_FUNNEL_DOC: dict[str, Any] = {
    "TCP": {"443": {"HTTPS": True}},
    "AllowFunnel": {"home-server.example.ts.net:443": True},
    "Web": {
        "home-server.example.ts.net:443": {
            "Handlers": {
                "/demo": {"Proxy": "http://127.0.0.1:9000"},
            },
        },
    },
}


_STATUS_DOC: dict[str, Any] = {
    "Version": "1.96.4",
    "BackendState": "Running",
    "MagicDNSSuffix": "example.ts.net",
    "Self": {
        "HostName": "home-server",
        "DNSName": "home-server.example.ts.net.",
        "TailscaleIPs": ["100.64.1.1", "fd7a:115c:a1e0::c401:1b41"],
        "Online": True,
        "OS": "linux",
    },
    "Peer": {
        "nodekey:aaaa": {
            "HostName": "phone",
            "DNSName": "phone.example.ts.net.",
            "TailscaleIPs": ["100.64.1.2"],
            "Online": True,
            "OS": "iOS",
            "LastSeen": "0001-01-01T00:00:00Z",  # zero-time => None
        },
        "nodekey:bbbb": {
            "HostName": "peer1",
            "DNSName": "peer1.example.ts.net.",
            "TailscaleIPs": ["100.64.1.4"],
            "Online": False,
            "OS": "linux",
            "LastSeen": "2026-04-27T16:21:17Z",
        },
        "nodekey:cccc": {
            "HostName": "peer2",
            "DNSName": "peer2.example.ts.net.",
            "TailscaleIPs": ["100.64.1.3"],
            "Online": True,
            "OS": "linux",
            "LastSeen": "2026-05-04T18:00:00Z",
        },
    },
}


# ---- helpers -----------------------------------------------------------


@pytest.fixture(autouse=True)
def _drop_cache() -> None:
    """Every test gets a clean module cache so calls don't bleed."""
    tailscale.reset_cache()


def _completed(stdout: bytes, returncode: int = 0, stderr: bytes = b"") -> Any:
    return subprocess.CompletedProcess(
        args=["tailscale"],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _patch_run(mapping: dict[tuple[str, ...], Any]):
    """Build a subprocess.run replacement that dispatches on argv suffix.

    Keys are argv tuples like ``("serve", "status", "--json")``; values
    are the ``CompletedProcess`` to return (or an exception class to
    raise). The first arg is always ``"tailscale"``, so we match on
    everything after."""

    def fake(argv, **_kwargs):
        key = tuple(argv[1:])
        result = mapping.get(key)
        if result is None:
            raise AssertionError(f"unexpected tailscale call: {argv}")
        if isinstance(result, type) and issubclass(result, BaseException):
            raise result("boom")
        if callable(result):
            return result(argv)
        return result

    return fake


# ---- serve -------------------------------------------------------------


def test_serve_status_parses_real_shape():
    fake = _patch_run({
        ("serve", "status", "--json"): _completed(json.dumps(_SERVE_DOC).encode()),
    })
    with patch("vexis_agent.core.tailscale.subprocess.run", side_effect=fake):
        result = tailscale.get_serve_status()
    assert result.error is None
    assert len(result.serves) == 2
    by_mount = {s.mount: s for s in result.serves}
    assert by_mount["/"].port == 443
    assert by_mount["/"].target == "http://localhost:8766"
    assert by_mount["/"].tls is True
    assert by_mount["/"].funnel is False
    assert by_mount["/shot"].target == "http://127.0.0.1:8767"


def test_serve_status_marks_funnel_mounts():
    fake = _patch_run({
        ("serve", "status", "--json"): _completed(json.dumps(_FUNNEL_DOC).encode()),
    })
    with patch("vexis_agent.core.tailscale.subprocess.run", side_effect=fake):
        result = tailscale.get_serve_status()
    assert result.error is None
    assert len(result.serves) == 1
    assert result.serves[0].mount == "/demo"
    assert result.serves[0].funnel is True


def test_serve_status_empty_doc_yields_empty_list():
    fake = _patch_run({
        ("serve", "status", "--json"): _completed(b"{}"),
    })
    with patch("vexis_agent.core.tailscale.subprocess.run", side_effect=fake):
        result = tailscale.get_serve_status()
    assert result.error is None
    assert result.serves == []


def test_serve_status_blank_body_treated_as_empty():
    # The CLI emits an empty body on some "nothing configured" paths.
    fake = _patch_run({("serve", "status", "--json"): _completed(b"")})
    with patch("vexis_agent.core.tailscale.subprocess.run", side_effect=fake):
        result = tailscale.get_serve_status()
    assert result.error is None
    assert result.serves == []


# ---- funnel ------------------------------------------------------------


def test_funnel_status_filters_to_funnel_mounts_only():
    fake = _patch_run({
        ("funnel", "status", "--json"): _completed(json.dumps(_FUNNEL_DOC).encode()),
    })
    with patch("vexis_agent.core.tailscale.subprocess.run", side_effect=fake):
        result = tailscale.get_funnel_status()
    assert result.error is None
    assert len(result.funnels) == 1
    assert result.funnels[0].mount == "/demo"
    assert result.funnels[0].tls is True


def test_funnel_status_skips_non_funnel_mounts():
    # AllowFunnel absent ⇒ no funnel rows even if Web is populated.
    fake = _patch_run({
        ("funnel", "status", "--json"): _completed(json.dumps(_SERVE_DOC).encode()),
    })
    with patch("vexis_agent.core.tailscale.subprocess.run", side_effect=fake):
        result = tailscale.get_funnel_status()
    assert result.error is None
    assert result.funnels == []


# ---- node + peers ------------------------------------------------------


def test_node_info_parses_self_block():
    fake = _patch_run({
        ("status", "--json"): _completed(json.dumps(_STATUS_DOC).encode()),
    })
    with patch("vexis_agent.core.tailscale.subprocess.run", side_effect=fake):
        result = tailscale.get_node_info()
    assert result.error is None
    assert result.node is not None
    assert result.node.hostname == "home-server.example.ts.net"
    assert result.node.ip == "100.64.1.1"
    assert result.node.online is True


def test_peers_parses_all_peers_and_orders_online_first():
    fake = _patch_run({
        ("status", "--json"): _completed(json.dumps(_STATUS_DOC).encode()),
    })
    with patch("vexis_agent.core.tailscale.subprocess.run", side_effect=fake):
        result = tailscale.get_peers()
    assert result.error is None
    hostnames = [p.hostname for p in result.peers]
    # iphone + peer2 online (alphabetical), peer1 offline last.
    assert hostnames == [
        "phone.example.ts.net",
        "peer2.example.ts.net",
        "peer1.example.ts.net",
    ]
    iphone = result.peers[0]
    assert iphone.last_seen is None  # zero-time scrubbed
    assert iphone.online is True
    peer1 = result.peers[2]
    assert peer1.online is False
    assert peer1.last_seen == "2026-04-27T16:21:17Z"


def test_peers_handles_solo_tailnet():
    doc = dict(_STATUS_DOC)
    doc.pop("Peer", None)
    fake = _patch_run({
        ("status", "--json"): _completed(json.dumps(doc).encode()),
    })
    with patch("vexis_agent.core.tailscale.subprocess.run", side_effect=fake):
        result = tailscale.get_peers()
    assert result.error is None
    assert result.peers == []


# ---- error paths -------------------------------------------------------


def test_cli_not_found_returns_typed_error():
    def fake(argv, **_kwargs):
        raise FileNotFoundError("tailscale")

    with patch("vexis_agent.core.tailscale.subprocess.run", side_effect=fake):
        result = tailscale.get_serve_status()
    assert "not found" in (result.error or "")
    assert result.serves == []


def test_cli_non_zero_returns_typed_error():
    fake = _patch_run({
        ("serve", "status", "--json"): _completed(
            b"", returncode=1, stderr=b"backend not running"
        ),
    })
    with patch("vexis_agent.core.tailscale.subprocess.run", side_effect=fake):
        result = tailscale.get_serve_status()
    assert result.error is not None
    assert "exited 1" in result.error
    assert "backend not running" in result.error
    assert result.serves == []


def test_cli_malformed_json_returns_typed_error():
    fake = _patch_run({
        ("serve", "status", "--json"): _completed(b"<html>nope</html>"),
    })
    with patch("vexis_agent.core.tailscale.subprocess.run", side_effect=fake):
        result = tailscale.get_serve_status()
    assert result.error is not None
    assert "invalid JSON" in result.error


def test_cli_timeout_returns_typed_error():
    def fake(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout", 5))

    with patch("vexis_agent.core.tailscale.subprocess.run", side_effect=fake):
        result = tailscale.get_node_info()
    assert result.error is not None
    assert "timed out" in result.error
    assert result.node is None


# ---- cache TTL ---------------------------------------------------------


def test_cache_collapses_two_calls_within_ttl():
    fake = _patch_run({
        ("serve", "status", "--json"): _completed(json.dumps(_SERVE_DOC).encode()),
    })
    with patch("vexis_agent.core.tailscale.subprocess.run", side_effect=fake) as mock_run:
        tailscale.get_serve_status()
        tailscale.get_serve_status()
    assert mock_run.call_count == 1


def test_cache_refetches_after_ttl_expires():
    fake = _patch_run({
        ("serve", "status", "--json"): _completed(json.dumps(_SERVE_DOC).encode()),
    })
    # Inject a fake clock so the test isn't a wall-clock waiter.
    fake_now = [0.0]

    def clock() -> float:
        return fake_now[0]

    cache = tailscale._TtlCache(ttl_seconds=10.0, clock=clock)
    with patch.object(tailscale, "_CACHE", cache):
        with patch("vexis_agent.core.tailscale.subprocess.run", side_effect=fake) as mock_run:
            tailscale.get_serve_status()         # t=0   miss
            fake_now[0] = 5.0
            tailscale.get_serve_status()         # t=5   hit
            fake_now[0] = 10.5
            tailscale.get_serve_status()         # t=10.5 miss
    assert mock_run.call_count == 2


def test_cache_keys_separate_per_function():
    # serve and funnel share the same JSON shape but different argv;
    # their cache slots must not collide.
    serve_doc = json.dumps(_SERVE_DOC).encode()
    funnel_doc = json.dumps(_FUNNEL_DOC).encode()
    fake = _patch_run({
        ("serve", "status", "--json"): _completed(serve_doc),
        ("funnel", "status", "--json"): _completed(funnel_doc),
    })
    with patch("vexis_agent.core.tailscale.subprocess.run", side_effect=fake) as mock_run:
        s = tailscale.get_serve_status()
        f = tailscale.get_funnel_status()
    assert mock_run.call_count == 2
    assert len(s.serves) == 2
    assert len(f.funnels) == 1

"""Read-only window into the Tailscale CLI.

Vexis already runs behind ``tailscale serve``; this module surfaces what
else the local node is exposing (serves, funnels) plus the tailnet's
peer list so the user can answer "what's reachable from my phone right
now?" without sshing into the box.

All four entry points (``get_serve_status``, ``get_funnel_status``,
``get_node_info``, ``get_peers``) shell out to the ``tailscale`` CLI
with a 5-second timeout, parse the structured JSON output, and return
typed dataclasses. Every CLI failure mode (missing binary, non-zero
exit, malformed JSON, timeout) is captured into ``error`` on the
returned status object — callers never see a raised exception. A
small in-memory cache with a 10-second TTL means the dashboard's 30s
poll and the Telegram /tailscale handler don't each re-shell out
(at peak, three concurrent consumers within the TTL window collapse
to one ``tailscale`` invocation).

CLI shape captured during the v1.96.4 audit:

  ``tailscale serve status --json`` and ``tailscale funnel status --json``
  return the same ``ipn.ServeConfig`` document. Web handlers live at
  ``Web["<host>:<port>"].Handlers["<mount>"].Proxy``. Whether a
  ``<host>:<port>`` is funnel-exposed is signalled by
  ``AllowFunnel["<host>:<port>"] == true`` in the same document.
  ``tailscale status --json`` returns ``Self`` (this node) plus
  ``Peer`` (a dict keyed by node-public-key). No sudo required.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger(__name__)

CLI_TIMEOUT_SECONDS = 5.0
CACHE_TTL_SECONDS = 10.0


@dataclass(frozen=True)
class Serve:
    """One ``tailscale serve`` mount on this node."""

    port: int
    mount: str
    target: str
    tls: bool
    funnel: bool  # True iff this mount is also exposed via funnel


@dataclass(frozen=True)
class Funnel:
    """One ``tailscale funnel`` mount on this node.

    Same shape as ``Serve`` — funnel is just serve plus
    ``AllowFunnel: true`` — but split out so the dashboard's two
    sections render cleanly without filtering."""

    port: int
    mount: str
    target: str
    tls: bool


@dataclass(frozen=True)
class NodeInfo:
    hostname: str  # tailnet DNSName, trailing dot stripped (empty when unknown)
    ip: str        # primary IPv4 from TailscaleIPs (empty when unknown)
    online: bool


@dataclass(frozen=True)
class Peer:
    hostname: str
    ip: str
    online: bool
    last_seen: str | None  # ISO timestamp from tailscaled, or None
    os: str


@dataclass(frozen=True)
class ServeStatus:
    serves: list[Serve] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class FunnelStatus:
    funnels: list[Funnel] = field(default_factory=list)
    error: str | None = None


@dataclass(frozen=True)
class NodeStatus:
    node: NodeInfo | None = None
    error: str | None = None


@dataclass(frozen=True)
class PeersStatus:
    peers: list[Peer] = field(default_factory=list)
    error: str | None = None


# ---- public API ------------------------------------------------------


def get_serve_status() -> ServeStatus:
    return _CACHE.get_or_compute("serve", _read_serve_status)


def get_funnel_status() -> FunnelStatus:
    return _CACHE.get_or_compute("funnel", _read_funnel_status)


def get_node_info() -> NodeStatus:
    return _CACHE.get_or_compute("node", _read_node_status)


def get_peers() -> PeersStatus:
    return _CACHE.get_or_compute("peers", _read_peers_status)


def reset_cache() -> None:
    """Drop every cached value. Used by tests; never called from prod."""
    _CACHE.clear()


# ---- subprocess + parse helpers --------------------------------------


def _run_cli(argv: list[str]) -> tuple[bytes | None, str | None]:
    """Run ``tailscale`` with the canonical timeout. Returns
    ``(stdout_bytes, error_message_or_none)``. Maps every failure
    mode the CLI can hit — missing binary, non-zero exit, timeout —
    into the error string so callers stay exception-free.
    """
    try:
        proc = subprocess.run(  # noqa: S603 — argv is a fixed CLI invocation
            argv,
            capture_output=True,
            timeout=CLI_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        return None, "tailscale CLI not found in PATH"
    except subprocess.TimeoutExpired:
        return None, f"tailscale CLI timed out after {CLI_TIMEOUT_SECONDS:.0f}s"
    except OSError as exc:
        return None, f"tailscale CLI failed to launch: {exc}"
    if proc.returncode != 0:
        stderr = (proc.stderr or b"").decode(errors="replace").strip()
        return None, (
            f"tailscale exited {proc.returncode}: "
            f"{stderr or '(no stderr)'}"
        )
    return proc.stdout, None


def _parse_json(blob: bytes) -> tuple[dict | None, str | None]:
    if not blob.strip():
        # Both serve and funnel may emit an empty body when nothing
        # is configured. Treat as an empty-but-valid document.
        return {}, None
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError as exc:
        return None, f"tailscale returned invalid JSON: {exc}"
    if not isinstance(parsed, dict):
        return None, "tailscale returned a non-object JSON payload"
    return parsed, None


def _serves_from_doc(doc: dict, *, want_funnel: bool) -> list[Serve | Funnel]:
    """Walk a ``serve status`` / ``funnel status`` document.

    ``want_funnel=False`` returns every Web handler as a ``Serve``,
    with ``funnel`` flagging mounts also reachable from the public
    internet. ``want_funnel=True`` returns ONLY the funnel-exposed
    mounts as ``Funnel`` instances.
    """
    web = doc.get("Web") or {}
    if not isinstance(web, dict):
        return []
    allow_funnel_raw = doc.get("AllowFunnel") or {}
    allow_funnel: dict[str, bool] = (
        allow_funnel_raw if isinstance(allow_funnel_raw, dict) else {}
    )
    out: list[Serve | Funnel] = []
    for host_port, payload in web.items():
        if not isinstance(payload, dict):
            continue
        port = _port_from_hostport(host_port)
        is_funnel = bool(allow_funnel.get(host_port))
        # ``TCP[<port>].HTTPS == true`` flags TLS termination on the
        # port — the only mode the modern CLI exposes for serve/funnel.
        tls = _tls_for_port(doc, port)
        handlers = payload.get("Handlers") or {}
        if not isinstance(handlers, dict):
            continue
        for mount, handler in handlers.items():
            if not isinstance(handler, dict):
                continue
            target = (
                handler.get("Proxy")
                or handler.get("Text")
                or handler.get("Path")
                or ""
            )
            if want_funnel:
                if not is_funnel:
                    continue
                out.append(Funnel(port=port, mount=mount, target=str(target), tls=tls))
            else:
                out.append(
                    Serve(
                        port=port,
                        mount=mount,
                        target=str(target),
                        tls=tls,
                        funnel=is_funnel,
                    )
                )
    out.sort(key=lambda row: (row.port, row.mount))
    return out


def _port_from_hostport(host_port: str) -> int:
    _, _, port_part = host_port.rpartition(":")
    try:
        return int(port_part)
    except ValueError:
        return 0


def _tls_for_port(doc: dict, port: int) -> bool:
    tcp = doc.get("TCP") or {}
    if not isinstance(tcp, dict):
        return False
    entry = tcp.get(str(port))
    if not isinstance(entry, dict):
        return False
    return bool(entry.get("HTTPS"))


def _read_serve_status() -> ServeStatus:
    blob, err = _run_cli(["tailscale", "serve", "status", "--json"])
    if err is not None:
        log.warning("tailscale serve status failed: %s", err)
        return ServeStatus(error=err)
    doc, parse_err = _parse_json(blob or b"")
    if parse_err is not None:
        log.warning("tailscale serve status: %s", parse_err)
        return ServeStatus(error=parse_err)
    serves_or_funnels = _serves_from_doc(doc or {}, want_funnel=False)
    serves: list[Serve] = [s for s in serves_or_funnels if isinstance(s, Serve)]
    return ServeStatus(serves=serves)


def _read_funnel_status() -> FunnelStatus:
    blob, err = _run_cli(["tailscale", "funnel", "status", "--json"])
    if err is not None:
        log.warning("tailscale funnel status failed: %s", err)
        return FunnelStatus(error=err)
    doc, parse_err = _parse_json(blob or b"")
    if parse_err is not None:
        log.warning("tailscale funnel status: %s", parse_err)
        return FunnelStatus(error=parse_err)
    funnels_or_serves = _serves_from_doc(doc or {}, want_funnel=True)
    funnels: list[Funnel] = [f for f in funnels_or_serves if isinstance(f, Funnel)]
    return FunnelStatus(funnels=funnels)


def _read_node_status() -> NodeStatus:
    doc, err = _read_status_doc()
    if err is not None or doc is None:
        return NodeStatus(error=err or "tailscale status returned no payload")
    self_obj = doc.get("Self")
    if not isinstance(self_obj, dict):
        return NodeStatus(error="tailscale status missing Self block")
    node = NodeInfo(
        hostname=str(self_obj.get("DNSName", "")).rstrip("."),
        ip=_first_ipv4(self_obj.get("TailscaleIPs")),
        online=bool(self_obj.get("Online", False)),
    )
    return NodeStatus(node=node)


def _read_peers_status() -> PeersStatus:
    doc, err = _read_status_doc()
    if err is not None or doc is None:
        return PeersStatus(error=err or "tailscale status returned no payload")
    peer_block = doc.get("Peer")
    if not isinstance(peer_block, dict):
        # No peers is valid (solo tailnet); only complain about a
        # malformed shape.
        return PeersStatus(peers=[])
    peers: list[Peer] = []
    for entry in peer_block.values():
        if not isinstance(entry, dict):
            continue
        peers.append(
            Peer(
                hostname=str(entry.get("DNSName", "")).rstrip("."),
                ip=_first_ipv4(entry.get("TailscaleIPs")),
                online=bool(entry.get("Online", False)),
                last_seen=_clean_iso(entry.get("LastSeen")),
                os=str(entry.get("OS", "")),
            )
        )
    # Online first, then alphabetical by hostname — matches how
    # the user reads the list (who's reachable right now?).
    peers.sort(key=lambda p: (not p.online, p.hostname.lower()))
    return PeersStatus(peers=peers)


def _read_status_doc() -> tuple[dict | None, str | None]:
    blob, err = _run_cli(["tailscale", "status", "--json"])
    if err is not None:
        log.warning("tailscale status failed: %s", err)
        return None, err
    doc, parse_err = _parse_json(blob or b"")
    if parse_err is not None:
        log.warning("tailscale status: %s", parse_err)
        return None, parse_err
    return doc, None


def _first_ipv4(ips) -> str:
    if not isinstance(ips, list):
        return ""
    for ip in ips:
        if isinstance(ip, str) and "." in ip and ":" not in ip:
            return ip
    return ""


def _clean_iso(value) -> str | None:
    """Tailscaled writes the zero time as ``0001-01-01T00:00:00Z``
    when a peer has never been seen. Surface that as ``None`` so
    consumers can render ``—`` instead of a misleading year-1 stamp.
    """
    if not isinstance(value, str) or not value:
        return None
    if value.startswith("0001-01-01"):
        return None
    return value


# ---- cache -----------------------------------------------------------


class _TtlCache:
    """Per-key TTL cache. Reads outside the lock; writes inside.

    The lock prevents two concurrent consumers from both seeing a
    miss, both shelling out, and one of them stomping the other's
    write — single-flight behavior is overkill here (the CLI calls
    are cheap) so we just accept the race.
    """

    def __init__(self, *, ttl_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._values: dict[str, tuple[float, object]] = {}
        self._lock = threading.Lock()

    def get_or_compute(self, key: str, fn: Callable[[], object]):
        now = self._clock()
        with self._lock:
            entry = self._values.get(key)
        if entry is not None and (now - entry[0]) < self._ttl:
            return entry[1]
        value = fn()
        with self._lock:
            self._values[key] = (now, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


_CACHE = _TtlCache(ttl_seconds=CACHE_TTL_SECONDS)

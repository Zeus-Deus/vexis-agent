"""Long-lived stdio JSON-RPC client for the Codemux MCP server.

Codemux exposes its terminal_read / pane_list / workspace_list tools
exclusively over its MCP server (``codemux mcp``). The control socket
at ``/run/user/<uid>/codemux.sock`` only knows ``status``; the rich
introspection tools are MCP-only.

Spawning ``codemux mcp`` per call is too expensive for a polling loop
running every 5–30s across many watched workspaces, so this client
keeps one ``codemux mcp`` subprocess alive and serialises requests
through it. If the subprocess dies (codemux app restarted, killed by
the user), :meth:`call` transparently respawns on the next call.

The protocol is plain JSON-RPC 2.0 over stdio:

  initialize        →  capabilities handshake
  notifications/initialized  →  fire-and-forget
  tools/call        →  invoke a named tool with arguments

This module deliberately does not depend on the ``mcp`` Python SDK —
the surface we need is tiny (3 tools, single-server) and pulling in
the full SDK would bloat the daemon's startup. If we ever need
sampling / progress / resources, swap to the SDK then.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from typing import Any, Optional

log = logging.getLogger(__name__)

CODEMUX_BINARY = "codemux"
INITIALIZE_PROTOCOL_VERSION = "2024-11-05"
CLIENT_NAME = "vexis-watcher"
CLIENT_VERSION = "0.1.0"
DEFAULT_TIMEOUT_SECONDS = 5.0

# Override knob for users whose codemux MCP binary isn't named
# ``codemux`` (e.g. a side-loaded ``codemux-remote`` build). When set
# this beats the ``mcp-servers.yaml`` lookup; an explicit kwarg to
# ``CodemuxMcpClient(binary=...)`` still beats this. See
# ``_resolve_binary()`` for the full precedence chain.
VEXIS_CODEMUX_BINARY_ENV = "VEXIS_CODEMUX_BINARY"
# The MCP server name we look up in ``~/.vexis/mcp-servers.yaml``.
# Hardcoded — the watcher only knows how to drive codemux today. If a
# future fork wants to rename the server, plumb a constructor arg
# rather than making this a knob.
_CODEMUX_MCP_NAME = "codemux"
# TODO: the YAML entry may legitimately set ``args`` to something
# other than ``["mcp"]`` once codemux grows new subcommands; today we
# always pass ``mcp`` regardless of what the YAML says. Revisit when
# codemux ships a second MCP-serving subcommand.
_CODEMUX_MCP_SUBCOMMAND = "mcp"

# Exponential respawn backoff for a chronically-failing ``codemux mcp``
# subprocess. Without this, a Codemux build that segfaults on every
# init would burn one fork-exec per poll tick × every watched
# workspace — a real DoS against the desktop. The schedule below
# doubles each failure: 1s, 2s, 4s, 8s, 16s, 32s, 60s (cap). After a
# successful call the counter resets, so a transient crash recovers
# instantly. ``next_attempt_at`` is wall-clock so backoff survives a
# burst of overlapping callers — they all see "still cooling down."
_RESPAWN_BACKOFF_BASE_SECONDS = 1.0
_RESPAWN_BACKOFF_MAX_SECONDS = 60.0


class CodemuxMcpUnavailable(RuntimeError):
    """Raised when the ``codemux`` binary is missing or unspawnable."""


class CodemuxMcpError(RuntimeError):
    """Raised when an MCP call returns ``isError: true`` or malformed data."""


def _binary_from_mcp_yaml() -> Optional[str]:
    """Return the ``command`` field of the codemux MCP entry in
    ``~/.vexis/mcp-servers.yaml``, or None if no such entry exists
    or the file can't be parsed.

    We deliberately do NOT route this through
    ``setup_wizard.detect_mcp_servers`` — that helper filters out
    entries whose binary isn't on ``$PATH``, but here we want the raw
    ``command`` string so ``shutil.which`` in ``_ensure_running`` is
    the single source of truth for "is it actually invokable" and the
    user can declare an aspirational path without us silently falling
    back to a stale default.

    Any failure (YAML drift, missing file, weird types) silently
    falls through to the next precedence step — the resolver MUST
    NOT crash startup over a malformed user file.
    """
    try:
        from vexis_agent.core.paths import vexis_dir  # lazy: keep startup graph small
        import yaml  # type: ignore[import-untyped]
    except Exception:
        return None
    try:
        path = vexis_dir() / "mcp-servers.yaml"
    except Exception:
        return None
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    servers = data.get("servers")
    if not isinstance(servers, list):
        return None
    for entry in servers:
        if not isinstance(entry, dict):
            continue
        if entry.get("name") != _CODEMUX_MCP_NAME:
            continue
        command = entry.get("command")
        if isinstance(command, str) and command:
            return command
    return None


def _resolve_binary() -> str:
    """Decide which binary the watcher should spawn for ``codemux mcp``.

    Precedence (highest first):
      1. ``$VEXIS_CODEMUX_BINARY`` environment variable.
      2. ``command`` field of the entry named ``codemux`` in
         ``~/.vexis/mcp-servers.yaml``.
      3. The literal ``"codemux"`` fallback (vanilla install).

    Reads disk on every call so adding the YAML entry takes effect on
    the next ``CodemuxMcpClient`` construction without code changes —
    matches the CLAUDE.md "config reads disk per call" invariant.
    Explicit constructor args (``binary=...``) bypass this entirely;
    tests rely on that contract.
    """
    env = os.environ.get(VEXIS_CODEMUX_BINARY_ENV)
    if env:
        return env
    yaml_cmd = _binary_from_mcp_yaml()
    if yaml_cmd:
        return yaml_cmd
    return CODEMUX_BINARY


class CodemuxMcpClient:
    """Persistent stdio JSON-RPC client.

    Thread-unsafe by design — callers funnel through a single
    asyncio-loop watcher and ``_lock`` serialises overlapping ``call``
    invocations so requests/responses don't interleave on the wire.
    """

    def __init__(self, *, binary: Optional[str] = None) -> None:
        # ``binary=None`` → run the env-var / YAML / default resolver.
        # An explicit kwarg (including the legacy ``binary="codemux"``
        # the backoff tests pass) bypasses the resolver verbatim.
        self._binary = binary if binary is not None else _resolve_binary()
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._next_id = 1
        self._lock = asyncio.Lock()
        # Respawn backoff state. ``_consecutive_failures`` increments
        # each time ``_ensure_running`` fails (or a call mid-RPC drops
        # the proc); resets to 0 on a successful ``call``. ``_next_attempt_at``
        # is monotonic seconds and gates respawn attempts so a chronically
        # broken codemux can't be hammered. Both are reset together in
        # ``_record_success``.
        self._consecutive_failures = 0
        self._next_attempt_at: float = 0.0
        # Latched at the cap so we only log "still cooling down" once
        # per cooldown band instead of every call.
        self._cap_logged = False

    @staticmethod
    def is_codemux_available() -> bool:
        """``codemux`` binary present on PATH."""
        return shutil.which(CODEMUX_BINARY) is not None

    async def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.stdin is not None and not proc.stdin.is_closing():
                proc.stdin.close()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except Exception:
                pass

    def _backoff_seconds(self) -> float:
        """Current cooldown for the next respawn attempt.

        Schedule: ``base * 2 ** (failures - 1)``, capped at ``MAX``.
        ``failures == 0`` → 0 (no cooldown). Pure function of state
        so tests can stub the clock and assert exact values.
        """
        if self._consecutive_failures <= 0:
            return 0.0
        exponent = self._consecutive_failures - 1
        return min(
            _RESPAWN_BACKOFF_MAX_SECONDS,
            _RESPAWN_BACKOFF_BASE_SECONDS * (2 ** exponent),
        )

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        cooldown = self._backoff_seconds()
        self._next_attempt_at = time.monotonic() + cooldown
        if cooldown >= _RESPAWN_BACKOFF_MAX_SECONDS and not self._cap_logged:
            log.warning(
                "codemux mcp respawn backoff hit the %.0fs cap after %d "
                "consecutive failures — the codemux binary is unhealthy. "
                "Subsequent calls will retry no faster than every %.0fs "
                "until a successful spawn resets the counter.",
                _RESPAWN_BACKOFF_MAX_SECONDS,
                self._consecutive_failures,
                _RESPAWN_BACKOFF_MAX_SECONDS,
            )
            self._cap_logged = True

    def _record_success(self) -> None:
        if self._consecutive_failures:
            log.info(
                "codemux mcp respawn recovered after %d failures",
                self._consecutive_failures,
            )
        self._consecutive_failures = 0
        self._next_attempt_at = 0.0
        self._cap_logged = False

    async def _ensure_running(self) -> asyncio.subprocess.Process:
        proc = self._proc
        if proc is not None and proc.returncode is None:
            return proc
        # Honour the cooldown window before paying for another spawn.
        # We raise a typed error rather than sleeping so the poller
        # tick is fast even when codemux is sick — the next tick will
        # re-check the clock and respawn when the window has elapsed.
        now = time.monotonic()
        if self._next_attempt_at and now < self._next_attempt_at:
            remaining = self._next_attempt_at - now
            raise CodemuxMcpError(
                f"codemux mcp in respawn-backoff cooldown for "
                f"{remaining:.1f}s more "
                f"(consecutive failures: {self._consecutive_failures})"
            )
        if shutil.which(self._binary) is None:
            # The binary disappearing also counts as a failure; without
            # this, repeated calls would tight-loop on the PATH check.
            self._record_failure()
            raise CodemuxMcpUnavailable(
                f"{self._binary!r} not on PATH; install codemux to enable the watcher"
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                self._binary, _CODEMUX_MCP_SUBCOMMAND,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except (OSError, RuntimeError) as exc:
            self._record_failure()
            raise CodemuxMcpError(
                f"failed to spawn codemux mcp: {exc}"
            ) from exc
        self._proc = proc
        try:
            await self._initialize(proc)
        except Exception:
            await self.close()
            self._record_failure()
            raise
        return proc

    async def _initialize(self, proc: asyncio.subprocess.Process) -> None:
        await self._send(proc, {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": "initialize",
            "params": {
                "protocolVersion": INITIALIZE_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        })
        self._next_id += 1
        # Match the request id we just sent on the way back.
        _ = await self._recv(proc)
        await self._send(proc, {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })

    async def _send(self, proc: asyncio.subprocess.Process, payload: dict) -> None:
        if proc.stdin is None or proc.stdin.is_closing():
            raise CodemuxMcpError("codemux mcp stdin closed mid-send")
        line = (json.dumps(payload) + "\n").encode()
        proc.stdin.write(line)
        await proc.stdin.drain()

    async def _recv(self, proc: asyncio.subprocess.Process) -> dict:
        if proc.stdout is None:
            raise CodemuxMcpError("codemux mcp stdout missing")
        raw = await asyncio.wait_for(
            proc.stdout.readline(), timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        if not raw:
            raise CodemuxMcpError("codemux mcp stdout EOF")
        try:
            return json.loads(raw.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CodemuxMcpError(f"malformed JSON from codemux mcp: {exc}") from exc

    async def call(self, tool: str, arguments: Optional[dict] = None) -> Any:
        """Invoke an MCP tool. Returns the decoded JSON body (text content
        is parsed as JSON when possible; raw string otherwise).
        Raises CodemuxMcpUnavailable / CodemuxMcpError on failure."""
        async with self._lock:
            try:
                proc = await self._ensure_running()
                req_id = self._next_id
                self._next_id += 1
                await self._send(proc, {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": arguments or {}},
                })
                resp = await self._recv(proc)
            except (BrokenPipeError, ConnectionResetError, asyncio.TimeoutError) as exc:
                # Subprocess died mid-call — drop it so the next call respawns.
                # This is a failure of the live process, so it advances the
                # respawn-backoff counter the same way an _ensure_running
                # failure does. Without this, a codemux that initialises
                # cleanly but crashes on every tools/call would never
                # back off.
                await self.close()
                self._record_failure()
                raise CodemuxMcpError(f"codemux mcp call failed: {exc}") from exc
        if "error" in resp:
            # Protocol-level errors (malformed request, unknown method)
            # are NOT subprocess crashes — the connection is fine,
            # the request was just wrong. No backoff.
            raise CodemuxMcpError(
                f"codemux mcp error for {tool}: {resp['error']}"
            )
        result = resp.get("result") or {}
        if result.get("isError"):
            # Tool-level errors (workspace closed, pane gone) are also
            # not subprocess crashes — codemux is alive and answered.
            text = _extract_text(result)
            raise CodemuxMcpError(f"{tool} failed: {text}")
        # We got a clean answer — codemux is healthy. Reset backoff.
        self._record_success()
        return _decode_result(result)


def _extract_text(result: dict) -> str:
    """Flatten the MCP ``content: [{type: text, text: ...}]`` shape."""
    parts: list[str] = []
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts).strip()


def _decode_result(result: dict) -> Any:
    """Codemux returns one ``text`` block per result whose body is a
    JSON document. Parse it when possible; fall back to the raw text
    for tools that don't return JSON."""
    text = _extract_text(result)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text

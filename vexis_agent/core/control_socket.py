"""Daemon control socket for in-process operations.

Listens on a Unix domain socket (default
$XDG_RUNTIME_DIR/vexis-agent/vexis-agent.sock) with mode 0600. Accepts
single-message-per-connection JSON requests of the form

    {"op": "<name>", "args": {...}}

and returns a single JSON line response

    {"ok": true,  "result": ...}     -- success
    {"ok": false, "error": "...",
     "kind": "ExceptionClassName"}   -- handled failure

Dispatch is delegated to a coroutine the caller wires up at construction.
The socket is the foundation for vexis-bg (background tasks) and may be
extended for future daemon-controlled features (e.g. an in-process
livestream rewrite).

The socket is local-only — file permissions enforce that only the
owning user can connect. Do not bind it on a TCP port.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

log = logging.getLogger(__name__)

DispatchFn = Callable[[str, dict], Awaitable[dict]]

_SOCKET_DIRNAME = "vexis-agent"
_SOCKET_BASENAME = "vexis-agent.sock"
_RECV_LIMIT_BYTES = 1 << 20  # 1 MiB ceiling on a single request line


def default_socket_path() -> Path:
    """Resolve the conventional runtime path for the control socket."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / _SOCKET_DIRNAME / _SOCKET_BASENAME


class ControlSocket:
    """Asyncio Unix-domain JSON RPC server.

    Lifecycle: ``await start()`` to bind, ``await stop()`` to tear down.
    Each accepted connection reads one JSON line, awaits the dispatch
    coroutine, writes one JSON line, then closes — a deliberately simple
    protocol that needs no framing beyond newlines.
    """

    def __init__(self, socket_path: Path, dispatch: DispatchFn) -> None:
        self._path = socket_path
        self._dispatch = dispatch
        self._server: asyncio.AbstractServer | None = None

    @property
    def path(self) -> Path:
        return self._path

    async def start(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # A stale socket from a prior crash would block bind() with
        # EADDRINUSE; remove it before binding.
        if self._path.exists() or self._path.is_symlink():
            try:
                self._path.unlink()
            except OSError as exc:
                log.warning("could not remove stale socket %s: %s", self._path, exc)
        self._server = await asyncio.start_unix_server(
            self._handle, path=str(self._path), limit=_RECV_LIMIT_BYTES
        )
        try:
            os.chmod(self._path, 0o600)
        except OSError as exc:
            log.warning("could not chmod socket %s: %s", self._path, exc)
        log.info("control socket listening at %s", self._path)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                log.debug("server.wait_closed raised", exc_info=True)
            self._server = None
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("could not remove socket %s: %s", self._path, exc)

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            try:
                line = await reader.readline()
            except (asyncio.LimitOverrunError, ConnectionResetError) as exc:
                log.warning("control socket read error: %s", exc)
                return
            if not line:
                return
            response = await self._dispatch_safely(line)
            payload = (json.dumps(response) + "\n").encode()
            try:
                writer.write(payload)
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                return
        finally:
            try:
                writer.close()
            except Exception:
                pass
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _dispatch_safely(self, line: bytes) -> dict:
        try:
            req = json.loads(line.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return {"ok": False, "error": f"invalid JSON: {exc}", "kind": "BadRequest"}
        if not isinstance(req, dict):
            return {
                "ok": False,
                "error": "request must be a JSON object",
                "kind": "BadRequest",
            }
        op = req.get("op")
        if not isinstance(op, str):
            return {"ok": False, "error": "missing 'op'", "kind": "BadRequest"}
        args = req.get("args", {})
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return {
                "ok": False,
                "error": "'args' must be an object",
                "kind": "BadRequest",
            }
        try:
            result = await self._dispatch(op, args)
        except Exception as exc:
            log.exception("dispatch raised for op=%s", op)
            return {"ok": False, "error": str(exc), "kind": type(exc).__name__}
        if not isinstance(result, dict):
            return {"ok": True, "result": result}
        return result

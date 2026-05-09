"""Tests for core/control_socket.py.

We bind the socket inside tmp_path, send line-delimited JSON via a
plain ``socket.socket`` client, and assert the protocol round-trip plus
the failure modes (malformed JSON, missing 'op', dispatch raising,
permissions).

Tests follow the codebase convention of sync test functions calling
asyncio.run() rather than pytest-asyncio.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
from pathlib import Path

from vexis_agent.core.control_socket import ControlSocket, default_socket_path


def _client_round_trip(socket_path: Path, payload: dict, timeout: float = 5.0) -> dict:
    """Open a Unix socket, send one JSON line, read one JSON line."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(socket_path))
        sock.sendall((json.dumps(payload) + "\n").encode())
        sock.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                break
            chunks.append(chunk)
        data = b"".join(chunks).decode().strip()
    finally:
        sock.close()
    return json.loads(data)


def _client_send_raw(socket_path: Path, payload: bytes, timeout: float = 5.0) -> bytes:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(socket_path))
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(8192)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        sock.close()


def test_default_socket_path_honours_xdg_runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert default_socket_path() == tmp_path / "vexis-agent" / "vexis-agent.sock"


def test_round_trip_dispatches_op_and_args(tmp_path):
    received: list[tuple[str, dict]] = []

    async def dispatch(op: str, args: dict) -> dict:
        received.append((op, args))
        return {"ok": True, "result": {"echo": args.get("name")}}

    sock_path = tmp_path / "ctl.sock"
    cs = ControlSocket(sock_path, dispatch)

    async def scenario() -> dict:
        await cs.start()
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None,
                _client_round_trip,
                sock_path,
                {"op": "ping", "args": {"name": "vexis"}},
            )
        finally:
            await cs.stop()

    response = asyncio.run(scenario())
    assert received == [("ping", {"name": "vexis"})]
    assert response == {"ok": True, "result": {"echo": "vexis"}}


def test_socket_is_owner_only_permission(tmp_path):
    async def dispatch(op: str, args: dict) -> dict:
        return {"ok": True, "result": None}

    sock_path = tmp_path / "ctl.sock"
    cs = ControlSocket(sock_path, dispatch)

    async def scenario() -> int:
        await cs.start()
        try:
            return stat.S_IMODE(os.lstat(sock_path).st_mode)
        finally:
            await cs.stop()

    mode = asyncio.run(scenario())
    assert mode == 0o600


def test_malformed_json_returns_error_response(tmp_path):
    async def dispatch(op: str, args: dict) -> dict:
        raise AssertionError("dispatch should not run for malformed input")

    sock_path = tmp_path / "ctl.sock"
    cs = ControlSocket(sock_path, dispatch)

    async def scenario() -> bytes:
        await cs.start()
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None,
                _client_send_raw,
                sock_path,
                b"{not valid json\n",
            )
        finally:
            await cs.stop()

    raw = asyncio.run(scenario())
    payload = json.loads(raw.decode().strip())
    assert payload["ok"] is False
    assert payload["kind"] == "BadRequest"


def test_missing_op_returns_error(tmp_path):
    async def dispatch(op: str, args: dict) -> dict:
        raise AssertionError("dispatch should not run when 'op' is absent")

    sock_path = tmp_path / "ctl.sock"
    cs = ControlSocket(sock_path, dispatch)

    async def scenario() -> dict:
        await cs.start()
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None,
                _client_round_trip,
                sock_path,
                {"args": {}},
            )
        finally:
            await cs.stop()

    resp = asyncio.run(scenario())
    assert resp["ok"] is False
    assert "op" in resp["error"].lower()


def test_dispatch_exception_returns_structured_error(tmp_path):
    async def dispatch(op: str, args: dict) -> dict:
        raise RuntimeError("kaboom")

    sock_path = tmp_path / "ctl.sock"
    cs = ControlSocket(sock_path, dispatch)

    async def scenario() -> dict:
        await cs.start()
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None,
                _client_round_trip,
                sock_path,
                {"op": "boom", "args": {}},
            )
        finally:
            await cs.stop()

    resp = asyncio.run(scenario())
    assert resp == {"ok": False, "error": "kaboom", "kind": "RuntimeError"}


def test_stop_removes_socket_file(tmp_path):
    async def dispatch(op: str, args: dict) -> dict:
        return {"ok": True, "result": None}

    sock_path = tmp_path / "ctl.sock"
    cs = ControlSocket(sock_path, dispatch)

    async def scenario() -> bool:
        await cs.start()
        existed_during = sock_path.exists()
        await cs.stop()
        return existed_during and not sock_path.exists()

    assert asyncio.run(scenario())


def test_start_clears_stale_socket_file(tmp_path):
    """A leftover socket from a previous crash must not block bind."""
    sock_path = tmp_path / "ctl.sock"
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    sock_path.write_text("not a real socket")  # plain file masquerading

    async def dispatch(op: str, args: dict) -> dict:
        return {"ok": True, "result": "ran"}

    cs = ControlSocket(sock_path, dispatch)

    async def scenario() -> dict:
        await cs.start()
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None,
                _client_round_trip,
                sock_path,
                {"op": "x", "args": {}},
            )
        finally:
            await cs.stop()

    resp = asyncio.run(scenario())
    assert resp["ok"] is True


def test_bare_dict_dispatch_result_passes_through(tmp_path):
    """If dispatch returns ``{"ok": True, ...}`` it's treated as the
    final envelope; ControlSocket doesn't double-wrap."""

    async def dispatch(op: str, args: dict) -> dict:
        return {"ok": True, "result": {"value": 42}, "extra": "hello"}

    sock_path = tmp_path / "ctl.sock"
    cs = ControlSocket(sock_path, dispatch)

    async def scenario() -> dict:
        await cs.start()
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None,
                _client_round_trip,
                sock_path,
                {"op": "x", "args": {}},
            )
        finally:
            await cs.stop()

    resp = asyncio.run(scenario())
    assert resp == {"ok": True, "result": {"value": 42}, "extra": "hello"}

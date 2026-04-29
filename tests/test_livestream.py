"""Tests for tools/livestream.py.

Coverage:
  - FrameProducer captures via mocked grim/hyprctl and exposes bytes
  - FrameProducer increments failure_streak on subprocess error
  - StreamServer serves /healthz with the documented JSON shape
  - StreamServer serves the index page as text/html
  - StreamServer /stream sets the multipart/x-mixed-replace header
  - LiveStreamDaemon._verify_tailscale raises LiveStreamError when
    tailscale is down (rc != 0) or backend != Running
  - state_file_path honours XDG_RUNTIME_DIR

Tests follow the codebase convention of sync test functions calling
asyncio.run() rather than pytest-asyncio.
"""

from __future__ import annotations

import asyncio
import json
import socket
from unittest.mock import patch

import aiohttp
import pytest

from tools import livestream
from tools.livestream import (
    FrameProducer,
    LiveStreamDaemon,
    LiveStreamError,
    StreamServer,
    state_file_path,
)


def test_state_file_path_honours_xdg_runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert state_file_path() == tmp_path / "vexis-agent" / "livestream.json"


def test_frame_producer_stores_capture_bytes():
    fake_jpeg = b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9"
    monitors = [{"name": "DP-1", "focused": True}]

    async def fake_run(name, argv, timeout, *, env=None, cwd=None):
        if argv[0] == "hyprctl":
            return 0, json.dumps(monitors).encode(), b""
        if argv[0] == "grim":
            return 0, fake_jpeg, b""
        raise AssertionError(f"unexpected argv: {argv}")

    async def scenario():
        producer = FrameProducer(interval=0.05, jpeg_quality=70)
        await producer.start()
        for _ in range(50):
            if producer.current_frame() is not None:
                break
            await asyncio.sleep(0.02)
        await producer.stop()
        return producer

    with patch.object(livestream, "run", fake_run):
        producer = asyncio.run(scenario())

    assert producer.current_frame() == fake_jpeg
    assert producer.last_capture_time() is not None
    assert producer.consecutive_failures == 0


def test_frame_producer_tracks_failure_streak():
    monitors = [{"name": "DP-1", "focused": True}]

    async def fake_run(name, argv, timeout, *, env=None, cwd=None):
        if argv[0] == "hyprctl":
            return 0, json.dumps(monitors).encode(), b""
        if argv[0] == "grim":
            return 1, b"", b"grim: nope"
        raise AssertionError(f"unexpected argv: {argv}")

    async def scenario():
        producer = FrameProducer(interval=0.02, jpeg_quality=70)
        await producer.start()
        for _ in range(60):
            if producer.consecutive_failures >= 2:
                break
            await asyncio.sleep(0.02)
        await producer.stop()
        return producer

    with patch.object(livestream, "run", fake_run):
        producer = asyncio.run(scenario())

    assert producer.current_frame() is None
    assert producer.consecutive_failures >= 2


class _FakeProducer:
    def __init__(self, frame: bytes | None = None, last: float | None = None) -> None:
        self._frame = frame
        self._last = last
        self.interval = 0.5

    def current_frame(self) -> bytes | None:
        return self._frame

    def last_capture_time(self) -> float | None:
        return self._last


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_stream_server_routes():
    port = _free_port()

    async def scenario():
        producer = _FakeProducer(frame=b"\xff\xd8\xff\xd9", last=1234.5)
        server = StreamServer(producer, port)  # type: ignore[arg-type]
        await server.start()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{port}/") as resp:
                    assert resp.status == 200
                    assert resp.content_type == "text/html"
                    body = await resp.text()
                    assert "Vexis live view" in body
                    # Default: <img src> uses the TAILSCALE_PATH constant
                    # so the page works when fronted by Tailscale Serve.
                    assert '<img src="/vexis/stream"' in body

                async with session.get(
                    f"http://127.0.0.1:{port}/",
                    headers={"X-Forwarded-Prefix": "/custom-prefix"},
                ) as resp:
                    body = await resp.text()
                    # When a reverse proxy advertises its prefix, use it.
                    assert '<img src="/custom-prefix/stream"' in body

                async with session.get(f"http://127.0.0.1:{port}/healthz") as resp:
                    assert resp.status == 200
                    payload = await resp.json()
                    assert payload["streaming"] is True
                    assert payload["fps"] == 2.0
                    assert isinstance(payload["last_capture_age_seconds"], float)

                async with session.get(f"http://127.0.0.1:{port}/stream") as resp:
                    assert resp.status == 200
                    ctype = resp.headers["Content-Type"]
                    assert ctype.startswith("multipart/x-mixed-replace")
                    assert "boundary=frame" in ctype
                    chunk = await resp.content.read(64)
                    assert chunk.startswith(b"--frame\r\n")
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_verify_tailscale_raises_when_subprocess_fails():
    async def fake_run(name, argv, timeout, *, env=None, cwd=None):
        return 1, b"", b"is the tailscaled service running?"

    async def scenario():
        daemon = LiveStreamDaemon()
        with pytest.raises(LiveStreamError, match="tailscale not running"):
            await daemon._verify_tailscale()

    with patch.object(livestream, "run", fake_run):
        asyncio.run(scenario())


def test_verify_tailscale_raises_when_backend_not_running():
    payload = {"BackendState": "Stopped", "Self": {"DNSName": "x.tail.ts.net."}}

    async def fake_run(name, argv, timeout, *, env=None, cwd=None):
        return 0, json.dumps(payload).encode(), b""

    async def scenario():
        daemon = LiveStreamDaemon()
        with pytest.raises(LiveStreamError, match="backend state is 'Stopped'"):
            await daemon._verify_tailscale()

    with patch.object(livestream, "run", fake_run):
        asyncio.run(scenario())


def test_verify_tailscale_extracts_dns_name():
    payload = {
        "BackendState": "Running",
        "Self": {"DNSName": "host.example.ts.net."},
    }

    async def fake_run(name, argv, timeout, *, env=None, cwd=None):
        return 0, json.dumps(payload).encode(), b""

    async def scenario():
        daemon = LiveStreamDaemon()
        await daemon._verify_tailscale()
        return daemon

    with patch.object(livestream, "run", fake_run):
        daemon = asyncio.run(scenario())

    assert daemon._tailscale_dns == "host.example.ts.net"

"""MJPEG live view of the focused monitor, served over Tailscale.

This module runs as a detached side-process spawned by
`tools.livestream_cli` (`python -m tools.livestream`). The side-process
owns:

  - A FrameProducer that captures frames every STREAM_INTERVAL_SECONDS,
    holding the latest JPEG in memory. Source is one of:
      * host (default) — grim against the focused Hyprland monitor;
      * sandbox — docker exec into the per-task sandbox container and
        screenshot its Xvfb display with `import` (ImageMagick) or
        `scrot` as fallback. The sandbox image must ship one of those
        tools — debian:bookworm-slim does not by default, so this path
        errors with a clear "install imagemagick / scrot" message.
  - An aiohttp server bound to 127.0.0.1:STREAM_PORT serving a tiny
    HTML page at /, the multipart/x-mixed-replace MJPEG stream at
    /stream, and a JSON /healthz route.
  - A `tailscale serve` mapping that fronts the local server on the
    user's tailnet at https://<host>.<tailnet>.ts.net/vexis.
  - A state file at $XDG_RUNTIME_DIR/vexis-agent/livestream.json
    (pid, url, started_at, last_activity, source). The CLI reads this file.
  - Signal handlers: SIGTERM/SIGINT = clean shutdown (removes the
    tailscale serve mapping); SIGUSR1 = touch (reset idle timer).
  - An idle watchdog that stops the daemon after IDLE_TIMEOUT_SECONDS
    of inactivity, or after CONSECUTIVE_FAILURE_LIMIT capture failures.

The source is passed in from `livestream_cli start --source ...` via
the env vars VEXIS_LIVESTREAM_SOURCE_KIND and
VEXIS_LIVESTREAM_SOURCE_TASK_ID. Default = host.

Limitation: the side-process pattern is a workaround for not having a
daemon control socket. A future step should fold LiveStream into the
main daemon and replace state-file IPC with in-process control.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

from vexis_agent.core.subprocess import run
from vexis_agent.tools.capture_source import CaptureSource

log = logging.getLogger("vexis.livestream")

STREAM_INTERVAL_SECONDS = 0.5
JPEG_QUALITY = 70
STREAM_PORT = 8765
TAILSCALE_PATH = "/vexis"
IDLE_TIMEOUT_SECONDS = 300
WATCHDOG_INTERVAL_SECONDS = 30
TAILSCALE_TIMEOUT_SECONDS = 10
GRIM_TIMEOUT_SECONDS = 3
HYPRCTL_TIMEOUT_SECONDS = 2
FIRST_FRAME_TIMEOUT_SECONDS = 2.0
CONSECUTIVE_FAILURE_LIMIT = 10
MONITOR_REFRESH_EVERY = 30
STREAM_POLL_SECONDS = 0.05


class LiveStreamError(Exception):
    """Raised when the live stream cannot start or run."""


def state_file_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "vexis-agent" / "livestream.json"


@dataclass
class StreamState:
    pid: int
    url: str
    started_at: datetime
    last_activity: datetime
    source_kind: str = "host"
    source_task_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "pid": self.pid,
            "url": self.url,
            "started_at": self.started_at.isoformat(),
            "last_activity": self.last_activity.isoformat(),
            "source_kind": self.source_kind,
            "source_task_id": self.source_task_id,
        }


# ---------- frame producer ----------


class FrameProducer:
    """Captures the configured source as JPEG every `interval` seconds.

    Source defaults to host (the original behaviour). Sandbox source
    requires the sandbox container to have `import` (ImageMagick) or
    `scrot` available; the detected tool is cached after the first
    successful capture to avoid re-probing per frame.
    """

    def __init__(
        self,
        interval: float,
        jpeg_quality: int,
        source: CaptureSource | None = None,
    ) -> None:
        self._interval = interval
        self._jpeg_quality = jpeg_quality
        self._source = source  # None == host (preserves existing behaviour)
        self._current_frame: bytes | None = None
        self._last_capture_time: float | None = None
        self._failure_streak = 0
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        # Host-source state
        self._monitor_name: str | None = None
        self._monitor_refresh_counter = 0
        # Sandbox-source state — populated lazily on first capture.
        self._sandbox_display: str | None = None
        self._sandbox_capture_tool: str | None = None  # "import" or "scrot"
        self._sandbox_container_name: str | None = None

    @property
    def source(self) -> CaptureSource | None:
        return self._source

    @property
    def interval(self) -> float:
        return self._interval

    @property
    def consecutive_failures(self) -> int:
        return self._failure_streak

    def current_frame(self) -> bytes | None:
        return self._current_frame

    def last_capture_time(self) -> float | None:
        return self._last_capture_time

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="vexis-livestream-producer")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._capture_one()
            except Exception as exc:
                self._failure_streak += 1
                log.warning("frame capture failed (#%d): %s", self._failure_streak, exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass

    async def _capture_one(self) -> None:
        if self._source is not None and self._source.kind == "sandbox":
            await self._capture_one_sandbox()
            return
        await self._capture_one_host()

    async def _capture_one_host(self) -> None:
        if (
            self._monitor_name is None
            or self._monitor_refresh_counter >= MONITOR_REFRESH_EVERY
        ):
            self._monitor_name = await self._find_focused_monitor()
            self._monitor_refresh_counter = 0
        self._monitor_refresh_counter += 1

        argv = [
            "grim",
            "-t",
            "jpeg",
            "-q",
            str(self._jpeg_quality),
            "-o",
            self._monitor_name,
            "-",
        ]
        rc, stdout, stderr = await run("grim", argv, GRIM_TIMEOUT_SECONDS)
        if rc != 0 or not stdout:
            raise RuntimeError(
                f"grim rc={rc}: {stderr.decode(errors='replace').strip() or '(no stderr)'}"
            )
        self._current_frame = stdout
        self._last_capture_time = time.time()
        self._failure_streak = 0

    async def _capture_one_sandbox(self) -> None:
        # Resolve display + container name lazily so we don't import the
        # sandbox stack until a sandbox-source daemon is actually
        # spawned. This keeps the host-only path free of docker import
        # cost.
        if self._sandbox_display is None or self._sandbox_container_name is None:
            await self._resolve_sandbox_metadata()
        if self._sandbox_capture_tool is None:
            self._sandbox_capture_tool = await self._detect_sandbox_capture_tool()

        if self._sandbox_capture_tool == "import":
            # ImageMagick can write JPEG straight to stdout with quality.
            inner = (
                f"import -display {self._sandbox_display} -window root "
                f"-quality {self._jpeg_quality} jpeg:-"
            )
        elif self._sandbox_capture_tool == "scrot":
            # scrot writes to a path; we capture to /tmp inside the
            # container and cat it back. /tmp is fine because it dies
            # with the container.
            inner = (
                f"DISPLAY={self._sandbox_display} scrot -o "
                f"-q {self._jpeg_quality} -F /tmp/vexis-livestream.jpg "
                "&& cat /tmp/vexis-livestream.jpg"
            )
        else:
            raise RuntimeError(
                f"unknown sandbox capture tool: {self._sandbox_capture_tool!r}"
            )

        argv = [
            "docker",
            "exec",
            self._sandbox_container_name,
            "sh",
            "-c",
            inner,
        ]
        rc, stdout, stderr = await run("docker", argv, GRIM_TIMEOUT_SECONDS)
        if rc != 0 or not stdout:
            raise RuntimeError(
                f"sandbox capture rc={rc} (tool={self._sandbox_capture_tool!r}): "
                f"{stderr.decode(errors='replace').strip() or '(no stderr)'}"
            )
        self._current_frame = stdout
        self._last_capture_time = time.time()
        self._failure_streak = 0

    async def _resolve_sandbox_metadata(self) -> None:
        assert self._source is not None and self._source.kind == "sandbox"
        task_id = self._source.task_id
        assert task_id is not None  # validated by CaptureSource

        from vexis_agent.tools.display import HeadlessDisplay
        from vexis_agent.tools.sandbox.sandbox import container_name_for

        try:
            env = await asyncio.to_thread(HeadlessDisplay(task_id).env)
        except Exception as exc:
            raise LiveStreamError(
                f"no display registered for task {task_id!r}: {exc}. "
                f"Start one with `vexis-display start {task_id}`."
            ) from exc

        display = env.get("DISPLAY") or ":99"
        if env.get("WAYLAND_DISPLAY"):
            # wayland-headless sandboxes don't have a working `import`
            # path (it requires X). Fast-fail rather than emit broken
            # frames the user has to debug.
            raise LiveStreamError(
                f"task {task_id!r} runs a wayland-headless display; "
                "livestream source=sandbox currently requires Xvfb."
            )
        self._sandbox_display = display
        self._sandbox_container_name = container_name_for(task_id)

    async def _detect_sandbox_capture_tool(self) -> str:
        """Probe the sandbox for a screenshot tool. Cached after first hit."""
        assert self._sandbox_container_name is not None
        # ``command -v`` is POSIX and exits 0 iff the named tool is on
        # PATH. We OR with `|| echo none` to avoid the non-zero exit
        # cascading to our subprocess wrapper.
        probe = (
            "if command -v import >/dev/null 2>&1; then echo import; "
            "elif command -v scrot >/dev/null 2>&1; then echo scrot; "
            "else echo none; fi"
        )
        argv = ["docker", "exec", self._sandbox_container_name, "sh", "-c", probe]
        rc, stdout, stderr = await run("docker", argv, GRIM_TIMEOUT_SECONDS)
        if rc != 0:
            raise LiveStreamError(
                f"failed to probe sandbox for capture tool: "
                f"{stderr.decode(errors='replace').strip()}"
            )
        tool = stdout.decode(errors="replace").strip().splitlines()[-1]
        if tool == "none":
            raise LiveStreamError(
                f"sandbox {self._sandbox_container_name!r} has neither "
                "`import` (imagemagick) nor `scrot`. Install one inside the "
                "sandbox: `vexis-sandbox exec <task-id> -- apt-get install -y "
                "imagemagick` (or scrot)."
            )
        if tool not in ("import", "scrot"):
            raise LiveStreamError(f"unexpected capture-tool probe output: {tool!r}")
        return tool

    async def _find_focused_monitor(self) -> str:
        rc, stdout, stderr = await run(
            "hyprctl", ["hyprctl", "monitors", "-j"], HYPRCTL_TIMEOUT_SECONDS
        )
        if rc != 0:
            raise RuntimeError(
                f"hyprctl monitors rc={rc}: {stderr.decode(errors='replace')}"
            )
        monitors = json.loads(stdout)
        for m in monitors:
            if m.get("focused"):
                return m["name"]
        if monitors:
            return monitors[0]["name"]
        raise RuntimeError("no monitors reported by hyprctl")


# ---------- HTTP server ----------


class StreamServer:
    """aiohttp server exposing /, /stream (MJPEG), /healthz."""

    def __init__(self, producer: FrameProducer, port: int) -> None:
        self._producer = producer
        self._port = port
        self._app = web.Application()
        self._app.router.add_get("/", self._index)
        self._app.router.add_get("/stream", self._stream)
        self._app.router.add_get("/healthz", self._healthz)
        self._runner = web.AppRunner(self._app, access_log=None)
        self._site: web.TCPSite | None = None

    @property
    def port(self) -> int:
        return self._port

    async def start(self) -> None:
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", self._port)
        await self._site.start()

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
        await self._runner.cleanup()

    async def _index(self, request: web.Request) -> web.Response:
        # Tailscale Serve fronts us at TAILSCALE_PATH and strips the
        # prefix when proxying, so the backend sees /, not /vexis. The
        # browser, however, is at https://host.ts.net/vexis — a
        # root-absolute "/stream" would resolve to the wrong URL.
        # Prefer X-Forwarded-Prefix if any reverse proxy sets it; else
        # fall back to our chosen TAILSCALE_PATH constant.
        prefix = request.headers.get("X-Forwarded-Prefix") or TAILSCALE_PATH
        body = (
            "<!doctype html><html><head><meta charset=utf-8>"
            "<title>Vexis live view</title>"
            "<style>body{margin:0;background:#000;color:#888;"
            "font-family:monospace;}h1{font-size:13px;padding:6px 10px;"
            "margin:0;background:#111;}img{display:block;width:100vw;"
            "height:calc(100vh - 28px);object-fit:contain;}</style>"
            "</head><body><h1>Vexis live view</h1>"
            f'<img src="{prefix}/stream" alt="vexis live view">'
            "</body></html>"
        )
        return web.Response(text=body, content_type="text/html")

    async def _stream(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "multipart/x-mixed-replace; boundary=frame",
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
            },
        )
        await response.prepare(request)
        seen_at: float | None = None
        try:
            while not request.transport.is_closing():
                last = self._producer.last_capture_time()
                frame = self._producer.current_frame()
                if frame is not None and last != seen_at:
                    chunk = (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Content-Length: "
                        + str(len(frame)).encode()
                        + b"\r\n\r\n"
                        + frame
                        + b"\r\n"
                    )
                    await response.write(chunk)
                    seen_at = last
                await asyncio.sleep(STREAM_POLL_SECONDS)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        return response

    async def _healthz(self, request: web.Request) -> web.Response:
        last = self._producer.last_capture_time()
        return web.json_response(
            {
                "streaming": True,
                "fps": round(1.0 / self._producer.interval, 2),
                "last_capture_age_seconds": (
                    round(time.time() - last, 3) if last is not None else None
                ),
            }
        )


# ---------- daemon ----------


class LiveStreamDaemon:
    def __init__(self, source: CaptureSource | None = None) -> None:
        self._source = source
        self._producer = FrameProducer(
            STREAM_INTERVAL_SECONDS, JPEG_QUALITY, source=source
        )
        self._server = StreamServer(self._producer, STREAM_PORT)
        self._url: str | None = None
        self._tailscale_dns: str | None = None
        self._started_at: datetime | None = None
        self._last_activity: datetime | None = None
        self._stop_event: asyncio.Event | None = None

    async def run(self) -> None:
        self._stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, self._handle_sigterm)
        loop.add_signal_handler(signal.SIGINT, self._handle_sigterm)
        loop.add_signal_handler(signal.SIGUSR1, self._handle_sigusr1)

        try:
            await self._verify_tailscale()
            await self._producer.start()
            await self._wait_for_first_frame()
            await self._server.start()
            self._url = await self._configure_tailscale_serve()
        except LiveStreamError as exc:
            log.error("livestream startup failed: %s", exc)
            await self._cleanup()
            raise

        self._started_at = datetime.now(timezone.utc)
        self._last_activity = self._started_at
        self._write_state()
        log.info("livestream ready at %s", self._url)

        watchdog = asyncio.create_task(
            self._watchdog(), name="vexis-livestream-watchdog"
        )
        try:
            await self._stop_event.wait()
        finally:
            watchdog.cancel()
            try:
                await watchdog
            except asyncio.CancelledError:
                pass
            await self._cleanup()

    def _handle_sigterm(self) -> None:
        log.info("livestream received SIGTERM/SIGINT")
        if self._stop_event is not None:
            self._stop_event.set()

    def _handle_sigusr1(self) -> None:
        self._last_activity = datetime.now(timezone.utc)
        self._write_state()
        log.debug("livestream touched")

    async def _verify_tailscale(self) -> None:
        try:
            rc, stdout, stderr = await run(
                "tailscale",
                ["tailscale", "status", "--json"],
                TAILSCALE_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            raise LiveStreamError("tailscale status timed out") from exc
        if rc != 0:
            err = stderr.decode(errors="replace").strip()
            raise LiveStreamError(f"tailscale not running: {err or '(no stderr)'}")
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise LiveStreamError(f"tailscale returned invalid JSON: {exc}") from exc
        backend = payload.get("BackendState")
        if backend != "Running":
            raise LiveStreamError(
                f"tailscale backend state is {backend!r}, expected 'Running'"
            )
        dns = (payload.get("Self") or {}).get("DNSName", "").rstrip(".")
        if not dns:
            raise LiveStreamError("tailscale status returned no DNSName for this node")
        self._tailscale_dns = dns

    async def _wait_for_first_frame(self) -> None:
        deadline = time.time() + FIRST_FRAME_TIMEOUT_SECONDS
        while time.time() < deadline:
            if self._producer.current_frame() is not None:
                return
            await asyncio.sleep(0.05)
        raise LiveStreamError(
            f"grim produced no frame within {FIRST_FRAME_TIMEOUT_SECONDS}s"
        )

    async def _configure_tailscale_serve(self) -> str:
        argv = [
            "tailscale",
            "serve",
            "--bg",
            "--https=443",
            f"--set-path={TAILSCALE_PATH}",
            f"http://localhost:{STREAM_PORT}",
        ]
        try:
            rc, stdout, stderr = await run("tailscale", argv, TAILSCALE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise LiveStreamError("tailscale serve timed out") from exc
        if rc != 0:
            err = (stderr or b"").decode(errors="replace").strip()
            out = (stdout or b"").decode(errors="replace").strip()
            raise LiveStreamError(
                f"tailscale serve failed (rc={rc}): {err or out or '(no output)'}"
            )
        return f"https://{self._tailscale_dns}{TAILSCALE_PATH}"

    async def _watchdog(self) -> None:
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=WATCHDOG_INTERVAL_SECONDS
                )
                return
            except asyncio.TimeoutError:
                pass
            now = datetime.now(timezone.utc)
            assert self._last_activity is not None
            idle = (now - self._last_activity).total_seconds()
            if idle > IDLE_TIMEOUT_SECONDS:
                log.info("livestream idle for %.0fs, stopping", idle)
                self._stop_event.set()
                return
            if self._producer.consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                log.error(
                    "livestream stopping after %d consecutive frame failures",
                    self._producer.consecutive_failures,
                )
                self._stop_event.set()
                return

    async def _cleanup(self) -> None:
        log.info("livestream cleaning up")
        try:
            argv = [
                "tailscale",
                "serve",
                "--https=443",
                f"--set-path={TAILSCALE_PATH}",
                "off",
            ]
            rc, _, stderr = await run("tailscale", argv, TAILSCALE_TIMEOUT_SECONDS)
            if rc != 0:
                log.warning(
                    "tailscale serve off failed (rc=%d): %s",
                    rc,
                    stderr.decode(errors="replace").strip(),
                )
        except Exception as exc:
            log.warning("tailscale cleanup error: %s", exc)
        try:
            await self._server.stop()
        except Exception as exc:
            log.warning("server stop error: %s", exc)
        try:
            await self._producer.stop()
        except Exception as exc:
            log.warning("producer stop error: %s", exc)
        try:
            state_file_path().unlink(missing_ok=True)
        except Exception:
            pass

    def _write_state(self) -> None:
        if self._url is None or self._started_at is None or self._last_activity is None:
            return
        path = state_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        state = StreamState(
            pid=os.getpid(),
            url=self._url,
            started_at=self._started_at,
            last_activity=self._last_activity,
            source_kind=self._source.kind if self._source else "host",
            source_task_id=self._source.task_id if self._source else None,
        )
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state.to_dict()))
        tmp.replace(path)


# ---------- side-process entrypoint ----------


def _source_from_env() -> CaptureSource | None:
    """Read VEXIS_LIVESTREAM_SOURCE_KIND / _TASK_ID, falling back to host.

    Returns None for "host" so the existing host-only behaviour stays
    identical (the FrameProducer treats None and CaptureSource(host)
    the same — keeping None makes test diffs smaller).
    """
    kind = (os.environ.get("VEXIS_LIVESTREAM_SOURCE_KIND") or "").strip().lower()
    if kind in ("", "host"):
        return None
    if kind == "sandbox":
        task_id = (os.environ.get("VEXIS_LIVESTREAM_SOURCE_TASK_ID") or "").strip()
        if not task_id:
            print(
                "livestream: VEXIS_LIVESTREAM_SOURCE_KIND=sandbox requires "
                "VEXIS_LIVESTREAM_SOURCE_TASK_ID",
                file=sys.stderr,
            )
            return None
        return CaptureSource(kind="sandbox", task_id=task_id, reason="user-explicit")
    print(
        f"livestream: ignoring unknown VEXIS_LIVESTREAM_SOURCE_KIND={kind!r}",
        file=sys.stderr,
    )
    return None


def _main() -> int:
    logging.basicConfig(
        level=os.environ.get("VEXIS_LIVESTREAM_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    daemon = LiveStreamDaemon(source=_source_from_env())
    try:
        asyncio.run(daemon.run())
    except LiveStreamError as exc:
        print(f"livestream: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(_main())

"""Screenshot + Hyprland state capture.

Pure tool: given a scope (and optionally a non-host source), returns a
path to a fresh PNG plus a structured state dict and a one-line summary.
Knows nothing about Telegram or brains.

Two capture paths share the same :class:`CaptureResult` envelope:

* **Host** (default) — ``grim`` + ``hyprctl`` against the real desktop.
  Honours the ``scope`` parameter (``focused-monitor`` / ``all-monitors``
  / ``focused-window``).
* **Sandbox** — delegates to ``UIDriver.vision_snapshot`` inside the
  per-task Docker sandbox, then moves the resulting PNG from the
  sandbox's mounted ``/scratch`` dir to ``/tmp/vexis-screenshot-<ts>.png``
  so the Telegram regex picks it up the same way as a host capture.
  ``scope`` is ignored here (a headless display has no monitors).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vexis_agent.core.subprocess import run
from vexis_agent.tools.capture_source import CaptureSource

log = logging.getLogger(__name__)

GRIM_TIMEOUT_SECONDS = 5
HYPRCTL_TIMEOUT_SECONDS = 2
SCREENSHOT_DIR = Path("/tmp")
SCREENSHOT_PREFIX = "vexis-screenshot-"
# How long to wait for the sandbox-side ``vision-snapshot`` runner to
# return before giving up. AT-SPI walk + screenshot fallback together
# typically finish well under 5s; 15s leaves headroom for cold-start
# images that need to load fonts/D-Bus the first time around.
SANDBOX_CAPTURE_TIMEOUT_SECONDS = 15

VALID_SCOPES = ("focused-monitor", "all-monitors", "focused-window")


class CaptureError(Exception):
    """Raised when a capture step fails. `image_path` is set when grim
    succeeded but state collection or summarising did not."""

    def __init__(self, message: str, image_path: Path | None = None) -> None:
        super().__init__(message)
        self.image_path = image_path


@dataclass
class CaptureResult:
    image_path: Path
    state: dict[str, Any] = field(default_factory=dict)
    summary: str = ""


async def capture_desktop(
    scope: str = "focused-monitor",
    source: CaptureSource | None = None,
) -> CaptureResult:
    """Capture a screenshot from the configured source.

    ``source=None`` and ``source.kind == "host"`` both go through the
    host path. ``source.kind == "sandbox"`` delegates to the per-task
    Docker sandbox via :class:`UIDriver.vision_snapshot`. In both
    cases the final image_path is a fresh
    ``/tmp/vexis-screenshot-<ts>.png`` so downstream consumers
    (the Telegram path-regex, brain output extraction) work
    identically regardless of source.
    """
    if source is not None and source.kind == "sandbox":
        if source.task_id is None:  # defensive — CaptureSource validates this
            raise CaptureError("sandbox source missing task_id")
        return await _capture_sandbox(source.task_id)
    return await _capture_host(scope)


async def _capture_host(scope: str) -> CaptureResult:
    if scope not in VALID_SCOPES:
        raise CaptureError(f"unknown scope: {scope!r}")

    monitors_raw, workspace_raw, clients_raw = await _collect_hypr_state()

    image_path = SCREENSHOT_DIR / f"{SCREENSHOT_PREFIX}{int(time.time())}.png"
    grim_argv = _build_grim_argv(scope, monitors_raw, clients_raw, image_path)
    await _run_grim(grim_argv)

    try:
        state = _build_state(monitors_raw, workspace_raw, clients_raw)
        summary = _build_summary(state)
    except Exception as exc:
        raise CaptureError(
            f"failed to build state/summary: {exc}", image_path=image_path
        ) from exc

    return CaptureResult(image_path=image_path, state=state, summary=summary)


async def _capture_sandbox(task_id: str) -> CaptureResult:
    # Lazy imports: keep this module's top-level dep graph minimal so
    # the pure host-capture path is reachable on installs that don't
    # have docker / the sandbox stack provisioned.
    from vexis_agent.tools.sandbox.sandbox import scratch_dir_for
    from vexis_agent.tools.ui.ui import ATSPIError, UIDriver

    # Filename matches the host convention so the Telegram regex in
    # _SCREENSHOT_PATH_RE picks it up unmodified, and so brain output
    # extraction stays oblivious to the source.
    fname = f"{SCREENSHOT_PREFIX}{int(time.time())}.png"
    container_path = f"/scratch/{fname}"
    scratch_host = scratch_dir_for(task_id)
    container_host_path = scratch_host / fname
    final_host_path = SCREENSHOT_DIR / fname

    driver = UIDriver(task_id)

    def _run_vision() -> dict:
        return driver.vision_snapshot(container_path)

    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(_run_vision),
            timeout=SANDBOX_CAPTURE_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError as exc:
        raise CaptureError(
            f"sandbox capture timed out after {SANDBOX_CAPTURE_TIMEOUT_SECONDS}s"
            f" (task={task_id!r})"
        ) from exc
    except ATSPIError as exc:
        raise CaptureError(f"sandbox capture failed: {exc}") from exc

    if not container_host_path.exists():
        raise CaptureError(
            f"sandbox runner reported success but {container_host_path} is "
            f"missing on host. Likely cause: /scratch isn't mounted (sandbox "
            f"was started without the default mounts)."
        )

    # Read-and-rewrite (NOT shutil.move) so the final file is owned by
    # the host user running the daemon. The original file inside the
    # sandbox-mounted scratch dir is created as root (docker runs as
    # root inside the container), and /tmp has the sticky bit set — so
    # leaving a root-owned PNG in /tmp would break the downstream
    # Telegram cleanup `image_path.unlink()`. Cleaning up the
    # root-owned source file is best-effort: a permission failure
    # leaves an orphan inside the sandbox's scratch dir, which dies
    # with the sandbox anyway.
    final_host_path.parent.mkdir(parents=True, exist_ok=True)
    data = container_host_path.read_bytes()
    final_host_path.write_bytes(data)
    try:
        container_host_path.unlink()
    except OSError as exc:
        log.debug("could not unlink sandbox-side %s: %s", container_host_path, exc)

    via = result.get("via", "screenshot")
    state = {
        "source": "sandbox",
        "task_id": task_id,
        "via": via,
    }
    summary = f"Sandbox {task_id} ({via})"
    return CaptureResult(image_path=final_host_path, state=state, summary=summary)


# ---------- hyprctl ----------


async def _collect_hypr_state() -> tuple[list[dict], dict, list[dict]]:
    results = await asyncio.gather(
        _hyprctl_json(["hyprctl", "monitors", "-j"]),
        _hyprctl_json(["hyprctl", "activeworkspace", "-j"]),
        _hyprctl_json(["hyprctl", "clients", "-j"]),
        return_exceptions=True,
    )
    for r in results:
        if isinstance(r, BaseException):
            raise CaptureError(f"hyprctl failed: {r}") from r
    monitors, active_ws, clients = results
    if not isinstance(monitors, list) or not isinstance(clients, list):
        raise CaptureError("hyprctl returned unexpected JSON shape")
    if not isinstance(active_ws, dict):
        active_ws = {}
    return monitors, active_ws, clients


async def _hyprctl_json(argv: list[str]) -> Any:
    stdout = await _run(argv, HYPRCTL_TIMEOUT_SECONDS)
    return json.loads(stdout)


# ---------- grim ----------


def _build_grim_argv(
    scope: str,
    monitors: list[dict],
    clients: list[dict],
    image_path: Path,
) -> list[str]:
    if scope == "all-monitors":
        return ["grim", str(image_path)]
    if scope == "focused-monitor":
        focused = next((m for m in monitors if m.get("focused")), None)
        if focused is None or not focused.get("name"):
            raise CaptureError("no focused monitor reported by hyprctl")
        return ["grim", "-o", str(focused["name"]), str(image_path)]
    if scope == "focused-window":
        active = next((c for c in clients if c.get("focusHistoryID") == 0), None)
        if active is None:
            raise CaptureError("no focused window")
        at = active.get("at") or [0, 0]
        size = active.get("size") or [0, 0]
        if not size or size[0] <= 0 or size[1] <= 0:
            raise CaptureError("focused window has zero size")
        geom = f"{int(at[0])},{int(at[1])} {int(size[0])}x{int(size[1])}"
        return ["grim", "-g", geom, str(image_path)]
    raise CaptureError(f"unknown scope: {scope!r}")


async def _run_grim(argv: list[str]) -> None:
    try:
        await _run(argv, GRIM_TIMEOUT_SECONDS)
    except CaptureError as exc:
        raise CaptureError(f"grim failed: {exc}") from exc


# ---------- state + summary ----------


def _build_state(
    monitors: list[dict], active_ws: dict, clients: list[dict]
) -> dict[str, Any]:
    monitor_id_to_name = {
        m["id"]: m["name"] for m in monitors if "id" in m and "name" in m
    }
    focused_monitor = next((m for m in monitors if m.get("focused")), None)

    windows = []
    for c in clients:
        ws = c.get("workspace") or {}
        windows.append(
            {
                "title": c.get("title", ""),
                "class": c.get("class", ""),
                "workspace_id": ws.get("id"),
                "monitor": monitor_id_to_name.get(c.get("monitor"), ""),
                "at": list(c.get("at", [])),
                "size": list(c.get("size", [])),
                "focused": c.get("focusHistoryID") == 0,
                "floating": bool(c.get("floating", False)),
            }
        )

    return {
        "active_workspace": {
            "id": active_ws.get("id"),
            "name": active_ws.get("name", ""),
        },
        "focused_monitor": (
            {
                "name": focused_monitor.get("name"),
                "width": focused_monitor.get("width"),
                "height": focused_monitor.get("height"),
            }
            if focused_monitor
            else {}
        ),
        "monitors": [
            {
                "name": m.get("name"),
                "active_workspace_id": (m.get("activeWorkspace") or {}).get("id"),
                "focused": bool(m.get("focused", False)),
            }
            for m in monitors
        ],
        "windows": windows,
    }


def _build_summary(state: dict[str, Any]) -> str:
    aw = state.get("active_workspace") or {}
    fm = state.get("focused_monitor") or {}
    workspace_id = aw.get("id")
    monitor_name = fm.get("name") or "?"

    windows = state.get("windows") or []
    on_active = [w for w in windows if w.get("workspace_id") == workspace_id]
    focused = next((w for w in on_active if w.get("focused")), None)
    others_on_active = [w for w in on_active if not w.get("focused")]

    other_workspaces = {
        w.get("workspace_id")
        for w in windows
        if w.get("workspace_id") not in (workspace_id, None)
    }

    head = f"Workspace {workspace_id} on {monitor_name} — "

    if not on_active:
        body = "empty desktop"
    elif focused is None:
        body = f"{_pretty_class(on_active[0]['class'])} (no focus)"
        if len(on_active) > 1:
            body += (
                f", {len(on_active) - 1} other window{_s(len(on_active) - 1)} visible"
            )
    else:
        body = _pretty_class(focused["class"])
        if len(others_on_active) == 1:
            body += f", tiled with {_pretty_class(others_on_active[0]['class'])}"
        elif len(others_on_active) > 1:
            n = len(others_on_active)
            body += f", +{n} other window{_s(n)} visible"

    tail = ""
    if other_workspaces:
        n = len(other_workspaces)
        tail = f", {n} other workspace{_s(n)} {'have' if n != 1 else 'has'} windows"

    return head + body + tail


def _pretty_class(raw: str) -> str:
    """Light cleanup for window class names so summaries read like prose
    rather than reverse-DNS strings. Best-effort; raw class is preserved
    in the structured state if precision matters."""
    if not raw:
        return "?"
    if raw.startswith("brave-"):
        return "Brave"
    if "." in raw:
        last = raw.rsplit(".", 1)[-1]
        if last:
            return last[:1].upper() + last[1:]
    return raw[:1].upper() + raw[1:]


def _s(n: int) -> str:
    return "" if n == 1 else "s"


# ---------- subprocess helper ----------


async def _run(argv: list[str], timeout: int) -> bytes:
    try:
        rc, stdout, stderr = await run(argv[0], argv, timeout)
    except asyncio.TimeoutError as exc:
        raise CaptureError(f"{argv[0]} timed out after {timeout}s") from exc

    if rc != 0:
        err = stderr.decode(errors="replace").strip()
        raise CaptureError(f"{argv[0]} exited {rc}: {err or '(no stderr)'}")
    return stdout

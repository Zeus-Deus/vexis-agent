"""Headless display provisioner.

Runs entirely *inside* the per-task sandbox container. The host never
sees the spawned ``Xvfb`` / ``Hyprland --headless`` process — when the
sandbox stops, the display process dies with it. We track a PID + log
file inside the container so :meth:`HeadlessDisplay.stop` can issue a
``kill`` and :meth:`HeadlessDisplay.env` can emit the right
``DISPLAY=`` / ``WAYLAND_DISPLAY=`` value.

Backend choice:

* ``xvfb``      — ``Xvfb :N -screen 0 WIDTHxHEIGHTx24 -nolisten tcp``.
* ``wayland-headless`` — ``Hyprland`` or ``cage`` started without a
  physical output, exposing ``$WAYLAND_DISPLAY=wayland-N``. The image
  is responsible for installing one of these; we try ``cage`` first
  (smaller, faster to boot) and fall back to ``Hyprland --headless``.
* ``auto`` (default) — picks ``xvfb`` because it has the broadest
  image-package coverage. Wayland is opt-in only.

Metadata persistence: same shape as :mod:`vexis_agent.tools.sandbox` —
a JSON file at ``$XDG_STATE_HOME/vexis-agent/displays/<task-id>.json``
so ``list`` can describe displays without re-execing into every container.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from vexis_agent.tools.sandbox import Sandbox, SandboxError


# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

DEFAULT_DISPLAY_NUMBER = 99
DEFAULT_RESOLUTION = "1920x1080"
SUPPORTED_BACKENDS = ("xvfb", "wayland-headless", "auto")

# Where inside the container we drop the PID + log so `stop` can find
# them on a fresh ``vexis-display`` invocation. ``/tmp`` works because
# both Xvfb and Hyprland support it; if a sandbox image clears /tmp on
# boot it has bigger problems than this module.
PID_FILE_TEMPLATE = "/tmp/vexis-display-{task_id}.pid"
LOG_FILE_TEMPLATE = "/tmp/vexis-display-{task_id}.log"
SOCKET_DIR = "/tmp"


# ----------------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------------


class DisplayError(RuntimeError):
    """Base for typed display failures."""


class DisplayNotFound(DisplayError):
    """No display has been started for this task yet."""


class DisplayStartFailed(DisplayError):
    """The compositor command exited or never bound a socket."""


class UnsupportedBackend(DisplayError):
    """The caller passed a backend keyword not in :data:`SUPPORTED_BACKENDS`."""


# ----------------------------------------------------------------------------
# Path helpers
# ----------------------------------------------------------------------------


def _state_root() -> Path:
    raw = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(raw) / "vexis-agent"


def metadata_path_for(task_id: str) -> Path:
    return _state_root() / "displays" / f"{task_id}.json"


def resolve_backend(name: str) -> str:
    """Map ``auto`` → concrete backend; raise on unknown keywords.

    Centralised so the CLI, the integration tests, and any future
    callers agree on the resolution. The current rule is "auto → xvfb"
    because Xvfb has the broadest image-package coverage. A future
    revision could read the sandbox image and pick wayland-headless
    when ``cage`` / ``Hyprland`` is detected; not worth the complexity
    today.
    """
    name = (name or "auto").lower()
    if name not in SUPPORTED_BACKENDS:
        raise UnsupportedBackend(
            f"Unknown display backend {name!r}; pick one of {SUPPORTED_BACKENDS}."
        )
    if name == "auto":
        return "xvfb"
    return name


# ----------------------------------------------------------------------------
# Metadata
# ----------------------------------------------------------------------------


@dataclass
class DisplayMetadata:
    task_id: str
    backend: str
    resolution: str
    display: str  # e.g. ":99" for Xvfb
    wayland_display: str | None = None
    pid: int | None = None
    log_path: str | None = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "backend": self.backend,
            "resolution": self.resolution,
            "display": self.display,
            "wayland_display": self.wayland_display,
            "pid": self.pid,
            "log_path": self.log_path,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DisplayMetadata":
        return cls(
            task_id=data["task_id"],
            backend=data["backend"],
            resolution=data.get("resolution") or DEFAULT_RESOLUTION,
            display=data["display"],
            wayland_display=data.get("wayland_display"),
            pid=data.get("pid"),
            log_path=data.get("log_path"),
        )


# ----------------------------------------------------------------------------
# HeadlessDisplay
# ----------------------------------------------------------------------------


@dataclass
class HeadlessDisplay:
    """One headless display per task-id, hosted inside that task's
    sandbox. Construction is cheap; ``start`` does the work."""

    task_id: str
    sandbox: Sandbox = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.sandbox is None:
            self.sandbox = Sandbox(self.task_id)

    # ----- properties --------------------------------------------------

    @property
    def metadata_path(self) -> Path:
        return metadata_path_for(self.task_id)

    # ----- lifecycle ---------------------------------------------------

    def start(
        self,
        *,
        backend: str = "auto",
        resolution: str = DEFAULT_RESOLUTION,
        display_number: int = DEFAULT_DISPLAY_NUMBER,
    ) -> DisplayMetadata:
        """Start the headless compositor inside the sandbox.

        Idempotent: if a display is already running for this task and
        the metadata file is valid, we return the cached metadata. To
        change backend/resolution, call :meth:`stop` first.
        """
        resolved = resolve_backend(backend)
        existing = self._read_metadata_safe()
        if existing is not None and self._is_running_in_sandbox(existing):
            return existing

        if resolved == "xvfb":
            meta = self._start_xvfb(resolution, display_number)
        elif resolved == "wayland-headless":
            meta = self._start_wayland(resolution, display_number)
        else:  # pragma: no cover — resolve_backend guards this
            raise UnsupportedBackend(resolved)
        self._write_metadata(meta)
        return meta

    def stop(self) -> bool:
        """Kill the display process. Returns False if nothing was running."""
        meta = self._read_metadata_safe()
        if meta is None:
            return False
        try:
            # Best-effort: a missing PID just means nothing to kill.
            if meta.pid is not None:
                self.sandbox.exec(
                    ["sh", "-c", f"kill {meta.pid} 2>/dev/null || true"],
                    auto_start=False,
                )
        except SandboxError:
            # Sandbox is already gone → display is gone too.
            pass
        try:
            self.metadata_path.unlink()
        except FileNotFoundError:
            pass
        return True

    def env(self) -> dict[str, str]:
        """Return env vars the caller should export when running GUI
        commands inside the same sandbox."""
        meta = self._read_metadata_safe()
        if meta is None:
            raise DisplayNotFound(
                f"No display recorded for task {self.task_id!r}. "
                f"Start one with `vexis-display start {self.task_id}` first."
            )
        env = {"DISPLAY": meta.display}
        if meta.wayland_display:
            env["WAYLAND_DISPLAY"] = meta.wayland_display
            env["XDG_RUNTIME_DIR"] = SOCKET_DIR
        return env

    # ----- backend implementations ------------------------------------

    def _start_xvfb(self, resolution: str, display_number: int) -> DisplayMetadata:
        display = f":{display_number}"
        pidfile = PID_FILE_TEMPLATE.format(task_id=self.task_id)
        logfile = LOG_FILE_TEMPLATE.format(task_id=self.task_id)
        # Use setsid so the Xvfb process detaches cleanly from the exec
        # session; otherwise docker exec hangs waiting for the child to
        # exit. The pidfile lets ``stop`` issue a kill without re-execing
        # ``pgrep``.
        cmd = (
            "set -e\n"
            f"setsid Xvfb {display} -screen 0 {resolution}x24 -nolisten tcp "
            f">{logfile} 2>&1 < /dev/null &\n"
            f"echo $! > {pidfile}\n"
            # Give the X server a moment to bind the socket
            "for i in 1 2 3 4 5 6 7 8 9 10; do\n"
            f"  if [ -S /tmp/.X11-unix/X{display_number} ]; then\n"
            "    break\n"
            "  fi\n"
            "  sleep 0.1\n"
            "done\n"
            f"cat {pidfile}\n"
        )
        res = self.sandbox.exec(["sh", "-c", cmd], auto_start=True, timeout=30)
        if not res.ok:
            raise DisplayStartFailed(
                f"Xvfb failed to start in task {self.task_id!r}: {res.stderr.strip() or res.stdout.strip()}"
            )
        try:
            pid = int((res.stdout.strip().splitlines() or ["0"])[-1])
        except ValueError:
            pid = None
        return DisplayMetadata(
            task_id=self.task_id,
            backend="xvfb",
            resolution=resolution,
            display=display,
            pid=pid,
            log_path=logfile,
        )

    def _start_wayland(self, resolution: str, display_number: int) -> DisplayMetadata:
        wayland_display = f"wayland-{display_number}"
        pidfile = PID_FILE_TEMPLATE.format(task_id=self.task_id)
        logfile = LOG_FILE_TEMPLATE.format(task_id=self.task_id)
        # Try cage first (single-window kiosk compositor, ~few MB), then
        # Hyprland --headless (full WM, much heavier but more
        # featureful). The shell uses ``command -v`` so we don't shell
        # out twice for the probe.
        cmd = (
            "set -e\n"
            f"mkdir -p {SOCKET_DIR}\n"
            f"export XDG_RUNTIME_DIR={SOCKET_DIR}\n"
            f"export WAYLAND_DISPLAY={wayland_display}\n"
            "if command -v cage >/dev/null 2>&1; then\n"
            f"  setsid cage -- sh -c 'sleep infinity' >{logfile} 2>&1 < /dev/null &\n"
            "elif command -v Hyprland >/dev/null 2>&1; then\n"
            f"  setsid Hyprland --headless >{logfile} 2>&1 < /dev/null &\n"
            "else\n"
            "  echo 'no wayland compositor (cage or Hyprland) in PATH' >&2\n"
            "  exit 127\n"
            "fi\n"
            f"echo $! > {pidfile}\n"
            "for i in 1 2 3 4 5 6 7 8 9 10; do\n"
            f"  if [ -S {SOCKET_DIR}/{wayland_display} ]; then break; fi\n"
            "  sleep 0.2\n"
            "done\n"
            f"cat {pidfile}\n"
        )
        res = self.sandbox.exec(["sh", "-c", cmd], auto_start=True, timeout=30)
        if not res.ok:
            raise DisplayStartFailed(
                f"wayland-headless start failed in task {self.task_id!r}: "
                f"{res.stderr.strip() or res.stdout.strip()}"
            )
        try:
            pid = int((res.stdout.strip().splitlines() or ["0"])[-1])
        except ValueError:
            pid = None
        # Wayland clients also need DISPLAY when running XWayland-backed
        # apps. We don't start XWayland here; callers that need it must
        # opt into the xvfb backend.
        return DisplayMetadata(
            task_id=self.task_id,
            backend="wayland-headless",
            resolution=resolution,
            display=":0",  # fallback so DISPLAY env still has a value
            wayland_display=wayland_display,
            pid=pid,
            log_path=logfile,
        )

    def _is_running_in_sandbox(self, meta: DisplayMetadata) -> bool:
        if meta.pid is None:
            return False
        try:
            res = self.sandbox.exec(
                ["sh", "-c", f"kill -0 {meta.pid} 2>/dev/null && echo alive || echo dead"],
                auto_start=False,
            )
        except SandboxError:
            return False
        return "alive" in res.stdout

    # ----- metadata IO -------------------------------------------------

    def _write_metadata(self, meta: DisplayMetadata) -> None:
        path = self.metadata_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta.to_dict(), indent=2))
        tmp.replace(path)

    def _read_metadata_safe(self) -> DisplayMetadata | None:
        try:
            data = json.loads(self.metadata_path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        try:
            return DisplayMetadata.from_dict(data)
        except (KeyError, TypeError):
            return None

    # ----- enumeration -------------------------------------------------

    @classmethod
    def list_all(cls) -> list[dict]:
        """Return one row per recorded display, regardless of whether
        the sandbox is still up. The CLI annotates ``running`` based on
        a live sandbox probe."""
        root = _state_root() / "displays"
        if not root.exists():
            return []
        out = []
        for entry in sorted(root.glob("*.json")):
            try:
                data = json.loads(entry.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            try:
                meta = DisplayMetadata.from_dict(data)
            except (KeyError, TypeError):
                continue
            out.append(meta.to_dict())
        return out

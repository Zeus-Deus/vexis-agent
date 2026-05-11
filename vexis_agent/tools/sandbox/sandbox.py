"""Per-task Docker sandbox.

A :class:`Sandbox` is a wrapper around a single named Docker container
that lives for the lifetime of one task. The whole point is:

* every ``exec`` call runs inside the *same* container, so filesystem
  state and side-effects (installed apt packages, background processes,
  cached build artifacts) persist across calls,
* tasks are isolated from each other — different ``task_id`` → different
  container → no cross-talk,
* the host stays clean: builds happen inside Docker, not on the host's
  Python or system package set.

Design notes:

* **Lazy start.** ``exec`` will auto-start the container with defaults
  if it isn't running, mirroring the plan's "started lazily on first
  exec." Explicit ``start()`` is for callers that need a non-default
  image or extra mounts.
* **Idempotent start.** Calling ``start()`` on a running sandbox is a
  no-op, not an error. Re-starting with a *different* image raises
  :class:`SandboxAlreadyRunning` (the existing container wins; caller
  must ``stop()`` first to change image).
* **Metadata on disk.** Each sandbox writes a tiny JSON file at
  ``$XDG_STATE_HOME/vexis-agent/sandboxes/<task-id>/metadata.json`` so
  the CLI's ``list`` subcommand can describe sandboxes without parsing
  ``docker inspect`` output. ``stop()`` deletes this file but
  intentionally leaves the per-task scratch directory in place so the
  user can inspect outputs after the task is gone (matches the plan).
* **Names.** Container names look like ``vexis-sb-<task-id>``. The
  prefix is what ``list_all`` filters on so we never touch unrelated
  containers on the host.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .backend import BackendError, DockerBackend, ExecResult


# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

CONTAINER_PREFIX = "vexis-sb-"
# Default image is intentionally minimal. Build tasks override per-task; the
# default keeps cold-start tiny for "test that exec works at all" callers.
DEFAULT_IMAGE = "debian:bookworm-slim"
DEFAULT_WORKSPACE_HOST = "vexis-workspace"  # relative to $HOME
DEFAULT_WORKSPACE_CONTAINER = "/workspace"
DEFAULT_SCRATCH_HOST_ROOT = "/tmp/vexis-sandbox"
DEFAULT_SCRATCH_CONTAINER = "/scratch"

# Same regex vexis-bg uses for task names; sandboxes share its namespace.
# (3-30 chars, lowercase letters/digits/hyphens, must start with a letter.)
TASK_ID_RE = re.compile(r"^[a-z][a-z0-9-]{2,29}$")


# ----------------------------------------------------------------------------
# Errors
# ----------------------------------------------------------------------------


class SandboxError(RuntimeError):
    """Base for typed sandbox failures."""


class SandboxNotFound(SandboxError):
    """Raised when ``exec``/``cp``/``stop`` is called on a sandbox that
    has never been started (or has already been stopped)."""


class SandboxAlreadyRunning(SandboxError):
    """Raised when ``start`` is asked to re-start with a different image
    than the running container's. Caller must ``stop`` first."""


class SandboxStartFailed(SandboxError):
    """Raised when ``docker run`` exits non-zero. Carries the captured
    stderr for diagnostics."""


class InvalidTaskId(SandboxError):
    """Raised when the task-id doesn't match :data:`TASK_ID_RE`."""


# ----------------------------------------------------------------------------
# Path helpers (exported for the CLI + verify module)
# ----------------------------------------------------------------------------


def _state_root() -> Path:
    """Mirror :func:`vexis_agent.core.paths.state_dir` without importing it.

    We can't import :mod:`vexis_agent.core.paths` here because the sandbox
    module is reachable from the standalone CLI before the daemon's core
    package is initialised on some installs. So we replicate the lookup.
    """
    raw = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(raw) / "vexis-agent"


def state_dir_for(task_id: str) -> Path:
    """Where the sandbox's metadata.json lives."""
    return _state_root() / "sandboxes" / task_id


def scratch_dir_for(task_id: str) -> Path:
    """Host-side scratch dir mounted at ``/scratch`` inside the container."""
    return Path(DEFAULT_SCRATCH_HOST_ROOT) / task_id / "scratch"


def container_name_for(task_id: str) -> str:
    return f"{CONTAINER_PREFIX}{task_id}"


def default_image() -> str:
    """Override via ``VEXIS_SANDBOX_DEFAULT_IMAGE`` for users who keep a
    customised base image preloaded on the host."""
    return os.environ.get("VEXIS_SANDBOX_DEFAULT_IMAGE") or DEFAULT_IMAGE


def default_workspace_mount() -> str:
    """Default workspace mount as a ``host:container`` string."""
    host = os.environ.get("VEXIS_WORKSPACE") or str(
        Path.home() / DEFAULT_WORKSPACE_HOST
    )
    return f"{host}:{DEFAULT_WORKSPACE_CONTAINER}"


# ----------------------------------------------------------------------------
# Metadata persisted to disk so ``list`` doesn't need ``docker inspect``
# ----------------------------------------------------------------------------


@dataclass
class SandboxMetadata:
    task_id: str
    container: str
    image: str
    mounts: list[str]
    created_at: str
    workdir: str = DEFAULT_WORKSPACE_CONTAINER

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "container": self.container,
            "image": self.image,
            "mounts": self.mounts,
            "created_at": self.created_at,
            "workdir": self.workdir,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SandboxMetadata":
        return cls(
            task_id=data["task_id"],
            container=data["container"],
            image=data["image"],
            mounts=list(data.get("mounts") or []),
            created_at=data.get("created_at") or _utc_now_iso(),
            workdir=data.get("workdir") or DEFAULT_WORKSPACE_CONTAINER,
        )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_task_id(task_id: str) -> None:
    if not isinstance(task_id, str) or not TASK_ID_RE.match(task_id):
        raise InvalidTaskId(
            f"Invalid task-id {task_id!r}. Use 3-30 chars: lowercase "
            "letters, digits, hyphens; must start with a letter."
        )


def _parse_mount(spec: str) -> tuple[str, str]:
    """Parse a ``host:container`` string. Robust enough for our needs
    (no ``:ro`` flag yet — easy to add later)."""
    if ":" not in spec:
        raise SandboxError(
            f"Invalid mount {spec!r}: expected 'host:container' form."
        )
    host, container = spec.split(":", 1)
    host = host.strip()
    container = container.strip()
    if not host or not container:
        raise SandboxError(
            f"Invalid mount {spec!r}: host and container paths must both be non-empty."
        )
    return host, container


# ----------------------------------------------------------------------------
# Sandbox
# ----------------------------------------------------------------------------


def _default_backend() -> DockerBackend:
    """Indirection so tests can monkeypatch ``DockerBackend`` in this
    module and have the substitution take effect — a bare
    ``default_factory=DockerBackend`` would freeze the class reference at
    Sandbox definition time, before any test fixture runs."""
    return DockerBackend()


@dataclass
class Sandbox:
    """One sandbox = one Docker container = one task-id.

    Construction is cheap and side-effect-free. ``start`` and ``exec`` do
    the actual work; both can be safely called from a CLI that wraps the
    object in a try/except per command.
    """

    task_id: str
    backend: DockerBackend = field(default_factory=_default_backend)

    def __post_init__(self) -> None:
        _validate_task_id(self.task_id)

    # ----- properties ---------------------------------------------------

    @property
    def container_name(self) -> str:
        return container_name_for(self.task_id)

    @property
    def metadata_path(self) -> Path:
        return state_dir_for(self.task_id) / "metadata.json"

    # ----- lifecycle ----------------------------------------------------

    def is_running(self) -> bool:
        """Return True iff the container exists AND is in 'running' state.

        We deliberately use ``docker ps --filter`` rather than ``inspect``
        because ``inspect`` errors out on missing containers; the ``ps``
        form just returns an empty string, which is half the lines of
        error handling.
        """
        res = self.backend.run(
            [
                "ps",
                "--filter",
                f"name=^{self.container_name}$",
                "--filter",
                "status=running",
                "--format",
                "{{.Names}}",
            ]
        )
        if not res.ok:
            raise BackendError(f"docker ps failed: {res.stderr.strip()}")
        return self.container_name in res.stdout.split()

    def exists(self) -> bool:
        """True iff a container with our name exists in any state."""
        res = self.backend.run(
            [
                "ps",
                "-a",
                "--filter",
                f"name=^{self.container_name}$",
                "--format",
                "{{.Names}}",
            ]
        )
        if not res.ok:
            raise BackendError(f"docker ps -a failed: {res.stderr.strip()}")
        return self.container_name in res.stdout.split()

    def start(
        self,
        *,
        image: str | None = None,
        mounts: Iterable[str] | None = None,
        workdir: str = DEFAULT_WORKSPACE_CONTAINER,
        env: dict[str, str] | None = None,
    ) -> SandboxMetadata:
        """Idempotently start the container.

        If it's already running, the existing metadata is returned and
        the requested image must match (or be ``None``). A request for a
        *different* image on a running sandbox raises
        :class:`SandboxAlreadyRunning` — the caller must ``stop`` first.
        """
        # If a metadata file already exists, the previous run claims a
        # sandbox under this task-id. We honour it if the container is
        # actually running; otherwise we treat the metadata as stale and
        # overwrite.
        existing = self._read_metadata_safe()
        if existing is not None and self.is_running():
            if image and image != existing.image:
                raise SandboxAlreadyRunning(
                    f"Sandbox {self.task_id!r} is already running with image "
                    f"{existing.image!r}; refusing to switch to {image!r}. "
                    "Stop the sandbox first."
                )
            return existing

        # Clean up any stopped-but-still-present container under our name
        # so ``docker run`` doesn't collide.
        if self.exists():
            rm = self.backend.run(["rm", "-f", self.container_name])
            if not rm.ok:
                raise SandboxStartFailed(
                    f"Couldn't remove stale container {self.container_name!r}: "
                    f"{rm.stderr.strip()}"
                )

        resolved_image = image or default_image()
        mount_specs = list(mounts) if mounts is not None else [default_workspace_mount()]
        # Always tack on the per-task scratch dir; this is the contract
        # documented in the plan ("/tmp/vexis-sandbox/<task-id>/scratch
        # for outputs"). We mkdir on the host because Docker would
        # otherwise create it as root-owned.
        scratch_host = scratch_dir_for(self.task_id)
        scratch_host.mkdir(parents=True, exist_ok=True)
        mount_specs.append(f"{scratch_host}:{DEFAULT_SCRATCH_CONTAINER}")

        argv: list[str] = [
            "run",
            "-d",
            "--name",
            self.container_name,
            "--label",
            "vexis-sandbox=1",
            "--label",
            f"vexis-task-id={self.task_id}",
            "--workdir",
            workdir,
        ]
        for spec in mount_specs:
            host, container = _parse_mount(spec)
            # Make sure host paths exist so docker doesn't auto-create
            # them as root. Skip for paths that obviously aren't local
            # dirs (anything starting with a volume name pattern).
            host_path = Path(host)
            if host.startswith("/") or host.startswith(os.path.expanduser("~")):
                host_path.mkdir(parents=True, exist_ok=True)
            argv.extend(["-v", f"{host}:{container}"])
        if env:
            for key, val in env.items():
                argv.extend(["-e", f"{key}={val}"])
        argv.extend([resolved_image, "sleep", "infinity"])

        run = self.backend.run(argv)
        if not run.ok:
            raise SandboxStartFailed(
                f"docker run failed for sandbox {self.task_id!r}: {run.stderr.strip()}"
            )

        meta = SandboxMetadata(
            task_id=self.task_id,
            container=self.container_name,
            image=resolved_image,
            mounts=mount_specs,
            created_at=_utc_now_iso(),
            workdir=workdir,
        )
        self._write_metadata(meta)
        return meta

    def stop(self, *, remove_metadata: bool = True) -> bool:
        """Stop and remove the container. Returns False if it didn't
        exist (idempotent stop is intentional — re-running stop should
        not be an error)."""
        if not self.exists():
            if remove_metadata:
                self._remove_metadata()
            return False
        rm = self.backend.run(["rm", "-f", self.container_name])
        if not rm.ok:
            raise SandboxError(
                f"docker rm -f failed for {self.container_name!r}: {rm.stderr.strip()}"
            )
        if remove_metadata:
            self._remove_metadata()
        return True

    # ----- exec / cp ----------------------------------------------------

    def exec(
        self,
        cmd: list[str] | str,
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
        auto_start: bool = True,
    ) -> ExecResult:
        """Run a command inside the sandbox.

        ``cmd`` may be a list (preferred) or a string; a string is fed to
        ``sh -c`` so users can pass pipes/redirects naturally.

        If the container isn't running and ``auto_start=True`` (default),
        we lazily start it with defaults. Set ``auto_start=False`` from
        contexts that need an explicit error instead of silent boot.
        """
        if not self.is_running():
            if not auto_start:
                raise SandboxNotFound(
                    f"Sandbox {self.task_id!r} is not running. Run "
                    f"`vexis-sandbox start {self.task_id}` first."
                )
            self.start()

        argv: list[str] = ["exec"]
        if cwd:
            argv.extend(["--workdir", cwd])
        if env:
            for key, val in env.items():
                argv.extend(["-e", f"{key}={val}"])
        argv.append(self.container_name)

        if isinstance(cmd, str):
            argv.extend(["sh", "-c", cmd])
        else:
            argv.extend(cmd)

        return self.backend.run(argv, timeout=timeout)

    def cp_to(self, local_src: str, container_dst: str) -> ExecResult:
        """Copy host file → container path. Lazy-start applies."""
        if not self.is_running():
            self.start()
        return self.backend.run(
            ["cp", str(local_src), f"{self.container_name}:{container_dst}"]
        )

    def cp_from(self, container_src: str, local_dst: str) -> ExecResult:
        """Copy container path → host file."""
        if not self.is_running():
            raise SandboxNotFound(
                f"Sandbox {self.task_id!r} is not running; nothing to copy from."
            )
        return self.backend.run(
            ["cp", f"{self.container_name}:{container_src}", str(local_dst)]
        )

    # ----- metadata persistence ----------------------------------------

    def _write_metadata(self, meta: SandboxMetadata) -> None:
        path = self.metadata_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta.to_dict(), indent=2))
        tmp.replace(path)

    def _read_metadata_safe(self) -> SandboxMetadata | None:
        path = self.metadata_path
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        try:
            return SandboxMetadata.from_dict(data)
        except (KeyError, TypeError):
            return None

    def _remove_metadata(self) -> None:
        try:
            self.metadata_path.unlink()
        except FileNotFoundError:
            pass

    # ----- enumeration --------------------------------------------------

    @classmethod
    def list_all(cls, backend: DockerBackend | None = None) -> list[dict]:
        """Return a summary for every sandbox container on the host.

        Source of truth is ``docker ps -a --filter label=vexis-sandbox=1``
        — we deliberately do NOT just scan the metadata directory because
        a metadata file can outlive its container (crash, manual rm) and
        we want ``list`` to show ground truth.
        """
        be = backend or DockerBackend()
        res = be.run(
            [
                "ps",
                "-a",
                "--filter",
                "label=vexis-sandbox=1",
                "--format",
                "{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.CreatedAt}}",
            ]
        )
        if not res.ok:
            raise BackendError(f"docker ps failed: {res.stderr.strip()}")
        out: list[dict] = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            name, status, image, created_at = parts[0], parts[1], parts[2], parts[3]
            task_id = name[len(CONTAINER_PREFIX):] if name.startswith(CONTAINER_PREFIX) else name
            out.append(
                {
                    "task_id": task_id,
                    "container": name,
                    "image": image,
                    "status": status,
                    "created_at": created_at,
                    "running": status.lower().startswith("up"),
                }
            )
        return out

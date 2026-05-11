"""Per-task Docker sandbox primitives for the build-and-test loop.

Public surface:

* ``Sandbox`` — one container per task-id, persistent state across exec
  calls, idempotent ``start``, JSON-friendly ``exec`` results.
* ``DockerBackend`` — the real ``docker`` CLI wrapper used in
  production. Every shell-out to Docker goes through this class so
  tests can swap in a ``FakeBackend`` without monkeypatching globals.
* ``FakeBackend`` — in-memory stand-in for tests; records calls and
  returns canned responses.
* ``SandboxError`` and subclasses — typed failures the CLI layer maps
  to non-zero exit codes.

Mirrors the import shape of ``vexis_agent.tools.browser`` (``profile.py``,
``session.py``, ``snapshot.py``) so contributors find what they expect.
"""

from .backend import DockerBackend, FakeBackend, ExecResult, BackendError
from .sandbox import (
    Sandbox,
    SandboxError,
    SandboxNotFound,
    SandboxAlreadyRunning,
    SandboxStartFailed,
    container_name_for,
    default_image,
    default_workspace_mount,
    state_dir_for,
)

__all__ = [
    "Sandbox",
    "SandboxError",
    "SandboxNotFound",
    "SandboxAlreadyRunning",
    "SandboxStartFailed",
    "DockerBackend",
    "FakeBackend",
    "ExecResult",
    "BackendError",
    "container_name_for",
    "default_image",
    "default_workspace_mount",
    "state_dir_for",
]

"""Opt-in integration tests for ``vexis-sandbox`` against real Docker.

These exercise the actual end-to-end loop: start a container, run a
command, observe persistent state, stop and verify cleanup. They're
gated behind the ``sandbox_docker`` pytest marker so CI runners without
Docker don't fail; run locally with ``-m sandbox_docker``.

Each test uses a unique task-id (UUID-derived) so concurrent test runs
don't collide on container names.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid

import pytest

from vexis_agent.tools.sandbox import Sandbox, container_name_for


pytestmark = pytest.mark.sandbox_docker


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        subprocess.run(
            ["docker", "info"], check=True, capture_output=True, timeout=5
        )
        return True
    except (subprocess.SubprocessError, OSError):
        return False


if not _docker_available():  # pragma: no cover - environment-gated
    pytest.skip(
        "docker not available — skipping sandbox integration tests",
        allow_module_level=True,
    )


@pytest.fixture
def task_id():
    """Each test gets a unique sandbox name; we tear it down on the way
    out so a failing assertion never leaves a container behind."""
    # ``a`` prefix so the regex (must start with letter) is satisfied.
    tid = "atest-" + uuid.uuid4().hex[:8]
    yield tid
    try:
        Sandbox(tid).stop()
    except Exception:
        # subprocess.run already swallows errors at this layer; if a
        # leak happens, the container has a clear vexis-sb- prefix so
        # the user can spot and remove it.
        pass


def test_state_persists_across_exec(task_id, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("VEXIS_WORKSPACE", str(tmp_path / "ws"))
    sb = Sandbox(task_id)
    sb.start(image="alpine:latest")

    # Write a marker file in /tmp inside the container
    r1 = sb.exec(["sh", "-c", "echo marker > /tmp/persist.txt && cat /tmp/persist.txt"])
    assert r1.ok, r1.stderr
    assert "marker" in r1.stdout

    # Second exec must see the file written by the first exec
    r2 = sb.exec(["cat", "/tmp/persist.txt"])
    assert r2.ok, r2.stderr
    assert r2.stdout.strip() == "marker"


def test_scratch_dir_is_writable_and_visible_on_host(task_id, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("VEXIS_WORKSPACE", str(tmp_path / "ws"))
    # Override scratch root so we don't pollute the real /tmp/vexis-sandbox.
    scratch_root = tmp_path / "scratch-root"
    monkeypatch.setattr(
        "vexis_agent.tools.sandbox.sandbox.DEFAULT_SCRATCH_HOST_ROOT",
        str(scratch_root),
    )

    sb = Sandbox(task_id)
    sb.start(image="alpine:latest")
    r = sb.exec(["sh", "-c", "echo hello > /scratch/out.txt"])
    assert r.ok, r.stderr

    host_file = scratch_root / task_id / "scratch" / "out.txt"
    assert host_file.exists(), "scratch dir mount not visible on host"
    assert host_file.read_text().strip() == "hello"


def test_stop_removes_container(task_id, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("VEXIS_WORKSPACE", str(tmp_path / "ws"))
    sb = Sandbox(task_id)
    sb.start(image="alpine:latest")
    assert sb.is_running()
    assert sb.stop() is True
    assert not sb.is_running()
    # Idempotent: re-stop is fine, just returns False
    assert sb.stop() is False


def test_list_all_shows_running_sandbox(task_id, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("VEXIS_WORKSPACE", str(tmp_path / "ws"))
    sb = Sandbox(task_id)
    sb.start(image="alpine:latest")
    rows = Sandbox.list_all()
    names = {row["container"] for row in rows}
    assert container_name_for(task_id) in names
    by_name = {row["container"]: row for row in rows}
    assert by_name[container_name_for(task_id)]["running"] is True

"""Opt-in integration test for ``vexis-display`` against a real
Docker + Xvfb-equipped sandbox.

Builds against ``vexis-test-xvfb:latest`` (a tiny debian:bookworm-slim
image with ``xvfb`` and ``x11-utils`` installed — see
``tests/fixtures/Dockerfile.test-xvfb`` for the build recipe). Skipped
when the image isn't present, so the suite never fails on CI runners
that haven't pre-built it. Run locally with ``-m display_real``.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid

import pytest

from vexis_agent.tools.display import HeadlessDisplay
from vexis_agent.tools.sandbox import Sandbox


pytestmark = pytest.mark.display_real

_TEST_IMAGE = "vexis-test-xvfb:latest"


def _image_present(image: str) -> bool:
    try:
        out = subprocess.check_output(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
            stderr=subprocess.DEVNULL,
        )
        return bool(out.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


if shutil.which("docker") is None or not _image_present(_TEST_IMAGE):  # pragma: no cover
    pytest.skip(
        f"docker / image {_TEST_IMAGE} not available; skipping display real test",
        allow_module_level=True,
    )


@pytest.fixture
def sandboxed_task(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("VEXIS_WORKSPACE", str(tmp_path / "ws"))
    tid = "adisp-" + uuid.uuid4().hex[:8]
    sb = Sandbox(tid)
    sb.start(image=_TEST_IMAGE)
    yield tid, sb
    sb.stop()


def test_xvfb_starts_and_socket_appears(sandboxed_task):
    tid, sb = sandboxed_task
    display = HeadlessDisplay(tid, sandbox=sb)
    meta = display.start()
    assert meta.backend == "xvfb"
    assert meta.display == ":99"
    # The X socket should now exist
    check = sb.exec(["sh", "-c", "ls -la /tmp/.X11-unix/"])
    assert "X99" in check.stdout, check.stdout
    # And xdpyinfo should be able to reach it
    info = sb.exec(["sh", "-c", "DISPLAY=:99 xdpyinfo | head -5"])
    assert info.ok, info.stderr
    assert "X.Org" in info.stdout or "X server" in info.stdout or "version number" in info.stdout


def test_stop_removes_metadata(sandboxed_task):
    tid, sb = sandboxed_task
    display = HeadlessDisplay(tid, sandbox=sb)
    display.start()
    assert display.stop() is True
    assert display.stop() is False


def test_env_emits_display(sandboxed_task):
    tid, sb = sandboxed_task
    display = HeadlessDisplay(tid, sandbox=sb)
    display.start()
    env = display.env()
    assert env["DISPLAY"] == ":99"

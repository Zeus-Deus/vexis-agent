"""Unit tests for :mod:`vexis_agent.tools.display`.

The display module is generic over anything with a ``Sandbox``-shaped
``exec`` method; tests inject a tiny stub so we can pin the exact
shell script issued to ``Xvfb`` / ``Hyprland`` without needing a real
Docker daemon. A separate ``-m display_real`` integration test covers
the live path against a real Xvfb-equipped sandbox.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vexis_agent.tools.sandbox.backend import ExecResult
from vexis_agent.tools.display import (
    DisplayMetadata,
    DisplayNotFound,
    DisplayStartFailed,
    HeadlessDisplay,
    UnsupportedBackend,
    resolve_backend,
)
from vexis_agent.tools.display.cli import main as cli_main
from vexis_agent.tools.display.display import metadata_path_for


class StubSandbox:
    """Mimics the parts of Sandbox that HeadlessDisplay touches:
    ``exec(cmd, auto_start, timeout)`` returning ``ExecResult``."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.next_results: list[ExecResult] = []
        self.default_result = ExecResult(("docker",), 0, "1234\n", "")

    def exec(self, cmd, *, cwd=None, env=None, timeout=None, auto_start=True):
        self.calls.append((tuple(cmd) if isinstance(cmd, list) else cmd, auto_start, timeout))
        if self.next_results:
            return self.next_results.pop(0)
        return self.default_result


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    yield


# ---------------------------------------------------------------------------
# resolve_backend
# ---------------------------------------------------------------------------


def test_resolve_backend_auto_maps_to_xvfb():
    assert resolve_backend("auto") == "xvfb"
    assert resolve_backend("AUTO") == "xvfb"
    assert resolve_backend("") == "xvfb"


def test_resolve_backend_passthrough():
    assert resolve_backend("xvfb") == "xvfb"
    assert resolve_backend("wayland-headless") == "wayland-headless"


def test_resolve_backend_unknown_raises():
    with pytest.raises(UnsupportedBackend):
        resolve_backend("vnc")


# ---------------------------------------------------------------------------
# HeadlessDisplay.start (xvfb path)
# ---------------------------------------------------------------------------


def test_start_xvfb_issues_expected_script_and_persists_metadata():
    sb = StubSandbox()
    sb.default_result = ExecResult(("docker",), 0, "4242\n", "")
    display = HeadlessDisplay("disp-xvfb", sandbox=sb)
    meta = display.start(resolution="1280x720")
    assert isinstance(meta, DisplayMetadata)
    assert meta.backend == "xvfb"
    assert meta.display == ":99"
    assert meta.pid == 4242
    # The shell script we issued must include Xvfb + the right resolution
    issued = sb.calls[0][0]
    assert issued[0] == "sh" and issued[1] == "-c"
    script = issued[2]
    assert "Xvfb :99" in script and "1280x720x24" in script
    # Metadata persisted on disk
    on_disk = json.loads(metadata_path_for("disp-xvfb").read_text())
    assert on_disk["backend"] == "xvfb"
    assert on_disk["pid"] == 4242


def test_start_xvfb_failure_surfaces_stderr():
    sb = StubSandbox()
    sb.next_results = [
        ExecResult(("docker",), 1, "", "Xvfb: cannot bind socket\n"),
    ]
    display = HeadlessDisplay("disp-fail", sandbox=sb)
    with pytest.raises(DisplayStartFailed) as exc:
        display.start()
    assert "Xvfb: cannot bind socket" in str(exc.value)


def test_start_is_idempotent_when_already_running():
    sb = StubSandbox()
    sb.next_results = [
        ExecResult(("docker",), 0, "1234\n", ""),  # initial start → PID 1234
        ExecResult(("docker",), 0, "alive\n", ""),  # liveness check on second start
    ]
    display = HeadlessDisplay("idem-disp", sandbox=sb)
    first = display.start()
    second = display.start()
    assert first.pid == second.pid
    # Only one Xvfb shell-out — the second start short-circuited on the
    # liveness probe.
    xvfb_calls = [c for c in sb.calls if "Xvfb" in c[0][2]]
    assert len(xvfb_calls) == 1


# ---------------------------------------------------------------------------
# stop / env / list
# ---------------------------------------------------------------------------


def test_stop_kills_the_process_and_removes_metadata():
    sb = StubSandbox()
    sb.default_result = ExecResult(("docker",), 0, "8765\n", "")
    display = HeadlessDisplay("disp-stop", sandbox=sb)
    display.start()
    assert metadata_path_for("disp-stop").exists()
    assert display.stop() is True
    # A `kill 8765` should have gone through
    kill_calls = [c for c in sb.calls if "kill 8765" in c[0][2] if isinstance(c[0], tuple)]
    assert kill_calls
    assert not metadata_path_for("disp-stop").exists()


def test_stop_idempotent_when_no_display():
    display = HeadlessDisplay("never-started", sandbox=StubSandbox())
    assert display.stop() is False


def test_env_returns_display_var():
    sb = StubSandbox()
    sb.default_result = ExecResult(("docker",), 0, "5555\n", "")
    display = HeadlessDisplay("disp-env", sandbox=sb)
    display.start()
    env = display.env()
    assert env["DISPLAY"] == ":99"
    assert "WAYLAND_DISPLAY" not in env


def test_env_missing_raises():
    display = HeadlessDisplay("nothing-here", sandbox=StubSandbox())
    with pytest.raises(DisplayNotFound):
        display.env()


def test_list_all_returns_persisted_displays(tmp_path):
    sb = StubSandbox()
    sb.default_result = ExecResult(("docker",), 0, "1\n", "")
    HeadlessDisplay("ls-one", sandbox=sb).start()
    HeadlessDisplay("ls-two", sandbox=sb).start()
    rows = HeadlessDisplay.list_all()
    task_ids = {r["task_id"] for r in rows}
    assert {"ls-one", "ls-two"} <= task_ids


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_start_emits_payload(monkeypatch, capsys):
    sb = StubSandbox()
    sb.default_result = ExecResult(("docker",), 0, "777\n", "")
    monkeypatch.setattr(
        "vexis_agent.tools.display.cli.HeadlessDisplay",
        lambda task_id: HeadlessDisplay(task_id, sandbox=sb),
    )
    rc = cli_main(["start", "cli-disp"])
    out = json.loads(capsys.readouterr().out.strip())
    assert rc == 0 and out["ok"] is True
    assert out["result"]["display"] == ":99"
    assert out["result"]["pid"] == 777


def test_cli_env_shell_form_prints_exports(monkeypatch, capsys):
    sb = StubSandbox()
    sb.default_result = ExecResult(("docker",), 0, "1\n", "")
    HeadlessDisplay("cli-env", sandbox=sb).start()
    monkeypatch.setattr(
        "vexis_agent.tools.display.cli.HeadlessDisplay",
        lambda task_id: HeadlessDisplay(task_id, sandbox=sb),
    )
    rc = cli_main(["env", "cli-env", "--shell"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "export DISPLAY=:99" in out


def test_cli_env_missing_exits_two(monkeypatch, capsys):
    sb = StubSandbox()
    monkeypatch.setattr(
        "vexis_agent.tools.display.cli.HeadlessDisplay",
        lambda task_id: HeadlessDisplay(task_id, sandbox=sb),
    )
    rc = cli_main(["env", "absent"])
    out = json.loads(capsys.readouterr().out.strip())
    assert rc == 2
    assert out["ok"] is False


def test_cli_start_unknown_backend_exits_two(monkeypatch, capsys):
    # argparse exits via SystemExit(2) when ``choices`` rejects the
    # value; we wrap rather than waiting for the parser to be removed.
    with pytest.raises(SystemExit) as exc:
        cli_main(["start", "x", "--backend", "vnc"])
    assert exc.value.code == 2

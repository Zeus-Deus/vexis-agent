"""Unit tests for :mod:`vexis_agent.tools.ui`.

These pin the host-side coordinator: argv composition for each action,
JSON-envelope parsing of runner output, error-mapping when the runner
returns ``ok: false`` or no output at all.

The runner source is exercised in a Python-level smoke test
(``test_runner_source_compiles``) that just ``compile()``s the embedded
string; full runtime coverage of the AT-SPI walker requires a live bus
and lives in the ``-m ui_real`` integration suite.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from vexis_agent.tools.sandbox.backend import ExecResult
from vexis_agent.tools.ui import (
    ATSPIError,
    SnapshotResult,
    UIAction,
    UIDriver,
    build_action_argv,
)
from vexis_agent.tools.ui.cli import main as cli_main
from vexis_agent.tools.ui.runner_src import RUNNER_SOURCE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class StubSandbox:
    """Same shape as the display tests' stub. Records calls and
    returns canned ExecResults."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.next_results: list[ExecResult] = []

    def exec(self, cmd, *, cwd=None, env=None, timeout=None, auto_start=True):
        self.calls.append(
            (
                tuple(cmd) if isinstance(cmd, list) else cmd,
                env,
                auto_start,
                timeout,
            )
        )
        if self.next_results:
            return self.next_results.pop(0)
        return ExecResult(("docker",), 0, "{\"ok\": true, \"result\": {}}\n", "")


class StubDisplay:
    """Hands back a fake DISPLAY env so the driver's _env() returns
    deterministic values for argv assertions."""

    def __init__(self, env_dict=None):
        self._env = env_dict if env_dict is not None else {"DISPLAY": ":99"}

    def env(self):
        return self._env


def _driver(env=None) -> tuple[UIDriver, StubSandbox]:
    sb = StubSandbox()
    drv = UIDriver(
        task_id="ui-test",
        sandbox=sb,
        display=StubDisplay(env),  # type: ignore[arg-type]
    )
    return drv, sb


# ---------------------------------------------------------------------------
# Runner source smoke
# ---------------------------------------------------------------------------


def test_runner_source_compiles():
    # Catch syntax regressions; we can't run it without pyatspi but
    # compile() proves the string is valid Python.
    compile(RUNNER_SOURCE, "<runner>", "exec")


# ---------------------------------------------------------------------------
# argv composition
# ---------------------------------------------------------------------------


def test_build_action_argv_snapshot():
    argv = build_action_argv(UIAction.SNAPSHOT, {})
    assert argv[0] == "python3"
    assert argv[1] == "-c"
    # Last two args are subcommand + json payload
    assert argv[-2] == "snapshot"
    assert json.loads(argv[-1]) == {}


def test_build_action_argv_click_payload():
    argv = build_action_argv(UIAction.CLICK, {"index": 3})
    assert argv[-2] == "click"
    assert json.loads(argv[-1]) == {"index": 3}


# ---------------------------------------------------------------------------
# Driver.snapshot
# ---------------------------------------------------------------------------


def test_driver_snapshot_parses_envelope():
    drv, sb = _driver()
    payload = json.dumps(
        {
            "ok": True,
            "result": {
                "snapshot": "[0]<button label=\"Save\" />",
                "element_count": 1,
                "stale": False,
                "hint": "",
            },
        }
    )
    sb.next_results = [ExecResult(("docker",), 0, payload + "\n", "")]
    snap = drv.snapshot()
    assert isinstance(snap, SnapshotResult)
    assert snap.element_count == 1
    assert "Save" in snap.snapshot


def test_driver_snapshot_uses_display_env():
    drv, sb = _driver(env={"DISPLAY": ":77", "WAYLAND_DISPLAY": "wayland-77"})
    payload = json.dumps(
        {"ok": True, "result": {"snapshot": "", "element_count": 0, "stale": True}}
    )
    sb.next_results = [ExecResult(("docker",), 0, payload + "\n", "")]
    drv.snapshot()
    # The env passed through to Sandbox.exec includes the display vars
    env = sb.calls[0][1]
    assert env["DISPLAY"] == ":77"
    assert env["WAYLAND_DISPLAY"] == "wayland-77"


def test_driver_snapshot_runner_failure_raises():
    drv, sb = _driver()
    payload = json.dumps({"ok": False, "error": "pyatspi not available"})
    sb.next_results = [ExecResult(("docker",), 1, payload + "\n", "")]
    with pytest.raises(ATSPIError) as exc:
        drv.snapshot()
    assert "pyatspi" in str(exc.value)


def test_driver_snapshot_empty_stdout_raises():
    drv, sb = _driver()
    sb.next_results = [ExecResult(("docker",), 1, "", "boom\n")]
    with pytest.raises(ATSPIError) as exc:
        drv.snapshot()
    assert "boom" in str(exc.value)


def test_driver_snapshot_garbage_output_raises():
    drv, sb = _driver()
    sb.next_results = [ExecResult(("docker",), 0, "not json at all\n", "")]
    with pytest.raises(ATSPIError):
        drv.snapshot()


# ---------------------------------------------------------------------------
# Click / type / press / focus
# ---------------------------------------------------------------------------


def test_driver_click_passes_index_payload():
    drv, sb = _driver()
    payload = json.dumps({"ok": True, "result": {"clicked": 5}})
    sb.next_results = [ExecResult(("docker",), 0, payload + "\n", "")]
    out = drv.click(5)
    assert out == {"clicked": 5}
    # argv[-1] should be the JSON args
    argv = sb.calls[0][0]
    assert json.loads(argv[-1]) == {"index": 5}


def test_driver_type_payload():
    drv, sb = _driver()
    payload = json.dumps({"ok": True, "result": {"typed": 2, "text": "hi"}})
    sb.next_results = [ExecResult(("docker",), 0, payload + "\n", "")]
    drv.type_text(2, "hi")
    argv = sb.calls[0][0]
    assert json.loads(argv[-1]) == {"index": 2, "text": "hi"}


def test_driver_press_chord():
    drv, sb = _driver()
    payload = json.dumps({"ok": True, "result": {"pressed": "Return"}})
    sb.next_results = [ExecResult(("docker",), 0, payload + "\n", "")]
    out = drv.press("Return")
    assert out["pressed"] == "Return"


def test_driver_vision_snapshot_default_path():
    drv, sb = _driver()
    payload = json.dumps(
        {"ok": True, "result": {"path": "/tmp/vexis-ui-snapshot.png", "via": "x11-import"}}
    )
    sb.next_results = [ExecResult(("docker",), 0, payload + "\n", "")]
    out = drv.vision_snapshot()
    assert out["path"].endswith(".png")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_snapshot_returns_dsl(monkeypatch, capsys):
    def fake_driver(task_id: str) -> UIDriver:
        sb = StubSandbox()
        payload = json.dumps(
            {
                "ok": True,
                "result": {
                    "snapshot": "[0]<button label=\"OK\" />",
                    "element_count": 1,
                    "stale": False,
                    "hint": "",
                },
            }
        )
        sb.next_results = [ExecResult(("docker",), 0, payload + "\n", "")]
        return UIDriver(task_id=task_id, sandbox=sb, display=StubDisplay())

    monkeypatch.setattr("vexis_agent.tools.ui.cli._driver", fake_driver)
    rc = cli_main(["snapshot", "ui-test"])
    out = json.loads(capsys.readouterr().out.strip())
    assert rc == 0 and out["ok"] is True
    assert "OK" in out["result"]["snapshot"]


def test_cli_click_propagates_error(monkeypatch, capsys):
    def fake_driver(task_id: str) -> UIDriver:
        sb = StubSandbox()
        sb.next_results = [
            ExecResult(
                ("docker",),
                1,
                json.dumps({"ok": False, "error": "index 99 not present"}) + "\n",
                "",
            )
        ]
        return UIDriver(task_id=task_id, sandbox=sb, display=StubDisplay())

    monkeypatch.setattr("vexis_agent.tools.ui.cli._driver", fake_driver)
    rc = cli_main(["click", "ui-test", "99"])
    out = json.loads(capsys.readouterr().out.strip())
    assert rc == 1
    assert out["ok"] is False
    assert "index 99" in out["error"]

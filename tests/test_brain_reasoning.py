"""Brain.spawn_aux reasoning_level + context_window passthrough.

Pin that each shipping brain implementation translates
``reasoning_level`` to its native CLI flag (``--effort`` on
claude-code, ``--variant`` on opencode) and that the inert
``context_window`` kwarg is accepted without affecting the argv
(no CLI flag exists on either brain — see Brain.spawn_aux's
docstring).

Mocks ``subprocess.run`` and inspects the captured argv. Same
infrastructure shape as ``tests/test_brain_model_not_found.py``.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vexis_agent.core.brain.claude_code import ClaudeCodeBrain
from vexis_agent.core.brain.opencode import OpenCodeBrain
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore


@pytest.fixture(autouse=True)
def _isolated_yaml_config(monkeypatch, tmp_path):
    from vexis_agent.core import yaml_config
    cfg_dir = tmp_path / "vexis-config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        yaml_config, "_config_path", lambda: cfg_dir / "config.yaml"
    )


@pytest.fixture
def cc_brain(tmp_path: Path) -> ClaudeCodeBrain:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ClaudeCodeBrain(
        workspace=ws,
        session=SessionStore(tmp_path / "cc-sessions.json"),
        running_tasks=RunningTasks(),
    )


@pytest.fixture
def oc_brain(tmp_path: Path) -> OpenCodeBrain:
    ws = tmp_path / "ws"
    ws.mkdir()
    return OpenCodeBrain(
        workspace=ws,
        session=SessionStore(tmp_path / "oc-sessions.json"),
        running_tasks=RunningTasks(),
    )


def _ok_completed_process() -> MagicMock:
    """Mimic subprocess.CompletedProcess. claude-code's spawn_aux
    consumes stdout as text; opencode's consumes it as bytes. We
    can't tell ahead of time which brain's calling — return text
    by default, individual tests override stdout if they want
    bytes."""
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.returncode = 0
    cp.stdout = "ok"
    cp.stderr = ""
    return cp


def _ok_bytes_completed_process() -> MagicMock:
    """opencode reads stdout as bytes (then .decode'd). Tests
    against opencode brain need bytes here or `.decode` raises."""
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.returncode = 0
    cp.stdout = b'{"type":"finished","reason":"end_turn","content":"ok"}\n'
    cp.stderr = b""
    return cp


# ──────────────────────────────────────────────────────────────────
# claude-code: --effort flag
# ──────────────────────────────────────────────────────────────────


def test_cc_passes_reasoning_level_via_effort_flag(cc_brain):
    """``reasoning_level="high"`` translates to ``--effort high``
    in the spawned argv. No flag when reasoning_level is None."""
    captured: list[list[str]] = []

    def _spy(argv, **_kw):
        captured.append(list(argv))
        return _ok_bytes_completed_process()

    with patch("subprocess.run", side_effect=_spy):
        asyncio.run(cc_brain.spawn_aux(
            "test prompt",
            reasoning_level="high",
        ))
    assert captured, "subprocess.run was not called"
    argv = captured[0]
    assert "--effort" in argv
    idx = argv.index("--effort")
    assert argv[idx + 1] == "high"


def test_cc_no_effort_flag_when_reasoning_level_none(cc_brain):
    """No reasoning_level → no --effort flag (brain picks default)."""
    captured: list[list[str]] = []
    with patch(
        "subprocess.run",
        side_effect=lambda argv, **_kw: (
            captured.append(list(argv)) or _ok_bytes_completed_process()
        ),
    ):
        asyncio.run(cc_brain.spawn_aux("test prompt"))
    assert "--effort" not in captured[0]


def test_cc_context_window_is_inert(cc_brain):
    """``context_window`` is accepted for ABC stability but the
    claude CLI has no runtime context flag — argv must be
    unchanged from the no-context-window case."""
    captured: list[list[str]] = []
    with patch(
        "subprocess.run",
        side_effect=lambda argv, **_kw: (
            captured.append(list(argv)) or _ok_bytes_completed_process()
        ),
    ):
        asyncio.run(cc_brain.spawn_aux(
            "test prompt", context_window=1000000,
        ))
    argv = captured[0]
    # No surprise flags introduced. Probe specifically: no --context,
    # --max-input, --max-tokens-input, etc.
    for flag in ("--context", "--max-input", "--max-input-tokens",
                 "--context-window"):
        assert flag not in argv, f"unexpected flag {flag} in argv"


def test_cc_reasoning_and_model_compose(cc_brain):
    """``reasoning_level`` works alongside an explicit
    ``model_tier``. Both flags appear in the argv; --effort
    after --model is fine for claude-code (order doesn't matter)."""
    captured: list[list[str]] = []
    with patch(
        "subprocess.run",
        side_effect=lambda argv, **_kw: (
            captured.append(list(argv)) or _ok_bytes_completed_process()
        ),
    ):
        asyncio.run(cc_brain.spawn_aux(
            "test prompt",
            model_tier="claude-opus-4-7",  # raw model name passes through
            reasoning_level="max",
        ))
    argv = captured[0]
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "claude-opus-4-7"
    assert "--effort" in argv
    assert argv[argv.index("--effort") + 1] == "max"


# ──────────────────────────────────────────────────────────────────
# opencode: --variant flag
# ──────────────────────────────────────────────────────────────────


def test_oc_passes_reasoning_level_via_variant_flag(oc_brain):
    """``reasoning_level="high"`` translates to ``--variant high``
    in the spawned argv."""
    captured: list[list[str]] = []
    with patch(
        "subprocess.run",
        side_effect=lambda argv, **_kw: (
            captured.append(list(argv)) or _ok_bytes_completed_process()
        ),
    ):
        asyncio.run(oc_brain.spawn_aux(
            "test prompt", reasoning_level="high",
        ))
    argv = captured[0]
    assert "--variant" in argv
    assert argv[argv.index("--variant") + 1] == "high"


def test_oc_no_variant_flag_when_reasoning_level_none(oc_brain):
    """No reasoning_level → no --variant flag."""
    captured: list[list[str]] = []
    with patch(
        "subprocess.run",
        side_effect=lambda argv, **_kw: (
            captured.append(list(argv)) or _ok_bytes_completed_process()
        ),
    ):
        asyncio.run(oc_brain.spawn_aux("test prompt"))
    assert "--variant" not in captured[0]


def test_oc_context_window_is_inert(oc_brain):
    """opencode CLI also has no runtime context flag."""
    captured: list[list[str]] = []
    with patch(
        "subprocess.run",
        side_effect=lambda argv, **_kw: (
            captured.append(list(argv)) or _ok_bytes_completed_process()
        ),
    ):
        asyncio.run(oc_brain.spawn_aux(
            "test prompt", context_window=1000000,
        ))
    argv = captured[0]
    for flag in ("--context", "--context-window", "--max-context"):
        assert flag not in argv, f"unexpected flag {flag} in argv"


# ──────────────────────────────────────────────────────────────────
# BrainNull: kwargs accepted + recorded
# ──────────────────────────────────────────────────────────────────


def test_null_records_reasoning_and_context_kwargs():
    """BrainNull is the test fake; it must accept the new kwargs
    AND record them so cross-brain contract tests can assert
    plumbing without spinning up real subprocesses."""
    from vexis_agent.core.brain.null import BrainNull
    from vexis_agent.core.brain.base import AuxResult
    null = BrainNull(
        aux_results=[AuxResult(stdout="", stderr="", returncode=0)],
    )
    asyncio.run(null.spawn_aux(
        "test prompt",
        model_tier="small",
        reasoning_level="medium",
        context_window=200000,
        subsystem="curator",
    ))
    rec = null.aux_call_records()[0]
    assert rec["reasoning_level"] == "medium"
    assert rec["context_window"] == 200000

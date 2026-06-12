"""Per-subagent memory-scope wrapper (2026-06-12 freeze fix).

Background: a single ``claude -p`` subagent ran a pathological grep
that ballooned to 2.4 GiB. Because the bot and everything it spawned
shared one systemd cgroup, the runaway pushed the cgroup over its
``MemoryHigh`` and the kernel throttled *every* task in it — including
the bot's own loop — into uninterruptible ``D`` sleep. The bot stopped
answering Telegram without crashing.

``wrap_with_memory_scope`` runs each spawn inside its own memory-capped
``systemd-run --scope`` so a runaway OOM-kills in isolation. These
tests pin the wrapper's structure, its two graceful-degradation paths,
the drift guard between the wrapper's slice name and the shipped slice
unit, and that all three brain spawn sites apply it.
"""

from __future__ import annotations

import inspect

import pytest

from vexis_agent.core.brain import _memory_scope
from vexis_agent.core.brain._memory_scope import VEXIS_SLICE, wrap_with_memory_scope

ORIG = ["claude", "-p", "hello"]


@pytest.fixture
def systemd_run_present(monkeypatch):
    """Pretend ``systemd-run`` is on PATH regardless of the host."""
    monkeypatch.setattr(
        _memory_scope.shutil,
        "which",
        lambda name: "/usr/bin/systemd-run" if name == "systemd-run" else None,
    )


def _enable(monkeypatch, cap="2G"):
    monkeypatch.setattr(
        _memory_scope, "brain_subprocess_memory_max", lambda: cap
    )


def test_wraps_when_enabled_and_systemd_run_present(monkeypatch, systemd_run_present):
    _enable(monkeypatch, "2G")
    out = wrap_with_memory_scope(ORIG)

    assert out[0] == "systemd-run"
    assert "--user" in out and "--scope" in out and "--collect" in out
    # Placed under the shared slice so the aggregate cap applies.
    i = out.index("--slice")
    assert out[i + 1] == VEXIS_SLICE
    # Carries the configured RSS cap + a swap cushion.
    assert "MemoryMax=2G" in out
    assert any(a.startswith("MemorySwapMax=") for a in out)
    # Original argv preserved verbatim after the ``--`` terminator.
    sep = out.index("--")
    assert out[sep + 1 :] == ORIG


def test_cap_value_is_passed_through(monkeypatch, systemd_run_present):
    _enable(monkeypatch, "1500M")
    assert "MemoryMax=1500M" in wrap_with_memory_scope(ORIG)


def test_noop_when_disabled(monkeypatch, systemd_run_present):
    """``brain.subprocess_memory_max: none`` → no scoping at all."""
    monkeypatch.setattr(
        _memory_scope, "brain_subprocess_memory_max", lambda: None
    )
    assert wrap_with_memory_scope(ORIG) == ORIG


def test_noop_when_systemd_run_absent(monkeypatch):
    """Non-systemd host / container: degrade gracefully, never fail
    the spawn just because the cap can't be enforced."""
    _enable(monkeypatch, "2G")
    monkeypatch.setattr(_memory_scope, "_warned_missing", False)
    monkeypatch.setattr(_memory_scope.shutil, "which", lambda name: None)
    assert wrap_with_memory_scope(ORIG) == ORIG


def test_does_not_mutate_input(monkeypatch, systemd_run_present):
    _enable(monkeypatch, "2G")
    argv = list(ORIG)
    wrap_with_memory_scope(argv)
    assert argv == ORIG


def test_slice_name_matches_shipped_unit():
    """Drift guard: the wrapper's ``--slice`` target must equal the
    slice unit the installer writes. If they diverge, scopes land
    under a transient (uncapped) slice and the aggregate host cap
    silently does nothing."""
    from vexis_agent.daemon import systemd

    assert VEXIS_SLICE == systemd.SLICE_NAME


@pytest.mark.parametrize(
    "method_name", ["_attempt_respond", "_attempt_astream", "spawn_aux"]
)
def test_brain_spawn_sites_apply_memory_scope(method_name):
    """Every place ``ClaudeCodeBrain`` launches ``claude`` must route
    its argv through ``wrap_with_memory_scope`` — otherwise that spawn
    can still freeze the bot. Guards against a future 4th spawn site
    being added unwrapped, or an existing one losing the wrap in a
    refactor."""
    from vexis_agent.core.brain.claude_code import ClaudeCodeBrain

    src = inspect.getsource(getattr(ClaudeCodeBrain, method_name))
    assert "wrap_with_memory_scope" in src, (
        f"{method_name} spawns claude without per-subagent memory scoping"
    )

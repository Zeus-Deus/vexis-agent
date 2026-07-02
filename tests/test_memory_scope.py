"""Per-subagent memory-scope wrapper (2026-06-12 freeze fix).

Background: a single ``claude -p`` subagent ran a pathological grep
that ballooned to 2.4 GiB. Because the bot and everything it spawned
shared one systemd cgroup, the runaway pushed the cgroup over its
``MemoryHigh`` and the kernel throttled *every* task in it — including
the bot's own loop — into uninterruptible ``D`` sleep. The bot stopped
answering Telegram without crashing.

``wrap_with_memory_scope`` runs each spawn inside its own memory-capped
``systemd-run --scope`` so a runaway OOM-kills in isolation. These
tests pin the wrapper's structure, its THREE graceful-degradation paths
(cap disabled / binary absent / systemd present-but-unusable — the
issue-#47 container case, decided by a one-time probe), the drift guard
between the wrapper's slice name and the shipped slice unit, and that
all three brain spawn sites apply it.

No test shells out to ``systemd-run``: the usability probe is either
short-circuited by a cached verdict (``probe_usable``) or driven by a
fake ``subprocess.run``. An autouse fixture resets the process-lifetime
probe cache before every test so ordering can't leak a verdict.
"""

from __future__ import annotations

import inspect
import logging
import subprocess

import pytest

from vexis_agent.core.brain import _memory_scope
from vexis_agent.core.brain._memory_scope import VEXIS_SLICE, wrap_with_memory_scope

ORIG = ["claude", "-p", "hello"]


@pytest.fixture(autouse=True)
def _reset_probe_state(monkeypatch):
    """Start every test with a clean, un-probed cache and no 'missing'
    warning latch. Both are process-lifetime module globals; without
    this reset a test that reaches the probe would leak its verdict into
    later tests. Mirrors how tests already reset ``_warned_missing``."""
    monkeypatch.setattr(_memory_scope, "_systemd_usable", None)
    monkeypatch.setattr(_memory_scope, "_warned_missing", False)


@pytest.fixture
def systemd_run_present(monkeypatch):
    """Pretend ``systemd-run`` is on PATH regardless of the host."""
    monkeypatch.setattr(
        _memory_scope.shutil,
        "which",
        lambda name: "/usr/bin/systemd-run" if name == "systemd-run" else None,
    )


@pytest.fixture
def probe_usable(_reset_probe_state, monkeypatch):
    """Force the one-time systemd-usability probe to 'usable' so wrap
    tests exercise the scoping path without shelling out to
    ``systemd-run``. Depends on the reset fixture so it wins the
    ordering and leaves a cached ``True`` in place."""
    monkeypatch.setattr(_memory_scope, "_systemd_usable", True)


class _FakeRun:
    """Records calls and returns a canned result (or raises) so tests
    can drive the probe without a real ``systemd-run``."""

    def __init__(self, *, returncode=0, stderr=b"", raises=None):
        self.returncode = returncode
        self.stderr = stderr
        self.raises = raises
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(
            argv, self.returncode, stdout=b"", stderr=self.stderr
        )


def _enable(monkeypatch, cap="2G"):
    monkeypatch.setattr(
        _memory_scope, "brain_subprocess_memory_max", lambda: cap
    )


# ── Wrapping shape (probe forced usable) ───────────────────────────


def test_wraps_when_enabled_and_systemd_run_present(
    monkeypatch, systemd_run_present, probe_usable
):
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


def test_cap_value_is_passed_through(monkeypatch, systemd_run_present, probe_usable):
    _enable(monkeypatch, "1500M")
    assert "MemoryMax=1500M" in wrap_with_memory_scope(ORIG)


def test_does_not_mutate_input(monkeypatch, systemd_run_present, probe_usable):
    _enable(monkeypatch, "2G")
    argv = list(ORIG)
    wrap_with_memory_scope(argv)
    assert argv == ORIG


# ── No-op paths ────────────────────────────────────────────────────


def test_noop_when_disabled(monkeypatch, systemd_run_present):
    """``brain.subprocess_memory_max: none`` → no scoping at all."""
    monkeypatch.setattr(
        _memory_scope, "brain_subprocess_memory_max", lambda: None
    )
    assert wrap_with_memory_scope(ORIG) == ORIG


def test_noop_when_systemd_run_absent(monkeypatch):
    """Non-systemd host / container without the binary: degrade
    gracefully, never fail the spawn just because the cap can't be
    enforced."""
    _enable(monkeypatch, "2G")
    monkeypatch.setattr(_memory_scope.shutil, "which", lambda name: None)
    assert wrap_with_memory_scope(ORIG) == ORIG


def test_noop_when_systemd_unusable(monkeypatch, systemd_run_present):
    """Issue #47: binary present but a scope can't be created (no bus).
    The probe fails (nonzero exit) → argv returned unchanged, spawned
    once."""
    _enable(monkeypatch, "2G")
    fake = _FakeRun(returncode=1, stderr=b"Failed to connect to bus\n")
    monkeypatch.setattr(_memory_scope.subprocess, "run", fake)
    assert wrap_with_memory_scope(ORIG) == ORIG
    assert len(fake.calls) == 1


# ── Probe behaviour ────────────────────────────────────────────────


def test_probe_success_then_wraps_and_caches(monkeypatch, systemd_run_present):
    """Probe exits 0 → wrapped argv is today's exact shape; the verdict
    is cached so a second wrap does not re-probe."""
    _enable(monkeypatch, "2G")
    fake = _FakeRun(returncode=0)
    monkeypatch.setattr(_memory_scope.subprocess, "run", fake)

    out = wrap_with_memory_scope(ORIG)
    assert out[0] == "systemd-run"
    assert "MemoryMax=2G" in out
    assert out[out.index("--") + 1 :] == ORIG
    assert len(fake.calls) == 1
    # Probe argv is a real trivial scope: same prefix, command ``true``.
    assert fake.calls[0][0] == "systemd-run"
    assert fake.calls[0][-1] == "true"

    out2 = wrap_with_memory_scope(ORIG)
    assert out2 == out
    assert len(fake.calls) == 1  # no re-probe


def test_probe_runs_at_most_once_on_failure(monkeypatch, systemd_run_present):
    """Failure verdict is cached too — many wraps, one probe."""
    _enable(monkeypatch, "2G")
    fake = _FakeRun(returncode=1, stderr=b"Failed to connect to bus\n")
    monkeypatch.setattr(_memory_scope.subprocess, "run", fake)
    for _ in range(10):
        assert wrap_with_memory_scope(ORIG) == ORIG
    assert len(fake.calls) == 1


def test_probe_uses_live_cap_not_fixed(monkeypatch, systemd_run_present):
    """The probe carries the *live* configured cap. This is the safe
    choice: a cap systemd rejects then degrades to an unwrapped spawn
    (like a busless host) instead of hard-failing every real spawn —
    the exact issue-#47 mode a fixed known-good probe cap would let back
    in through the cap value."""
    _enable(monkeypatch, "9Z")  # deliberately implausible cap
    fake = _FakeRun(returncode=1, stderr=b"Invalid argument\n")
    monkeypatch.setattr(_memory_scope.subprocess, "run", fake)
    assert wrap_with_memory_scope(ORIG) == ORIG
    assert "MemoryMax=9Z" in fake.calls[0]


def test_probe_timeout_is_unusable(monkeypatch, systemd_run_present):
    """A hung D-Bus (TimeoutExpired) → unusable, spawn unscoped."""
    _enable(monkeypatch, "2G")
    fake = _FakeRun(
        raises=subprocess.TimeoutExpired(cmd=["systemd-run"], timeout=5.0)
    )
    monkeypatch.setattr(_memory_scope.subprocess, "run", fake)
    assert wrap_with_memory_scope(ORIG) == ORIG


def test_probe_oserror_is_unusable(monkeypatch, systemd_run_present):
    """An OSError from the probe subprocess → unusable, spawn unscoped."""
    _enable(monkeypatch, "2G")
    fake = _FakeRun(raises=OSError("boom"))
    monkeypatch.setattr(_memory_scope.subprocess, "run", fake)
    assert wrap_with_memory_scope(ORIG) == ORIG


def test_probe_failure_warns_exactly_once(monkeypatch, systemd_run_present, caplog):
    """Binary-present-but-broken logs one WARNING (surprising, unlike an
    absent binary) with the probe's stderr as the reason — and only
    once across repeated wraps."""
    _enable(monkeypatch, "2G")
    fake = _FakeRun(
        returncode=1, stderr=b"Failed to connect to bus: No such file\n"
    )
    monkeypatch.setattr(_memory_scope.subprocess, "run", fake)
    with caplog.at_level(
        logging.WARNING, logger="vexis_agent.core.brain._memory_scope"
    ):
        for _ in range(5):
            wrap_with_memory_scope(ORIG)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert len(fake.calls) == 1
    msg = warnings[0].getMessage()
    assert "Failed to connect to bus" in msg
    assert "brain.subprocess_memory_max" in msg  # names the knob


def test_probe_not_run_when_cap_disabled(monkeypatch, systemd_run_present):
    """Cap disabled short-circuits before the probe — no subprocess."""
    monkeypatch.setattr(
        _memory_scope, "brain_subprocess_memory_max", lambda: None
    )
    fake = _FakeRun(returncode=0)
    monkeypatch.setattr(_memory_scope.subprocess, "run", fake)
    assert wrap_with_memory_scope(ORIG) == ORIG
    assert fake.calls == []


def test_probe_not_run_when_binary_missing(monkeypatch):
    """Absent binary short-circuits before the probe — no subprocess."""
    _enable(monkeypatch, "2G")
    monkeypatch.setattr(_memory_scope.shutil, "which", lambda name: None)
    fake = _FakeRun(returncode=0)
    monkeypatch.setattr(_memory_scope.subprocess, "run", fake)
    assert wrap_with_memory_scope(ORIG) == ORIG
    assert fake.calls == []


# ── Drift guard + spawn-site coverage ──────────────────────────────


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

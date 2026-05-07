"""Day 4 of model UX — model discovery tests.

Coverage:
- claude-code: hardcoded list returned, contains expected aliases
- opencode: subprocess success, FileNotFoundError, TimeoutExpired,
  non-zero exit, empty output
- 5-minute cache: cached calls don't re-run subprocess; expired
  entries refresh
- invalidate_discovery_cache + refresh_opencode_models
- discover_models dispatch (claude-code, opencode, unknown)
- discovery_for_validator helper shape

Design citation: ``.plans/model-management-ux-research.md`` §6 Day 4.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from core import model_discovery as md


# ──────────────────────────────────────────────────────────────────
# Test setup — clear cache between tests
# ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_cache():
    md.invalidate_discovery_cache()
    yield
    md.invalidate_discovery_cache()


# ──────────────────────────────────────────────────────────────────
# claude-code: hardcoded curated list
# ──────────────────────────────────────────────────────────────────


def test_claude_code_returns_aliases():
    models = md.discover_claude_code_models()
    assert "haiku" in models
    assert "sonnet" in models
    assert "opus" in models


def test_claude_code_returns_curated_full_names():
    """Pin the curated current-generation set. Drift surfaces when
    the constant gets edited (e.g. new Anthropic release adds a
    name); at that point this test gets updated alongside."""
    models = md.discover_claude_code_models()
    assert "claude-haiku-4-5" in models
    assert "claude-sonnet-4-6" in models
    assert "claude-opus-4-1" in models


def test_claude_code_does_not_call_subprocess():
    """Pin: claude-code discovery is in-process. No subprocess
    overhead per call. (Important because the validator may call
    this on every request in Day 4+.)"""
    with patch("subprocess.run") as run_spy:
        md.discover_claude_code_models()
    assert not run_spy.called


# ──────────────────────────────────────────────────────────────────
# opencode: subprocess success
# ──────────────────────────────────────────────────────────────────


def _fake_completed(stdout: str, returncode: int = 0) -> MagicMock:
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.stdout = stdout
    cp.stderr = ""
    cp.returncode = returncode
    return cp


def test_opencode_success_returns_parsed_set():
    fake_stdout = (
        "anthropic/claude-haiku-3-5\n"
        "anthropic/claude-sonnet-4\n"
        "openai/gpt-4o\n"
    )
    with patch(
        "subprocess.run", return_value=_fake_completed(fake_stdout),
    ):
        models = md.discover_opencode_models()
    assert models == {
        "anthropic/claude-haiku-3-5",
        "anthropic/claude-sonnet-4",
        "openai/gpt-4o",
    }


def test_opencode_strips_blank_lines():
    fake_stdout = "anthropic/x\n\n  \nopenai/y\n"
    with patch(
        "subprocess.run", return_value=_fake_completed(fake_stdout),
    ):
        models = md.discover_opencode_models()
    assert models == {"anthropic/x", "openai/y"}


# ──────────────────────────────────────────────────────────────────
# opencode: failure modes (each → empty set)
# ──────────────────────────────────────────────────────────────────


def test_opencode_missing_binary_returns_empty_set():
    """The realistic case for claude-code-only users. Empty set
    → validator's rule 6 silently skips the membership check."""
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        models = md.discover_opencode_models()
    assert models == set()


def test_opencode_timeout_returns_empty_set_and_warns(caplog):
    """Binary present but slow (models.dev cache miss + slow
    upstream). Log warning so the user knows discovery degraded."""
    import logging
    caplog.set_level(logging.WARNING)
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="opencode", timeout=10),
    ):
        models = md.discover_opencode_models()
    assert models == set()
    assert any("timed out" in r.message for r in caplog.records)


def test_opencode_non_zero_exit_returns_empty_set():
    """Auth issue or models.dev unreachable. Validator's rule 6
    degrades gracefully."""
    with patch(
        "subprocess.run", return_value=_fake_completed("", returncode=1),
    ):
        models = md.discover_opencode_models()
    assert models == set()


def test_opencode_empty_output_returns_empty_set():
    with patch(
        "subprocess.run", return_value=_fake_completed(""),
    ):
        models = md.discover_opencode_models()
    assert models == set()


# ──────────────────────────────────────────────────────────────────
# 5-minute cache behaviour
# ──────────────────────────────────────────────────────────────────


def test_opencode_cached_within_5_minutes():
    """Two calls within the TTL hit the subprocess once."""
    fake_stdout = "anthropic/x\nopenai/y\n"
    with patch(
        "subprocess.run", return_value=_fake_completed(fake_stdout),
    ) as run_spy:
        first = md.discover_opencode_models()
        second = md.discover_opencode_models()
    assert first == second
    assert run_spy.call_count == 1


def test_invalidate_clears_specific_brain():
    fake_stdout = "anthropic/x\n"
    with patch(
        "subprocess.run", return_value=_fake_completed(fake_stdout),
    ) as run_spy:
        md.discover_opencode_models()  # populates
        md.invalidate_discovery_cache("opencode")
        md.discover_opencode_models()  # re-fetches
    assert run_spy.call_count == 2


def test_invalidate_all_clears_every_brain():
    fake_stdout = "anthropic/x\n"
    with patch(
        "subprocess.run", return_value=_fake_completed(fake_stdout),
    ) as run_spy:
        md.discover_opencode_models()
        md.invalidate_discovery_cache(None)
        md.discover_opencode_models()
    assert run_spy.call_count == 2


def test_refresh_opencode_models_invalidates_and_calls_refresh(monkeypatch):
    """refresh_opencode_models busts the cache AND runs
    ``opencode models --refresh`` so models.dev's own cache
    refreshes too. Both subprocess calls fire on success."""
    fake_stdout = "anthropic/x\n"
    calls: list[list[str]] = []

    def _fake_run(argv, *_a, **_k):
        calls.append(list(argv))
        return _fake_completed(fake_stdout)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    md.discover_opencode_models()  # populates cache (1 call)
    result = md.refresh_opencode_models()
    assert result == {"anthropic/x"}
    # Three calls expected: initial discover, refresh subprocess,
    # and the post-refresh discover.
    assert len(calls) == 3
    assert calls[1] == ["opencode", "models", "--refresh"]


def test_refresh_tolerates_subprocess_failure():
    """If ``opencode models --refresh`` fails, the function still
    returns the (possibly empty) discovery result. Refresh is
    best-effort."""
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        result = md.refresh_opencode_models()
    assert result == set()


# ──────────────────────────────────────────────────────────────────
# discover_models dispatch + discovery_for_validator helper
# ──────────────────────────────────────────────────────────────────


def test_discover_models_dispatches_per_brain():
    cc = md.discover_models("claude-code")
    assert "haiku" in cc
    with patch("subprocess.run", return_value=_fake_completed("a/b\n")):
        oc = md.discover_models("opencode")
    assert oc == {"a/b"}


def test_discover_models_unknown_brain_returns_empty():
    """Validator's rule 6 silently skips when the discovered set
    is empty — matches the unknown-brain case."""
    assert md.discover_models("future-brain") == set()
    assert md.discover_models("null") == set()


def test_discovery_for_validator_builds_dict():
    with patch("subprocess.run", return_value=_fake_completed("a/b\n")):
        d = md.discovery_for_validator(["claude-code", "opencode"])
    assert "claude-code" in d
    assert "opencode" in d
    assert "haiku" in d["claude-code"]
    assert d["opencode"] == {"a/b"}

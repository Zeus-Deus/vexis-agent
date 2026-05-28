"""Pytest conftest for the codemux add-on's own test suite.

Re-uses the autouse isolation fixtures from the repo-level
``tests/conftest.py`` so add-on tests get the same private
``~/.vexis/`` redirection, blocked live discovery, and other
test-safety nets that the repo tests rely on.

Without this, tests that probe binary resolution would see the
real ``codemux-remote`` binary on PATH (the test machine often
has Codemux installed) and tests that read ``~/.vexis/mcp-servers.yaml``
would see the user's actual config.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_vexis_dir_for_addon(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    """Same posture as the repo-level _isolate_vexis_dir."""
    private_root = tmp_path / "_vexis_isolated"
    private_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "vexis_agent.core.paths.vexis_dir", lambda: private_root,
    )
    yield


@pytest.fixture(autouse=True)
def _block_codemux_binary_lookups(monkeypatch: pytest.MonkeyPatch):
    """The binary-resolution tests expect the fallback string
    ``"codemux"`` when neither env var nor YAML supplies a path.
    On a developer machine with codemux installed,
    ``shutil.which("codemux")`` returns a real path. The tests
    that probe the fallback patch shutil.which themselves; this
    fixture clears the env var so the patches take effect cleanly.
    """
    monkeypatch.delenv("VEXIS_CODEMUX_BINARY", raising=False)
    yield

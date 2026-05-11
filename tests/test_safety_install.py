"""Tests for ``vexis_agent.core.safety_install``.

Covers the workspace ``.claude/settings.json`` writer:
  * Fresh write into an empty workspace.
  * Merge with user-owned settings (unrelated keys preserved).
  * Merge with pre-existing user hooks (Vexis entry appended,
    user entries untouched).
  * Idempotent re-run (second call writes nothing).
  * Sentinel-based update-in-place (sys.executable changes between
    runs → command field rewritten, no duplicate hook entry).
  * Corrupt existing settings.json is replaced.
  * End-to-end wiring: constructing a ``ClaudeCodeBrain`` writes
    the hook automatically.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.safety_install import (
    _OWNERSHIP_SENTINEL,
    ensure_workspace_safety_hook,
    hook_command,
)
from vexis_agent.core.sessions import SessionStore


def _settings_path(workspace: Path) -> Path:
    return workspace / ".claude" / "settings.json"


def _read_settings(workspace: Path) -> dict:
    return json.loads(_settings_path(workspace).read_text(encoding="utf-8"))


# ---------- fresh write ----------


def test_fresh_workspace_creates_settings_with_hook(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()

    changed = ensure_workspace_safety_hook(workspace)
    assert changed is True

    settings = _read_settings(workspace)
    pre = settings["hooks"]["PreToolUse"]
    assert len(pre) == 1
    assert pre[0]["matcher"] == "Bash"
    inner = pre[0]["hooks"]
    assert len(inner) == 1
    assert inner[0]["type"] == "command"
    assert _OWNERSHIP_SENTINEL in inner[0]["command"]
    assert sys.executable in inner[0]["command"]


def test_hook_command_uses_module_invocation() -> None:
    cmd = hook_command()
    # The "-m vexis_agent.cli safety-hook" form is what lets us
    # ignore PATH issues entirely. If this ever drifts to a bare
    # `vexis-agent` invocation, the sentinel-based ownership check
    # could miss legacy entries.
    assert "-m vexis_agent.cli safety-hook" in cmd
    assert sys.executable in cmd


def test_hook_command_carries_pythonpath() -> None:
    """End-to-end testing surfaced a real bug: the spawned hook
    subprocess runs with the workspace as cwd, not the project
    dir. Without an explicit PYTHONPATH, broken editable installs
    and pipx-isolation edge cases fail with ModuleNotFoundError.
    Pin the fix: hook_command() must prepend a PYTHONPATH that
    points at the dir containing vexis_agent/."""
    cmd = hook_command()
    assert cmd.startswith("PYTHONPATH="), (
        "hook command must lead with PYTHONPATH= prefix — see "
        "ModuleNotFoundError regression note in hook_command() docstring"
    )
    # The path immediately after PYTHONPATH= must resolve to a dir
    # that has vexis_agent/ as a subdirectory.
    import shlex

    tokens = shlex.split(cmd)
    env_assign = tokens[0]
    _, _, pythonpath_value = env_assign.partition("=")
    assert (Path(pythonpath_value) / "vexis_agent").is_dir(), (
        f"PYTHONPATH={pythonpath_value!r} does not point at the "
        "vexis_agent source dir"
    )


def test_hook_command_handles_paths_with_spaces(monkeypatch) -> None:
    """Defensive: if vexis is installed under a path containing
    spaces or shell metacharacters, the hook command must survive
    ``/bin/sh -c`` parsing — shlex.quote on both python and
    PYTHONPATH values is what makes that work."""
    import vexis_agent.core.safety_install as si

    fake_root = Path("/some/weird path/with $ chars")
    monkeypatch.setattr(si, "_vexis_source_root", lambda: fake_root)
    monkeypatch.setattr("sys.executable", "/another weird/path/python")
    cmd = si.hook_command()
    # shlex.split must round-trip the command — if not, /bin/sh -c
    # would split it incorrectly and the hook would fail.
    tokens = __import__("shlex").split(cmd)
    assert tokens[0].endswith(str(fake_root))  # PYTHONPATH=<fake_root>
    assert "/another weird/path/python" in tokens
    assert "-m" in tokens
    assert "vexis_agent.cli" in tokens
    assert "safety-hook" in tokens


# ---------- idempotency ----------


def test_second_call_is_noop(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()

    first = ensure_workspace_safety_hook(workspace)
    second = ensure_workspace_safety_hook(workspace)

    assert first is True
    assert second is False  # nothing changed → no rewrite


def test_second_call_does_not_duplicate_hook(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()

    ensure_workspace_safety_hook(workspace)
    ensure_workspace_safety_hook(workspace)
    ensure_workspace_safety_hook(workspace)

    inner = _read_settings(workspace)["hooks"]["PreToolUse"][0]["hooks"]
    # Sentinel matches exactly once even after three install calls.
    matching = [h for h in inner if _OWNERSHIP_SENTINEL in h.get("command", "")]
    assert len(matching) == 1


# ---------- merge with user settings ----------


def test_unrelated_top_level_keys_preserved(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / ".claude").mkdir(parents=True)
    _settings_path(workspace).write_text(
        json.dumps({
            "permissions": {"allow": ["Read", "Bash(ls:*)"]},
            "env": {"FOO": "bar"},
            "model": "sonnet",
        }),
        encoding="utf-8",
    )

    ensure_workspace_safety_hook(workspace)
    settings = _read_settings(workspace)

    assert settings["permissions"] == {"allow": ["Read", "Bash(ls:*)"]}
    assert settings["env"] == {"FOO": "bar"}
    assert settings["model"] == "sonnet"
    assert "hooks" in settings  # our hook was added


def test_user_pretooluse_for_other_tool_preserved(tmp_path: Path) -> None:
    """If the user has a PreToolUse hook for a non-Bash tool, ours
    coexists in a separate matcher group — theirs is untouched."""
    workspace = tmp_path / "ws"
    (workspace / ".claude").mkdir(parents=True)
    user_hook = {
        "matcher": "Write",
        "hooks": [{"type": "command", "command": "/opt/audit/write-log.sh"}],
    }
    _settings_path(workspace).write_text(
        json.dumps({"hooks": {"PreToolUse": [user_hook]}}),
        encoding="utf-8",
    )

    ensure_workspace_safety_hook(workspace)
    pre = _read_settings(workspace)["hooks"]["PreToolUse"]

    # Both groups present, user's untouched.
    assert len(pre) == 2
    by_matcher = {g["matcher"]: g for g in pre}
    assert by_matcher["Write"] == user_hook
    assert _OWNERSHIP_SENTINEL in by_matcher["Bash"]["hooks"][0]["command"]


def test_user_bash_hook_appended_not_replaced(tmp_path: Path) -> None:
    """If the user has their OWN Bash PreToolUse hook, we land
    inside the same matcher group as a sibling entry — never
    replacing their entry."""
    workspace = tmp_path / "ws"
    (workspace / ".claude").mkdir(parents=True)
    user_hook_entry = {
        "type": "command",
        "command": "/opt/audit/bash-log.sh",
    }
    _settings_path(workspace).write_text(
        json.dumps({
            "hooks": {
                "PreToolUse": [{
                    "matcher": "Bash",
                    "hooks": [user_hook_entry],
                }],
            },
        }),
        encoding="utf-8",
    )

    ensure_workspace_safety_hook(workspace)
    pre = _read_settings(workspace)["hooks"]["PreToolUse"]

    # Single Bash matcher group, two sibling entries.
    assert len(pre) == 1
    inner = pre[0]["hooks"]
    assert len(inner) == 2
    assert user_hook_entry in inner
    assert any(_OWNERSHIP_SENTINEL in h["command"] for h in inner)


# ---------- update-in-place via sentinel ----------


def test_stale_command_updated_in_place(tmp_path: Path) -> None:
    """Simulate the user moving conda envs: existing settings.json
    has our sentinel but with an old sys.executable. Re-installer
    rewrites that entry in place — no duplicate."""
    workspace = tmp_path / "ws"
    (workspace / ".claude").mkdir(parents=True)
    stale = "/old/path/to/python -m vexis_agent.cli safety-hook"
    _settings_path(workspace).write_text(
        json.dumps({
            "hooks": {
                "PreToolUse": [{
                    "matcher": "Bash",
                    "hooks": [{"type": "command", "command": stale}],
                }],
            },
        }),
        encoding="utf-8",
    )

    changed = ensure_workspace_safety_hook(workspace)
    assert changed is True

    inner = _read_settings(workspace)["hooks"]["PreToolUse"][0]["hooks"]
    assert len(inner) == 1  # NOT duplicated
    assert inner[0]["command"] == hook_command()
    assert inner[0]["command"] != stale


# ---------- corrupt input recovery ----------


def test_corrupt_settings_replaced(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / ".claude").mkdir(parents=True)
    _settings_path(workspace).write_text("{not valid json", encoding="utf-8")

    changed = ensure_workspace_safety_hook(workspace)
    assert changed is True

    settings = _read_settings(workspace)
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == "Bash"


def test_hooks_block_wrong_type_replaced(tmp_path: Path) -> None:
    """If the user has ``hooks: "not a dict"`` we replace just the
    hooks block — other keys survive."""
    workspace = tmp_path / "ws"
    (workspace / ".claude").mkdir(parents=True)
    _settings_path(workspace).write_text(
        json.dumps({"hooks": "this should be a dict", "other": 42}),
        encoding="utf-8",
    )

    ensure_workspace_safety_hook(workspace)
    settings = _read_settings(workspace)

    assert settings["other"] == 42
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == "Bash"


# ---------- end-to-end brain wiring ----------


def test_brain_construction_installs_hook(tmp_path: Path) -> None:
    """The full smoke: instantiating ClaudeCodeBrain on a fresh
    workspace writes the settings.json. Catches regressions where
    a future refactor of ``__init__`` forgets the install call."""
    pytest.importorskip("vexis_agent.core.brain.claude_code")
    from vexis_agent.core.brain.claude_code import ClaudeCodeBrain

    workspace = tmp_path / "ws"
    workspace.mkdir()
    session = SessionStore(tmp_path / "sessions.json")

    ClaudeCodeBrain(
        workspace=workspace,
        session=session,
        running_tasks=RunningTasks(),
    )

    assert _settings_path(workspace).exists()
    settings = _read_settings(workspace)
    inner = settings["hooks"]["PreToolUse"][0]["hooks"]
    assert any(_OWNERSHIP_SENTINEL in h["command"] for h in inner)

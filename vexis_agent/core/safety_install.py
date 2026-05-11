"""Workspace ``.claude/settings.json`` writer for the Step 6.5 safety hook.

Wires :mod:`vexis_agent.core.safety_hook` into Claude Code's
PreToolUse hook system by ensuring an entry exists in the workspace's
``.claude/settings.json``. Idempotent + merge-friendly: any unrelated
keys, user hooks, or other matcher groups are preserved verbatim.

Lifecycle
---------
* Called from ``BrainClaudeCode.__init__`` at daemon startup.
* Writes ``<workspace>/.claude/settings.json``.
* Workspace-scoped is intentional: claude-code reads the workspace
  settings.json on every ``-p`` spawn (the daemon's per-turn entry
  point), so the hook applies to every brain turn, including aux
  subprocesses (curator, goal judge) which share ``cwd``.
* Vexis owns this file's PreToolUse-Bash entry but nothing else.
  User-added hooks for other tools, or for non-Bash matchers, are
  left alone.

Why not ``~/.claude/settings.json``?
  Because that's the per-user file claude-code itself reads outside
  Vexis. Writing there would pollute other claude-code sessions
  (e.g. a user manually running ``claude`` from a different cwd).
  Workspace-scoped keeps the blast radius limited to Vexis turns.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Anything containing this substring in its ``command`` field is
# considered "Vexis's hook entry" and will be updated in place
# rather than duplicated. Stable across sys.executable changes
# (which is the volatile bit when users move conda envs).
_OWNERSHIP_SENTINEL = "vexis_agent.cli safety-hook"


def hook_command() -> str:
    """Build the shell command claude-code should run for PreToolUse.

    Uses ``sys.executable`` rather than the bare ``vexis-agent``
    console script so PATH ordering inside the spawned subprocess
    can't accidentally pick up a different vexis install. The
    sentinel substring (``vexis_agent.cli safety-hook``) is the
    ownership marker the installer searches for on re-runs.
    """
    return f"{sys.executable} -m vexis_agent.cli safety-hook"


def _read_existing(path: Path) -> dict[str, Any]:
    """Read settings.json or return ``{}`` for any failure mode.

    Malformed JSON is treated as ``{}`` and the file is rewritten.
    This is intentional: if the file is corrupt, our hook entry
    wouldn't be honored anyway, and overwriting with a valid file
    is strictly safer than refusing to install.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        log.warning(
            "safety_install: existing %s is not valid JSON — replacing.",
            path,
        )
        return {}
    return data if isinstance(data, dict) else {}


def _merge_hook(settings: dict[str, Any], command: str) -> dict[str, Any]:
    """Return a new settings dict with our PreToolUse-Bash hook
    ensured. The input dict is not mutated."""
    out = json.loads(json.dumps(settings))  # cheap deep-copy via JSON

    hooks_block = out.get("hooks")
    if not isinstance(hooks_block, dict):
        hooks_block = {}
        out["hooks"] = hooks_block

    pre_list = hooks_block.get("PreToolUse")
    if not isinstance(pre_list, list):
        pre_list = []
        hooks_block["PreToolUse"] = pre_list

    # Find a matcher group keyed on "Bash" (canonical matcher value
    # claude-code uses). We don't try to match arbitrary regexes
    # that might also cover Bash — too brittle and the user can
    # always remove our duplicate group by hand.
    bash_group = None
    for group in pre_list:
        if isinstance(group, dict) and group.get("matcher") == "Bash":
            bash_group = group
            break
    if bash_group is None:
        bash_group = {"matcher": "Bash", "hooks": []}
        pre_list.append(bash_group)

    inner = bash_group.get("hooks")
    if not isinstance(inner, list):
        inner = []
        bash_group["hooks"] = inner

    # Find our entry by ownership sentinel and update it in place.
    # New install → append.
    for entry in inner:
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("command"), str)
            and _OWNERSHIP_SENTINEL in entry["command"]
        ):
            entry["type"] = "command"
            entry["command"] = command
            break
    else:
        inner.append({"type": "command", "command": command})

    return out


def _atomic_write(path: Path, payload: str) -> None:
    """Write ``payload`` to ``path`` atomically (tempfile + rename).

    Same pattern :mod:`vexis_agent.core.brain.claude_code` uses for
    ``.mcp.json``. Survives crashes mid-write; no half-rendered
    settings.json can appear on disk.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=f"{path.name}.", suffix=".tmp", dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def ensure_workspace_safety_hook(workspace: Path) -> bool:
    """Ensure ``<workspace>/.claude/settings.json`` contains our hook.

    Returns ``True`` if the file was created or modified, ``False`` if
    it was already up-to-date. Idempotent: calling repeatedly with no
    changes in between writes nothing to disk.

    Failures are logged and swallowed — the daemon must come up even
    if hook installation fails. The cost is reduced safety, not a
    broken daemon, which matches the "fail-open at the hook layer"
    philosophy in :mod:`vexis_agent.core.safety_hook`.
    """
    path = workspace / ".claude" / "settings.json"
    try:
        existing = _read_existing(path)
        merged = _merge_hook(existing, hook_command())
        # Serialize once and compare. ``indent=2, sort_keys=False``
        # matches the convention .mcp.json uses elsewhere in vexis
        # so diffs in workspace settings are human-readable.
        new_text = json.dumps(merged, indent=2, sort_keys=False) + "\n"
        try:
            old_text = path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            old_text = None
        if old_text == new_text:
            return False
        _atomic_write(path, new_text)
        log.info("safety_install: wrote PreToolUse hook to %s", path)
        return True
    except Exception as exc:  # pragma: no cover — defensive
        log.warning(
            "safety_install: failed to install PreToolUse hook at %s: %s",
            path, exc,
        )
        return False

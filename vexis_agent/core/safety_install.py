"""Workspace settings writers for the Step 6.5 safety hook.

Two installers, one per brain:

* :func:`ensure_workspace_safety_hook` — claude-code path. Writes
  the PreToolUse hook entry into ``<workspace>/.claude/settings.json``.
* :func:`ensure_opencode_safety_plugin` — opencode path. Copies
  the shipped ``opencode_safety_plugin.mjs`` into the workspace
  and merges its path into ``<workspace>/opencode.json``'s
  ``plugin: []`` array.

Both are idempotent + merge-friendly: any unrelated keys, user
hooks, or user-owned plugin entries are preserved verbatim.

Lifecycle
---------
* Each installer is called from the matching ``Brain.__init__``
  at daemon startup.
* Writes are workspace-scoped, not user-global — limiting blast
  radius to vexis turns and keeping standalone ``claude`` /
  ``opencode`` invocations unaffected.
* Vexis owns its sentinel-marked entries but nothing else.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
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


def _vexis_source_root() -> Path:
    """Return the directory containing the ``vexis_agent`` package.

    safety_install.py lives at ``<root>/vexis_agent/core/safety_install.py``;
    walking up three parents yields the dir that has ``vexis_agent/``
    as a subdirectory. That's the right value for ``PYTHONPATH``.
    """
    return Path(__file__).resolve().parent.parent.parent


def hook_command() -> str:
    """Build the shell command claude-code should run for PreToolUse.

    Two robustness measures vs the naive
    ``{sys.executable} -m vexis_agent.cli safety-hook``:

    1. ``PYTHONPATH`` prefix pointing at the source dir.
       claude-code spawns the hook with a workspace cwd, not the
       project dir. Editable installs that bind ``vexis_agent`` to
       a stale or relocated path (or scenarios where the daemon's
       ``sys.path`` differs from the spawned subprocess) would
       otherwise fail with ``ModuleNotFoundError: No module named
       'vexis_agent'``. For proper site-packages installs the
       PYTHONPATH addition is a redundant no-op (the source root
       IS site-packages and is already on sys.path) — harmless.
    2. Both path values are ``shlex.quote()``-escaped so spaces or
       quoting metacharacters in the install path can't break the
       command claude-code execs via ``/bin/sh -c``.

    The sentinel substring (``vexis_agent.cli safety-hook``) is the
    ownership marker the installer searches for on re-runs — kept
    intact across the PYTHONPATH change so existing settings.json
    entries get updated in place rather than duplicated.
    """
    python = shlex.quote(sys.executable)
    pythonpath = shlex.quote(str(_vexis_source_root()))
    return f"PYTHONPATH={pythonpath} {python} -m vexis_agent.cli safety-hook"


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


# ──────────────────────────────────────────────────────────────────
# OpenCode path
# ──────────────────────────────────────────────────────────────────

# Filename of the plugin copy that lives in the workspace. The
# dot-prefix keeps it out of casual ``ls`` output without making
# it actually hidden from opencode's loader (relative paths are
# resolved cwd-relative, dot-prefix doesn't change resolution).
_OPENCODE_PLUGIN_FILENAME = ".vexis-opencode-safety.mjs"

# Name of the source file shipped under vexis_agent/data/. Read via
# importlib.resources so pipx-installed users (no source checkout)
# can also resolve it.
_OPENCODE_PLUGIN_DATA_NAME = "opencode_safety_plugin.mjs"

# Substring used to identify our entry inside opencode.json's
# ``plugin: [...]`` array. The filename itself is the sentinel —
# stable across machines and across vexis upgrades.
_OPENCODE_PLUGIN_SENTINEL = "vexis-opencode-safety"


def _read_plugin_source() -> str:
    """Read the shipped plugin file. Raises FileNotFoundError if the
    package was built without ``data/*`` — that would mean the wheel
    is broken and the daemon should surface it loudly."""
    from vexis_agent.data import read_text  # local import — keeps cli.py light

    text = read_text(_OPENCODE_PLUGIN_DATA_NAME)
    if text is None:
        raise FileNotFoundError(
            f"shipped plugin {_OPENCODE_PLUGIN_DATA_NAME!r} not found in "
            "vexis_agent.data — wheel may be malformed"
        )
    return text


def _merge_opencode_plugin(
    settings: dict[str, Any], plugin_path: str,
) -> dict[str, Any]:
    """Return a new opencode.json dict with our plugin entry ensured.

    Plugin entries can be either a bare string (path) or a
    ``[path, options]`` tuple (per @opencode-ai/plugin's Config
    type). We accept both shapes when inspecting existing entries
    and always emit the bare-string form for ours."""
    out = json.loads(json.dumps(settings))  # cheap deep-copy

    plugin_list = out.get("plugin")
    if not isinstance(plugin_list, list):
        plugin_list = []
        out["plugin"] = plugin_list

    def _entry_path(entry: Any) -> str | None:
        if isinstance(entry, str):
            return entry
        if isinstance(entry, list) and entry and isinstance(entry[0], str):
            return entry[0]
        return None

    found = False
    for i, entry in enumerate(plugin_list):
        path_str = _entry_path(entry)
        if path_str is not None and _OPENCODE_PLUGIN_SENTINEL in path_str:
            # Update in place — preserve options tuple shape if user
            # wrapped ours for some reason.
            if isinstance(entry, list):
                plugin_list[i] = [plugin_path, *entry[1:]]
            else:
                plugin_list[i] = plugin_path
            found = True
            break
    if not found:
        plugin_list.append(plugin_path)

    return out


def ensure_opencode_safety_plugin(workspace: Path) -> bool:
    """Install Step 6.5 hard enforcement for opencode foreground turns.

    Two-part write — both are atomic + idempotent:
      1. Copy ``opencode_safety_plugin.mjs`` from package data to
         ``<workspace>/.vexis-opencode-safety.mjs``. Overwrites if
         the shipped version differs from on-disk (so vexis upgrades
         propagate fresh regex sets).
      2. Add ``./.vexis-opencode-safety.mjs`` to opencode.json's
         ``plugin: []`` array. Existing user-owned plugin entries
         are preserved; ours is matched by filename sentinel and
         updated in place rather than duplicated.

    Returns ``True`` if anything on disk changed. Failures are
    logged + swallowed — see :func:`ensure_workspace_safety_hook`
    for the fail-open rationale.

    Note: aux opencode spawns (curator, goal judge, extractors)
    don't go through this installer's wrap — they're already
    blanket-denied for shell via the ``permission.shell = "deny"``
    ruleset in ``_OPENCODE_CONFIG_CONTENT``. The plugin runs there
    too (opencode loads it from opencode.json regardless of agent
    mode) but its hook just sees no Bash calls to inspect.
    """
    changed = False
    plugin_file = workspace / _OPENCODE_PLUGIN_FILENAME
    settings_file = workspace / "opencode.json"
    plugin_relpath = f"./{_OPENCODE_PLUGIN_FILENAME}"

    try:
        # ── Step 1: write the plugin file.
        new_source = _read_plugin_source()
        try:
            old_source = plugin_file.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            old_source = None
        if old_source != new_source:
            _atomic_write(plugin_file, new_source)
            log.info(
                "safety_install: wrote opencode safety plugin to %s",
                plugin_file,
            )
            changed = True

        # ── Step 2: merge the plugin path into opencode.json.
        existing = _read_existing(settings_file)
        merged = _merge_opencode_plugin(existing, plugin_relpath)
        new_text = json.dumps(merged, indent=2, sort_keys=False) + "\n"
        try:
            old_text = settings_file.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            old_text = None
        if old_text != new_text:
            _atomic_write(settings_file, new_text)
            log.info(
                "safety_install: added opencode safety plugin to %s",
                settings_file,
            )
            changed = True

        return changed
    except Exception as exc:  # pragma: no cover — defensive
        log.warning(
            "safety_install: failed to install opencode safety plugin "
            "at %s: %s", workspace, exc,
        )
        return False

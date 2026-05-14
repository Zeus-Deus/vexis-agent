"""Structural tripwire: claude-code's session storage stays sealed
inside the claude-code brain.

vexis-agent runs on a swappable brain (``claude-code`` default,
``opencode`` opt-in). A user who picks opencode has no
``~/.claude/projects/<cwd>/<uuid>.jsonl`` files at all — opencode
keeps sessions in ``opencode.db``. So any subsystem that wants to
read a session transcript MUST go through ``brain.iter_messages``;
reaching for ``claude_session_jsonl_dir`` directly is a parity bug
that silently degrades on opencode (it did, twice — the
relationships turn-index hook and restart-recovery both read JSONL
directly until this guard was added).

This test parses every module under ``vexis_agent/`` and fails if
anything outside the allowlist *imports* ``claude_session_jsonl_dir``
from ``core.transcripts``. Import-level (not grep-level) so docstring
mentions don't trip it and a function-local import can't sneak past.

Allowlist:
- ``core/transcripts.py`` — defines the function; it *is* claude-code's
  storage layer.
- ``core/brain/claude_code.py`` — the claude-code brain implementation;
  resolving the JSONL path is its whole job.

If you're adding a new brain or a new subsystem and this test fails:
route the transcript read through ``brain.iter_messages(session_uuid)``
instead. See ``core/learning_curator.py`` for the brain-agnostic
pattern and ``CLAUDE.md`` → Invariants.
"""

from __future__ import annotations

import ast
from pathlib import Path

_VEXIS_ROOT = Path(__file__).resolve().parent.parent / "vexis_agent"

# Paths (relative to vexis_agent/) permitted to touch
# claude_session_jsonl_dir directly.
_ALLOWLIST = {
    "core/transcripts.py",
    "core/brain/claude_code.py",
}

_GUARDED_NAME = "claude_session_jsonl_dir"


def _imports_guarded_name(tree: ast.AST) -> bool:
    """True if the module imports ``claude_session_jsonl_dir`` by any
    form — ``from ... import claude_session_jsonl_dir`` (module-level
    or function-local) or ``import vexis_agent.core.transcripts`` then
    attribute access. We only need to catch the ``from-import`` form;
    nothing in the tree currently uses the attribute form, and the
    ``from-import`` is the ergonomic path a leak would actually take.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == _GUARDED_NAME:
                    return True
    return False


def test_no_module_outside_brain_reads_claude_jsonl_dir() -> None:
    offenders: list[str] = []
    for py in _VEXIS_ROOT.rglob("*.py"):
        rel = py.relative_to(_VEXIS_ROOT).as_posix()
        if rel in _ALLOWLIST:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        if _imports_guarded_name(tree):
            offenders.append(rel)
    assert not offenders, (
        "These modules import claude_session_jsonl_dir directly — that "
        "breaks the opencode brain (no JSONL files exist there). Route "
        "transcript reads through brain.iter_messages() instead:\n  "
        + "\n  ".join(sorted(offenders))
    )


def test_allowlisted_files_exist() -> None:
    """Guards the guard: if an allowlisted file is renamed/moved, fail
    loudly here rather than silently widening the allowlist's reach."""
    for rel in _ALLOWLIST:
        assert (_VEXIS_ROOT / rel).is_file(), (
            f"allowlisted path {rel!r} no longer exists — update "
            "_ALLOWLIST in this test to match the new location."
        )

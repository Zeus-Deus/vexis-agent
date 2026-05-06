"""Phase C Day 6: snapshot tests for ``build_system_prompt`` per brain.

These pin the *shape* of the prompt each brain hands to its CLI so
future edits surface diffs in PR review. A full byte-for-byte
snapshot would lock the SOUL.md / CAPABILITIES.md text — too brittle.
Instead we assert structural invariants:

- which canonical sections appear in which brain's prompt,
- which sections must NOT appear (the ``<available_skills>`` block
  for opencode — it discovers skills natively),
- which tool-name leaks must not regress (the §1 grep findings
  Day 6 cleaned up),
- ordering is stable (claude-code historically renders SOUL →
  CAPABILITIES → memory → skills index).

The cross-brain parametrise also verifies the prompt is at least
one paragraph long for every brain — catches a bug where a
build_system_prompt accidentally returns "" or just whitespace.

Design citation: ``.plans/brain-abstraction-research.md`` §5 Day 6.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.brain.base import Brain
from core.brain.claude_code import ClaudeCodeBrain
from core.brain.opencode import OpenCodeBrain
from core.running_tasks import RunningTasks
from core.sessions import SessionStore


# ──────────────────────────────────────────────────────────────────
# Tool-name leak constants — pinned from the Day 6 cleanup pass
# ──────────────────────────────────────────────────────────────────


# These claude-code-specific tool names should NOT appear in the
# system prompt rendered for ANY brain, because they're either
# (a) gone from CAPABILITIES.md after Day 6's cleanup, or (b)
# kept only as documentation phrases that wouldn't tell the model
# "use this exact tool". The regex search is for the exact
# bulleted-tool shape claude-code's tool registry uses.
_FORBIDDEN_TOOL_NAME_PHRASES = [
    "use the `Read` tool",
    "use the Read tool",
    "Use the `Read` tool",
    "Use the Read tool",
    "Claude Code session",
    "claude -p --output-format stream-json",
]


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws-snapshot"
    ws.mkdir()
    # No SOUL.md — exercise the DEFAULT_SOUL fallback path.
    return ws


@pytest.fixture
def claude_brain(workspace: Path, tmp_path: Path) -> ClaudeCodeBrain:
    return ClaudeCodeBrain(
        workspace=workspace,
        session=SessionStore(tmp_path / "sessions-cc.json"),
        running_tasks=RunningTasks(),
    )


@pytest.fixture
def opencode_brain(workspace: Path, tmp_path: Path) -> OpenCodeBrain:
    return OpenCodeBrain(
        workspace=workspace,
        session=SessionStore(tmp_path / "sessions-oc.json"),
        running_tasks=RunningTasks(),
    )


# ──────────────────────────────────────────────────────────────────
# Cross-brain non-empty + tool-name-leak structural invariants
# ──────────────────────────────────────────────────────────────────


@pytest.fixture(params=["claude_code", "opencode"])
def brain_for_snapshot(
    request, claude_brain, opencode_brain,
) -> Brain:
    if request.param == "claude_code":
        return claude_brain
    return opencode_brain


def test_build_system_prompt_is_non_empty(brain_for_snapshot: Brain):
    """A brain that returns ``""`` would silently break every turn
    — the model's first message would be just the user input with
    no system context. Catch that immediately."""
    prompt = brain_for_snapshot.build_system_prompt()
    assert isinstance(prompt, str)
    assert prompt.strip(), "system prompt is empty / whitespace"
    # At least a paragraph — guards against degenerate single-word
    # returns.
    assert len(prompt) > 200


def test_build_system_prompt_includes_vexis_identity(
    brain_for_snapshot: Brain,
):
    """SOUL.md (or DEFAULT_SOUL) must surface — the model needs to
    know it's Vexis."""
    prompt = brain_for_snapshot.build_system_prompt()
    assert "Vexis" in prompt


def test_build_system_prompt_no_tool_name_leaks(
    brain_for_snapshot: Brain,
):
    """The Day 6 cleanup removed phrases that direct the model at
    claude-code-specific tool names (``Read``, ``Bash``,
    ``Edit``). Future edits to CAPABILITIES.md that reintroduce
    these phrases would make opencode misbehave (lowercase tool
    names; no PascalCase ``Read``). The forbidden-phrase list is
    a tripwire — if you intentionally need one of these phrases,
    update the constant in this file with a comment explaining
    why."""
    prompt = brain_for_snapshot.build_system_prompt()
    for phrase in _FORBIDDEN_TOOL_NAME_PHRASES:
        assert phrase not in prompt, (
            f"forbidden tool-name phrase reappeared in "
            f"{type(brain_for_snapshot).__name__}'s system prompt: {phrase!r}"
        )


# ──────────────────────────────────────────────────────────────────
# Per-brain structural assertions
# ──────────────────────────────────────────────────────────────────


def test_claude_code_prompt_includes_skills_index_block(
    claude_brain: ClaudeCodeBrain, workspace: Path,
):
    """claude-code emits its own ``<available_skills>`` block
    populated from ``<workspace>/skills/**/SKILL.md``. With a
    seeded skill we verify the index appears in the prompt — pin
    to detect a regression where the skills block is accidentally
    dropped."""
    skill_dir = workspace / "skills" / "snapshot-test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: snapshot-test-skill\n"
        "description: A unique sentinel for the claude-code snapshot.\n"
        "origin: hand-written\n---\nbody",
        encoding="utf-8",
    )
    prompt = claude_brain.build_system_prompt()
    # Vexis's index renders bullets like
    # ``- snapshot-test-skill: A unique sentinel for the claude-code snapshot.``
    assert (
        "- snapshot-test-skill: A unique sentinel for the claude-code snapshot."
        in prompt
    )


def test_opencode_prompt_omits_skills_index_block(
    opencode_brain: OpenCodeBrain, workspace: Path,
):
    """OpenCode auto-discovers ``skills/**/SKILL.md`` natively
    and emits its own ``<available_skills>`` block — vexis's
    ``build_system_prompt`` for opencode MUST omit the index to
    avoid double-injection. Snapshot-pinned alongside the
    claude-code positive case so a future edit that accidentally
    lands the index in opencode's prompt fails this test."""
    skill_dir = workspace / "skills" / "snapshot-test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: snapshot-test-skill\n"
        "description: A unique sentinel for the opencode snapshot.\n"
        "origin: hand-written\n---\nbody",
        encoding="utf-8",
    )
    prompt = opencode_brain.build_system_prompt()
    assert (
        "- snapshot-test-skill: A unique sentinel for the opencode snapshot."
        not in prompt
    )


# ──────────────────────────────────────────────────────────────────
# Section-ordering invariant (claude-code)
# ──────────────────────────────────────────────────────────────────


def test_claude_code_prompt_section_order_is_stable(
    claude_brain: ClaudeCodeBrain, workspace: Path,
):
    """claude-code's prompt has historically rendered:
    SOUL → CAPABILITIES → memory blocks → skills index. We
    verify SOUL precedes CAPABILITIES — the only ordering
    invariant the curator relies on (the curator reads the
    rendered prompt to detect SOUL drift in Phase 4 review;
    misordered sections would break that detector)."""
    # Seed a marker in CAPABILITIES so we can find it. We're
    # asserting on the project's real CAPABILITIES.md so the test
    # surfaces real edits that move the section anchor.
    prompt = claude_brain.build_system_prompt()
    # SOUL contribution: "Vexis" appears in DEFAULT_SOUL.
    soul_idx = prompt.find("Vexis")
    # CAPABILITIES contribution: "Capabilities" header at the
    # top of CAPABILITIES.md.
    cap_idx = prompt.find("Capabilities")
    if cap_idx == -1:
        # Fallback marker — section header may have been renamed;
        # try the project-name marker that lives further into the
        # capabilities body.
        cap_idx = prompt.find("vexis-agent")
    assert soul_idx >= 0
    assert cap_idx >= 0
    assert soul_idx < cap_idx, (
        "SOUL should render before CAPABILITIES — section order "
        "regression"
    )

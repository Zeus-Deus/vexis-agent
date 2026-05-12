"""agent-platform-style in-session skill self-authoring guidance.

These tests pin the invariants that drive the
``in-session skill authoring`` feature: every brain's system prompt must
carry the authoring nudge ("after a non-trivial task, save a
skill"; "patch outdated skills on use") regardless of skill count,
so a brand-new install with zero skills still bootstraps its own
library on the first real task.

Mirrors the an upstream agent platform ``SKILLS_GUIDANCE`` injection
in ``agent/prompt_builder.py:179-186``, adapted to vexis's
``vexis-skill`` CLI surface and shadow-tree review flow.

Three layers of pin:
  1. The pure function ``build_skill_authoring_block`` returns the
     expected key phrases. Single source of truth — drift here
     should be deliberate and surface in PR review.
  2. Every brain's ``build_system_prompt`` actually includes the
     block, even with an empty workspace.
  3. Positional invariant: the block lands AFTER capabilities and
     BEFORE memory blocks. Stable position keeps Anthropic's
     prefix cache hits warm across turns.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vexis_agent.core.brain.base import Brain
from vexis_agent.core.brain.claude_code import ClaudeCodeBrain
from vexis_agent.core.brain.opencode import OpenCodeBrain
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore
from vexis_agent.core.skills import build_skill_authoring_block


# ──────────────────────────────────────────────────────────────────
# Key phrases the authoring block MUST surface. Drift here is the
# loudest possible signal that someone gutted the agent-platform-style
# behaviour — they show up in every system prompt the daemon sends,
# so a deletion regression is one PR review away from shipping to
# all users.
# ──────────────────────────────────────────────────────────────────

_REQUIRED_PHRASES = [
    # Header so the brain can find the block by anchor.
    "## Skill authoring (mandatory)",
    # The reflex trigger — captures "after a non-trivial task".
    "non-trivial task",
    # The CLI verbs the brain needs to actually act.
    "vexis-skill create",
    "vexis-skill patch",
    # The "don't wait" imperative — the canonical phrasing.
    "don't wait to be asked",
    # The shortcut-vs-discovery distinction (the part that drives
    # the JS-eval-beats-clicking outcome from Kyle Jeong's demo).
    "SHORTCUT",
    # Frozen-snapshot semantics: the brain must know the new skill
    # won't appear in this turn's <available_skills> block (mirrors
    # the existing memory-write rule it already knows).
    "frozen-snapshot",
    # Dashboard escape hatch: the user has a place to undo a bad
    # call, so the brain shouldn't be paralyzed by uncertainty.
    "dashboard Skills tab",
]


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Empty workspace — no SOUL.md, no skills, no memory.

    The authoring guidance must surface anyway. That's the
    cold-start bootstrap property we care about.
    """
    ws = tmp_path / "ws-authoring"
    ws.mkdir()
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


@pytest.fixture(params=["claude_code", "opencode"])
def brain_for_prompt(
    request, claude_brain, opencode_brain,
) -> Brain:
    if request.param == "claude_code":
        return claude_brain
    return opencode_brain


# ──────────────────────────────────────────────────────────────────
# Layer 1 — pure function content
# ──────────────────────────────────────────────────────────────────


def test_authoring_block_returns_non_empty_string():
    block = build_skill_authoring_block()
    assert isinstance(block, str)
    assert block.strip(), "authoring block must not be empty"
    # Sanity floor: a one-liner would mean the guidance got gutted
    # to a stub. The real text is ~600 chars.
    assert len(block) > 300


def test_authoring_block_is_a_pure_function():
    """No filesystem access, no config read: two calls return the
    exact same bytes. This is what lets the brain layer cache the
    full system prompt per-session UUID for prefix-cache stability
    without worrying that the authoring block mutated between turns."""
    a = build_skill_authoring_block()
    b = build_skill_authoring_block()
    assert a == b
    assert a is not b or len(a) > 0  # identity not required; equality is


@pytest.mark.parametrize("phrase", _REQUIRED_PHRASES)
def test_authoring_block_contains_required_phrase(phrase: str):
    """Each required phrase is its own parametrise case so a
    failure tells you exactly which imperative got dropped — not
    just "the block changed."
    """
    block = build_skill_authoring_block()
    assert phrase in block, (
        f"authoring block missing required phrase: {phrase!r}. "
        f"If this is intentional, update _REQUIRED_PHRASES in this "
        f"test and explain why in the PR — the phrase list is the "
        f"behavioural contract."
    )


# ──────────────────────────────────────────────────────────────────
# Layer 2 — present in every brain's system prompt, even cold
# ──────────────────────────────────────────────────────────────────


def test_authoring_block_appears_in_every_brain_prompt(
    brain_for_prompt: Brain,
):
    """The block must surface in BOTH claude-code and opencode
    system prompts. Cold workspace: no SOUL, no skills, no memory.
    If the brain layer accidentally gated the guidance on
    skill-count or workspace state, this fails."""
    prompt = brain_for_prompt.build_system_prompt()
    assert "## Skill authoring (mandatory)" in prompt, (
        f"{type(brain_for_prompt).__name__} system prompt missing "
        f"the authoring block. Without it, the brain has no nudge "
        f"to ever create a skill — bootstrap from zero skills is "
        f"impossible."
    )


@pytest.mark.parametrize("phrase", _REQUIRED_PHRASES)
def test_brain_prompt_carries_each_required_phrase(
    brain_for_prompt: Brain, phrase: str,
):
    """Verifies the wiring (not just the constant): every key
    phrase from the authoring block actually ends up in the
    rendered system prompt, for both brains."""
    prompt = brain_for_prompt.build_system_prompt()
    assert phrase in prompt, (
        f"{type(brain_for_prompt).__name__} prompt missing "
        f"phrase {phrase!r} — wiring regression"
    )


# ──────────────────────────────────────────────────────────────────
# Layer 3 — positional invariant (cache stability)
# ──────────────────────────────────────────────────────────────────


def test_authoring_block_position_after_capabilities_before_memory(
    brain_for_prompt: Brain, workspace: Path,
):
    """The authoring block sits AFTER capabilities (so "what tools
    do I have" is established first) and BEFORE memory (so the
    "how to capture learnings" rule precedes the actual memory
    contents). Stable ordering keeps the prefix cache warm — moving
    the block invalidates Anthropic's KV cache for every session
    afterward.

    Seed a small MEMORY.md so we have a concrete marker to scan
    for. The block must appear before "MEMORY.md" rendered into
    the memory section.
    """
    # Seed memory we can search for in the rendered prompt.
    mem_dir = workspace / "memories"
    mem_dir.mkdir(parents=True, exist_ok=True)
    (mem_dir / "MEMORY.md").write_text(
        "Cache-position-marker-7ZQK: a sentinel memory entry.\n",
        encoding="utf-8",
    )

    prompt = brain_for_prompt.build_system_prompt()

    cap_idx = prompt.find("# Capabilities")
    auth_idx = prompt.find("## Skill authoring (mandatory)")
    mem_idx = prompt.find("Cache-position-marker-7ZQK")

    assert cap_idx >= 0, "Capabilities header missing"
    assert auth_idx >= 0, "Authoring block missing"
    assert mem_idx >= 0, "Seeded memory marker missing"

    assert cap_idx < auth_idx, (
        "Authoring block should follow CAPABILITIES — moving it "
        "invalidates prefix-cache for the static head."
    )
    assert auth_idx < mem_idx, (
        "Authoring block should precede MEMORY — putting it after "
        "memory means the brain reads user-specific facts before "
        "the rule that governs how to file new ones."
    )


# ──────────────────────────────────────────────────────────────────
# CAPABILITIES.md consistency — defense in depth
# ──────────────────────────────────────────────────────────────────


def test_capabilities_md_carries_patch_on_use_imperative():
    """CAPABILITIES.md is the longer-form companion to the
    authoring guidance constant. The "don't wait to be asked"
    imperative on PATCH must appear there too — otherwise a
    future contributor who only reads CAPABILITIES.md and not
    ``core/skills.py`` could legitimately delete the constant
    thinking it duplicates the file.

    Whitespace-normalized search: the markdown body wraps long
    lines at ~70 chars, so the phrase often spans a newline. The
    model reads the rendered markdown holistically; we mirror
    that by collapsing whitespace before the literal search.
    """
    import re

    from vexis_agent.data import read_capabilities

    body = read_capabilities() or ""
    flat = re.sub(r"\s+", " ", body)
    assert "don't wait to be asked" in flat, (
        "CAPABILITIES.md lost the patch-on-use imperative. The "
        "long-form text and the authoring guidance constant are "
        "intentionally redundant — defense in depth against either "
        "being edited without the other."
    )


def test_capabilities_md_carries_save_the_shortcut_hint():
    """The shortcut-vs-discovery distinction is the punchline of
    the upstream browser demo (102s → 35s, 23 turns → 8 turns). It
    must be present in CAPABILITIES.md so even a brain that
    skipped the authoring block on a long-context turn still has
    the rule available when it goes looking."""
    from vexis_agent.data import read_capabilities

    body = read_capabilities() or ""
    # Lowercase match — the markdown uses "Save the shortcut, not
    # the discovery path." with mixed case in the body.
    assert "shortcut" in body.lower()
    assert "discovery path" in body.lower()

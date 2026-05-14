"""CLAUDE.md structural-invariant tripwires.

Day 1 covers the size limit. Future structural rules (every
feature section ends with a Pointers block; the Invariants
section precedes the feature sections; etc.) land as siblings
in this file rather than spawning new test files. The general
``_invariants`` framing leaves room for those siblings.

Failing the size test is a documented signal to EXTRACT content
to ``docs/<feature>.md`` or fold cross-feature facts into the
``## Invariants`` section. Bumping ``CLAUDE_MD_MAX_LINES`` is
rarely the right answer — see CLAUDE.md ``## How to edit this
file`` for the maintenance policy.

Pattern mirrors ``tests/test_system_prompt_snapshots.py``'s
``FORBIDDEN_TOOL_NAME_PHRASES`` tripwire.

Design citation: ``.plans/claude-md-reorganization-research.md``
§7 + §6 Day 1b.
"""

from pathlib import Path


# Why 224 (originally 220, defended in the research doc):
#   - Cleaned target after the Day 1 rewrite: ~190 lines.
#   - 220 - 190 = 30 lines of headroom = exactly one new feature
#     section at the policy-prescribed maximum.
#   - 250 would invite the same comfort-driven drift the cleanup
#     fixed; 200 would risk tripping on Day 1 itself given
#     formatting variance.
#   - +4 (220 → 224): the brain-parity Invariants entry
#     ("transcript reads route through brain.iter_messages()") —
#     a new cross-feature contract, and the Invariants section is
#     still under ~40 lines. This is the sanctioned bump path.
# Bump only when the growth comes from new cross-feature
# contracts in the Invariants section AND that section is
# itself still under ~40 lines. Never bump for per-feature
# bloat.
CLAUDE_MD_MAX_LINES = 224


def test_claude_md_stays_under_size_limit() -> None:
    path = Path(__file__).resolve().parent.parent / "CLAUDE.md"
    line_count = len(path.read_text(encoding="utf-8").splitlines())
    assert line_count <= CLAUDE_MD_MAX_LINES, (
        f"CLAUDE.md is {line_count} lines (limit: "
        f"{CLAUDE_MD_MAX_LINES}). Extract content to "
        f"docs/<feature>.md or fold into ## Invariants. Bumping "
        f"the limit is rarely the right answer — see CLAUDE.md "
        f"'## How to edit this file'."
    )

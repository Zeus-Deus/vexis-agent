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


# Why 254 (originally 220, defended in the research doc):
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
#   - +7 (224 → 231): the capture-source-routing Invariants entry
#     ("Screenshot/livestream source picks go through
#     capture_source.resolve_source()") — another cross-feature
#     contract spanning Telegram, both CLIs, and the livestream
#     daemon. Invariants section is still ~42 lines, just at the cap.
#   - +23 (231 → 254): a brand-new feature section "Conversation
#     compression (Issue #11)" — ~22 lines, sits at the policy-
#     prescribed ~30-line per-feature ceiling. No Invariants growth
#     this time; the SUMMARY_PREFIX recursion-guard contract is
#     stated in the feature section's TL;DR rather than promoted
#     to Invariants because Invariants is already ≥40 lines and
#     the policy is "extract rather than grow Invariants". The
#     compressor's contract is referenced from the docstring of
#     ``core.brain.compressor.SUMMARY_PREFIX`` and pinned by
#     ``tests/test_compressor.py``.
#   - +25 (254 → 279): two-part bump landing Issues #9 + #10.
#     +4 of it extends the existing "Aux subsystems route through
#     ``brain.spawn_aux``" Invariant to include the new
#     ``allowed_tools`` allowlist contract from #10 — same bullet,
#     not a new entry, so the Invariants bullet count stays at 7.
#     The remaining +21 is a brand-new feature section
#     "File-mutation verifier footer (Issue #9)" — ~20 lines, well
#     inside the ~30-line per-feature ceiling. Invariants is now
#     ~49 lines (over the ~40 target); per established escape
#     hatch (see the compression bump above) the #9 contracts
#     (verifier-marker / recursion-guard interaction, hot-reload
#     flag semantics) live in the feature section's TL;DR rather
#     than growing Invariants further. Pinned by
#     ``tests/test_brain_spawn_aux_allowlist.py`` and
#     ``tests/test_brain_file_mutation_footer.py``.
# Bump only when the growth comes from new cross-feature
# contracts in the Invariants section AND that section is
# itself still under ~40 lines, OR a genuinely new feature
# section that respects the ~30-line per-section ceiling.
# Never bump for per-feature bloat in existing sections.
CLAUDE_MD_MAX_LINES = 279


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

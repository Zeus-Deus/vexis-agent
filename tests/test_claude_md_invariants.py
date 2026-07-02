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
#   - +21 (279 → 300): new feature section "Codemux orchestration
#     watcher" — ~20 lines, well inside the ~30-line per-section
#     ceiling. No Invariants growth this time; the conditional-
#     activation + plug-in source contracts live in the feature
#     section's TL;DR rather than being promoted to Invariants
#     because Invariants is already past the ~40-line target.
#     Pinned by ``tests/test_watcher.py``,
#     ``tests/test_watcher_prompt_injection.py``, and
#     ``tests/test_watcher_dispatch.py``.
#   - +21 (300 → 321): new feature section "Capability prompt
#     blocks (Issue #30)" — ~21 lines, inside the ~30-line
#     per-section ceiling. Documents the decomposition of the
#     CAPABILITIES.md monolith into per-tool prompt blocks. No
#     Invariants growth; the byte-identity contract
#     (assemble_capability_docs() == the golden snapshot) lives in
#     the feature section's TL;DR per the established escape hatch
#     (Invariants is already ≥40 lines). Pinned by
#     ``tests/test_capability_blocks.py``.
#   - +7 (321 → 328): a new Invariants bullet "Core subsystems are
#     individually gated, default-on (issue #39)" — the cross-feature
#     contract that every core subsystem (background tasks, watcher,
#     scheduling, goals, the two learning systems, the two transports)
#     carries its own default-on `enabled` switch, that skill/memory
#     vs relationship learning are DISTINCT switches, and that no
#     preset ships. This is exactly the sanctioned bump path: a NEW
#     cross-feature contract in Invariants, not per-feature bloat. The
#     full toggle map / transport-selection mechanics are extracted to
#     ``docs/modular-subsystems.md``; the bullet is a 7-line pointer.
#     Pinned by ``tests/test_subsystem_toggles.py`` and the
#     ``test_*_still_starts`` cases in ``tests/test_learning_curator.py``.
#   - +18 (328 → 346): new feature section "Web conversations
#     (issue #48)" — ~17 lines, inside the ~30-line per-section
#     ceiling. Per-conversation web sessions: the optional
#     ``session`` per-turn seam (``SessionView`` threaded
#     handler → brain, ``None`` = byte-identical legacy) plus the
#     transport-owned ``conversation_id`` → named-session /
#     chat-id-band mapping. No Invariants growth; the mapping and
#     back-compat contracts live in the section's TL;DR per the
#     established escape hatch and are pinned by
#     ``tests/test_web_conversations.py``.
# Bump only when the growth comes from new cross-feature
# contracts in the Invariants section AND that section is
# itself still under ~40 lines, OR a genuinely new feature
# section that respects the ~30-line per-section ceiling.
# Never bump for per-feature bloat in existing sections.
CLAUDE_MD_MAX_LINES = 346


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

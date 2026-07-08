"""Capability-block decomposition contract (issue #30).

The former ``CAPABILITIES.md`` monolith is decomposed into
per-capability prompt blocks that live next to the code they document.
These tests pin the contract that made the decomposition safe to ship:

  1. **Byte-identity.** ``assemble_capability_docs()`` reproduces the
     pre-decomposition monolith verbatim, against the frozen golden
     snapshot ``tests/data/capabilities_golden.md``. This is the
     "no change to what the agent sees in-prompt" acceptance criterion.
  2. **Both brains.** claude-code AND opencode embed the assembled
     capabilities verbatim, so the decomposition holds for every brain.
  3. **Browser self-documents.** The acceptance flagship: the
     web-browsing block is owned by ``tools/browser/capability.py``,
     not by ``CAPABILITIES.md``.
  4. **Core shrank.** ``CAPABILITIES.md`` keeps only identity + the
     add-on model; per-tool how-to moved out.
  5. **No section lost.** Every ``##`` section of the monolith is owned
     by exactly one registered block.
  6. **Extensible without touching CAPABILITIES.md.** A new block
     registered at runtime surfaces in the assembled docs; the file is
     never edited.

If you intentionally change capability prose, regenerate the golden:
the diff in this test is the proof the change is deliberate. Update
the golden in the SAME PR and explain why in the commit.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

import vexis_agent.core.capabilities as cap
from vexis_agent.core.capabilities import (
    assemble_capability_docs,
    iter_capability_blocks,
    register_capability_block,
)

GOLDEN = Path(__file__).resolve().parent / "data" / "capabilities_golden.md"
CAPABILITIES_MD = (
    Path(__file__).resolve().parent.parent
    / "vexis_agent"
    / "data"
    / "CAPABILITIES.md"
)


@pytest.fixture
def restore_registry():
    """Snapshot/restore the process-global registry so the runtime-
    registration tests can mutate it without leaking into the rest of
    the suite."""
    # Ensure builtin modules are loaded first so the snapshot is the
    # canonical state, not the pre-load empty one.
    iter_capability_blocks()
    snap = dict(cap._REGISTRY)
    loaded = cap._loaded
    try:
        yield
    finally:
        cap._REGISTRY.clear()
        cap._REGISTRY.update(snap)
        cap._loaded = loaded


# ──────────────────────────────────────────────────────────────────
# 1. Byte-identity to the frozen monolith
# ──────────────────────────────────────────────────────────────────


def test_assembled_matches_golden_byte_for_byte():
    """The whole point: the model sees the same prompt. A single-byte
    drift fails here. If this fails after an intentional prose edit,
    regenerate tests/data/capabilities_golden.md in the same PR."""
    golden = GOLDEN.read_text(encoding="utf-8")
    assembled = assemble_capability_docs()
    assert assembled == golden, (
        "assemble_capability_docs() drifted from the frozen monolith. "
        f"(assembled {len(assembled)} bytes vs golden {len(golden)} bytes)"
    )


def test_assembled_ends_with_single_newline():
    """The monolith ended with exactly one trailing newline; the
    join must not introduce a double newline that would perturb the
    prefix-cache hash for the rest of the prompt."""
    assembled = assemble_capability_docs()
    assert assembled.endswith("\n")
    assert not assembled.endswith("\n\n")


# ──────────────────────────────────────────────────────────────────
# 2. Both brains embed it verbatim
# ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("brain", ["claude_code", "opencode"])
def test_both_brains_embed_assembled_capabilities(brain):
    from vexis_agent.core.brain.claude_code import build_system_prompt
    from vexis_agent.core.brain.opencode import (
        _build_system_prompt_for_workspace,
    )

    golden = GOLDEN.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d)
        if brain == "claude_code":
            prompt = build_system_prompt(ws)
        else:
            prompt = _build_system_prompt_for_workspace(ws)
    assert golden in prompt, (
        f"{brain}: assembled capabilities not embedded verbatim in the "
        "system prompt"
    )
    # SOUL must still precede the capabilities section.
    assert prompt.find("Vexis") < prompt.find("# Capabilities")


# ──────────────────────────────────────────────────────────────────
# 3. Browser documents itself (acceptance flagship)
# ──────────────────────────────────────────────────────────────────


def test_browser_block_is_owned_by_the_browser_addon():
    """The web-browsing block moved OUT of core builtins into the browser
    add-on (``vexis_agent/addons/browser/capability.py``). So it is NOT
    in the builtins-only ``iter_capability_blocks()``, and the module
    that owns its text lives under the add-on — next to the browser
    integration. (Presence in the assembled prompt when the add-on loads
    is covered by tests/test_addon_capability_block.py.)"""
    names = {b.name for b in iter_capability_blocks()}
    assert "web-browsing" not in names

    from vexis_agent.addons.browser.capability import web_browsing_block

    block = web_browsing_block()
    # The browser is now delivered as the vexis-browser MCP server; the
    # block leads with the MCP tools and keeps the vexis-browse CLI as
    # an equivalent fallback.
    assert block.startswith("## Web browsing — `vexis-browser`")
    assert "stealth Camoufox" in block
    assert "browser_navigate" in block  # primary: MCP tool name
    assert "vexis-browse" in block  # back-compat CLI still documented


def test_browser_docs_no_longer_in_capabilities_md():
    """The flagship move: the browser section left the monolith."""
    core = CAPABILITIES_MD.read_text(encoding="utf-8")
    assert "vexis-browse" not in core
    assert "Web browsing" not in core


# ──────────────────────────────────────────────────────────────────
# 4. CAPABILITIES.md shrank to the stable core
# ──────────────────────────────────────────────────────────────────


def test_capabilities_md_is_core_only():
    core = CAPABILITIES_MD.read_text(encoding="utf-8")
    # Keeps: identity + the add-on model.
    assert core.startswith("# Capabilities")
    assert "## Adding new abilities" in core
    # Drops: every per-tool how-to that moved into an owning module.
    for tool_marker in (
        "vexis-browse",
        "vexis-stream",
        "vexis-sandbox",
        "vexis-display",
        "vexis-bg",
        "vexis-mem",
        "vexis-skill",
        "vexis-desktop",
        "vexis-watch",
        "vexis-agent schedule",
        "omarchy-kb",
    ):
        assert tool_marker not in core, (
            f"{tool_marker!r} should have moved out of CAPABILITIES.md "
            "into its owning capability module"
        )
    # And it genuinely shrank (the monolith was ~1000 lines).
    assert len(core.splitlines()) < 60


# ──────────────────────────────────────────────────────────────────
# 5. No section lost or duplicated
# ──────────────────────────────────────────────────────────────────


def test_every_monolith_section_owned_by_exactly_one_block():
    """Each ``## `` header in the golden monolith must be the start of
    exactly one registered block's text — nothing dropped, nothing
    double-claimed."""
    golden = GOLDEN.read_text(encoding="utf-8")
    golden_headers = re.findall(r"(?m)^## .*$", golden)

    owned: list[str] = []
    for block in iter_capability_blocks():
        text = block.provider() or ""
        owned.extend(re.findall(r"(?m)^## .*$", text))

    assert sorted(owned) == sorted(golden_headers), (
        "section ownership mismatch — a monolith section is missing "
        "from the blocks or claimed twice.\n"
        f"only in golden: {sorted(set(golden_headers) - set(owned))}\n"
        f"only in blocks: {sorted(set(owned) - set(golden_headers))}"
    )


def test_core_block_is_first_and_reads_capabilities_md():
    blocks = iter_capability_blocks()
    assert blocks[0].name == "core"
    assert blocks[0].order == cap.CORE_BLOCK_ORDER
    core_text = blocks[0].provider()
    assert core_text is not None
    assert core_text.startswith("# Capabilities")


# ──────────────────────────────────────────────────────────────────
# 6. Extensible without editing CAPABILITIES.md
# ──────────────────────────────────────────────────────────────────


def test_new_capability_surfaces_without_editing_capabilities_md(
    restore_registry,
):
    before = CAPABILITIES_MD.read_text(encoding="utf-8")
    sentinel = "## Sentinel capability — registered at runtime"
    register_capability_block(
        "sentinel-runtime",
        order=999.0,
        provider=lambda: sentinel + "\n\nProof that adding a capability "
        "needs no CAPABILITIES.md edit.",
    )
    assembled = assemble_capability_docs()
    assert sentinel in assembled
    # The file on disk was never touched.
    assert CAPABILITIES_MD.read_text(encoding="utf-8") == before


def test_provider_exception_is_skipped_not_fatal(restore_registry):
    """A capability block whose provider raises must NOT take down the
    whole prompt build — assemble logs and skips it, keeping every
    other block. (Defensive degradation: a broken tool's docs vanish
    rather than crashing every brain turn. The golden test guards the
    happy path; this guards the failure path.)"""
    def _boom():
        raise RuntimeError("provider blew up")

    register_capability_block("explosive-runtime", order=997.0, provider=_boom)
    # Should not raise, and should still contain the real blocks.
    assembled = assemble_capability_docs()
    assert "## Scheduling — `vexis-agent schedule`" in assembled
    assert "# Capabilities" in assembled


def test_provider_returning_none_is_dropped(restore_registry):
    """A provider may return None (or empty) to hide its block;
    assembly drops it cleanly, leaving the output identical to the
    golden — no stray blank separator from the dropped block."""
    golden = GOLDEN.read_text(encoding="utf-8")
    register_capability_block("hidden-runtime", order=996.0, provider=lambda: None)
    register_capability_block("empty-runtime", order=995.0, provider=lambda: "   ")
    assert assemble_capability_docs() == golden


def test_duplicate_name_rejected(restore_registry):
    # "scheduling" is a core builtin block name (order 14).
    with pytest.raises(ValueError, match="already registered"):
        register_capability_block(
            "scheduling", order=998.0, provider=lambda: "x"
        )


def test_duplicate_order_rejected(restore_registry):
    # order 14 belongs to the scheduling builtin block.
    with pytest.raises(ValueError, match="order .* already taken"):
        register_capability_block(
            "totally-new-name", order=14, provider=lambda: "x"
        )


# ──────────────────────────────────────────────────────────────────
# 7. Core capability modules stay add-on-import-free (core/ ↔ addons/)
# ──────────────────────────────────────────────────────────────────


def _imports_from_addons(path: Path) -> list[str]:
    """AST-scan ``path`` for any import of ``vexis_agent.addons.*``.

    Checked via AST (not a substring grep) so docstring prose that
    *mentions* an add-on path doesn't trip the gate."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("vexis_agent.addons"):
                bad.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("vexis_agent.addons"):
                    bad.append(alias.name)
    return bad


def test_self_extension_block_does_not_import_addons():
    """The self-extension block is a CORE capability module: it must not
    import from any add-on. (The codemux-orchestration block, which used
    to be the core block guarded here, now lives IN the codemux add-on —
    where importing core's PluginContext is legitimate. Core staying
    add-on-agnostic is pinned by test_codemux_extraction_invariant.py.)"""
    path = (
        Path(__file__).resolve().parent.parent
        / "vexis_agent"
        / "core"
        / "capabilities"
        / "self_extension.py"
    )
    bad = _imports_from_addons(path)
    assert not bad, f"self_extension.py imports from add-ons: {bad}"


# ──────────────────────────────────────────────────────────────────
# 8. Background-subagent lifecycle block (issue #61)
# ──────────────────────────────────────────────────────────────────


def test_background_subagents_block_registered_and_positioned():
    """The issue-61 steering block registers at order 9.25 — directly
    after background-tasks (9) and before goals (9.5) — so it reads as
    an elaboration of the in-turn-subagent note in background_capability."""
    blocks = {b.name: b for b in iter_capability_blocks()}
    assert "background-subagents" in blocks
    assert blocks["background-subagents"].order == 9.25
    # Adjacency: nothing sits between background-tasks and this block.
    ordered = [b.name for b in iter_capability_blocks()]
    i = ordered.index("background-subagents")
    assert ordered[i - 1] == "background-tasks"
    assert ordered[i + 1] == "goals"


def test_background_subagents_block_pins_lifecycle_vocabulary():
    """Pin the load-bearing strings so prose drift fails loudly: the
    config knob, its env translation, the Agent-tool param, and the
    route-elsewhere guidance (kanban / /goal)."""
    from vexis_agent.tools.background_subagents_capability import (
        background_subagents_block,
    )

    block = background_subagents_block()
    assert block.startswith("## Background subagents (Agent tool)")
    assert "brain.background_agent_wait" in block
    assert "CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS" in block
    assert "run_in_background: false" in block
    assert "run_in_background: true" in block
    # Long autonomous work is routed OUT of a background subagent.
    assert "kanban" in block
    assert "/goal" in block


def test_background_subagents_block_in_assembled_output():
    """It assembles into the full capabilities section at the right
    position — between the background-tasks and goals sections."""
    assembled = assemble_capability_docs()
    heading = "## Background subagents (Agent tool)"
    assert heading in assembled
    assert (
        assembled.index("## Background tasks")
        < assembled.index(heading)
        < assembled.index("## Goals — `/goal`")
    )


def test_every_builtin_capability_module_is_addon_import_free():
    """Every module in _BUILTIN_CAPABILITY_MODULES is core — none may
    import from vexis_agent.addons.*. The codemux block's move out of
    this list (into the add-on) is exactly what this guards: a core
    builtin can't depend on an add-on."""
    import importlib.util

    repo_root = Path(__file__).resolve().parent.parent
    offenders: dict[str, list[str]] = {}
    for module_path in cap._BUILTIN_CAPABILITY_MODULES:
        spec = importlib.util.find_spec(module_path)
        assert spec is not None and spec.origin, (
            f"builtin capability module {module_path!r} is not importable"
        )
        src = Path(spec.origin)
        # Sanity: every builtin lives under vexis_agent/, never addons/.
        assert "addons" not in src.relative_to(repo_root).parts, (
            f"builtin capability module {module_path!r} lives under addons/"
        )
        bad = _imports_from_addons(src)
        if bad:
            offenders[module_path] = bad
    assert not offenders, f"builtin capability modules import add-ons: {offenders}"

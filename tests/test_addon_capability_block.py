"""Add-on hook onto the core "Capabilities" section.

``ctx.register_capability_block(name, provider, *, order=)`` lets an
add-on contribute a how-to block to the system-prompt Capabilities
section — the same section the core ``*_capability.py`` modules own.

The wiring is module-level: the hook delegates through
``AddonRuntime.add_capability_block`` to the core
``register_capability_block``, so the add-on block lands in the SAME
``_REGISTRY`` that ``assemble_capability_docs()`` reads. That registry
is process-global, so these tests snapshot/restore it (the
``restore_registry`` fixture) to avoid leaking into the rest of the
suite — mirroring ``tests/test_capability_blocks.py``.

Pins:
  * an add-on block's text appears in ``assemble_capability_docs()`` at
    the position its ``order`` dictates;
  * duplicate ``name`` (against core or another add-on) is rejected;
  * duplicate ``order`` (against core or another add-on) is rejected;
  * the hook also bookkeeps into the runtime so ``capability_blocks()``
    reflects what the add-on contributed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import vexis_agent.core.capabilities as cap
from vexis_agent.core.addons.context import (
    AddonConfig,
    PluginContext,
    make_context,
)
from vexis_agent.core.addons.errors import AddonConflictError
from vexis_agent.core.addons.registry import AddonRuntime
from vexis_agent.core.capabilities import (
    assemble_capability_docs,
    iter_capability_blocks,
)


@pytest.fixture
def restore_registry():
    """Snapshot/restore the process-global core registry so add-on
    registrations don't leak across tests (the hook delegates into it)."""
    iter_capability_blocks()  # ensure core blocks are loaded first
    snap = dict(cap._REGISTRY)
    loaded = cap._loaded
    try:
        yield
    finally:
        cap._REGISTRY.clear()
        cap._REGISTRY.update(snap)
        cap._loaded = loaded


@pytest.fixture
def runtime() -> AddonRuntime:
    return AddonRuntime(user_id="test-user")


@pytest.fixture
def ctx(runtime: AddonRuntime) -> PluginContext:
    return make_context(
        runtime,
        addon_name="demo",
        addon_dir=Path("/tmp/demo"),
        config=AddonConfig(),
    )


def test_addon_block_appears_in_assembled_docs(
    restore_registry, ctx: PluginContext
):
    sentinel = "## Demo add-on capability\n\nProvided by an add-on hook."
    ctx.register_capability_block(
        "demo-cap", lambda: sentinel, order=900.0
    )
    assembled = assemble_capability_docs()
    assert sentinel in assembled


def test_addon_block_sorts_by_order_between_core_blocks(
    restore_registry, ctx: PluginContext
):
    """A high order lands at the END; a tiny-but-nonzero order lands
    right after the core block (order 0). The block placement is purely
    a function of ``order``, shared with the core blocks."""
    late = "## Late add-on block"
    early = "## Early add-on block"
    ctx.register_capability_block("late-cap", lambda: late, order=999.0)
    ctx.register_capability_block("early-cap", lambda: early, order=0.5)

    assembled = assemble_capability_docs()
    # early-cap (0.5) sits before late-cap (999.0).
    assert assembled.index(early) < assembled.index(late)
    # early-cap (0.5) sits after the core block (order 0).
    assert assembled.index("# Capabilities") < assembled.index(early)
    # late-cap is the last section in the doc.
    assert assembled.rstrip().endswith(late)


def test_addon_block_provider_returning_none_is_dropped(
    restore_registry, ctx: PluginContext
):
    """A None-returning provider hides the block, leaving output equal
    to the unmodified assembly (no stray separator)."""
    before = assemble_capability_docs()
    ctx.register_capability_block("hidden-cap", lambda: None, order=901.0)
    assert assemble_capability_docs() == before


def test_addon_block_records_into_runtime(
    restore_registry, ctx: PluginContext, runtime: AddonRuntime
):
    ctx.register_capability_block("booked-cap", lambda: "## x", order=902.0)
    blocks = list(runtime.capability_blocks())
    assert len(blocks) == 1
    assert blocks[0].name == "booked-cap"
    assert blocks[0].order == 902.0
    assert blocks[0].addon_name == "demo"
    assert blocks[0].provider() == "## x"


def test_duplicate_name_against_core_rejected(
    restore_registry, ctx: PluginContext
):
    # "scheduling" is a core builtin block name (order 14). ("web-browsing"
    # is no longer core — it moved into the browser add-on.)
    with pytest.raises(AddonConflictError, match="already registered"):
        ctx.register_capability_block(
            "scheduling", lambda: "## x", order=903.0
        )


def test_duplicate_name_across_addons_rejected(
    restore_registry, runtime: AddonRuntime
):
    ctx_a = make_context(
        runtime, addon_name="a", addon_dir=Path("/tmp/a"), config=AddonConfig()
    )
    ctx_b = make_context(
        runtime, addon_name="b", addon_dir=Path("/tmp/b"), config=AddonConfig()
    )
    ctx_a.register_capability_block("shared", lambda: "## a", order=904.0)
    with pytest.raises(AddonConflictError, match="already registered"):
        ctx_b.register_capability_block("shared", lambda: "## b", order=905.0)


def test_duplicate_order_against_core_rejected(
    restore_registry, ctx: PluginContext
):
    # order 0 is CORE_BLOCK_ORDER (the shrunk CAPABILITIES.md).
    with pytest.raises(AddonConflictError, match="conflicts with an existing"):
        ctx.register_capability_block(
            "new-name", lambda: "## x", order=cap.CORE_BLOCK_ORDER
        )


def test_duplicate_order_across_addons_rejected(
    restore_registry, runtime: AddonRuntime
):
    ctx_a = make_context(
        runtime, addon_name="a", addon_dir=Path("/tmp/a"), config=AddonConfig()
    )
    ctx_b = make_context(
        runtime, addon_name="b", addon_dir=Path("/tmp/b"), config=AddonConfig()
    )
    ctx_a.register_capability_block("a-cap", lambda: "## a", order=906.0)
    with pytest.raises(AddonConflictError, match="conflicts with an existing"):
        ctx_b.register_capability_block("b-cap", lambda: "## b", order=906.0)


# ──────────────────────────────────────────────────────────────────
# The codemux add-on uses this hook for its real capability block.
# (codemux-orchestration moved out of core/tools into the add-on; the
# hook above is the seam it lands through.)
# ──────────────────────────────────────────────────────────────────


def test_codemux_addon_registers_its_capability_block(restore_registry):
    """The real codemux ``register()`` contributes the
    ``codemux-orchestration`` block (order 15) via the hook — so the
    "Codemux orchestration" section assembles ONLY when codemux loads,
    not for every install. This is the move from
    ``tools/watch_capability.py`` (core) into the add-on."""
    from vexis_agent.addons.codemux import register

    addon_dir = (
        Path(__file__).resolve().parent.parent
        / "vexis_agent"
        / "addons"
        / "codemux"
    )
    runtime = AddonRuntime(user_id="test-user")
    ctx = make_context(
        runtime,
        addon_name="codemux",
        addon_dir=addon_dir,
        config=AddonConfig(),
    )

    # Absent before load.
    assert "## Codemux orchestration" not in assemble_capability_docs()
    assert "codemux-orchestration" not in cap._REGISTRY

    register(ctx)

    # Present after load: in the core registry (order 15), bookkept in
    # the runtime, and sorted last in the assembled docs.
    assert cap._REGISTRY["codemux-orchestration"].order == 15
    assert "codemux-orchestration" in {
        r.name for r in runtime.capability_blocks()
    }
    assembled = assemble_capability_docs()
    assert "## Codemux orchestration — `vexis-watch`" in assembled
    # codemux (order 15) sorts after the last core builtin (scheduling,
    # order 14). web-browsing is no longer assembled here — it's a
    # separate add-on that this test doesn't load.
    assert assembled.index("## Codemux orchestration") > assembled.index(
        "## Scheduling"
    )


def test_codemux_capability_block_is_not_a_core_builtin():
    """The codemux block must NOT be in _BUILTIN_CAPABILITY_MODULES —
    that's the whole point of the move. (If it crept back in, the
    builtins-only golden would carry it and the add-on would
    double-register on load.)"""
    for module_path in cap._BUILTIN_CAPABILITY_MODULES:
        assert "watch_capability" not in module_path
        assert "addons" not in module_path

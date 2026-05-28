"""Verify the bundled codemux add-on discovers and loads cleanly.

This is the simplest end-to-end test for the bundled add-on
pipeline: drop ``addons/codemux/`` in the wheel, set
``addons.enabled: [codemux]`` in user config, and the loader
should find it, parse the manifest, run register(ctx), and
record success.

Tests in this file MUST pass at every commit during the Phase B
extraction — otherwise the user's codemux integration breaks
mid-migration. The behaviour the add-on registers grows commit
by commit; what stays constant is "the add-on loads."
"""

from __future__ import annotations

from vexis_agent.core.addons import (
    AddonRuntime,
    bundled_addons_root,
    discover_addons,
    load_addon,
)


def test_codemux_addon_discovers_as_bundled():
    """The codemux add-on lives in ``vexis_agent/addons/codemux/``,
    which is the bundled discovery root. With ``codemux`` enabled,
    the loader finds it as ``source='bundled'``."""
    discovered = discover_addons(enabled=["codemux"])
    by_name = {d.manifest.name: d for d in discovered}
    assert "codemux" in by_name, (
        f"codemux add-on not discovered. Found: {list(by_name)}. "
        f"Bundled root: {bundled_addons_root()}"
    )
    me = by_name["codemux"]
    assert me.source == "bundled"
    assert me.manifest.kind == "standalone"


def test_codemux_addon_manifest_declares_required_mcp():
    """The manifest pins that the codemux MCP is required (not
    optional) — surfaced by ``vexis-addons doctor`` when missing."""
    discovered = discover_addons(enabled=["codemux"])
    me = next(d for d in discovered if d.manifest.name == "codemux")
    mcps = me.manifest.requires.mcp_servers
    codemux_mcp = next((m for m in mcps if m.name == "codemux"), None)
    assert codemux_mcp is not None
    assert codemux_mcp.optional is False


def test_codemux_addon_registers_via_register_function():
    """End-to-end: discovery → import → register(ctx) → runtime
    records register_ok=True. This is the smoke test that pins the
    add-on pipeline against changes that break bundled add-ons."""
    discovered = discover_addons(enabled=["codemux"])
    me = next(d for d in discovered if d.manifest.name == "codemux")
    runtime = AddonRuntime()
    ok = load_addon(me, runtime)
    assert ok is True
    loaded = next(a for a in runtime.loaded_addons() if a.manifest.name == "codemux")
    assert loaded.register_ok is True
    assert loaded.register_error is None


def test_codemux_addon_installs_skill():
    """The codemux add-on ships skills/codemux.md and registers it
    via ctx.register_skill. The skill_install layer copies it into
    workspaces at session start (covered by separate tests once
    that layer lands; here we just verify the registration)."""
    discovered = discover_addons(enabled=["codemux"])
    me = next(d for d in discovered if d.manifest.name == "codemux")
    runtime = AddonRuntime()
    load_addon(me, runtime)
    skills = [s for s in runtime.skills() if s.addon_name == "codemux"]
    assert len(skills) == 1
    assert skills[0].skill_file.name == "codemux.md"
    assert skills[0].skill_file.is_file()

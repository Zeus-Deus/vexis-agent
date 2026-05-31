"""Add-on MCP defaults reach the brain.

Pins the wiring that makes ``ctx.register_mcp_server_default(spec)`` no
longer inert: at daemon startup ``merge_addon_mcp_defaults`` folds
every registered default into the active brain's native MCP config, so
the next brain spawn actually gets the tool.

Coverage:
- end-to-end: an add-on that registers a default makes that server
  show up in claude-code's ``.mcp.json`` AND opencode's
  ``opencode.json`` (brain parity);
- merge precedence: user ``mcp-servers.yaml`` wins on a name collision,
  add-on defaults only fill gaps;
- idempotency: running the merge twice neither duplicates nor corrupts
  entries;
- no add-on defaults => no write (we don't churn the native file);
- the test brain (BrainNull) is handed the merged specs, no exception.

The brains are constructed against a tmp workspace + a tmp SessionStore
+ a fresh RunningTasks (the MCP writer only touches ``self._workspace``,
but the ctors require the other two). conftest's autouse
``_isolate_vexis_dir`` repoints ``core.paths.vexis_dir`` at a per-test
throwaway dir; the ``vexis_home`` fixture resolves that same dir so a
yaml we drop is exactly what ``detect_mcp_servers`` reads.

NB: ``detect_mcp_servers`` PATH-filters stdio entries via
``shutil.which`` — a fake user ``command`` is silently dropped — so
the user-side entries in the precedence tests are REMOTE (``url``)
servers, which have no binary check and resolve deterministically.

Run: ``python -m pytest tests/test_addon_mcp_defaults.py``.
"""
from __future__ import annotations

import json

import pytest

from vexis_agent.core.addon_mcp import (
    merge_addon_mcp_defaults,
    resolve_merged_mcp_specs,
)
from vexis_agent.core.addons.context import make_context
from vexis_agent.core.addons.registry import AddonRuntime
from vexis_agent.core.brain.base import McpServerSpec
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore

# opencode namespaces every vexis-owned MCP server under this prefix.
_VEXIS_PREFIX = "vexis-"


# — brain factories (config-only; the MCP writer needs the workspace) --


def _claude(tmp_path):
    from vexis_agent.core.brain.claude_code import ClaudeCodeBrain

    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    return ClaudeCodeBrain(
        workspace=ws,
        session=SessionStore(tmp_path / "sessions.json"),
        running_tasks=RunningTasks(),
    )


def _opencode(tmp_path):
    from vexis_agent.core.brain.opencode import OpenCodeBrain

    ws = tmp_path / "ws-oc"
    ws.mkdir(parents=True, exist_ok=True)
    return OpenCodeBrain(
        workspace=ws,
        session=SessionStore(tmp_path / "sessions-oc.json"),
        running_tasks=RunningTasks(),
    )


# — registration + yaml helpers ------------------------------------


def _runtime_with_default(spec: McpServerSpec, addon: str = "demo") -> AddonRuntime:
    """An ``AddonRuntime`` after one add-on registered ``spec`` via the
    real ``PluginContext`` — exercises the public registration surface,
    not the runtime internals."""
    rt = AddonRuntime()
    ctx = make_context(
        rt,
        addon_name=addon,
        addon_dir=None,  # the MCP hook never touches addon_dir
        config=None,
    )
    ctx.register_mcp_server_default(spec)
    return rt


def _write_user_yaml(home, servers: list[dict]) -> None:
    import yaml

    (home / "mcp-servers.yaml").write_text(
        yaml.safe_dump({"servers": servers}), encoding="utf-8"
    )


@pytest.fixture
def vexis_home(tmp_path, monkeypatch):
    """A per-test vexis state dir that ``detect_mcp_servers`` reads.

    ``setup_wizard`` binds ``vexis_dir`` at import (``from
    ...core.paths import vexis_dir``), so conftest's autouse patch on
    ``core.paths.vexis_dir`` does NOT reach ``_user_mcp_specs`` — it
    keeps calling the module-level binding. Patch THAT binding directly
    so a yaml we drop here is exactly the file the reader opens."""
    home = tmp_path / "vexis_home"
    home.mkdir()
    monkeypatch.setattr("vexis_agent.setup_wizard.vexis_dir", lambda: home)
    return home


def _read_claude(brain):
    return json.loads((brain._workspace / ".mcp.json").read_text())["mcpServers"]


def _read_opencode(brain):
    return json.loads((brain._workspace / "opencode.json").read_text())["mcp"]


# — end-to-end: the default reaches each brain ---------------------


def test_addon_default_reaches_claude_code(tmp_path, vexis_home):
    rt = _runtime_with_default(
        McpServerSpec(name="weather", command="weather-mcp", args=["--x"])
    )
    brain = _claude(tmp_path)

    n = merge_addon_mcp_defaults(brain, rt)

    servers = _read_claude(brain)
    assert "weather" in servers
    assert servers["weather"]["command"] == "weather-mcp"
    assert servers["weather"]["args"] == ["--x"]
    assert n >= 1


def test_addon_default_reaches_opencode(tmp_path, vexis_home):
    rt = _runtime_with_default(
        McpServerSpec(name="weather", command="weather-mcp", args=["--x"])
    )
    brain = _opencode(tmp_path)

    merge_addon_mcp_defaults(brain, rt)

    block = _read_opencode(brain)
    key = _VEXIS_PREFIX + "weather"
    assert key in block
    assert block[key]["type"] == "local"
    assert block[key]["command"] == ["weather-mcp", "--x"]
    assert block[key]["enabled"] is True


# — precedence: user mcp-servers.yaml wins on name collision -------
# NB: user entries are REMOTE (url) — detect_mcp_servers PATH-filters
# stdio entries, so a fake user command would be silently dropped.


def test_user_yaml_wins_over_addon_default(tmp_path, vexis_home):
    # Same NAME from both sources, different shape. User wins: the
    # add-on's stdio entry is fully replaced by the user's remote one.
    _write_user_yaml(
        vexis_home, [{"name": "weather", "url": "https://user.example/mcp"}]
    )
    rt = _runtime_with_default(
        McpServerSpec(name="weather", command="addon-weather", args=["A"])
    )
    brain = _claude(tmp_path)

    merge_addon_mcp_defaults(brain, rt)

    servers = _read_claude(brain)
    assert servers["weather"].get("url") == "https://user.example/mcp"
    assert "command" not in servers["weather"]


def test_resolve_precedence_user_overrides_addon(tmp_path, vexis_home):
    # resolve_merged_mcp_specs directly: a colliding name resolves to
    # the user's entry, with no duplication.
    _write_user_yaml(
        vexis_home, [{"name": "dup", "url": "https://user.example/mcp"}]
    )
    rt = _runtime_with_default(McpServerSpec(name="dup", command="from-addon"))

    specs = resolve_merged_mcp_specs(rt)
    by_name = {s.name: s for s in specs}
    assert by_name["dup"].url == "https://user.example/mcp"
    assert by_name["dup"].command is None  # user (remote) won
    assert [s.name for s in specs].count("dup") == 1


def test_addon_default_fills_gap_alongside_user_yaml(tmp_path, vexis_home):
    # Distinct names: the add-on default fills a gap the user yaml does
    # not cover; both are present (gap-fill, not clobber).
    _write_user_yaml(
        vexis_home, [{"name": "fs", "url": "https://fs.example/mcp"}]
    )
    rt = _runtime_with_default(
        McpServerSpec(name="weather", command="weather-mcp")
    )

    by_name = {s.name: s for s in resolve_merged_mcp_specs(rt)}

    assert by_name["weather"].command == "weather-mcp"  # add-on gap-fill
    assert "fs" in by_name  # distinct user entry survives


# — idempotency: two runs == one run ------------------------------


def test_merge_is_idempotent(tmp_path, vexis_home):
    rt = _runtime_with_default(
        McpServerSpec(name="weather", command="weather-mcp")
    )
    brain = _claude(tmp_path)

    merge_addon_mcp_defaults(brain, rt)
    first = (brain._workspace / ".mcp.json").read_text()
    merge_addon_mcp_defaults(brain, rt)
    second = (brain._workspace / ".mcp.json").read_text()

    assert first == second  # byte-identical; no dup, no corruption
    servers = json.loads(second)["mcpServers"]
    assert list(servers).count("weather") == 1


# — no defaults => no write ---------------------------------------


def test_no_defaults_no_write(tmp_path, vexis_home):
    rt = AddonRuntime()  # nothing registered
    brain = _claude(tmp_path)

    n = merge_addon_mcp_defaults(brain, rt)

    assert n == 0
    assert not (brain._workspace / ".mcp.json").exists()


# — the test brain receives the merged specs, no exception --------


def test_null_brain_receives_specs(tmp_path, vexis_home):
    # BrainNull.write_mcp_config records the call (it does not raise);
    # the merge hands it the resolved specs and reports them — proof
    # the brain-agnostic path is exercised end-to-end on the test fake.
    from vexis_agent.core.brain.null import BrainNull

    brain = BrainNull()
    rt = _runtime_with_default(McpServerSpec(name="weather", command="w"))

    n = merge_addon_mcp_defaults(brain, rt)

    assert n == 1
    written = brain._mcp_writes[-1]
    assert any(s.name == "weather" for s in written)

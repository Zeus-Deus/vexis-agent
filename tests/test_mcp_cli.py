"""Phase 5m — vexis-agent mcp <subcommand>.

CLI for managing ~/.vexis/mcp-servers.yaml. Wraps the same
write-both-native-files pipeline the wizard uses, so add/remove
through the CLI is functionally equivalent to hand-editing the
yaml + re-running setup.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from vexis_agent.daemon import mcp as mcp_mod


# ── parse_env_assignments ─────────────────────────────────────────


def test_parse_env_assignments_basic() -> None:
    out = mcp_mod.parse_env_assignments(["X=y", "FOO=bar"])
    assert out == {"X": "y", "FOO": "bar"}


def test_parse_env_assignments_allows_empty_value() -> None:
    """Some MCP servers accept empty-value env vars to flag a feature."""
    out = mcp_mod.parse_env_assignments(["FLAG="])
    assert out == {"FLAG": ""}


def test_parse_env_assignments_rejects_missing_eq() -> None:
    with pytest.raises(ValueError):
        mcp_mod.parse_env_assignments(["BARE_KEY"])


def test_parse_env_assignments_rejects_empty_key() -> None:
    with pytest.raises(ValueError):
        mcp_mod.parse_env_assignments(["=value"])


# ── add_server / remove_server / list_servers / refresh ───────────


def test_add_server_writes_yaml(isolated_paths) -> None:
    result = mcp_mod.add_server(
        name="my-tool",
        command="my-tool",
        args=["--mcp"],
        env={"K": "v"},
    )
    assert not result.replaced_existing
    body = yaml.safe_load(result.yaml_path.read_text())
    assert body == {
        "servers": [{
            "name": "my-tool",
            "command": "my-tool",
            "args": ["--mcp"],
            "env": {"K": "v"},
        }]
    }


def test_add_server_replaces_existing(isolated_paths) -> None:
    mcp_mod.add_server(name="x", command="x")
    result = mcp_mod.add_server(name="x", command="x", args=["--new-flag"])
    assert result.replaced_existing
    body = yaml.safe_load(result.yaml_path.read_text())
    assert body["servers"] == [{
        "name": "x",
        "command": "x",
        "args": ["--new-flag"],
    }]


def test_add_server_refreshes_workspace_natives(isolated_paths) -> None:
    """add_server triggers refresh_workspace, which writes BOTH
    per-brain native files. Verifies the universal-config behaviour
    composes through the CLI. (Workspace path resolves via
    setup_wizard.workspace_path which honours VEXIS_WORKSPACE so
    our fixture's env var lines up here even with conftest's
    vexis_dir autouse patch.)"""
    mcp_mod.add_server(
        name="example", command="bash", args=["-c", "echo hi"]
    )
    workspace = isolated_paths["workspace"]
    assert (workspace / ".mcp.json").is_file()
    assert (workspace / "opencode.json").is_file()


def test_remove_server_drops_entry(isolated_paths) -> None:
    mcp_mod.add_server(name="a", command="a")
    mcp_mod.add_server(name="b", command="b")
    result = mcp_mod.remove_server(name="a")
    assert result.found
    body = yaml.safe_load(result.yaml_path.read_text())
    names = [e["name"] for e in body["servers"]]
    assert names == ["b"]


def test_remove_server_noop_when_missing(isolated_paths) -> None:
    """No-op when the server isn't in the yaml. Refresh isn't
    triggered (no work to do)."""
    mcp_mod.add_server(name="present", command="x")
    result = mcp_mod.remove_server(name="absent")
    assert not result.found
    assert result.refreshed_paths == []


def test_list_servers_combines_builtin_and_user(
    isolated_paths, monkeypatch
) -> None:
    """list_servers shows BOTH user-declared (yaml) and built-in
    (registry detectors), tagging each with its source."""
    bin_dir = isolated_paths["root"] / "bin"
    bin_dir.mkdir()
    # Make omarchy-kb (the built-in detector's binary) reachable.
    (bin_dir / "omarchy-kb").write_text("")
    (bin_dir / "omarchy-kb").chmod(0o755)
    (bin_dir / "user-tool").write_text("")
    (bin_dir / "user-tool").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    mcp_mod.add_server(name="user-tool", command="user-tool")
    rows = mcp_mod.list_servers()
    by_name = {r.name: r for r in rows}
    assert by_name["omarchy-kb"].source == "builtin"
    assert by_name["omarchy-kb"].on_path is True
    assert by_name["user-tool"].source == "user"
    assert by_name["user-tool"].on_path is True


def test_list_servers_user_overrides_builtin(
    isolated_paths, monkeypatch
) -> None:
    """If a user yaml entry has the same name as a built-in
    detector, the user entry wins. Tagged source=user."""
    bin_dir = isolated_paths["root"] / "bin"
    bin_dir.mkdir()
    (bin_dir / "omarchy-kb").write_text("")
    (bin_dir / "omarchy-kb").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    mcp_mod.add_server(
        name="omarchy-kb", command="omarchy-kb", args=["--my-flag"]
    )
    rows = mcp_mod.list_servers()
    omarchy = [r for r in rows if r.name == "omarchy-kb"]
    assert len(omarchy) == 1
    assert omarchy[0].source == "user"
    assert omarchy[0].args == ["--my-flag"]


def test_list_servers_marks_unreachable_user_entries(
    isolated_paths, monkeypatch
) -> None:
    """A user-declared server whose binary isn't on PATH still
    shows up in the listing — but with on_path=False so the user
    knows why the brain doesn't see it."""
    monkeypatch.setenv("PATH", str(isolated_paths["root"] / "no-bin"))
    mcp_mod.add_server(name="aspirational", command="not-installed")
    rows = mcp_mod.list_servers()
    aspirational = [r for r in rows if r.name == "aspirational"]
    assert aspirational and aspirational[0].on_path is False


def test_refresh_workspace_writes_both_natives(
    isolated_paths, monkeypatch
) -> None:
    bin_dir = isolated_paths["root"] / "bin"
    bin_dir.mkdir()
    (bin_dir / "tool").write_text("")
    (bin_dir / "tool").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    mcp_mod.add_server(name="tool", command="tool")
    # add_server already refreshed; calling refresh_workspace again
    # must be idempotent + write the same files.
    result = mcp_mod.refresh_workspace()
    paths = sorted(p.name for p in result.refreshed_paths)
    assert paths == [".mcp.json", "opencode.json"]
    assert result.server_count == 1


def test_yaml_round_trip_preserves_entries(isolated_paths) -> None:
    """Add three; remove the middle one; remaining two stay in
    insertion order."""
    mcp_mod.add_server(name="alpha", command="a")
    mcp_mod.add_server(name="beta", command="b")
    mcp_mod.add_server(name="gamma", command="c")
    mcp_mod.remove_server(name="beta")
    # Read via the same _yaml_path() path the module uses so we
    # respect whatever the conftest's _isolate_vexis_dir fixture
    # patched (the env var alone gets shadowed by the autouse
    # monkeypatch on core.paths.vexis_dir).
    body = yaml.safe_load(mcp_mod._yaml_path().read_text())
    names = [e["name"] for e in body["servers"]]
    assert names == ["alpha", "gamma"]


# ── status_servers ────────────────────────────────────────────────


def test_status_reports_unwired_when_only_yaml(
    isolated_paths, monkeypatch
) -> None:
    """Server is in yaml + binary on PATH but the workspace
    natives haven't been refreshed (e.g. user hand-edited yaml,
    didn't run refresh). Status should mark it as incomplete with
    the missing native files called out."""
    bin_dir = isolated_paths["root"] / "bin"
    bin_dir.mkdir()
    (bin_dir / "tool").write_text("")
    (bin_dir / "tool").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    # Write yaml directly without going through add_server (which
    # would auto-refresh).
    mcp_mod._write_yaml({"servers": [{"name": "tool", "command": "tool"}]})
    rows = mcp_mod.status_servers()
    tool = next(r for r in rows if r.entry.name == "tool")
    assert tool.entry.on_path is True
    assert tool.in_claude_native is False
    assert tool.in_opencode_native is False
    assert tool.fully_wired is False


def test_status_reports_fully_wired_after_add(
    isolated_paths, monkeypatch
) -> None:
    """add_server triggers refresh, so by the time it returns the
    server should be fully_wired (on PATH + in both natives)."""
    bin_dir = isolated_paths["root"] / "bin"
    bin_dir.mkdir()
    (bin_dir / "tool").write_text("")
    (bin_dir / "tool").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    mcp_mod.add_server(name="tool", command="tool")
    rows = mcp_mod.status_servers()
    tool = next(r for r in rows if r.entry.name == "tool")
    assert tool.fully_wired


def test_status_path_missing_when_binary_uninstalled(
    isolated_paths, monkeypatch
) -> None:
    """User-declared server whose binary isn't on PATH: status
    surfaces on_path=False and fully_wired=False even if the
    native files do contain it (because PATH-presence is a hard
    requirement for the brain to actually launch the server)."""
    monkeypatch.setenv("PATH", str(isolated_paths["root"] / "no-bin"))
    mcp_mod._write_yaml({"servers": [{"name": "x", "command": "missing"}]})
    rows = mcp_mod.status_servers()
    x = next(r for r in rows if r.entry.name == "x")
    assert x.entry.on_path is False
    assert x.fully_wired is False


# ── shared fixture: isolated VEXIS_HOME + VEXIS_WORKSPACE ─────────


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    home = tmp_path / "v"
    workspace = tmp_path / "ws"
    home.mkdir()
    workspace.mkdir()
    monkeypatch.setenv("VEXIS_HOME", str(home))
    monkeypatch.setenv("VEXIS_WORKSPACE", str(workspace))
    return {"root": tmp_path, "home": home, "workspace": workspace}

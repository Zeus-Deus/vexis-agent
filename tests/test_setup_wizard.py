"""Phase 4 — interactive setup wizard.

The wizard's small surface (`require_tty`, `ensure_*`, `update_env_value`,
`run_setup`, `format_summary`) is exercised here with mocked prompts +
filesystem + service install so the tests stay hermetic — no actual
systemctl invocation, no waiting on stdin.
"""

from __future__ import annotations

import io
import os
import stat
from pathlib import Path

import pytest

from vexis_agent import setup_wizard as sw


# ── individual step tests ─────────────────────────────────────────


def test_require_tty_blocks_non_tty(tmp_path) -> None:
    """A piped or redirected stdin should refuse — the wizard is
    interactive and a non-TTY run would loop on an empty input."""
    stub = io.StringIO("")  # io.StringIO returns False from isatty.
    with pytest.raises(sw.SetupAborted) as exc:
        sw.require_tty(stdin=stub)
    assert "non-TTY" in str(exc.value)


def test_require_tty_passes_when_isatty_true() -> None:
    class FakeTTY:
        def isatty(self) -> bool:
            return True

    sw.require_tty(stdin=FakeTTY())


def test_ensure_config_yaml_creates_when_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VEXIS_HOME", str(tmp_path / "v"))
    home = tmp_path / "v"
    path = sw.ensure_config_yaml(home)
    assert path == home / "config.yaml"
    assert path.is_file()
    body = path.read_text(encoding="utf-8")
    # Anchor against a stable schema landmark.
    assert "brain:" in body and "kind: claude-code" in body


def test_ensure_config_yaml_skips_when_present(tmp_path) -> None:
    home = tmp_path / "v"
    home.mkdir()
    pre_existing = home / "config.yaml"
    pre_existing.write_text("# user-edited config\n", encoding="utf-8")
    path = sw.ensure_config_yaml(home)
    assert path.read_text(encoding="utf-8") == "# user-edited config\n"


def test_ensure_dotenv_sets_mode_0600(tmp_path) -> None:
    home = tmp_path / "v"
    home.mkdir()
    path = sw.ensure_dotenv(home)
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"expected mode 0600, got {oct(mode)}"
    assert "TELEGRAM_BOT_TOKEN" in path.read_text(encoding="utf-8")


def test_ensure_dotenv_tightens_existing_perms(tmp_path) -> None:
    """A pre-existing .env with 0644 must be tightened — re-running
    setup shouldn't leave secrets world-readable."""
    home = tmp_path / "v"
    home.mkdir()
    path = home / ".env"
    path.write_text("TELEGRAM_BOT_TOKEN=abc\n", encoding="utf-8")
    path.chmod(0o644)
    sw.ensure_dotenv(home)
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_archive_existing_round_trip(tmp_path) -> None:
    src = tmp_path / "config.yaml"
    src.write_text("hello\n", encoding="utf-8")
    archive = sw.archive_existing(src)
    assert archive is not None
    assert archive.exists() and not src.exists()
    assert archive.name.startswith("config.yaml.bak.")
    assert archive.read_text(encoding="utf-8") == "hello\n"


def test_archive_existing_noop_when_missing(tmp_path) -> None:
    assert sw.archive_existing(tmp_path / "nope") is None


def test_update_env_value_appends_when_missing(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("# header\nLOG_LEVEL=INFO\n", encoding="utf-8")
    sw.update_env_value(env, "TELEGRAM_BOT_TOKEN", "abc:def")
    body = env.read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN=abc:def" in body
    assert "LOG_LEVEL=INFO" in body
    assert "# header" in body


def test_update_env_value_replaces_existing(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "TELEGRAM_BOT_TOKEN=old\nTELEGRAM_ALLOWED_USER_ID=12\n", encoding="utf-8"
    )
    sw.update_env_value(env, "TELEGRAM_BOT_TOKEN", "new")
    body = env.read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN=new" in body
    assert "TELEGRAM_BOT_TOKEN=old" not in body
    assert "TELEGRAM_ALLOWED_USER_ID=12" in body


def test_update_env_value_skips_comment_keys(tmp_path) -> None:
    """A commented-out key with a matching name must not be treated as
    an existing definition — that would corrupt user-editable
    examples like '# TELEGRAM_BOT_TOKEN=...'."""
    env = tmp_path / ".env"
    env.write_text(
        "# TELEGRAM_BOT_TOKEN=placeholder\n", encoding="utf-8"
    )
    sw.update_env_value(env, "TELEGRAM_BOT_TOKEN", "real-token")
    body = env.read_text(encoding="utf-8")
    assert "# TELEGRAM_BOT_TOKEN=placeholder" in body
    assert "TELEGRAM_BOT_TOKEN=real-token" in body


# ── orchestration ────────────────────────────────────────────────


def _canned_prompts(answers: dict[str, str]):
    def _prompt(message: str, secret: bool) -> str:
        for key, value in answers.items():
            if key in message:
                return value
        return ""

    return _prompt


@pytest.fixture
def isolated_setup_env(tmp_path, monkeypatch):
    """Isolate VEXIS_HOME + VEXIS_WORKSPACE so wizard runs don't write
    to the developer's real ~/.vexis or ~/vexis-workspace."""
    monkeypatch.setenv("VEXIS_HOME", str(tmp_path / "v"))
    monkeypatch.setenv("VEXIS_WORKSPACE", str(tmp_path / "ws"))
    return tmp_path


def test_run_setup_writes_config_and_dotenv(isolated_setup_env) -> None:
    answers = {
        "Telegram bot token": "1234:abcd",
        "Allowed Telegram user ID": "98765",
    }
    result = sw.run_setup(
        prompt=_canned_prompts(answers),
        install_service=False,
        require_interactive=False,
        print_banner=False,
        brain_kind_override="claude-code",
    )
    assert result.config_path.read_text(encoding="utf-8").startswith(
        "# vexis-agent — example configuration."
    )
    env_body = result.dotenv_path.read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN=1234:abcd" in env_body
    assert "TELEGRAM_ALLOWED_USER_ID=98765" in env_body
    assert result.service_installed is False
    # Workspace was created with subdirs
    assert result.workspace.is_dir()
    assert (result.workspace / "memories").is_dir()
    assert (result.workspace / "skills").is_dir()
    assert result.workspace_claude_md.is_file()


def test_run_setup_reset_archives_existing(isolated_setup_env) -> None:
    home = isolated_setup_env / "v"
    home.mkdir()
    (home / "config.yaml").write_text("# old config\n", encoding="utf-8")
    (home / ".env").write_text("TELEGRAM_BOT_TOKEN=old\n", encoding="utf-8")

    result = sw.run_setup(
        prompt=_canned_prompts({"token": "new", "user ID": "1"}),
        install_service=False,
        reset=True,
        require_interactive=False,
        print_banner=False,
        brain_kind_override="claude-code",
    )
    assert result.archived_config is not None
    assert result.archived_dotenv is not None
    assert result.archived_config.exists()
    assert result.archived_dotenv.exists()
    assert "vexis-agent — example" in result.config_path.read_text()


def test_run_setup_install_service_calls_install(monkeypatch, isolated_setup_env) -> None:
    called = {}

    def fake_install_user_unit(**kwargs):
        called["kwargs"] = kwargs
        return Path("/fake/path")

    monkeypatch.setattr(
        "vexis_agent.daemon.systemd.install_user_unit", fake_install_user_unit
    )
    result = sw.run_setup(
        prompt=_canned_prompts({"token": "x", "user ID": "1"}),
        install_service=True,
        require_interactive=False,
        print_banner=False,
        brain_kind_override="claude-code",
    )
    assert "kwargs" in called
    assert result.service_installed is True


# ── Phase 5b helpers ──────────────────────────────────────────────


def test_check_brain_cli_claude_code_present(tmp_path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "claude"
    fake.write_text("")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    bc = sw.check_brain_cli("claude-code")
    assert bc.kind == "claude-code"
    assert bc.binary == "claude"
    assert bc.found is True


def test_check_brain_cli_opencode_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    bc = sw.check_brain_cli("opencode")
    assert bc.binary == "opencode"
    assert bc.found is False
    assert "opencode.ai/install" in bc.install_hint


def test_check_brain_cli_null_no_binary() -> None:
    bc = sw.check_brain_cli("null")
    assert bc.found is True
    assert bc.binary == ""


def test_workspace_path_honors_env(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VEXIS_WORKSPACE", str(tmp_path / "ws"))
    assert sw.workspace_path() == tmp_path / "ws"
    monkeypatch.delenv("VEXIS_WORKSPACE")
    assert sw.workspace_path() == Path.home() / "vexis-workspace"


def test_ensure_workspace_creates_subdirs(tmp_path) -> None:
    ws = tmp_path / "ws"
    sw.ensure_workspace(ws)
    assert ws.is_dir()
    assert (ws / "memories").is_dir()
    assert (ws / "skills").is_dir()


def test_ensure_workspace_claude_md_writes_template(tmp_path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    path, written = sw.ensure_workspace_claude_md(ws)
    assert written is True
    body = path.read_text(encoding="utf-8")
    assert "Vexis workspace" in body or "Vexis" in body
    # Idempotent on re-run
    path2, written2 = sw.ensure_workspace_claude_md(ws)
    assert written2 is False


def test_ensure_agents_md_symlink_only_for_opencode(tmp_path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "CLAUDE.md").write_text("x\n")
    # claude-code → no symlink created
    link, status = sw.ensure_agents_md_symlink(ws, "claude-code")
    assert link is None
    assert status == "skipped_not_opencode"
    assert not (ws / "AGENTS.md").exists()
    # opencode → symlink created
    link, status = sw.ensure_agents_md_symlink(ws, "opencode")
    assert status == "created"
    assert link.is_symlink()
    assert os.readlink(link) == "CLAUDE.md"


def test_ensure_agents_md_symlink_idempotent(tmp_path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "CLAUDE.md").write_text("x\n")
    sw.ensure_agents_md_symlink(ws, "opencode")
    _, status = sw.ensure_agents_md_symlink(ws, "opencode")
    assert status == "already_correct"


def test_ensure_agents_md_symlink_refuses_real_file(tmp_path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "CLAUDE.md").write_text("x\n")
    (ws / "AGENTS.md").write_text("hand-written content\n")
    link, status = sw.ensure_agents_md_symlink(ws, "opencode")
    assert status == "refused_real_file"
    # Real file untouched
    assert (ws / "AGENTS.md").read_text() == "hand-written content\n"


def test_check_tailscale_when_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    ts = sw.check_tailscale()
    assert ts.installed is False
    assert ts.logged_in is False


def test_run_setup_creates_workspace_for_opencode(isolated_setup_env, monkeypatch) -> None:
    """When the user picks opencode (or it's overridden), the wizard
    should write the AGENTS.md symlink for opencode discovery."""
    result = sw.run_setup(
        prompt=_canned_prompts({"token": "x", "user ID": "1"}),
        install_service=False,
        require_interactive=False,
        print_banner=False,
        brain_kind_override="opencode",
    )
    assert result.agents_md_status == "created"
    link = result.workspace / "AGENTS.md"
    assert link.is_symlink()
    assert os.readlink(link) == "CLAUDE.md"


def test_run_setup_skips_agents_md_for_claude_code(isolated_setup_env) -> None:
    result = sw.run_setup(
        prompt=_canned_prompts({"token": "x", "user ID": "1"}),
        install_service=False,
        require_interactive=False,
        print_banner=False,
        brain_kind_override="claude-code",
    )
    assert result.agents_md_status == "skipped_not_opencode"
    assert not (result.workspace / "AGENTS.md").exists()


# ── MCP detection + write ─────────────────────────────────────────


def test_detect_mcp_servers_when_omarchy_kb_present(tmp_path, monkeypatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "omarchy-kb"
    fake.write_text("")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    detected = sw.detect_mcp_servers()
    names = [s["name"] for s in detected]
    assert "omarchy-kb" in names


def test_detect_mcp_servers_when_none(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert sw.detect_mcp_servers() == []


def test_write_mcp_config_claude_code(tmp_path) -> None:
    import json

    workspace = tmp_path / "ws"
    workspace.mkdir()
    specs = [{"name": "omarchy-kb", "command": "omarchy-kb", "args": []}]
    path = sw.write_mcp_config(workspace, "claude-code", specs)
    assert path == workspace / ".mcp.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    assert "mcpServers" in body
    assert body["mcpServers"]["omarchy-kb"]["command"] == "omarchy-kb"


def test_write_mcp_config_opencode_namespace_prefix(tmp_path) -> None:
    """opencode namespace-merges with the 'vexis-' prefix; non-prefixed
    user entries must survive untouched."""
    import json

    workspace = tmp_path / "ws"
    workspace.mkdir()
    # User-owned non-prefixed entry
    (workspace / "opencode.json").write_text(
        json.dumps(
            {
                "mcp": {
                    "user-thing": {"type": "local", "command": ["x"], "enabled": True}
                }
            }
        ),
        encoding="utf-8",
    )
    specs = [{"name": "omarchy-kb", "command": "omarchy-kb", "args": []}]
    path = sw.write_mcp_config(workspace, "opencode", specs)
    body = json.loads(path.read_text(encoding="utf-8"))
    assert "user-thing" in body["mcp"]  # preserved
    assert "vexis-omarchy-kb" in body["mcp"]  # prefixed
    assert body["mcp"]["vexis-omarchy-kb"]["command"] == ["omarchy-kb"]


def test_write_mcp_config_null_brain_returns_none(tmp_path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    assert sw.write_mcp_config(workspace, "null", []) is None


def test_run_setup_wires_mcp_when_detected(
    isolated_setup_env, monkeypatch
) -> None:
    """End-to-end: drop a fake omarchy-kb on PATH, run setup, verify
    the workspace .mcp.json mentions it."""
    bin_dir = isolated_setup_env / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "omarchy-kb"
    fake.write_text("")
    fake.chmod(0o755)
    # Need claude on PATH too so the brain check passes; both share PATH.
    fake_claude = bin_dir / "claude"
    fake_claude.write_text("")
    fake_claude.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))

    result = sw.run_setup(
        prompt=_canned_prompts({"token": "x", "user ID": "1"}),
        install_service=False,
        require_interactive=False,
        print_banner=False,
        brain_kind_override="claude-code",
    )
    assert result.mcp_servers_wired == ["omarchy-kb"]
    assert result.mcp_config_path == result.workspace / ".mcp.json"
    assert result.mcp_config_path.is_file()


def test_set_brain_kind_rewrites_existing_value(tmp_path) -> None:
    """The shipped template ships kind: claude-code; if the user
    picks opencode in the picker, _set_brain_kind has to swap that
    line in place without disturbing surrounding YAML."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "brain:\n"
        "  kind: claude-code\n"
        "  # comment that must survive\n"
        "memory:\n"
        "  memory_char_limit: 2200\n",
        encoding="utf-8",
    )
    sw._set_brain_kind(cfg, "opencode")
    body = cfg.read_text(encoding="utf-8")
    assert "kind: opencode" in body
    assert "kind: claude-code" not in body
    assert "comment that must survive" in body
    assert "memory_char_limit: 2200" in body


def test_run_setup_brain_picker_writes_chosen_kind(
    isolated_setup_env, monkeypatch
) -> None:
    """Inject a choice fn that picks index 1 (opencode); verify
    config.yaml gets rewritten and the wizard treats the rest of
    the run as opencode (AGENTS.md symlink etc.)."""
    answers = {"Telegram bot token": "abc", "Allowed Telegram user ID": "1"}

    def picked_opencode(message, options, default_idx):
        # 0=claude-code, 1=opencode, 2=null
        return 1

    result = sw.run_setup(
        prompt=_canned_prompts(answers),
        choice=picked_opencode,
        install_service=False,
        require_interactive=False,
        print_banner=False,
    )
    body = result.config_path.read_text(encoding="utf-8")
    assert "kind: opencode" in body
    # Opencode flow → AGENTS.md symlink lands.
    assert result.agents_md_status == "created"


def test_run_setup_brain_picker_default_keeps_template_kind(
    isolated_setup_env, monkeypatch
) -> None:
    """Empty input / default selection must keep the template's
    kind unchanged — re-running setup shouldn't churn config.yaml."""
    answers = {"Telegram bot token": "abc", "Allowed Telegram user ID": "1"}

    def picked_default(message, options, default_idx):
        return default_idx

    result = sw.run_setup(
        prompt=_canned_prompts(answers),
        choice=picked_default,
        install_service=False,
        require_interactive=False,
        print_banner=False,
    )
    body = result.config_path.read_text(encoding="utf-8")
    assert "kind: claude-code" in body


def test_user_mcp_specs_empty_when_yaml_missing(
    isolated_setup_env, monkeypatch
) -> None:
    """No mcp-servers.yaml → empty list (default state). The wizard
    falls through to the built-in detectors only."""
    assert sw._user_mcp_specs() == []


def test_user_mcp_specs_reads_yaml_and_filters_by_path(
    isolated_setup_env, monkeypatch
) -> None:
    """Entries whose binary isn't on PATH get filtered out so the
    workspace MCP config doesn't reference dead invocations."""
    home = isolated_setup_env / "v"
    home.mkdir()
    bin_dir = isolated_setup_env / "bin"
    bin_dir.mkdir()
    # 'real-tool' is on PATH; 'missing-tool' isn't.
    real = bin_dir / "real-tool"
    real.write_text("")
    real.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    (home / "mcp-servers.yaml").write_text(
        "servers:\n"
        "  - name: present\n"
        "    command: real-tool\n"
        "    args: ['arg1']\n"
        "    env:\n"
        "      X: 'y'\n"
        "  - name: missing\n"
        "    command: missing-tool\n",
        encoding="utf-8",
    )
    specs = sw._user_mcp_specs()
    assert len(specs) == 1
    assert specs[0]["name"] == "present"
    assert specs[0]["args"] == ["arg1"]
    assert specs[0]["env"] == {"X": "y"}


def test_user_mcp_specs_skips_malformed_entries(
    isolated_setup_env, monkeypatch
) -> None:
    home = isolated_setup_env / "v"
    home.mkdir()
    bin_dir = isolated_setup_env / "bin"
    bin_dir.mkdir()
    (bin_dir / "ok-tool").write_text("")
    (bin_dir / "ok-tool").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    (home / "mcp-servers.yaml").write_text(
        "servers:\n"
        "  - name: missing-command\n"  # no command → dropped
        "  - command: missing-name\n"  # no name → dropped
        "  - name: ok\n"
        "    command: ok-tool\n",
        encoding="utf-8",
    )
    specs = sw._user_mcp_specs()
    names = [s["name"] for s in specs]
    assert names == ["ok"]


def test_user_mcp_specs_swallows_yaml_errors(
    isolated_setup_env, monkeypatch
) -> None:
    """Bad YAML must not crash the wizard. Returns empty list +
    warns; the built-in detectors still run."""
    home = isolated_setup_env / "v"
    home.mkdir()
    (home / "mcp-servers.yaml").write_text(
        "servers: [unclosed\n", encoding="utf-8"
    )
    assert sw._user_mcp_specs() == []


def test_detect_mcp_servers_combines_builtin_and_user(
    isolated_setup_env, monkeypatch
) -> None:
    home = isolated_setup_env / "v"
    home.mkdir()
    bin_dir = isolated_setup_env / "bin"
    bin_dir.mkdir()
    # Both omarchy-kb (built-in) and a user-declared peekaboo are
    # on PATH; both should land in the output.
    (bin_dir / "omarchy-kb").write_text("")
    (bin_dir / "omarchy-kb").chmod(0o755)
    (bin_dir / "peekaboo").write_text("")
    (bin_dir / "peekaboo").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    (home / "mcp-servers.yaml").write_text(
        "servers:\n"
        "  - name: peekaboo\n"
        "    command: peekaboo\n",
        encoding="utf-8",
    )
    detected = sw.detect_mcp_servers()
    names = [s["name"] for s in detected]
    assert "omarchy-kb" in names
    assert "peekaboo" in names


def test_detect_mcp_servers_user_overrides_builtin(
    isolated_setup_env, monkeypatch
) -> None:
    """If a user declares an entry with the same name as a built-in,
    the user's version wins. Future-proofing for the case where a
    user wants different env vars / args than the built-in detector
    chose."""
    home = isolated_setup_env / "v"
    home.mkdir()
    bin_dir = isolated_setup_env / "bin"
    bin_dir.mkdir()
    (bin_dir / "omarchy-kb").write_text("")
    (bin_dir / "omarchy-kb").chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    (home / "mcp-servers.yaml").write_text(
        "servers:\n"
        "  - name: omarchy-kb\n"
        "    command: omarchy-kb\n"
        "    args: ['--user-flag']\n",
        encoding="utf-8",
    )
    detected = sw.detect_mcp_servers()
    omarchy_entries = [s for s in detected if s["name"] == "omarchy-kb"]
    assert len(omarchy_entries) == 1, "user override didn't replace built-in"
    assert omarchy_entries[0]["args"] == ["--user-flag"]


def test_run_setup_skips_mcp_when_nothing_detected(
    isolated_setup_env, monkeypatch
) -> None:
    monkeypatch.setenv("PATH", str(isolated_setup_env / "no-mcp-here"))
    result = sw.run_setup(
        prompt=_canned_prompts({"token": "x", "user ID": "1"}),
        install_service=False,
        require_interactive=False,
        print_banner=False,
        brain_kind_override="claude-code",
    )
    assert result.mcp_servers_wired == []
    assert result.mcp_config_path is None


def test_format_summary_renders_archive_lines(tmp_path) -> None:
    result = sw.SetupResult(
        home=tmp_path,
        config_path=tmp_path / "config.yaml",
        dotenv_path=tmp_path / ".env",
        archived_config=tmp_path / "config.yaml.bak.X",
        archived_dotenv=tmp_path / ".env.bak.X",
        service_installed=False,
    )
    out = sw.format_summary(result)
    # Two spaces after the colon — column-aligned with the other
    # summary lines (config:/secrets:/workspace:/archived:).
    assert "archived:  " + str(tmp_path / "config.yaml.bak.X") in out
    assert "Next steps:" in out


def test_format_summary_when_service_installed(tmp_path) -> None:
    result = sw.SetupResult(
        home=tmp_path,
        config_path=tmp_path / "config.yaml",
        dotenv_path=tmp_path / ".env",
        service_installed=True,
    )
    out = sw.format_summary(result)
    assert "systemctl --user enable --now vexis-agent.service" in out

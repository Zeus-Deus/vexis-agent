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


def test_run_setup_writes_config_and_dotenv(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("VEXIS_HOME", str(tmp_path / "v"))
    answers = {
        "Telegram bot token": "1234:abcd",
        "Allowed Telegram user ID": "98765",
    }
    result = sw.run_setup(
        prompt=_canned_prompts(answers),
        install_service=False,
        require_interactive=False,
    )
    assert result.config_path.read_text(encoding="utf-8").startswith(
        "# vexis-agent — example configuration."
    )
    env_body = result.dotenv_path.read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN=1234:abcd" in env_body
    assert "TELEGRAM_ALLOWED_USER_ID=98765" in env_body
    assert result.service_installed is False


def test_run_setup_reset_archives_existing(tmp_path, monkeypatch) -> None:
    home = tmp_path / "v"
    home.mkdir()
    monkeypatch.setenv("VEXIS_HOME", str(home))
    (home / "config.yaml").write_text("# old config\n", encoding="utf-8")
    (home / ".env").write_text("TELEGRAM_BOT_TOKEN=old\n", encoding="utf-8")

    result = sw.run_setup(
        prompt=_canned_prompts({"token": "new", "user ID": "1"}),
        install_service=False,
        reset=True,
        require_interactive=False,
    )
    assert result.archived_config is not None
    assert result.archived_dotenv is not None
    assert result.archived_config.exists()
    assert result.archived_dotenv.exists()
    # Fresh files were re-written from templates.
    assert "vexis-agent — example" in result.config_path.read_text()


def test_run_setup_install_service_calls_install(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("VEXIS_HOME", str(tmp_path / "v"))
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
    )
    assert "kwargs" in called
    assert result.service_installed is True


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
    assert "archived: " + str(tmp_path / "config.yaml.bak.X") in out
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

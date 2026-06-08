"""Headless / web-only provisioning (issue #40).

Issue #39 already shipped the web-only *runtime* (the
``transports.telegram`` / ``transports.web`` toggles read in
``main._run``, ``load_config(require_telegram=...)``, and the headless
park loop). Issue #40 adds the *provisioning* half on top of it:

  1. ``daemon.doctor.check_secrets`` — no longer FAILs on absent Telegram
     secrets when the Telegram transport is disabled (``transports.telegram:
     false``), so a headless container passes ``vexis-agent doctor``.
  2. ``setup_wizard.run_setup(web_only=True)`` + the ``vexis-agent setup
     --non-interactive`` / ``--web-only`` CLI path — unattended
     provisioning that writes ``transports.telegram: false`` (#39's
     vocabulary), leaves no active Telegram values in .env, and never
     touches a TTY.

Runtime behaviour (load_config relaxation, transport selection, park
loop) is owned + tested by #39 (``tests/test_subsystem_toggles.py``);
these tests cover only the #40 delta, plus one integration check that
``setup --web-only`` produces a config the #39 toggle reads as off.

Isolation note: ``yaml_config``, ``setup_wizard`` and ``daemon.doctor``
each did ``from core.paths import vexis_dir`` at import, so the conftest
autouse patch on ``core.paths.vexis_dir`` doesn't reach them.
``_point_vexis_home`` patches every binding (and sets $VEXIS_HOME) so
config reads, dotenv reads, the wizard, and doctor all agree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from typer.testing import CliRunner


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VEXIS_WEB_ONLY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_ID", raising=False)


def _point_vexis_home(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    """Redirect EVERY ``vexis_dir`` reader at ``home``. ``yaml_config``,
    ``setup_wizard`` and ``daemon.doctor`` hold their own
    ``from core.paths import vexis_dir`` references that the conftest's
    ``core.paths.vexis_dir`` patch doesn't reach; we patch each binding
    and also set $VEXIS_HOME so the original resolver agrees too. Later
    patches win over the conftest's autouse one."""
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("VEXIS_HOME", str(home))
    monkeypatch.setattr("vexis_agent.core.paths.vexis_dir", lambda: home)
    monkeypatch.setattr("vexis_agent.core.yaml_config.vexis_dir", lambda: home)
    monkeypatch.setattr("vexis_agent.setup_wizard.vexis_dir", lambda: home)
    monkeypatch.setattr("vexis_agent.daemon.doctor.vexis_dir", lambda: home)


def _active_env_keys(env_text: str) -> set[str]:
    """KEY names of uncommented ``KEY=...`` lines in a dotenv."""
    keys = set()
    for ln in env_text.splitlines():
        s = ln.strip()
        if s and not s.startswith("#") and "=" in s:
            keys.add(s.split("=", 1)[0].strip())
    return keys


# ──────────────────────────────────────────────────────────────────────
# 1. doctor.check_secrets respects the #39 transport toggle
# ──────────────────────────────────────────────────────────────────────


def test_doctor_secrets_telegram_disabled_not_fail(tmp_path, monkeypatch) -> None:
    """transports.telegram: false + no secrets → clean pass, not FAIL."""
    from vexis_agent.daemon import doctor

    home = tmp_path / "home"
    _point_vexis_home(monkeypatch, home)
    _clear_env(monkeypatch)
    (home / "config.yaml").write_text(
        "transports:\n  telegram: false\n  web: true\n", encoding="utf-8"
    )

    result = doctor.check_secrets()
    assert result.status is doctor.Status.OK


def test_doctor_secrets_telegram_enabled_missing_fails(tmp_path, monkeypatch) -> None:
    """Default (telegram transport on) + no secrets → FAIL, unchanged."""
    from vexis_agent.daemon import doctor

    home = tmp_path / "home"
    _point_vexis_home(monkeypatch, home)
    _clear_env(monkeypatch)
    # No config at all → transports default on.
    result = doctor.check_secrets()
    assert result.status is doctor.Status.FAIL


def test_doctor_secrets_present_ok(tmp_path, monkeypatch) -> None:
    from vexis_agent.daemon import doctor

    home = tmp_path / "home"
    _point_vexis_home(monkeypatch, home)
    _clear_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "42")
    result = doctor.check_secrets()
    assert result.status is doctor.Status.OK


def test_doctor_overall_passes_headless(tmp_path, monkeypatch) -> None:
    """The Telegram-secrets check must not be a source of FAIL in a
    headless deploy (other env-dependent checks aside)."""
    from vexis_agent.daemon import doctor

    home = tmp_path / "home"
    _point_vexis_home(monkeypatch, home)
    _clear_env(monkeypatch)
    (home / "config.yaml").write_text(
        "transports:\n  telegram: false\n", encoding="utf-8"
    )
    results = doctor.run_all()
    secrets = [r for r in results if "Telegram" in r.name]
    assert secrets and secrets[0].status is not doctor.Status.FAIL


# ──────────────────────────────────────────────────────────────────────
# 2a. run_setup(web_only=True)
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_setup_env(tmp_path, monkeypatch):
    _point_vexis_home(monkeypatch, tmp_path / "v")
    monkeypatch.setenv("VEXIS_WORKSPACE", str(tmp_path / "ws"))
    _clear_env(monkeypatch)
    return tmp_path


def test_run_setup_web_only_writes_transports_and_skips_telegram(
    isolated_setup_env,
) -> None:
    from vexis_agent import setup_wizard as sw

    result = sw.run_setup(
        prompt=sw.env_backed_prompt,
        confirm=sw.noninteractive_confirm,
        choice=sw.noninteractive_choice,
        install_service=False,
        require_interactive=False,
        print_banner=False,
        brain_kind_override="null",
        web_only=True,
    )
    assert result.web_only is True

    config_body = result.config_path.read_text(encoding="utf-8")
    assert "transports:" in config_body
    assert "telegram: false" in config_body

    env_body = result.dotenv_path.read_text(encoding="utf-8")
    active = _active_env_keys(env_body)
    assert "TELEGRAM_BOT_TOKEN" not in active
    assert "TELEGRAM_ALLOWED_USER_ID" not in active


def test_run_setup_web_only_toggle_reads_off(isolated_setup_env, monkeypatch) -> None:
    """Integration: the config setup wrote makes #39's
    transport_telegram_enabled() report off (so the daemon boots
    headless)."""
    from vexis_agent import setup_wizard as sw
    from vexis_agent.core.yaml_config import (
        transport_telegram_enabled,
        transport_web_enabled,
    )

    sw.run_setup(
        prompt=sw.env_backed_prompt,
        confirm=sw.noninteractive_confirm,
        choice=sw.noninteractive_choice,
        install_service=False,
        require_interactive=False,
        print_banner=False,
        brain_kind_override="null",
        web_only=True,
    )
    # yaml_config.vexis_dir is patched at the same home by the fixture.
    assert transport_telegram_enabled() is False
    assert transport_web_enabled() is True


def test_run_setup_default_writes_telegram(isolated_setup_env) -> None:
    from vexis_agent import setup_wizard as sw

    def _answers(message: str, secret: bool) -> str:
        if "Telegram bot token" in message:
            return "1234:abcd"
        if "Allowed Telegram user ID" in message:
            return "98765"
        return ""

    result = sw.run_setup(
        prompt=_answers,
        install_service=False,
        require_interactive=False,
        print_banner=False,
        brain_kind_override="null",
    )
    assert result.web_only is False
    env_body = result.dotenv_path.read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN=1234:abcd" in env_body
    assert "telegram: false" not in result.config_path.read_text(encoding="utf-8")


# ── setup_wizard unit helpers ──────────────────────────────────────────


def test_set_transports_appends_block(tmp_path) -> None:
    from vexis_agent import setup_wizard as sw

    cfg = tmp_path / "config.yaml"
    cfg.write_text("brain:\n  kind: null\n", encoding="utf-8")
    sw._set_transports(cfg, telegram=False, web=True)
    body = cfg.read_text(encoding="utf-8")
    assert "transports:" in body
    assert "  telegram: false" in body
    assert "  web: true" in body
    assert "kind: null" in body
    # Parses as valid YAML with the expected values.
    import yaml

    data = yaml.safe_load(body)
    assert data["transports"]["telegram"] is False
    assert data["transports"]["web"] is True


def test_set_transports_idempotent_no_duplicate_block(tmp_path) -> None:
    """Re-running strips the prior active block — no duplicate keys."""
    from vexis_agent import setup_wizard as sw

    cfg = tmp_path / "config.yaml"
    cfg.write_text("brain:\n  kind: null\n", encoding="utf-8")
    sw._set_transports(cfg, telegram=False, web=True)
    sw._set_transports(cfg, telegram=False, web=True)
    body = cfg.read_text(encoding="utf-8")
    # Exactly one active (column-0) transports: key.
    assert sum(1 for ln in body.splitlines() if ln.rstrip() == "transports:") == 1


def test_set_transports_preserves_commented_example(tmp_path) -> None:
    """A commented ``# transports:`` example (as the template ships) is
    NOT treated as an active block and survives untouched."""
    from vexis_agent import setup_wizard as sw

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "# transports:        # example\n#   telegram: true\nbrain:\n  kind: null\n",
        encoding="utf-8",
    )
    sw._set_transports(cfg, telegram=False, web=True)
    body = cfg.read_text(encoding="utf-8")
    assert "# transports:        # example" in body
    assert "#   telegram: true" in body
    import yaml

    assert yaml.safe_load(body)["transports"]["telegram"] is False


def test_comment_out_env_key(tmp_path) -> None:
    from vexis_agent import setup_wizard as sw

    env = tmp_path / ".env"
    env.write_text(
        "TELEGRAM_BOT_TOKEN=placeholder\nLOG_LEVEL=INFO\n", encoding="utf-8"
    )
    assert sw.comment_out_env_key(env, "TELEGRAM_BOT_TOKEN") is True
    body = env.read_text(encoding="utf-8")
    assert "# TELEGRAM_BOT_TOKEN=placeholder" in body
    assert "LOG_LEVEL=INFO" in body
    # Idempotent: already commented → no change.
    assert sw.comment_out_env_key(env, "TELEGRAM_BOT_TOKEN") is False


def test_env_backed_prompt_maps_from_env(monkeypatch) -> None:
    from vexis_agent import setup_wizard as sw

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_ID", "7")
    assert sw.env_backed_prompt("Telegram bot token", True) == "tok"
    assert sw.env_backed_prompt("Allowed Telegram user ID", False) == "7"
    assert sw.env_backed_prompt("Something else entirely", False) == ""


# ──────────────────────────────────────────────────────────────────────
# 2b. CLI `setup --non-interactive` / `--web-only`
# ──────────────────────────────────────────────────────────────────────


def test_cli_setup_web_only_non_interactive(tmp_path, monkeypatch) -> None:
    """Headless acceptance path: exit 0, transports.telegram:false
    written, no active Telegram values, no TTY."""
    from vexis_agent.cli import app

    home = tmp_path / "v"
    _point_vexis_home(monkeypatch, home)
    monkeypatch.setenv("VEXIS_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("VEXIS_BRAIN_KIND", "null")
    _clear_env(monkeypatch)

    result = CliRunner().invoke(app, ["setup", "--non-interactive", "--web-only"])
    assert result.exit_code == 0, result.output

    config_body = (home / "config.yaml").read_text(encoding="utf-8")
    assert "telegram: false" in config_body
    env_body = (home / ".env").read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN" not in _active_env_keys(env_body)


def test_cli_setup_web_only_env_var_implies_non_interactive(tmp_path, monkeypatch) -> None:
    """VEXIS_WEB_ONLY=1 alone (no --non-interactive) provisions headless —
    the issue's acceptance command."""
    from vexis_agent.cli import app

    home = tmp_path / "v"
    _point_vexis_home(monkeypatch, home)
    monkeypatch.setenv("VEXIS_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("VEXIS_BRAIN_KIND", "null")
    _clear_env(monkeypatch)
    monkeypatch.setenv("VEXIS_WEB_ONLY", "1")

    result = CliRunner().invoke(app, ["setup", "--non-interactive"])
    assert result.exit_code == 0, result.output
    assert "telegram: false" in (home / "config.yaml").read_text(encoding="utf-8")

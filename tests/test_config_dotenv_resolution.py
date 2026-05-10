"""Regression tests for the load_dotenv path-resolution bug
(v0.1.0 → v0.1.1).

The bug: ``core.config.load_config()`` called ``load_dotenv()`` with no
arguments. python-dotenv's default delegates to
``find_dotenv(usecwd=False)`` which walks up the directory tree from
the caller's ``__file__`` looking for a ``.env`` sibling. With
vexis-agent installed via pipx, ``__file__`` lives inside
``~/.local/share/pipx/venvs/vexis-agent/lib/python3.X/site-packages/
vexis_agent/core/config.py`` — walking up from there never reaches
``~/.vexis/.env``, so no env vars got loaded and the daemon crashed
on startup with "Missing required env var: TELEGRAM_BOT_TOKEN".

Surfaced on the first public home-server install of v0.1.0 — the
daemon went into a tight crash-restart loop (RestartSec=5) until
systemd gave up. Fixed by passing ``vexis_dir() / ".env"`` so the
canonical VEXIS_HOME resolver is the single source of truth.

These tests pin the contract: ``load_config`` must read its dotenv
from the path ``core.paths.vexis_dir()`` returns. We patch
``vexis_dir`` directly (overriding the autouse isolation fixture in
conftest.py — "later patches win" per its docstring) so we control
the exact path the dotenv loader will hit.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _write_dotenv(home: Path) -> None:
    """Drop a complete .env into ``home`` so load_config can succeed."""
    home.mkdir(parents=True, exist_ok=True)
    (home / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=test-token-aaaa-bbbb\n"
        "TELEGRAM_ALLOWED_USER_ID=1234567890\n",
        encoding="utf-8",
    )


def _clear_secrets_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip any TELEGRAM_* the test runner inherited so the only
    way load_config can succeed is by reading the .env file."""
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_ID"):
        monkeypatch.delenv(key, raising=False)


def _patch_vexis_dir(
    monkeypatch: pytest.MonkeyPatch, target: Path
) -> None:
    """Override the conftest autouse vexis_dir patch with our own.
    Per its docstring: "later patches win"."""
    target.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "vexis_agent.core.paths.vexis_dir", lambda: target
    )


def test_load_config_reads_dotenv_from_vexis_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fix: ``load_config`` reads ``vexis_dir() / ".env"``.
    Pre-fix it walked up from the package's __file__ inside the
    pipx venv and never reached the user's actual ~/.vexis/.env."""
    home = tmp_path / "vexis-home"
    _write_dotenv(home)
    _patch_vexis_dir(monkeypatch, home)
    _clear_secrets_from_env(monkeypatch)

    from vexis_agent.core.config import load_config

    cfg = load_config()
    assert cfg.telegram_bot_token == "test-token-aaaa-bbbb"
    assert cfg.telegram_allowed_user_id == 1234567890


def test_load_config_does_not_read_cwd_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin the regression: a stray .env sitting in the cwd must NOT
    influence load_config. Pre-fix python-dotenv could fall back to
    cwd in some configurations; post-fix we pass an explicit path
    so cwd is strictly ignored. Defends against a hostile cwd
    poisoning the daemon's secrets via path-confusion."""
    home = tmp_path / "real-home"
    _write_dotenv(home)
    _patch_vexis_dir(monkeypatch, home)
    _clear_secrets_from_env(monkeypatch)

    decoy = tmp_path / "cwd"
    decoy.mkdir()
    (decoy / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=HOSTILE-TOKEN-DO-NOT-USE\n"
        "TELEGRAM_ALLOWED_USER_ID=999\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(decoy)

    from vexis_agent.core.config import load_config

    cfg = load_config()
    assert cfg.telegram_bot_token == "test-token-aaaa-bbbb", (
        "load_config picked up cwd/.env instead of vexis_dir()/.env — "
        "the path-resolution bug has regressed."
    )
    assert cfg.telegram_allowed_user_id == 1234567890


def test_load_config_raises_when_dotenv_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh VEXIS_HOME without a .env file: ``load_config`` must
    raise the original ``Missing required env var`` error so the
    setup wizard's "run setup first" message is reachable. python-
    dotenv silently returns False on missing files; the failure mode
    must still happen at the ``_require`` step."""
    home = tmp_path / "empty-home"
    _patch_vexis_dir(monkeypatch, home)
    _clear_secrets_from_env(monkeypatch)

    from vexis_agent.core.config import load_config

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        load_config()


def test_load_config_uses_vexis_dir_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pin the implementation contract: load_config must call
    ``core.paths.vexis_dir`` (not re-implement the lookup) so VEXIS_HOME
    overrides, ~/.vexis defaults, and any future test-isolation
    patches all flow through one resolver. Future refactors that
    bypass vexis_dir would silently re-introduce the original bug."""
    home = tmp_path / "tracker"
    _write_dotenv(home)
    _clear_secrets_from_env(monkeypatch)

    calls: list[None] = []

    def _tracking_vexis_dir() -> Path:
        calls.append(None)
        return home

    monkeypatch.setattr(
        "vexis_agent.core.paths.vexis_dir", _tracking_vexis_dir
    )

    from vexis_agent.core.config import load_config

    load_config()
    assert calls, "load_config did not consult vexis_dir() — refactor regression."

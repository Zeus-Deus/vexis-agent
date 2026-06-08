"""Load and validate environment configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Config:
    telegram_bot_token: str
    telegram_allowed_user_id: int
    workspace: str
    log_level: str


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


def load_config(require_telegram: bool = True) -> Config:
    # ``require_telegram`` defaults True so every existing caller —
    # and any deployment that hasn't touched the new ``transports``
    # config — keeps the original hard-require behaviour byte-for-
    # byte: a missing TELEGRAM_BOT_TOKEN still raises so the setup
    # wizard's "run setup first" message is reachable (issue #39).
    #
    # When the Telegram transport is disabled (``transports.telegram:
    # false``), ``main._run`` calls this with ``require_telegram=False``
    # so a headless web-/CLI-only deployment doesn't need a bot token
    # or an allowed-user id it will never use. The token then falls
    # back to "" and the allowed-user id to 0 — the web dashboard
    # carries its own bearer-token auth, independent of this id.
    #
    # Pass an explicit path. python-dotenv's bare ``load_dotenv()``
    # delegates to ``find_dotenv(usecwd=False)`` which walks up from
    # the caller's ``__file__`` — i.e. from inside the pipx venv at
    # ``~/.local/share/pipx/venvs/vexis-agent/...``. That walk-up
    # never reaches ``~/.vexis/.env``, so when the daemon ran from
    # systemd no env vars got loaded and the bot token assertion
    # below tripped on every start (tight crash-restart loop until
    # systemd's RestartSec gave up). Surfaced in v0.1.0 on first
    # public install — fixed by routing through the canonical
    # vexis_dir() resolver, which honours VEXIS_HOME and falls back
    # to ~/.vexis. Defense-in-depth: the systemd unit also sets
    # EnvironmentFile=-{home}/.env so even if dotenv ever fails, the
    # daemon still inherits the secrets from systemd's environment.
    #
    # Function-scoped import so the conftest autouse fixture's
    # monkeypatch of ``vexis_agent.core.paths.vexis_dir`` actually
    # applies (a module-level ``from ... import`` would bind
    # ``vexis_dir`` in this module's namespace at import time and
    # bypass the patch — same gotcha the conftest docstring calls
    # out for ``_voice_set``).
    from vexis_agent.core.paths import vexis_dir

    load_dotenv(vexis_dir() / ".env")

    if require_telegram:
        token = _require("TELEGRAM_BOT_TOKEN")
        raw_user_id = _require("TELEGRAM_ALLOWED_USER_ID")
    else:
        # Telegram disabled: secrets are optional. Honour them if the
        # operator set them anyway (handy for flipping Telegram back on
        # without re-running setup), otherwise fall back to inert
        # defaults.
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        raw_user_id = os.environ.get("TELEGRAM_ALLOWED_USER_ID", "").strip() or "0"
    try:
        user_id = int(raw_user_id)
    except ValueError as exc:
        raise RuntimeError(
            f"TELEGRAM_ALLOWED_USER_ID must be an integer, got: {raw_user_id!r}"
        ) from exc

    workspace = os.environ.get("VEXIS_WORKSPACE", "~/vexis-workspace").strip()

    log_level = os.environ.get("LOG_LEVEL", "INFO").strip().upper()

    return Config(
        telegram_bot_token=token,
        telegram_allowed_user_id=user_id,
        workspace=workspace,
        log_level=log_level,
    )

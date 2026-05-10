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


def load_config() -> Config:
    load_dotenv()

    token = _require("TELEGRAM_BOT_TOKEN")
    raw_user_id = _require("TELEGRAM_ALLOWED_USER_ID")
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

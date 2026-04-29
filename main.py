"""Vexis-Agent entry point."""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from pathlib import Path

from brains.claude_code import ClaudeCodeBrain
from core.config import load_config
from core.handler import MessageHandler
from core.logging import setup_logging
from core.paths import state_dir, workspace_dir
from core.sessions import SessionStore
from transports.telegram import TelegramTransport

log = logging.getLogger(__name__)


async def _run() -> None:
    config = load_config()
    setup_logging(config.log_level)

    for cmd in ("claude", "voxtype", "ffmpeg"):
        if shutil.which(cmd) is None:
            raise RuntimeError(f"`{cmd}` CLI not found on PATH")

    workspace: Path = workspace_dir(config.workspace)
    log.info("Workspace resolved to %s", workspace)

    sessions = SessionStore(state_path=state_dir() / "session.json")
    brain = ClaudeCodeBrain(
        workspace=workspace,
        session=sessions,
        timeout_seconds=config.claude_timeout_seconds,
    )
    handler = MessageHandler(
        brain=brain,
        sessions=sessions,
        allowed_user_id=config.telegram_allowed_user_id,
    )
    transport = TelegramTransport(
        token=config.telegram_bot_token,
        handler=handler,
        allowed_user_id=config.telegram_allowed_user_id,
    )

    log.info("Vexis-Agent starting")
    await transport.run()


if __name__ == "__main__":
    try:
        asyncio.run(_run())
    except RuntimeError as exc:
        # Startup failures: env validation, missing claude on PATH, etc.
        print(f"vexis-agent: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass

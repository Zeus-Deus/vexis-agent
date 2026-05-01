"""Vexis-Agent entry point."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path

from brains.claude_code import ClaudeCodeBrain, build_system_prompt
from core.background_tasks import (
    BackgroundTaskError,
    BackgroundTaskLimitReached,
    BackgroundTasks,
    NameAlreadyInUse,
    TaskNotFound,
)
from core.config import load_config
from core.control_socket import ControlSocket, default_socket_path
from core.curator import CuratorController
from core.handler import MessageHandler
from core.logging import setup_logging
from core.notify import Notifier
from core.paths import state_dir, workspace_dir
from core.running_tasks import RunningTasks
from core.sessions import SessionStore
from transports.telegram import TelegramTransport

log = logging.getLogger(__name__)


async def _run() -> None:
    config = load_config()
    setup_logging(config.log_level)

    for cmd in (
        "claude",
        "voxtype",
        "ffmpeg",
        "grim",
        "hyprctl",
        "jq",
        "ydotool",
        "wtype",
    ):
        if shutil.which(cmd) is None:
            raise RuntimeError(f"`{cmd}` CLI not found on PATH")

    for cmd in ("tailscale",):
        if shutil.which(cmd) is None:
            log.warning("`%s` not found on PATH; live streaming unavailable", cmd)

    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    socket = Path(runtime) / ".ydotool_socket"
    if not socket.exists():
        log.warning(
            "ydotool socket not found at %s; mouse/keyboard actuation will fail "
            "until ydotool.service is running",
            socket,
        )

    workspace: Path = workspace_dir(config.workspace)
    log.info("Workspace resolved to %s", workspace)

    soul_path = workspace / "SOUL.md"
    if not soul_path.exists():
        log.info(
            "SOUL.md not found at %s. Using default personality. "
            "Create the file to customize.",
            soul_path,
        )

    capabilities_path = Path(__file__).resolve().parent / "CAPABILITIES.md"
    if not capabilities_path.is_file():
        log.warning(
            "CAPABILITIES.md missing from project root (%s). "
            "Vexis won't know which tools are available.",
            capabilities_path,
        )

    sessions = SessionStore(state_path=state_dir() / "session.json")
    running_tasks = RunningTasks()

    # The notifier is shared between the handler (which consumes context
    # at the start of each brain turn) and the transport (which binds
    # the PTB application once Telegram is initialised). Same instance,
    # two roles — that's how notifications and brain context stay in sync.
    notifier = Notifier()
    background_tasks = BackgroundTasks(
        workspace=workspace,
        system_prompt_provider=lambda: build_system_prompt(workspace),
    )
    control_socket = ControlSocket(
        default_socket_path(),
        _build_dispatch(background_tasks),
    )

    brain = ClaudeCodeBrain(
        workspace=workspace,
        session=sessions,
        running_tasks=running_tasks,
    )
    handler = MessageHandler(
        brain=brain,
        sessions=sessions,
        allowed_user_id=config.telegram_allowed_user_id,
        notifier=notifier,
        workspace=workspace,
    )
    curator = CuratorController(workspace=workspace, notifier=notifier)
    transport = TelegramTransport(
        token=config.telegram_bot_token,
        handler=handler,
        running_tasks=running_tasks,
        allowed_user_id=config.telegram_allowed_user_id,
        background_tasks=background_tasks,
        notifier=notifier,
        curator=curator,
    )

    log.info("Vexis-Agent starting")
    await control_socket.start()
    curator.start(asyncio.get_running_loop())
    try:
        await transport.run()
    finally:
        curator.stop()
        await control_socket.stop()
        await background_tasks.shutdown()


def _build_dispatch(bg: BackgroundTasks):
    """Wire control-socket ops to BackgroundTasks methods.

    The dispatcher is intentionally exhaustive — adding a new op here is
    the same effort as adding a new bg method, and unknown ops return a
    structured error rather than silently 200ing.
    """

    async def dispatch(op: str, args: dict) -> dict:
        if op == "bg_spawn":
            try:
                chat_id = int(args["chat_id"])
                name = str(args["name"])
                prompt = str(args["prompt"])
            except (KeyError, TypeError, ValueError) as exc:
                return {
                    "ok": False,
                    "error": f"bad spawn args: {exc}",
                    "kind": "BadRequest",
                }
            try:
                task = await bg.spawn(chat_id, name, prompt)
            except (
                BackgroundTaskLimitReached,
                NameAlreadyInUse,
                BackgroundTaskError,
            ) as exc:
                return {
                    "ok": False,
                    "error": str(exc),
                    "kind": type(exc).__name__,
                }
            return {
                "ok": True,
                "result": {
                    "name": task.name,
                    "spawned_at": task.spawned_at.isoformat(),
                    "pid": task.pid,
                    "log_path": str(task.log_path),
                },
            }
        if op == "bg_cancel":
            name = str(args.get("name", ""))
            if not name:
                return {"ok": False, "error": "missing 'name'", "kind": "BadRequest"}
            try:
                cancelled = await bg.cancel(name)
            except TaskNotFound as exc:
                return {"ok": False, "error": str(exc), "kind": "TaskNotFound"}
            reason = "cancelled" if cancelled else "task is not running anymore"
            return {"ok": True, "result": {"cancelled": cancelled, "reason": reason}}
        if op == "bg_status":
            name = args.get("name")
            if isinstance(name, str) and name:
                task = await bg.get(name)
                if task is None:
                    return {
                        "ok": False,
                        "error": f"No background task named '{name}'.",
                        "kind": "TaskNotFound",
                    }
                return {"ok": True, "result": task.to_summary()}
            tasks = await bg.status_summary()
            return {"ok": True, "result": tasks}
        if op == "bg_tail":
            name = str(args.get("name", ""))
            if not name:
                return {"ok": False, "error": "missing 'name'", "kind": "BadRequest"}
            lines_arg = args.get("lines", 50)
            try:
                lines = int(lines_arg)
            except (TypeError, ValueError):
                return {
                    "ok": False,
                    "error": "'lines' must be an int",
                    "kind": "BadRequest",
                }
            if lines <= 0:
                lines = 50
            try:
                text = await bg.tail_log(name, lines)
            except TaskNotFound as exc:
                return {"ok": False, "error": str(exc), "kind": "TaskNotFound"}
            return {"ok": True, "result": {"text": text}}
        return {"ok": False, "error": f"unknown op '{op}'", "kind": "BadRequest"}

    return dispatch


if __name__ == "__main__":
    try:
        asyncio.run(_run())
    except RuntimeError as exc:
        # Startup failures: env validation, missing claude on PATH, etc.
        print(f"vexis-agent: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass

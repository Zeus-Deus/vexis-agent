"""Vexis-Agent entry point."""

from __future__ import annotations

import asyncio
import atexit
import errno
import fcntl
import logging
import os
import shutil
import signal
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
from core.learning_curator import LearningController
from core.logging import setup_logging
from core.notify import Notifier
from core.paths import daemon_pid_path, state_dir, workspace_dir
from core.running_tasks import RunningTasks
from core.sessions import SessionStore
from core.web_server import DEFAULT_DASHBOARD_PORT, DashboardConfig, WebDashboard
from tools.browser import BrowserTools, get_manager as get_browser_manager
from transports.telegram import TelegramTransport

log = logging.getLogger(__name__)


class DaemonAlreadyRunning(RuntimeError):
    """Raised at startup when another vexis-agent process holds the
    PID lock at ``~/.vexis/daemon.pid``. The error message names the
    incumbent PID so the user can identify and stop it."""


def _alive(pid: int) -> bool:
    """``kill -0 PID`` — true iff the process exists and we can signal
    it. ``PermissionError`` (EPERM) is treated as alive: another user
    owns the PID, but it IS running, which is what the lock cares about.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_daemon_lock(pid_path: Path | None = None) -> int:
    """Acquire the single-instance daemon lock.

    Writes the current PID to ``~/.vexis/daemon.pid`` (or the override).
    Refuses to start when an alive incumbent already holds the lock;
    cleans up stale locks (PID file present but process dead) and
    proceeds. Race-safe via ``fcntl.flock`` on the file itself: two
    daemons starting in the same millisecond serialize on the lock and
    only the first one wins.

    Registers an ``atexit`` cleanup and SIGTERM/SIGINT handlers that
    unlink the file — but only if it still contains our own PID, so a
    later instance that legitimately replaced us isn't dispossessed by
    our shutdown.

    Raises :class:`DaemonAlreadyRunning` when a live incumbent exists.
    Returns the open file descriptor (kept open for the process
    lifetime so the flock survives).
    """
    target = pid_path or daemon_pid_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(target), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EWOULDBLOCK, errno.EACCES):
                raise
            # Another startup is mid-acquire. Read whatever it wrote
            # so the error message can name the incumbent.
            try:
                existing = int(os.read(fd, 64).decode("ascii", "ignore").strip() or "0")
            except (ValueError, OSError):
                existing = 0
            os.close(fd)
            raise DaemonAlreadyRunning(
                f"Vexis daemon already starting (lock held by PID {existing or '?'}); "
                f"refusing to start a second instance."
            ) from None

        # We hold the exclusive flock. Read the existing PID to decide
        # stale-vs-alive.
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            existing = int(os.read(fd, 64).decode("ascii", "ignore").strip() or "0")
        except (ValueError, OSError):
            existing = 0

        if existing and existing != os.getpid() and _alive(existing):
            os.close(fd)
            raise DaemonAlreadyRunning(
                f"Vexis daemon already running as PID {existing}. Stop it "
                f"with `kill {existing}` (or check ~/.vexis/daemon.pid if "
                f"that PID is wrong) before starting a new instance."
            )

        # Stale or empty — overwrite with our PID.
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(fd)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise

    own_pid = os.getpid()

    def _release() -> None:
        # Only unlink if the file still names us. Defensive against a
        # later instance that legitimately replaced our lock (which
        # would only happen if we crashed without releasing — flock
        # is freed on process exit so the next startup would clear us).
        try:
            current = target.read_text(encoding="utf-8").strip()
        except (FileNotFoundError, OSError):
            current = ""
        if current == str(own_pid):
            try:
                target.unlink()
            except OSError:
                pass

    atexit.register(_release)

    def _on_signal(signum: int, _frame) -> None:
        _release()
        # Re-raise the default behaviour so the asyncio loop unwinds.
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _on_signal)
        except (ValueError, OSError):
            # Non-main-thread or unsupported signal — atexit still fires.
            pass

    return fd


async def _run() -> None:
    config = load_config()
    setup_logging(config.log_level)

    acquire_daemon_lock()

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
    browser_manager = get_browser_manager()
    browser_tools = BrowserTools(browser_manager, workspace)
    control_socket = ControlSocket(
        default_socket_path(),
        _build_dispatch(background_tasks, browser_tools),
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
    learning_curator = LearningController(workspace=workspace, notifier=notifier)

    dashboard_port = _dashboard_port_from_env()
    dashboard = WebDashboard(
        workspace=workspace,
        sessions=sessions,
        running_tasks=running_tasks,
        background_tasks=background_tasks,
        curator=curator,
        browser=browser_tools,
        config=DashboardConfig(
            port=dashboard_port,
            web_dist=Path(__file__).resolve().parent / "web" / "dist",
        ),
    )

    transport = TelegramTransport(
        token=config.telegram_bot_token,
        handler=handler,
        running_tasks=running_tasks,
        allowed_user_id=config.telegram_allowed_user_id,
        background_tasks=background_tasks,
        notifier=notifier,
        curator=curator,
        learning_curator=learning_curator,
        dashboard=dashboard,
    )

    log.info("Vexis-Agent starting")
    await control_socket.start()
    await dashboard.start()
    curator.start(asyncio.get_running_loop())
    learning_curator.start(asyncio.get_running_loop())
    try:
        await transport.run()
    finally:
        learning_curator.stop()
        curator.stop()
        await dashboard.stop()
        await control_socket.stop()
        await background_tasks.shutdown()
        await browser_manager.stop()


def _dashboard_port_from_env() -> int:
    raw = os.environ.get("VEXIS_DASHBOARD_PORT")
    if not raw:
        return DEFAULT_DASHBOARD_PORT
    try:
        port = int(raw)
    except ValueError:
        log.warning(
            "Ignoring VEXIS_DASHBOARD_PORT=%r (not an int); using default %d",
            raw,
            DEFAULT_DASHBOARD_PORT,
        )
        return DEFAULT_DASHBOARD_PORT
    if port <= 0 or port > 65535:
        log.warning(
            "Ignoring VEXIS_DASHBOARD_PORT=%d (out of range); using default %d",
            port,
            DEFAULT_DASHBOARD_PORT,
        )
        return DEFAULT_DASHBOARD_PORT
    return port


def _build_dispatch(bg: BackgroundTasks, browser: BrowserTools):
    """Wire control-socket ops to in-daemon singletons.

    The dispatcher is intentionally exhaustive — adding a new op here is
    the same effort as adding a new bg/browser method, and unknown ops
    return a structured error rather than silently 200ing.
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
        if op == "browser_navigate":
            url = args.get("url", "")
            return await browser.navigate(url if isinstance(url, str) else "")
        if op == "browser_snapshot":
            return await browser.snapshot(bool(args.get("full", False)))
        if op == "browser_click":
            try:
                index = int(args.get("index"))
            except (TypeError, ValueError):
                return {
                    "ok": False,
                    "error": "'index' must be an integer",
                    "kind": "BadRequest",
                }
            return await browser.click(index)
        if op == "browser_type":
            try:
                index = int(args.get("index"))
            except (TypeError, ValueError):
                return {
                    "ok": False,
                    "error": "'index' must be an integer",
                    "kind": "BadRequest",
                }
            text = args.get("text", "")
            if not isinstance(text, str):
                return {
                    "ok": False,
                    "error": "'text' must be a string",
                    "kind": "BadRequest",
                }
            clear = bool(args.get("clear", True))
            return await browser.type(index, text, clear)
        if op == "browser_press":
            key = args.get("key", "")
            return await browser.press(key if isinstance(key, str) else "")
        if op == "browser_back":
            return await browser.back()
        if op == "browser_scroll":
            direction = args.get("direction", "")
            if not isinstance(direction, str):
                direction = ""
            try:
                pages = float(args.get("pages", 1.0))
            except (TypeError, ValueError):
                return {
                    "ok": False,
                    "error": "'pages' must be a number",
                    "kind": "BadRequest",
                }
            return await browser.scroll(direction, pages)
        if op == "browser_screenshot":
            include_b64_raw = args.get("include_base64")
            include_b64 = (
                bool(include_b64_raw) if include_b64_raw is not None else None
            )
            return await browser.screenshot(
                bool(args.get("full_page", False)),
                include_base64=include_b64,
            )
        return {"ok": False, "error": f"unknown op '{op}'", "kind": "BadRequest"}

    return dispatch


if __name__ == "__main__":
    try:
        asyncio.run(_run())
    except DaemonAlreadyRunning as exc:
        # Distinct exit code so a supervisor (systemd, nohup loop,
        # whatever) can tell "another instance owns this" apart from
        # actual config errors. Stderr — logging may not be set up
        # yet when the lock check fires.
        print(f"vexis-agent: {exc}", file=sys.stderr)
        sys.exit(2)
    except RuntimeError as exc:
        # Startup failures: env validation, missing claude on PATH, etc.
        print(f"vexis-agent: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass

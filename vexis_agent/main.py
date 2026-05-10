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

from vexis_agent.core.brain.claude_code import ClaudeCodeBrain, build_system_prompt
from vexis_agent.core.background_tasks import (
    BackgroundTaskError,
    BackgroundTaskLimitReached,
    BackgroundTasks,
    NameAlreadyInUse,
    TaskNotFound,
)
from vexis_agent.core.config import load_config
from vexis_agent.core.control_socket import ControlSocket, default_socket_path
from vexis_agent.core.curator import CuratorController
from vexis_agent.core.handler import MessageHandler
from vexis_agent.core.learning_curator import LearningController
from vexis_agent.core.logging import setup_logging
from vexis_agent.core.notify import Notifier
from vexis_agent.core.paths import daemon_pid_path, state_dir, workspace_dir
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore
from vexis_agent.core.web_server import DEFAULT_DASHBOARD_PORT, DashboardConfig, WebDashboard
from vexis_agent.tools.browser import BrowserTools, get_manager as get_browser_manager
from vexis_agent.transports.telegram import TelegramTransport
from vexis_agent.transports.web import WebChatTransport

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

    # Brain CLI prerequisite is conditional on the configured kind —
    # users on opencode shouldn't be blocked by a missing `claude`,
    # and vice-versa. The null brain has no CLI to require (test fake).
    from vexis_agent.core.yaml_config import brain_kind as _brain_kind_fn

    _BRAIN_BINARIES: dict[str, tuple[str, str]] = {
        "claude-code": (
            "claude",
            "Install via the official guide at "
            "https://docs.anthropic.com/claude/claude-code, then run "
            "'claude /login'.",
        ),
        "opencode": (
            "opencode",
            "Install with: curl -fsSL https://opencode.ai/install | bash",
        ),
        "null": ("", ""),  # test fake — no CLI required.
    }
    kind = _brain_kind_fn()
    binary, install_hint = _BRAIN_BINARIES.get(kind, ("claude", ""))
    if binary and shutil.which(binary) is None:
        raise RuntimeError(
            f"`{binary}` CLI not found on PATH "
            f"(brain.kind={kind} in ~/.vexis/config.yaml). {install_hint}"
        )

    # Per-feature soft dependencies. The daemon used to hard-require
    # all of these (Hyprland-only-or-die); Phase 5j demoted them to
    # warnings so vexis runs anywhere — Telegram chat works without
    # any of these — and the tools that actually need them surface
    # the missing-binary error at invocation time.
    #
    # Each feature group declares which capability it powers so the
    # startup banner is honest about what *will* and *won't* work
    # on this install. Setup wizard + doctor mirror this taxonomy.
    _FEATURE_TOOLS: dict[str, dict[str, str]] = {
        "voice notes": {
            "voxtype": "Speech-to-text wrapper. Install separately; absent → voice notes won't transcribe.",
            "ffmpeg":  "Audio decoding. Install via your distro (pacman/apt/dnf).",
        },
        "desktop control (Hyprland/Wayland)": {
            "hyprctl": "Ships with Hyprland; absent → window/workspace dispatches no-op.",
            "wtype":   "Wayland typing (Hyprland/sway). Absent → vexis-type doesn't work.",
            "ydotool": "Wayland uinput (mouse + keys). Absent → vexis-click/key/move don't work. Needs ydotool.service running.",
            "grim":    "Wayland screenshots. Absent → screenshot tool returns an error.",
        },
        "shell helpers": {
            "jq":      "JSON parsing for some dispatch wrappers.",
        },
    }
    missing_features: list[str] = []
    for feature, tools in _FEATURE_TOOLS.items():
        missing = [cmd for cmd in tools if shutil.which(cmd) is None]
        if missing:
            missing_features.append(feature)
            log.warning(
                "feature unavailable: %s — missing %s. The daemon runs; "
                "tools that need these will return a clear error when "
                "invoked. Run 'vexis-agent doctor' for install hints.",
                feature, ", ".join(missing),
            )
    if missing_features:
        log.info(
            "vexis-agent starting with %d feature group(s) degraded; "
            "Telegram chat + brain dispatch still work.",
            len(missing_features),
        )

    if shutil.which("tailscale") is None:
        log.warning(
            "tailscale not on PATH; live streaming + remote dashboard "
            "URL unavailable. Daemon continues; install Tailscale and "
            "run 'tailscale up' to enable."
        )

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

    # v3c Day 5: seed USER.md with the relationships meta-system
    # context line on first daemon boot. Idempotent — the marker
    # in existing entries skips the install on subsequent starts.
    # The seed describes the silent-extraction-default mental
    # model so the brain knows about the queue + approval surface
    # before it sees its first candidate. Direct write (not via
    # the candidate queue) because this is meta-system context
    # about the system itself, not a recurring observation.
    try:
        from vexis_agent.core.memory import MemoryStore
        from vexis_agent.core.paths import memories_dir as memories_dir_fn
        from vexis_agent.core.relationships import (
            RELATIONSHIPS_USER_SEED_MARKER,
            RELATIONSHIPS_USER_SEED_TEXT,
        )
        memory_store = MemoryStore(memories_dir_fn(workspace))
        if memory_store.ensure_seed(
            "user",
            marker=RELATIONSHIPS_USER_SEED_MARKER,
            content=RELATIONSHIPS_USER_SEED_TEXT,
        ):
            log.info("Installed v3c relationships seed into USER.md")
    except Exception:
        # Seeding is convenience, not load-bearing — never fail
        # daemon startup over it.
        log.exception("relationships USER.md seed install raised")

    # CAPABILITIES.md ships as package data (vexis_agent/data/) so
    # pipx-installed users without a source checkout still get it.
    # The startup warning fires only if the wheel build dropped the
    # file — a packaging regression, not an end-user problem.
    from vexis_agent.data import read_capabilities

    if read_capabilities() is None:
        log.warning(
            "CAPABILITIES.md missing from package data. "
            "Vexis won't know which tools are available — likely a "
            "packaging build issue; reinstall with 'vexis-agent update'."
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

    # Phase C Day 3: ``brain.kind`` selects the agent CLI to spawn
    # under. Default ``claude-code`` keeps the pre-Phase-C path
    # unchanged. ``opencode`` is opt-in (foreground turns work
    # end-to-end Day 3; transcript readback lands Day 4).
    # ``null`` is the test fake — useful for dashboard-only smoke.
    from vexis_agent.core.yaml_config import brain_kind as _brain_kind
    _kind = _brain_kind()

    # Model UX Day 1: validate the on-disk config and log findings
    # at severity-appropriate levels. Doesn't crash; same fall-back
    # posture as ``brain_kind()`` itself. The slash command (Day 2)
    # and dashboard (Day 4) will reject ``error``-severity findings
    # at write time; startup is observe-only.
    try:
        from vexis_agent.core.model_discovery import (
            discovery_for_validator as _validator_discovery,
        )
        from vexis_agent.core.model_validator import (
            log_findings as _log_validator_findings,
            validate_models_config as _validate_models_config,
        )
        from vexis_agent.core.yaml_config import (
            VALID_BRAIN_KINDS as _validator_brain_kinds,
            _read_raw as _read_raw_config,
        )
        # Day 4 of model picker UX wires discovery into the startup
        # pass so rule 6 (available-models membership) surfaces at
        # boot — opencode users with stale model ids in their
        # config see the error before their first /model spawn.
        _findings = _validate_models_config(
            _read_raw_config(), _kind,
            available_models_per_brain=_validator_discovery(
                _validator_brain_kinds,
            ),
        )
        if _findings:
            log.info(
                "model_validator: %d finding(s) at startup; see below",
                len(_findings),
            )
            _log_validator_findings(_findings)
    except Exception:
        # Never let validator failures block daemon startup.
        log.exception("model_validator startup pass raised; continuing")

    if _kind == "opencode":
        from vexis_agent.core.brain.opencode import OpenCodeBrain
        brain = OpenCodeBrain(
            workspace=workspace,
            session=sessions,
            running_tasks=running_tasks,
        )
        log.info("Brain: OpenCodeBrain (brain.kind=opencode)")
    elif _kind == "null":
        from vexis_agent.core.brain.null import BrainNull
        brain = BrainNull()
        log.warning(
            "Brain: BrainNull (brain.kind=null) — no real model "
            "calls will fire; this is a test/diagnostic mode."
        )
    else:
        brain = ClaudeCodeBrain(
            workspace=workspace,
            session=sessions,
            running_tasks=running_tasks,
        )
        log.info("Brain: ClaudeCodeBrain (brain.kind=claude-code)")
    handler = MessageHandler(
        brain=brain,
        sessions=sessions,
        allowed_user_id=config.telegram_allowed_user_id,
        notifier=notifier,
        workspace=workspace,
    )
    curator = CuratorController(
        workspace=workspace, notifier=notifier, brain=brain,
    )
    learning_curator = LearningController(
        workspace=workspace, notifier=notifier, brain=brain,
    )

    # /schedule feature (see docs/schedules.md). Manager is a daemon
    # thread that fires due schedules into the chat FIFO. Disabled
    # via schedules.enabled: false in config.yaml — when off the
    # tick body is a no-op and the slash command replies with the
    # disabled note.
    from vexis_agent.core.paths import vexis_dir
    from vexis_agent.core.schedule_manager import ScheduleManager
    from vexis_agent.core.schedule_state import ScheduleStore
    schedule_store = ScheduleStore(vexis_dir() / "schedules.json")
    schedule_manager = ScheduleManager(
        schedule_store,
        running_tasks=running_tasks,
        allowed_user_id=config.telegram_allowed_user_id,
    )

    # Web chat bridges the dashboard chat UI to the same MessageHandler
    # the Telegram transport uses. Sharing the handler means both
    # transports see the same SessionStore and Notifier — slash commands
    # in Telegram and clicks in the chat sidebar mutate the same state.
    # The chat_id namespace is partitioned (transports/web.py:WEB_CHAT_ID)
    # so the per-chat notifier buffers don't cross-contaminate.
    web_chat = WebChatTransport(
        handler=handler,
        allowed_user_id=config.telegram_allowed_user_id,
    )

    dashboard_port = _dashboard_port_from_env()
    dashboard = WebDashboard(
        workspace=workspace,
        sessions=sessions,
        running_tasks=running_tasks,
        background_tasks=background_tasks,
        curator=curator,
        browser=browser_tools,
        learning=learning_curator,
        config=DashboardConfig(
            port=dashboard_port,
            web_dist=_resolve_web_dist(),
        ),
        chat=web_chat,
        # Day 5 of model UX: the canary-check helper needs to know
        # what brain class the daemon actually instantiated so the
        # dashboard payload's global_findings can surface the
        # "edited brain.kind without restarting" warning. ``_kind``
        # is the value ``brain_kind()`` returned at startup; the
        # check runs on every dashboard poll against the
        # current on-disk value.
        running_brain_kind=_kind,
    )

    # Late-attach the schedule store so the dashboard /api/v1/schedules*
    # endpoints can read/mutate it. Kept off the WebDashboard constructor
    # for backwards compatibility with test/alternate wirings.
    dashboard.attach_schedule_store(schedule_store)

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
        schedule_store=schedule_store,
    )

    log.info("Vexis-Agent starting")
    await control_socket.start()
    await dashboard.start()
    curator.start(asyncio.get_running_loop())
    learning_curator.start(asyncio.get_running_loop())
    schedule_manager.start(asyncio.get_running_loop())
    try:
        await transport.run()
    finally:
        schedule_manager.stop()
        learning_curator.stop()
        curator.stop()
        await dashboard.stop()
        await control_socket.stop()
        await background_tasks.shutdown()
        await browser_manager.stop()


def _resolve_web_dist() -> Path:
    """Locate the built dashboard frontend.

    Two locations to check, in order:

      1. ``vexis_agent/web_dist/`` — bundled into the wheel, ships
         with the package. This is what pipx-installed users get.
         Always populated by ``cp -r web/dist vexis_agent/web_dist``
         at release time (see release skill); the path is included
         via ``[tool.setuptools.package-data]`` in pyproject.toml.

      2. ``<repo>/web/dist/`` — the source-checkout build output
         from ``cd web && npm run build``. Used when running the
         daemon from an editable install (``pip install -e .``)
         where the bundled copy under site-packages would be stale
         relative to your live frontend edits.

    Falling back from (1) to (2) lets dev workflows that re-run
    ``npm run build`` see their changes immediately, while pipx
    users always have a working dashboard out of the box.

    If neither exists (very unusual — broken install), return the
    expected bundled path; the dashboard route will 404 and
    web_server logs a clear warning.

    Surfaced in v0.1.4 after the first public install: prior to
    this resolver, ``main.py`` hard-coded ``web/dist`` at the repo
    root and pipx-installed users got "frontend not built" errors
    on every dashboard hit because the wheel didn't ship the bundle.
    """
    bundled = Path(__file__).resolve().parent / "web_dist"
    if (bundled / "index.html").exists():
        return bundled

    # Source checkout: <repo>/vexis_agent/main.py → <repo>/web/dist
    source = Path(__file__).resolve().parent.parent / "web" / "dist"
    if (source / "index.html").exists():
        return source

    # Neither exists — return the bundled path so the eventual error
    # ("frontend not built") points at the location we'd expect for
    # a healthy pipx install. Source-checkout users hitting this
    # need to run ``cd web && npm run build`` once.
    return bundled


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


def main() -> None:
    """Daemon entry. Used by ``python -m vexis_agent.main``, by the
    ``vexis-agent run`` Typer command, and by direct ``python main.py``
    invocations during dev. Pre-Phase-2 callers expect side-effects on
    invocation, not a returned coroutine — keep that contract."""
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


if __name__ == "__main__":
    main()

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

from vexis_agent.core.addons import (
    AddonRuntime,
    discover_addons,
    load_addon,
)
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


async def _run() -> bool:
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
    # pipx-installed users without a source checkout still get it. It
    # is now the stable-core block (identity + the add-on/skill/MCP
    # model); per-tool how-to is assembled from per-capability modules
    # next to each tool (issue #30, vexis_agent.core.capabilities). The
    # startup warning fires only if the wheel build dropped the core
    # file — a packaging regression, not an end-user problem.
    from vexis_agent.data import read_capabilities

    if read_capabilities() is None:
        log.warning(
            "CAPABILITIES.md (capability core block) missing from package "
            "data. Vexis's system prompt will lose its identity + add-on "
            "model section — likely a packaging build issue; reinstall "
            "with 'vexis-agent update'."
        )

    sessions = SessionStore(state_path=state_dir() / "session.json")
    running_tasks = RunningTasks()

    # The notifier is shared between the handler (which consumes context
    # at the start of each brain turn) and the transport (which binds
    # the PTB application once Telegram is initialised). Same instance,
    # two roles — that's how notifications and brain context stay in sync.
    notifier = Notifier()
    # Sandbox routing is opt-in per task but the runner is wired in
    # unconditionally whenever Docker + the CLI are present; absent
    # those, BackgroundTasks falls back to direct execution and warns.
    from vexis_agent.core.sandbox_runner import SandboxRunner

    sandbox_runner: SandboxRunner | None = None
    if SandboxRunner.is_available():
        sandbox_runner = SandboxRunner()
    else:
        log.info(
            "vexis-sandbox CLI / docker not detected; background tasks "
            "will run without sandbox isolation."
        )
    background_tasks = BackgroundTasks(
        workspace=workspace,
        system_prompt_provider=lambda: build_system_prompt(workspace),
        sandbox_runner=sandbox_runner,
    )

    # The browser is no longer hardcoded here — it ships as the bundled
    # ``browser`` add-on (vexis_agent/addons/browser/). The add-on
    # instantiates the SessionManager + BrowserTools in its register(),
    # registers the nine browser_* dispatch handlers, owns session
    # lifecycle via a background task, and exposes the live BrowserTools
    # as the ``"browser"`` runtime service the dashboard reads. Core
    # stays browser-agnostic.

    # Watcher subsystem (generic registry + polling loop). Always
    # instantiated; source plugins are supplied by add-ons via
    # ``ctx.register_watcher_source`` + the in-core register_source
    # registry. The codemux add-on supplies the only shipping source;
    # future PTY / tmux sources plug in the same way through their
    # own add-ons. With no sources registered the registry sits empty
    # and the poller does nothing — zero cost for users who haven't
    # enabled a watcher source.
    from vexis_agent.core.watcher import WatcherController
    from vexis_agent.core.yaml_config import (
        watcher_oscillation_window_seconds as _watcher_osc,
        watcher_poll_interval_seconds as _watcher_poll,
    )
    watcher = WatcherController(
        poll_interval_seconds=_watcher_poll(),
        oscillation_window_seconds=_watcher_osc(),
    )
    log.info(
        "watcher: controller active (poll=%.1fs, oscillation_window=%.1fs); "
        "sources supplied by enabled add-ons",
        _watcher_poll(), _watcher_osc(),
    )

    # Add-on system (see docs/addons.md). Discover everything the
    # user opted into via ``addons.enabled`` in ~/.vexis/config.yaml,
    # load each via ``register(ctx)``, and hold the runtime so the
    # rest of the daemon can consult its registrations. The watcher
    # is attached as a shared service BEFORE addons load so add-ons
    # that need to talk to it (codemux's /codemux handler, the
    # watch_register dispatcher) can look it up via
    # ``ctx.get_service("watcher")`` at call time.
    # ``user_id`` is the multi-user seam — always "default"
    # in single-user mode, parameterised when multi-user lands.
    from vexis_agent.core.yaml_config import (
        addon_config as _addon_config,
        addons_disabled as _addons_disabled,
        addons_enabled as _addons_enabled,
    )
    addon_runtime = AddonRuntime()
    addon_runtime.attach_service("watcher", watcher)
    # The workspace path is a shared service so add-ons that bind to it
    # (the browser add-on's BrowserTools) resolve it the same way the
    # daemon did, without re-reading config. Attached BEFORE add-ons
    # load so register() can read it.
    addon_runtime.attach_service("workspace", workspace)
    # ``discover_addons`` defaults bundled add-ons in
    # ``DEFAULT_ENABLED_BUNDLED`` (browser) to on — they load even when
    # the user's config has no ``addons.enabled`` line, so capabilities
    # extracted from core (web browsing) survive an upgrade without a
    # config edit. ``addons.disabled`` still turns them off.
    for _discovered in discover_addons(
        enabled=_addons_enabled(),
        disabled=_addons_disabled(),
    ):
        load_addon(
            _discovered,
            addon_runtime,
            user_config=_addon_config(_discovered.manifest.name),
        )
    _loaded_count = sum(1 for a in addon_runtime.loaded_addons() if a.register_ok)
    if _loaded_count:
        log.info("addons: loaded %d add-on(s)", _loaded_count)

    # Upgrade-UX warning: users who had codemux working pre-Phase-B may
    # see it silently stop on first restart because the add-on system
    # is explicit-allow-list. Detect the common case (codemux MCP wired
    # but add-on not enabled) and log a clear "run this to fix" line.
    # Single log line, no auto-mutation — touching the user's config
    # without asking is the kind of thing that bites people.
    _enabled_set = set(_addons_enabled())
    if "codemux" not in _enabled_set:
        try:
            from vexis_agent.setup_wizard import detect_mcp_servers
            _servers = detect_mcp_servers()
            if any(
                isinstance(s, dict) and s.get("name") == "codemux"
                for s in _servers
            ):
                log.warning(
                    "addons: codemux MCP is configured but the codemux "
                    "add-on is not enabled. Run `vexis-addons enable "
                    "codemux` and restart to restore /codemux + the "
                    "watcher pings."
                )
        except Exception:
            pass  # best-effort; never block startup on this hint

    # Install add-on-shipped skills into the workspace so the brain's
    # session-start skill discovery picks them up. Idempotent — only
    # writes files whose content differs from disk. See
    # core/addon_skills.py for the layout and provenance-sidecar
    # convention.
    from vexis_agent.core.addon_skills import install_addon_skills
    install_addon_skills(workspace, addon_runtime)

    control_socket = ControlSocket(
        default_socket_path(),
        _build_dispatch(
            background_tasks,
            watcher,
            addon_runtime=addon_runtime,
        ),
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

    # System-prompt header blocks are now supplied by add-ons via
    # ``ctx.register_system_prompt_block``. The codemux add-on's
    # "Active Codemux work: N workspaces" line lives there; future
    # add-ons add their own. Each provider returns either a string
    # (injected verbatim) or None (skip for this session). Wired
    # unconditionally — the runtime returns an empty list when no
    # add-ons registered blocks.
    def _addon_header_blocks() -> list[str]:
        out: list[str] = []
        for reg in addon_runtime.header_blocks():
            try:
                value = reg.provider()
            except Exception:
                log.exception(
                    "addon %r header-block provider %r raised; skipping",
                    reg.addon_name, reg.name,
                )
                continue
            if value:
                out.append(value)
        return out
    extra_prompt_blocks = _addon_header_blocks

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
            extra_prompt_blocks=extra_prompt_blocks,
        )
        log.info("Brain: ClaudeCodeBrain (brain.kind=claude-code)")

    # Add-on MCP defaults (see docs/addons.md). An add-on can declare
    # an MCP server via ``ctx.register_mcp_server_default``; this is the
    # one live consumer. Now that the brain AND the add-on runtime both
    # exist, fold those defaults into the active brain's native MCP
    # config. Goes through ``brain.write_mcp_config`` so claude-code
    # (.mcp.json) and opencode (opencode.json) are both served; the next
    # ``claude -p`` / ``opencode run`` spawn re-reads the file, so no
    # daemon restart is needed. Precedence: user mcp-servers.yaml wins on
    # a name collision, add-on defaults only fill gaps; user-owned
    # native-file entries are preserved by the brain writer itself.
    from vexis_agent.core.addon_mcp import merge_addon_mcp_defaults
    merge_addon_mcp_defaults(brain, addon_runtime)

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

    # Kanban (see docs/kanban.md). Multi-task durable work queue.
    # Behind ``kanban.enabled`` (default true). The store backs a
    # SQLite DB at ~/.vexis/kanban.db; the controller's dispatcher
    # ticks every ``kanban.dispatch_interval_seconds`` (default 60s),
    # claims ready tasks, and spawns workers via brain.spawn_aux.
    # Telegram /kanban commands and the /api/v1/kanban/* dashboard
    # routes both consume the same store; the WS event stream and
    # Telegram notifier subscribe to the shared task_events table.
    from vexis_agent.core.kanban.db import KanbanStore
    from vexis_agent.core.kanban.dispatcher import KanbanController
    from vexis_agent.core.kanban.lanes import kanban_enabled
    kanban_store: KanbanStore | None = None
    kanban_controller: KanbanController | None = None
    if kanban_enabled():
        kanban_store = KanbanStore(vexis_dir() / "kanban.db")
        kanban_controller = KanbanController(
            store=kanban_store,
            brain=brain,
            workspace=workspace,
        )
    else:
        log.info("kanban: disabled via config (kanban.enabled=false)")

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
        # The dashboard reaches the live browser via the add-on runtime
        # service registry (``get_service("browser")``) instead of a
        # direct BrowserTools handle — so core/web_server never imports
        # the browser add-on. When the browser add-on is disabled the
        # service is absent and the Browser tab routes degrade
        # gracefully (503 / hidden payload).
        addon_runtime=addon_runtime,
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
    # Same pattern for kanban — the dashboard's /api/v1/kanban/* and
    # WS /api/v1/kanban/events return 503 until the store lands.
    if kanban_store is not None:
        dashboard.attach_kanban_store(kanban_store)

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
        kanban_store=kanban_store,
        watcher=watcher,
        addon_runtime=addon_runtime,
    )
    # The watcher pushes its idle pings through the same notifier the
    # rest of the daemon uses — same per-chat context buffer, same
    # Markdown fall-back, same retry shape as vexis-bg's exit pings.
    watcher.set_notify(notifier.send)

    # Wire the dispatch callback so ScheduleManager fires route through
    # the transport's ``claim() ? drain : enqueue`` protocol instead of
    # raw FIFO enqueue. Without this, a fire at idle wall-clock time
    # (2:30 AM) strands the prompt in the deque until the next real
    # user message wakes a fresh claim — the v0.4.0 bug. The transport
    # exists by here; ScheduleManager.start() hasn't been called yet,
    # so the very first tick sees the dispatch_fn wired.
    schedule_manager.set_dispatch_fn(transport.dispatch_scheduled_fire)
    # Reverse wire: the drain calls back into the manager with the
    # real brain outcome so ``last_status`` reflects truth instead of
    # the pre-emptive "ok" the dispatch-time _record_fire writes.
    # Without this, a brain failure (the 15 May 2026 Anthropic 500)
    # leaves the schedule showing ok forever despite the user seeing
    # an error in Telegram.
    transport._schedule_outcome_cb = schedule_manager.report_fire_outcome

    log.info("Vexis-Agent starting")
    await control_socket.start()
    await dashboard.start()
    curator.start(asyncio.get_running_loop())
    learning_curator.start(asyncio.get_running_loop())
    schedule_manager.start(asyncio.get_running_loop())
    if kanban_controller is not None:
        kanban_controller.start(asyncio.get_running_loop())
    await watcher.start()
    # Add-on background tasks come last so they boot against a daemon
    # that's already serving (control socket up, dashboard up, brain
    # ready). They get cancelled FIRST in the finally block for the
    # symmetric reason — let them drain before core subsystems vanish.
    await addon_runtime.start_all_background_tasks()
    try:
        await transport.run()
    finally:
        await addon_runtime.stop_all_background_tasks()
        await watcher.stop()
        if kanban_controller is not None:
            await kanban_controller.stop()
        if kanban_store is not None:
            kanban_store.close()
        schedule_manager.stop()
        learning_curator.stop()
        curator.stop()
        await dashboard.stop()
        await control_socket.stop()
        await background_tasks.shutdown()
        # The browser session is torn down by the browser add-on's
        # ``browser-session-lifecycle`` background task, cancelled above
        # via ``addon_runtime.stop_all_background_tasks()`` — no direct
        # ``browser_manager.stop()`` here (core stays browser-agnostic).
    # By here every socket the daemon owns (control socket, dashboard,
    # Telegram long-poll) is closed, so a re-exec'd image can re-bind
    # cleanly. ``main()`` performs the execv when this is True; a normal
    # SIGTERM/SIGINT shutdown leaves it False and the process exits.
    return bool(getattr(transport, "_restart_requested", False))


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


def _build_dispatch(
    bg: BackgroundTasks,
    watcher: "object | None" = None,  # WatcherController; weak-typed to avoid import cycle
    addon_runtime: "AddonRuntime | None" = None,
):
    """Wire control-socket ops to in-daemon singletons.

    The dispatcher is intentionally exhaustive — adding a new op here is
    the same effort as adding a new bg method, and unknown ops return a
    structured error rather than silently 200ing.

    The nine ``browser_*`` ops are NOT hardcoded here anymore — they are
    registered by the bundled browser add-on via
    ``ctx.register_dispatch_handler`` and routed through the
    add-on-dispatch-first check below. With the browser add-on disabled,
    ``vexis-browse`` ops fall through to the unknown-op error, which is
    the honest answer (the browser isn't loaded).

    ``watcher`` is ``None`` when the Codemux MCP isn't configured;
    ``watch_*`` ops in that mode return ``CodemuxNotConfigured`` so
    ``vexis-watch`` can print the conditional-activation message and
    exit cleanly. The op surface is otherwise identical, which keeps
    the CLI / spec stable across MCP-on / MCP-off states.

    ``addon_runtime`` is checked FIRST when dispatching: an add-on
    that registered an op via ``ctx.register_dispatch_handler`` wins
    over the hardcoded branches below. This lets Phase B move
    ``watch_*`` ops into the codemux add-on without changing the
    control-socket protocol or the ``vexis-watch`` CLI.
    """
    # Lazy import keeps tests of _build_dispatch that don't care about
    # the watcher path independent of the watcher import side effects.
    from vexis_agent.core.watcher import (
        DEFAULT_IDLE_AFTER_SECONDS as _W_DEFAULT_IDLE,
        DuplicateName as _WDuplicate,
        UnknownName as _WUnknown,
        UNAVAILABLE_MESSAGE as _WUnavailable,
    )
    from vexis_agent.core.watcher.sources import SourceUnavailable as _WSrcGone

    # Friendlier "workspace not active" path. When the user invokes
    # ``vexis-watch register --workspace <id>`` and the target workspace
    # isn't the one currently focused in Codemux, the MCP can't read
    # its panes — workspace_info/pane_list operate on the active
    # workspace only. We surface a distinct ``WorkspaceNotActive`` so
    # the CLI can print a fix-suggesting message rather than a raw
    # SourceUnavailable. Detected by string-match on the resolver's
    # error wording — the resolver lives in a single file so the
    # phrasing is pinned by import.
    _WS_NOT_ACTIVE_MARKERS = ("is not the active codemux workspace",)

    def _watcher_unavailable() -> dict:
        return {
            "ok": False,
            "error": _WUnavailable,
            "kind": "CodemuxNotConfigured",
        }

    async def dispatch(op: str, args: dict) -> dict:
        # Add-on dispatch handlers win over hardcoded branches —
        # Phase B will use this to extract ``watch_*`` into the
        # codemux add-on without changing the control-socket
        # protocol. The hardcoded branches below still answer for
        # any op not claimed by an add-on, so nothing breaks until
        # the codemux extraction actually lands.
        if addon_runtime is not None:
            _addon_handlers = addon_runtime.dispatch_handlers()
            _reg = _addon_handlers.get(op)
            if _reg is not None:
                try:
                    return await _reg.handler(args)
                except Exception as exc:  # noqa: BLE001
                    log.exception(
                        "addon %r dispatch handler for %r raised",
                        _reg.addon_name, op,
                    )
                    return {
                        "ok": False,
                        "error": f"addon dispatch error: {exc}",
                        "kind": "AddonDispatchError",
                    }

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
            # Optional fields; absent → defaults are applied inside
            # bg.spawn (heuristic-based sandbox decision, no verify).
            raw_sandbox = args.get("sandbox")
            sandbox: bool | None
            if raw_sandbox is None:
                sandbox = None
            else:
                sandbox = bool(raw_sandbox)
            verify_checks_raw = args.get("verify_checks")
            verify_checks = (
                str(verify_checks_raw)
                if isinstance(verify_checks_raw, str) and verify_checks_raw
                else None
            )
            model_raw = args.get("model")
            model = (
                str(model_raw)
                if isinstance(model_raw, str) and model_raw
                else None
            )
            try:
                task = await bg.spawn(
                    chat_id,
                    name,
                    prompt,
                    sandbox=sandbox,
                    verify_checks=verify_checks,
                    model=model,
                )
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
                    "sandbox_enabled": task.sandbox_enabled,
                    "verify_checks_path": task.verify_checks_path,
                    "model": task.model,
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
        # browser_* ops are owned by the bundled browser add-on and
        # routed via the add-on-dispatch-first check at the top of this
        # function — there are no hardcoded browser branches here.
        if op == "watch_register":
            # The codemux add-on owns the resolver path (workspace_id
            # → session_id) and registers its own watch_register
            # handler — which the addon-dispatch-first check at the
            # top of dispatch() routes to BEFORE this branch. We only
            # reach this fallback when NO add-on registered
            # watch_register, which today means no codemux add-on is
            # loaded. The generic register path still works for
            # future non-codemux source plugins that supply their
            # own ``identifier``.
            if watcher is None:
                return _watcher_unavailable()
            try:
                name = str(args["name"])
                source_type = str(args.get("source", "codemux"))
                agent_kind = str(args["agent_kind"])
                chat_id = int(args["chat_id"])
            except (KeyError, TypeError, ValueError) as exc:
                return {
                    "ok": False,
                    "error": f"bad watch_register args: {exc}",
                    "kind": "BadRequest",
                }
            idle_after = args.get("idle_after_seconds")
            try:
                idle_after_int = (
                    int(idle_after) if idle_after is not None
                    else _W_DEFAULT_IDLE
                )
            except (TypeError, ValueError):
                return {
                    "ok": False,
                    "error": "'idle_after_seconds' must be an int",
                    "kind": "BadRequest",
                }
            goal_hint_raw = args.get("goal_hint")
            goal_hint = goal_hint_raw if isinstance(goal_hint_raw, str) else None
            repo_path_raw = args.get("repo_path")
            repo_path = repo_path_raw if isinstance(repo_path_raw, str) else None
            workspace_id_raw = args.get("workspace_id")
            workspace_id = (
                workspace_id_raw if isinstance(workspace_id_raw, str) else None
            )
            identifier_raw = args.get("identifier")
            identifier = (
                identifier_raw if isinstance(identifier_raw, str) else None
            )
            if not identifier:
                return {
                    "ok": False,
                    "error": (
                        f"source {source_type!r} requires 'identifier'; "
                        f"if you wanted workspace_id auto-resolution, "
                        f"enable the codemux add-on "
                        f"(``vexis-addons enable codemux``)."
                    ),
                    "kind": "BadRequest",
                }
            try:
                agent = await watcher.register_agent(
                    name=name,
                    source_type=source_type,
                    identifier=identifier,
                    agent_kind=agent_kind,
                    chat_id=chat_id,
                    idle_after_seconds=idle_after_int,
                    goal_hint=goal_hint,
                    repo_path=repo_path,
                    workspace_id=workspace_id,
                )
            except _WDuplicate as exc:
                return {"ok": False, "error": str(exc), "kind": "DuplicateName"}
            except _WSrcGone as exc:
                msg = str(exc)
                kind = "SourceUnavailable"
                if any(m in msg.lower() for m in _WS_NOT_ACTIVE_MARKERS):
                    kind = "WorkspaceNotActive"
                return {"ok": False, "error": msg, "kind": kind}
            except ValueError as exc:
                return {"ok": False, "error": str(exc), "kind": "BadRequest"}
            return {"ok": True, "result": agent.to_dict()}
        if op == "watch_unregister":
            if watcher is None:
                return _watcher_unavailable()
            name = str(args.get("name", ""))
            if not name:
                return {"ok": False, "error": "missing 'name'", "kind": "BadRequest"}
            try:
                agent = await watcher.unregister_agent(name)
            except _WUnknown as exc:
                return {"ok": False, "error": str(exc), "kind": "UnknownName"}
            return {"ok": True, "result": agent.to_dict()}
        if op == "watch_list":
            if watcher is None:
                return _watcher_unavailable()
            return {
                "ok": True,
                "result": [a.to_dict() for a in watcher.list_agents()],
            }
        if op == "watch_status":
            if watcher is None:
                return _watcher_unavailable()
            name = args.get("name")
            if isinstance(name, str) and name:
                agent = watcher.get_agent(name)
                if agent is None:
                    return {
                        "ok": False,
                        "error": f"no watched agent named {name!r}",
                        "kind": "UnknownName",
                    }
                return {"ok": True, "result": agent.to_dict()}
            return {
                "ok": True,
                "result": [a.to_dict() for a in watcher.list_agents()],
            }
        if op == "watch_mute":
            if watcher is None:
                return _watcher_unavailable()
            name = str(args.get("name", ""))
            if not name:
                return {"ok": False, "error": "missing 'name'", "kind": "BadRequest"}
            muted = bool(args.get("muted", True))
            try:
                agent = await watcher.mute_agent(name, muted)
            except _WUnknown as exc:
                return {"ok": False, "error": str(exc), "kind": "UnknownName"}
            return {"ok": True, "result": agent.to_dict()}
        if op == "watch_tail":
            if watcher is None:
                return _watcher_unavailable()
            name = str(args.get("name", ""))
            if not name:
                return {"ok": False, "error": "missing 'name'", "kind": "BadRequest"}
            lines_arg = args.get("lines", 20)
            try:
                lines = int(lines_arg)
            except (TypeError, ValueError):
                return {
                    "ok": False,
                    "error": "'lines' must be an int",
                    "kind": "BadRequest",
                }
            if lines <= 0:
                lines = 20
            try:
                text = await watcher.tail(name, lines)
            except _WUnknown as exc:
                return {"ok": False, "error": str(exc), "kind": "UnknownName"}
            except _WSrcGone as exc:
                return {"ok": False, "error": str(exc), "kind": "SourceUnavailable"}
            return {"ok": True, "result": {"text": text}}
        return {"ok": False, "error": f"unknown op '{op}'", "kind": "BadRequest"}

    return dispatch


def _restart_argv() -> list[str]:
    """argv for the in-place daemon re-exec.

    Re-exec via ``python -m vexis_agent.cli run`` — byte-for-byte the
    command the systemd unit's ``ExecStart`` uses (see
    ``daemon/systemd.py``), so the restart lands on the same battle-
    tested launch path rather than reusing ``sys.argv`` (which differs
    across the console script, ``python -m``, and systemd). Verified to
    reach real daemon startup (it fails only on missing secrets, never on
    module resolution). Pure + side-effect-free so it's unit-testable
    without execv."""
    return [sys.executable, "-m", "vexis_agent.cli", "run"]


def _exec_restart() -> None:
    """Re-exec the daemon in place (same PID) for the /restart command.

    Called by ``main()`` only after ``_run()``'s graceful teardown has
    closed the control socket, dashboard, and Telegram polling, so the
    fresh image re-binds cleanly. The PID-lock fd is O_CLOEXEC (Python
    fds are non-inheritable since PEP 446), so the flock releases at the
    execv boundary and the new image re-acquires it; the PID is
    unchanged, so the lock's stale-vs-alive check (``existing ==
    getpid()``) passes rather than tripping the already-running guard.
    Sessions live on disk, so the chat resumes on the next message —
    with whatever brain CLI version / model / ``brain.kind`` is now
    configured. Under systemd the MainPID is preserved, so the unit
    stays active across the swap.

    ``os.execv`` only returns if it FAILS (it never returns on success —
    the image is replaced). On the rare failure (e.g. a vanished
    interpreter) we log loudly and re-raise so the process exits
    non-zero: under systemd that trips ``Restart=on-failure`` and the
    unit respawns within ``RestartSec``, so the daemon never ends up
    silently dead-but-not-responding. (A foreground ``vexis-agent run``
    has no supervisor, so the loud log is the user's signal.)"""
    argv = _restart_argv()
    log.info("Re-executing daemon for /restart (pid=%d): %s", os.getpid(), argv)
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        os.execv(sys.executable, argv)
    except OSError:
        log.exception(
            "Daemon re-exec FAILED (pid=%d) — process will exit non-zero so "
            "a supervisor (systemd Restart=on-failure) can respawn it.",
            os.getpid(),
        )
        raise


def main() -> None:
    """Daemon entry. Used by ``python -m vexis_agent.main``, by the
    ``vexis-agent run`` Typer command, and by direct ``python main.py``
    invocations during dev. Pre-Phase-2 callers expect side-effects on
    invocation, not a returned coroutine — keep that contract."""
    restart_requested = False
    try:
        restart_requested = asyncio.run(_run())
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
        return
    if restart_requested:
        # Never returns — replaces the process image. Must run AFTER
        # asyncio.run() has fully unwound the loop so no fd survives
        # except the (CLOEXEC) lock fd, which the execv drops for us.
        _exec_restart()


if __name__ == "__main__":
    main()

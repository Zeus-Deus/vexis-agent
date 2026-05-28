"""Process-wide registry of loaded add-ons + everything they registered.

The :class:`AddonRuntime` is the central state object for the
add-on system. It is created once at daemon startup, populated as
each add-on's ``register(ctx)`` runs, and then queried by:

  * ``main.py`` — looks up dispatch handlers + starts background tasks.
  * ``transports/telegram.py`` — iterates ``telegram_commands()``
    to wire up the bot's CommandHandlers.
  * The watcher subsystem — reads ``watcher_sources()`` to know
    which Source plugins to consult.
  * The brain's prompt builder — calls each ``system_prompt_block()``
    provider per session start.
  * The web dashboard — discovers add-on-supplied pages.
  * ``vexis addons inspect`` / ``status`` CLIs — full read-only view.

Conflict policy: every register-by-name hook (telegram_command,
dispatch_handler, watcher_source, header_block, dashboard_page,
mcp_server_default) raises :class:`AddonConflictError` on duplicate
keys. Skills and background tasks are list-additive — same skill
file shipped by two add-ons is fine, they just both copy.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .errors import AddonConflictError
from .manifest import Manifest

#: Default user_id used in single-user mode. The PluginContext carries
#: this through to every add-on so per-user state can key on it from
#: day one — future multi-user mode just stops hardcoding ``"default"``.
DEFAULT_USER_ID = "default"


# ---------- registration record types ----------------------------------------


@dataclass(frozen=True)
class TelegramCommandRegistration:
    addon_name: str
    name: str  # without leading slash
    handler: Callable[..., Awaitable[None]]
    menu_description: Optional[str]


@dataclass(frozen=True)
class DispatchHandlerRegistration:
    addon_name: str
    op_name: str
    handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class BackgroundTaskRegistration:
    addon_name: str
    name: str
    factory: Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class WatcherSourceRegistration:
    addon_name: str
    source_type: str
    source: Any  # vexis_agent.core.watcher.sources.base.Source; avoid import cycle


@dataclass(frozen=True)
class SystemPromptBlockRegistration:
    addon_name: str
    name: str
    provider: Callable[[], Optional[str]]


@dataclass(frozen=True)
class SkillRegistration:
    addon_name: str
    skill_file: Path  # absolute path inside the add-on directory
    target_subdir: str  # under workspace skills/, "." for root


@dataclass(frozen=True)
class McpServerDefaultRegistration:
    addon_name: str
    spec: Any  # McpServerSpec; defined in core.brain.base, avoid import cycle


@dataclass(frozen=True)
class DashboardPageRegistration:
    addon_name: str
    manifest: dict[str, Any]


# ---------- the runtime ------------------------------------------------------


@dataclass
class LoadedAddon:
    """One add-on that the loader successfully imported.

    Carries the parsed manifest + the resolved on-disk directory.
    Whether ``register()`` ran to completion is tracked by
    ``register_ok`` — a malformed ``register()`` body leaves the
    add-on listed (so the dashboard / CLI can show the error) but
    no registrations land in the runtime.
    """

    manifest: Manifest
    addon_dir: Path
    register_ok: bool = False
    register_error: Optional[str] = None


class AddonRuntime:
    """Per-process registry of loaded add-ons and their registrations.

    Instantiated once at daemon startup in ``main.py``. Methods come
    in two flavours:

      * ``register_*`` — called from inside ``register(ctx)`` via the
        :class:`PluginContext` facade. Mutates internal state.
        Conflicts raise :class:`AddonConflictError`.
      * ``*()`` (plural getters) — read-only iterators over what's
        been registered, for the rest of the daemon to consume.

    The runtime is NOT thread-safe by design. Add-ons register during
    single-threaded startup; consumers iterate after registration is
    frozen. If a future hot-reload feature lands, lock down the
    mutator paths then — premature locking now would obscure call
    sites.
    """

    def __init__(
        self,
        *,
        user_id: str = DEFAULT_USER_ID,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.user_id = user_id
        self._log = logger or logging.getLogger("vexis_agent.addons.runtime")

        self._loaded: dict[str, LoadedAddon] = {}

        # Name-keyed (one-per-name) registrations.
        self._telegram_commands: dict[str, TelegramCommandRegistration] = {}
        self._dispatch_handlers: dict[str, DispatchHandlerRegistration] = {}
        self._watcher_sources: dict[str, WatcherSourceRegistration] = {}
        self._header_blocks: dict[str, SystemPromptBlockRegistration] = {}
        self._mcp_defaults: dict[str, McpServerDefaultRegistration] = {}

        # List-additive registrations (multiple per name OK).
        self._background_tasks: list[BackgroundTaskRegistration] = []
        self._skills: list[SkillRegistration] = []
        self._dashboard_pages: list[DashboardPageRegistration] = []

        # Running asyncio tasks spawned by start_all_background_tasks.
        # Populated AFTER register() ran, NOT at registration time —
        # the event loop may not exist when register() fires.
        self._running_tasks: list[asyncio.Task] = []

        # Shared services keyed by name (e.g. "watcher"). Daemon
        # singletons that add-ons need to look up at call time —
        # NOT at register() time, because the daemon may build them
        # after the addon loads. PluginContext.get_service(name)
        # exposes the lookup. See ``attach_service`` / ``get_service``.
        self._services: dict[str, Any] = {}

    # ---------- loaded-addon bookkeeping --------------------------------

    def record_loaded(self, addon: LoadedAddon) -> None:
        """Loader calls this once per discovered add-on, regardless of
        whether ``register()`` succeeds — keeps the dashboard / CLI
        able to show errored add-ons."""
        self._loaded[addon.manifest.name] = addon

    def mark_register_result(
        self, name: str, *, ok: bool, error: Optional[str] = None
    ) -> None:
        """Update the loaded-addon record after ``register()`` returns."""
        existing = self._loaded.get(name)
        if existing is None:
            return
        # Dataclasses default to mutable; we mutate in place rather than
        # re-instantiating because the loader's reference points here.
        existing.register_ok = ok
        existing.register_error = error

    def loaded_addons(self) -> list[LoadedAddon]:
        return list(self._loaded.values())

    # ---------- register_* hooks (called via PluginContext) -------------

    def add_telegram_command(self, reg: TelegramCommandRegistration) -> None:
        existing = self._telegram_commands.get(reg.name)
        if existing is not None:
            raise AddonConflictError(
                f"telegram command '/{reg.name}' already registered by "
                f"add-on {existing.addon_name!r}; "
                f"add-on {reg.addon_name!r} cannot claim it too",
                addon_name=reg.addon_name,
            )
        self._telegram_commands[reg.name] = reg

    def add_dispatch_handler(self, reg: DispatchHandlerRegistration) -> None:
        existing = self._dispatch_handlers.get(reg.op_name)
        if existing is not None:
            raise AddonConflictError(
                f"dispatch op '{reg.op_name}' already registered by "
                f"add-on {existing.addon_name!r}; "
                f"add-on {reg.addon_name!r} cannot claim it too",
                addon_name=reg.addon_name,
            )
        self._dispatch_handlers[reg.op_name] = reg

    def add_watcher_source(self, reg: WatcherSourceRegistration) -> None:
        existing = self._watcher_sources.get(reg.source_type)
        if existing is not None:
            raise AddonConflictError(
                f"watcher source-type {reg.source_type!r} already registered by "
                f"add-on {existing.addon_name!r}; "
                f"add-on {reg.addon_name!r} cannot claim it too",
                addon_name=reg.addon_name,
            )
        self._watcher_sources[reg.source_type] = reg

    def add_header_block(self, reg: SystemPromptBlockRegistration) -> None:
        existing = self._header_blocks.get(reg.name)
        if existing is not None:
            raise AddonConflictError(
                f"system-prompt block {reg.name!r} already registered by "
                f"add-on {existing.addon_name!r}; "
                f"add-on {reg.addon_name!r} cannot claim it too",
                addon_name=reg.addon_name,
            )
        self._header_blocks[reg.name] = reg

    def add_mcp_default(self, reg: McpServerDefaultRegistration) -> None:
        # Spec-name-keyed conflict: an MCP server identifier is a
        # cross-add-on namespace (~/.vexis/mcp-servers.yaml is flat).
        spec_name = getattr(reg.spec, "name", None) or str(id(reg.spec))
        existing = self._mcp_defaults.get(spec_name)
        if existing is not None:
            raise AddonConflictError(
                f"mcp-server default {spec_name!r} already declared by "
                f"add-on {existing.addon_name!r}; "
                f"add-on {reg.addon_name!r} cannot claim it too",
                addon_name=reg.addon_name,
            )
        self._mcp_defaults[spec_name] = reg

    def add_background_task(self, reg: BackgroundTaskRegistration) -> None:
        # List-additive; many add-ons can run many tasks. Name is for
        # logs only, no uniqueness required.
        self._background_tasks.append(reg)

    def add_skill(self, reg: SkillRegistration) -> None:
        # List-additive; duplicate skill files from different add-ons
        # are harmless (the install step is idempotent).
        self._skills.append(reg)

    def add_dashboard_page(self, reg: DashboardPageRegistration) -> None:
        # List-additive; dashboard merges all pages into its tab list.
        self._dashboard_pages.append(reg)

    # ---------- shared services -----------------------------------------

    def attach_service(self, name: str, obj: Any) -> None:
        """Make a daemon singleton (e.g. the watcher) available to
        add-ons via ``ctx.get_service(name)``.

        Called by main.py at startup, AFTER the singleton is built
        but BEFORE the add-on's background tasks start. Re-attaching
        an existing name is allowed — the latest wins; useful for
        tests that wire a fake then swap in the real instance.
        """
        self._services[name] = obj

    def get_service(self, name: str) -> Any:
        """Lookup a service by name. ``None`` if not attached.

        Add-ons calling ``ctx.get_service`` should defend against
        ``None`` — it means the daemon never wired the service this
        version. Don't crash; degrade or log + skip.
        """
        return self._services.get(name)

    # ---------- read-only accessors (consumed by main / telegram / …) ---

    def telegram_commands(self) -> Iterable[TelegramCommandRegistration]:
        return list(self._telegram_commands.values())

    def dispatch_handlers(self) -> dict[str, DispatchHandlerRegistration]:
        # Returns a copy so callers can't mutate the underlying map.
        return dict(self._dispatch_handlers)

    def watcher_sources(self) -> Iterable[WatcherSourceRegistration]:
        return list(self._watcher_sources.values())

    def header_blocks(self) -> Iterable[SystemPromptBlockRegistration]:
        return list(self._header_blocks.values())

    def mcp_defaults(self) -> Iterable[McpServerDefaultRegistration]:
        return list(self._mcp_defaults.values())

    def background_tasks(self) -> Iterable[BackgroundTaskRegistration]:
        return list(self._background_tasks)

    def skills(self) -> Iterable[SkillRegistration]:
        return list(self._skills)

    def dashboard_pages(self) -> Iterable[DashboardPageRegistration]:
        return list(self._dashboard_pages)

    # ---------- background-task lifecycle -------------------------------

    async def start_all_background_tasks(self) -> list[asyncio.Task]:
        """Spawn every registered background task as an ``asyncio.Task``.

        Called once from ``main.py`` after the event loop is running
        (NOT during ``register()`` — the loop may not exist yet at
        registration time). Each task is wrapped in a sentinel that
        catches and logs exceptions, so a crashed task in one add-on
        can't take down the daemon. Returns the spawned tasks so the
        caller can cancel them on shutdown via
        :meth:`stop_all_background_tasks`.

        Calling more than once is a programming error and raises —
        background tasks are start-once-per-daemon-instance.
        """
        if self._running_tasks:
            raise RuntimeError(
                "start_all_background_tasks already called; "
                f"{len(self._running_tasks)} tasks are running"
            )
        for reg in self._background_tasks:
            task = asyncio.create_task(
                self._guarded_run(reg),
                name=f"addon:{reg.addon_name}:{reg.name}",
            )
            self._running_tasks.append(task)
        self._log.info(
            "addons: started %d background task(s)", len(self._running_tasks)
        )
        return list(self._running_tasks)

    async def stop_all_background_tasks(
        self, *, timeout_seconds: float = 5.0
    ) -> None:
        """Cancel every running task, wait for clean shutdown.

        Called from the daemon's shutdown path. ``timeout_seconds`` is
        an upper bound — tasks that ignore cancellation get logged
        and forced; we don't block daemon shutdown indefinitely on a
        misbehaving add-on.
        """
        if not self._running_tasks:
            return
        for task in self._running_tasks:
            task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._running_tasks, return_exceptions=True),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            self._log.warning(
                "addons: %d background task(s) did not stop within %.1fs",
                sum(1 for t in self._running_tasks if not t.done()),
                timeout_seconds,
            )
        self._running_tasks.clear()

    async def _guarded_run(
        self, reg: BackgroundTaskRegistration
    ) -> None:
        """Run one task's factory, log any exception that escapes.

        The factory itself returns a coroutine that runs until
        cancellation — we await it here. ``CancelledError`` flows
        through cleanly so daemon shutdown is silent; any other
        exception is logged with full traceback then swallowed.
        """
        try:
            await reg.factory()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._log.exception(
                "addons: background task %r from add-on %r crashed",
                reg.name, reg.addon_name,
            )

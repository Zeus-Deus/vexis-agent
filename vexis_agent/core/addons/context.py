"""``PluginContext`` — the single facade an add-on's ``register()`` sees.

The contract is: an add-on imports nothing from ``vexis_agent.core``
or any other vexis internal module — it touches only ``ctx.*`` methods
on the context passed to its ``register(ctx)`` function. This is what
keeps the add-on system refactor-safe: change any internal wiring,
add-ons stay unchanged as long as the context API is stable.

Each ``ctx.register_*`` method takes the user-facing argument shape,
wraps it in a :class:`Registration` record from
:mod:`vexis_agent.core.addons.registry`, and pushes it into the
runtime. The runtime owns conflict detection and consumption.

Per-user indirection (``ctx.user_id``) is plumbed through every
registration so future multi-user mode just changes the value the
runtime hands to ``make_context()`` — no add-on code change.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .registry import (
    AddonRuntime,
    BackgroundTaskRegistration,
    DashboardPageRegistration,
    DispatchHandlerRegistration,
    McpServerDefaultRegistration,
    SkillRegistration,
    SystemPromptBlockRegistration,
    TelegramCommandRegistration,
    WatcherSourceRegistration,
)


@dataclass(frozen=True)
class AddonConfig:
    """One add-on's slice of ``~/.vexis/config.yaml`` under ``addons.<name>.*``.

    Wraps a plain dict so callers can use ``config.get("key", default)``
    without worrying about missing keys. Defaults from the manifest's
    ``config_schema`` are merged in by the loader BEFORE the
    :class:`PluginContext` is built — so by the time the add-on sees
    ``config``, every declared field has a value.

    Frozen because add-ons must not mutate their config at runtime —
    if they need writable state, they own a file under ``~/.vexis/``
    keyed by ``ctx.user_id``.
    """

    values: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.values[key]

    def __contains__(self, key: str) -> bool:
        return key in self.values


@dataclass(frozen=True)
class PluginContext:
    """Everything an add-on's ``register(ctx)`` is allowed to touch.

    Frozen on purpose — an add-on can't reassign its own context to
    poke at things it shouldn't see. The mutable bits (the runtime)
    live behind methods so we can sanity-check / log / fail-loud on
    misuse without rewriting every add-on.
    """

    addon_name: str
    addon_dir: Path
    user_id: str
    config: AddonConfig
    log: logging.Logger
    _runtime: AddonRuntime  # private-ish; not part of the public API

    # ---------- registration hooks --------------------------------------
    # Every method here is sync — the work is just appending to a list
    # in the runtime. The actual handlers / sources / tasks the add-on
    # passes in can be async (and almost always are); the registration
    # call itself is not.

    def register_telegram_command(
        self,
        name: str,
        handler: Callable[..., Awaitable[None]],
        *,
        menu_description: Optional[str] = None,
    ) -> None:
        """Wire a ``/<name>`` slash command into the Telegram bot.

        ``handler`` receives ``(update, context)`` per python-telegram-bot
        convention and may be a coroutine. ``menu_description``, if set,
        shows in Telegram's ``/`` autocomplete menu (max ~50 chars).
        Conflict (two add-ons claiming the same name) raises
        :class:`AddonConflictError`.
        """
        self._runtime.add_telegram_command(
            TelegramCommandRegistration(
                addon_name=self.addon_name,
                name=name.lstrip("/"),
                handler=handler,
                menu_description=menu_description,
            )
        )

    def register_dispatch_handler(
        self,
        op_name: str,
        handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    ) -> None:
        """Handle one operation on the daemon's control socket.

        The control socket is the IPC vexis uses internally (e.g. for
        ``vexis-watch register`` to talk to the running daemon).
        Handler receives a dict payload and returns a dict response;
        encoding / dispatch is owned by the control-socket layer.
        """
        self._runtime.add_dispatch_handler(
            DispatchHandlerRegistration(
                addon_name=self.addon_name,
                op_name=op_name,
                handler=handler,
            )
        )

    def register_background_task(
        self,
        name: str,
        factory: Callable[[], Awaitable[None]],
    ) -> None:
        """Run a long-lived coroutine for the lifetime of the daemon.

        ``factory`` is called once, AFTER ``register()`` has returned
        and the daemon's event loop is running. It should run until
        cancelled (the daemon cancels all background tasks on
        shutdown). Crashes are logged but don't kill the daemon.
        """
        self._runtime.add_background_task(
            BackgroundTaskRegistration(
                addon_name=self.addon_name,
                name=name,
                factory=factory,
            )
        )

    def register_watcher_source(
        self,
        source_type: str,
        source: Any,  # vexis_agent.core.watcher.sources.base.Source
    ) -> None:
        """Make a watcher source-type available to ``vexis-watch`` and
        the polling loop.

        ``source`` must be an instance of
        :class:`vexis_agent.core.watcher.sources.base.Source`. The
        watcher consumes registered sources via
        :meth:`AddonRuntime.watcher_sources`; the historical
        ``register_source()`` global in
        ``core.watcher.sources.base`` stays for direct in-core
        registration (test fakes, etc.) but is no longer the
        production path.
        """
        self._runtime.add_watcher_source(
            WatcherSourceRegistration(
                addon_name=self.addon_name,
                source_type=source_type,
                source=source,
            )
        )

    def register_system_prompt_block(
        self,
        name: str,
        provider: Callable[[], Optional[str]],
    ) -> None:
        """Inject a one-line block into the brain's system prompt at
        every session start.

        ``provider`` is called per session start and returns either a
        string (injected verbatim) or ``None`` (skip — block hidden
        for this session). Use it for "active state" headers: "N
        background tasks running", "Codemux workspace foo active",
        etc. Long blocks are discouraged; the brain reads this every
        turn and it eats context.
        """
        self._runtime.add_header_block(
            SystemPromptBlockRegistration(
                addon_name=self.addon_name,
                name=name,
                provider=provider,
            )
        )

    def register_mcp_server_default(self, spec: Any) -> None:
        """Declare an MCP server this add-on expects to be configured.

        ``spec`` is a
        :class:`vexis_agent.core.brain.base.McpServerSpec`. The setup
        wizard reads these to offer "would you like to enable the
        codemux MCP?" prompts when the user installs an add-on whose
        MCP isn't yet in ``~/.vexis/mcp-servers.yaml``. The add-on
        STILL needs to declare the MCP in its manifest under
        ``requires.mcp_servers`` — this hook is for surfacing
        recommended defaults, not gating.
        """
        self._runtime.add_mcp_default(
            McpServerDefaultRegistration(
                addon_name=self.addon_name,
                spec=spec,
            )
        )

    def register_skill(
        self,
        skill_file: Path,
        *,
        target_subdir: str = ".",
    ) -> None:
        """Ship a SKILL.md (or other skill file) into each workspace's
        ``skills/`` directory at session start.

        ``skill_file`` must be an absolute path inside ``addon_dir`` —
        we don't allow add-ons to install arbitrary files from
        anywhere on disk. ``target_subdir`` is the path inside
        ``<workspace>/skills/`` to drop the file under (``.`` for the
        root, ``"codemux"`` for a ``skills/codemux/`` subfolder).
        Install is idempotent: re-running doesn't overwrite if the
        target's hash matches.
        """
        if not skill_file.is_absolute():
            raise ValueError(
                f"register_skill requires an absolute path, got {skill_file!r}"
            )
        try:
            skill_file.relative_to(self.addon_dir)
        except ValueError as e:
            raise ValueError(
                f"register_skill: {skill_file!r} is not inside addon_dir "
                f"{self.addon_dir!r}"
            ) from e
        self._runtime.add_skill(
            SkillRegistration(
                addon_name=self.addon_name,
                skill_file=skill_file,
                target_subdir=target_subdir,
            )
        )

    def register_dashboard_page(self, manifest: dict[str, Any]) -> None:
        """Add a tab to the web dashboard.

        ``manifest`` mirrors Hermes's dashboard-plugin manifest:
        ``{label, icon, tab, entry, css, api}`` where ``entry`` is
        the path to a built JS bundle and ``api`` (optional) is a
        FastAPI ``APIRouter`` factory path inside the add-on. The
        web server mounts ``api`` at ``/api/addons/<addon_name>/``
        and serves ``entry`` from the tab path.
        """
        self._runtime.add_dashboard_page(
            DashboardPageRegistration(
                addon_name=self.addon_name,
                manifest=dict(manifest),  # defensive copy
            )
        )


def make_context(
    runtime: AddonRuntime,
    *,
    addon_name: str,
    addon_dir: Path,
    config: AddonConfig,
    user_id: Optional[str] = None,
) -> PluginContext:
    """Build a :class:`PluginContext` bound to a specific add-on.

    Called by the loader once per add-on, immediately before invoking
    the add-on's ``register(ctx)``. ``user_id`` defaults to the
    runtime's own ``user_id`` (today: always ``"default"``); passing
    it explicitly is the multi-user seam — a future per-user loader
    would pass the actual user id here.
    """
    effective_user = user_id or runtime.user_id
    log = logging.getLogger(f"vexis_agent.addons.{addon_name}")
    return PluginContext(
        addon_name=addon_name,
        addon_dir=addon_dir,
        user_id=effective_user,
        config=config,
        log=log,
        _runtime=runtime,
    )

"""Browser — the flagship bundled in-process add-on.

This add-on owns the entire browser integration that used to be
hardcoded into core:

  * The ``vexis-browser`` MCP server default — the brain's PRIMARY
    browser interface. ``register_mcp_server_default`` declares a
    stdio MCP server (``vexis-browser-mcp``) that the daemon writes
    into both brains' native MCP config at startup, so the brain calls
    ``browser_*`` as native MCP tools. Because that boundary is plain
    MCP, swapping to a different browser server (Playwright MCP, a
    cloud browser, a new engine) is a config change — no daemon edit,
    no release.
  * The nine ``browser_*`` control-socket dispatch handlers
    (navigate / snapshot / click / read / type / press / back /
    scroll / screenshot) — the engine behind BOTH the MCP server and
    the back-compat ``vexis-browse`` CLI (both are thin front-ends
    that forward here over the control socket).
  * The ``web-browsing`` capability prompt block (order 13), moved
    out of ``vexis_agent/tools/browser/capability.py`` so the "Web
    browsing" system-prompt section appears ONLY when this add-on is
    enabled.
  * The ``browser.md`` skill auto-installed into every workspace.
  * The live ``BrowserTools`` instance, exposed as the ``"browser"``
    runtime service so the web dashboard can reach the session
    WITHOUT core importing this add-on (the add-on-isolation
    invariant). The dashboard degrades gracefully when the service
    is absent (browser add-on disabled).
  * Session lifecycle: a background task holds the session manager
    and tears it down on cancellation (daemon shutdown).

The browser engine itself (``SessionManager``, ``BrowserTools``,
snapshot DSL, captcha layer, profile helpers) lives under
``vexis_agent/tools/browser/`` — ``tools/`` is importable by both core
and add-ons, which is what lets the dashboard share the small value
types (errors, profile-dir helpers, captcha config) without importing
this package. This add-on is the *integration* layer; the heavy
lifting stays in ``tools/browser``.

Config: ``addons.browser.*`` is canonical, with legacy top-level
``[browser]`` honoured for back-compat. The merge happens in
``core.yaml_config._browser_section`` so the engine readers and the
dashboard payload always agree — see that helper. This add-on declares
the same knobs in its manifest ``config_schema`` so ``vexis-addons
inspect`` documents them.
"""

from __future__ import annotations

import asyncio

from vexis_agent.core.addons import PluginContext


def register(ctx: PluginContext) -> None:
    """Wire the browser add-on into vexis-agent.

    1. Instantiate the process-global ``SessionManager`` + a
       ``BrowserTools`` bound to the active workspace.
    2. Register the nine ``browser_*`` dispatch handlers — the
       add-on-dispatch-first check in ``main._build_dispatch`` routes
       control-socket ops here before any hardcoded branch (there are
       none left for the browser).
    3. Register the ``vexis-browser`` MCP server default so the brain
       gets native ``browser_*`` MCP tools (both brains).
    4. Register the ``web-browsing`` capability block (order 13).
    5. Register the ``browser.md`` skill.
    6. Attach the live ``BrowserTools`` as the ``"browser"`` runtime
       service so the dashboard can read session state.
    7. Register a lifecycle background task that owns ``manager.stop()``
       on cancellation (daemon shutdown).
    """
    from vexis_agent.addons.browser.capability import register_capability
    from vexis_agent.addons.browser.dispatch import build_browser_handlers
    from vexis_agent.core.brain.base import McpServerSpec
    from vexis_agent.tools.browser import BrowserTools, get_manager
    from vexis_agent.tools.browser.mcp_server import SERVER_NAME

    # 1. Browser engine instances. ``get_manager`` returns the
    #    process-global singleton (also what ``vexis-browse`` and the
    #    dashboard recycle button drive), so there is exactly one live
    #    Camoufox session per daemon regardless of caller.
    workspace = _resolve_workspace(ctx)
    manager = get_manager()
    browser_tools = BrowserTools(manager, workspace)

    # 2. Nine browser_* dispatch handlers. The control socket dispatches
    #    each op to the matching handler; ``vexis-browse`` is the brain's
    #    CLI in front of these.
    for op_name, handler in build_browser_handlers(browser_tools).items():
        ctx.register_dispatch_handler(op_name, handler)

    # 3. The vexis-browser MCP server default — the brain's PRIMARY
    #    browser interface. The daemon's ``merge_addon_mcp_defaults``
    #    (core/addon_mcp.py) folds this into BOTH brains' native MCP
    #    config at startup, so the brain gets native ``browser_*`` MCP
    #    tools (mcp__vexis-browser__browser_navigate, ...). The server
    #    is a stdio adapter (vexis-browser-mcp) that forwards to the
    #    same dispatch handlers registered above, over the control
    #    socket — so MCP and the vexis-browse CLI drive the one
    #    persistent session identically. A user entry of the same name
    #    in mcp-servers.yaml wins (lets them point at a different
    #    browser MCP server with no code change).
    ctx.register_mcp_server_default(
        McpServerSpec(name=SERVER_NAME, command="vexis-browser-mcp", args=[])
    )

    # 4. web-browsing capability block (order 13) — the brain-facing
    #    how-to, present only when this add-on is loaded.
    register_capability(ctx)

    # 5. browser.md skill auto-installed into every workspace.
    skill_file = ctx.addon_dir / "skills" / "browser.md"
    if skill_file.is_file():
        ctx.register_skill(skill_file)

    # 6. Expose the live BrowserTools to the dashboard via the runtime
    #    service registry. ``web_server`` fetches it with
    #    ``get_service("browser")`` and hides/disables the Browser tab
    #    routes when it's absent — so core never imports this add-on.
    ctx._runtime.attach_service("browser", browser_tools)

    # 7. Session lifecycle. The background task parks until cancelled,
    #    then stops the manager — symmetric with the daemon's old
    #    ``await browser_manager.stop()`` in main's finally block, but
    #    owned by the add-on now. ``stop_all_background_tasks`` cancels
    #    it during shutdown, draining before core subsystems vanish.
    async def _lifecycle() -> None:
        try:
            await asyncio.Event().wait()  # park until cancelled
        except asyncio.CancelledError:
            await manager.stop()
            raise

    ctx.register_background_task("browser-session-lifecycle", _lifecycle)

    ctx.log.info("browser add-on loaded (workspace=%s)", workspace)


def _resolve_workspace(ctx: PluginContext):
    """Resolve the active workspace path for ``BrowserTools``.

    Prefers the ``"workspace"`` runtime service main.py attaches before
    add-ons load; falls back to resolving from config so the add-on
    still works under test harnesses that don't attach the service.
    Returns a ``pathlib.Path``.
    """
    from pathlib import Path

    ws = ctx.get_service("workspace")
    if isinstance(ws, Path):
        return ws
    if ws:
        return Path(ws)
    from vexis_agent.core.config import load_config
    from vexis_agent.core.paths import workspace_dir

    return workspace_dir(load_config().workspace)

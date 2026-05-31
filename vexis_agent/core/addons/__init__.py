"""Add-on system for vexis-agent.

Public API surface — anything not re-exported here is internal and
may move between releases. Add-ons should import only what's listed
below; touching anything else means your add-on can break on a
vexis-agent point release.

Typical add-on entry-point pattern::

    # vexis_agent/addons/myaddon/__init__.py
    from vexis_agent.core.addons import PluginContext

    def register(ctx: PluginContext) -> None:
        ctx.register_telegram_command("hello", on_hello,
                                      menu_description="Say hi")
        ctx.register_background_task("hello-poller", run_poller)

The daemon side (``main.py``) uses :func:`discover_addons` +
:func:`load_addon` to find and wire add-ons at startup. See
``docs/addons.md`` for the full lifecycle, manifest schema, and
multi-user notes.
"""

from __future__ import annotations

from .context import AddonConfig, PluginContext, make_context
from .errors import (
    AddonConflictError,
    AddonError,
    AddonLoadError,
    AddonRequirementError,
    ManifestError,
)
from .loader import (
    DEFAULT_ENABLED_BUNDLED,
    DiscoveredAddon,
    PROJECT_ADDONS_ENV,
    bundled_addons_root,
    build_addon_config,
    discover_addons,
    load_addon,
    project_addons_root,
    user_addons_root,
)
from .manifest import (
    ALLOWED_KINDS,
    ConfigField,
    Manifest,
    McpRequirement,
    Provides,
    Requires,
    find_manifest_file,
    parse_manifest,
)
from .registry import (
    DEFAULT_USER_ID,
    AddonRuntime,
    BackgroundTaskRegistration,
    DashboardPageRegistration,
    DispatchHandlerRegistration,
    LoadedAddon,
    McpServerDefaultRegistration,
    SkillRegistration,
    SystemPromptBlockRegistration,
    TelegramCommandRegistration,
    WatcherSourceRegistration,
)

__all__ = [
    # context
    "AddonConfig",
    "PluginContext",
    "make_context",
    # errors
    "AddonError",
    "AddonConflictError",
    "AddonLoadError",
    "AddonRequirementError",
    "ManifestError",
    # loader
    "DEFAULT_ENABLED_BUNDLED",
    "DiscoveredAddon",
    "PROJECT_ADDONS_ENV",
    "bundled_addons_root",
    "build_addon_config",
    "discover_addons",
    "load_addon",
    "project_addons_root",
    "user_addons_root",
    # manifest
    "ALLOWED_KINDS",
    "ConfigField",
    "Manifest",
    "McpRequirement",
    "Provides",
    "Requires",
    "find_manifest_file",
    "parse_manifest",
    # registry
    "DEFAULT_USER_ID",
    "AddonRuntime",
    "BackgroundTaskRegistration",
    "DashboardPageRegistration",
    "DispatchHandlerRegistration",
    "LoadedAddon",
    "McpServerDefaultRegistration",
    "SkillRegistration",
    "SystemPromptBlockRegistration",
    "TelegramCommandRegistration",
    "WatcherSourceRegistration",
]

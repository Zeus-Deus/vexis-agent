"""Merge add-on-declared MCP server defaults into the brain's native
MCP config.

An add-on declares an MCP server it wants the brain to have via
``ctx.register_mcp_server_default(spec)`` (see
:meth:`vexis_agent.core.addons.context.PluginContext.register_mcp_server_default`).
Those specs accumulate in the :class:`AddonRuntime`; this module is
their one live consumer. It runs once at daemon startup, AFTER add-ons
are loaded and the brain is constructed, and folds the defaults into
whichever brain is active by going through the brain's own
``write_mcp_config`` — so BOTH brains (claude-code -> ``.mcp.json``,
opencode -> ``opencode.json``) are served by a single call. The daemon
spawns the brain fresh per turn (``claude -p`` / ``opencode run``),
re-reading the native config each spawn, so the very next turn sees
the add-on servers without a daemon restart.

Precedence, highest wins:

1. **User ``$VEXIS_HOME/mcp-servers.yaml``.** The universal source of
   truth the wizard / ``vexis-agent mcp add`` writes
   (``setup_wizard.detect_mcp_servers``). On a name collision with an
   add-on default, the user's entry wins — an add-on can only FILL a
   gap, never override an explicit user choice. We achieve this by
   keying a dict by ``spec.name`` and inserting add-on defaults FIRST,
   user yaml SECOND: a same-named user spec overwrites the add-on one.
2. **Add-on defaults** (``runtime.mcp_defaults()``). Fill gaps only.

Preservation of pre-existing native-file entries is the brain
writer's own policy, NOT this module's. opencode's
``write_mcp_config`` MERGES — every top-level key and every
user-owned (non-``vexis-``-prefixed) ``mcp`` entry survives.
claude-code's is replace-all by design, because ``.mcp.json`` is a
vexis-owned workspace file (see each writer's docstring). We
deliberately pass the full merged spec set through the SAME
``write_mcp_config`` the wizard / ``vexis-agent mcp refresh`` use so
behaviour can't drift between the startup merge and the CLI path.

Idempotent: re-running at the next startup rebuilds the same
name->spec map, and ``write_mcp_config`` does an atomic temp-file +
rename, so repeated startups never duplicate or corrupt entries.

Core stays add-on-agnostic: this module imports nothing from
``vexis_agent.addons.*``. It only touches the core
:class:`AddonRuntime` (received as an argument) and core/top-level
helpers.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vexis_agent.core.addons.registry import AddonRuntime
    from vexis_agent.core.brain.base import Brain, McpServerSpec

log = logging.getLogger(__name__)

__all__ = ["merge_addon_mcp_defaults", "resolve_merged_mcp_specs"]


def resolve_merged_mcp_specs(
    runtime: "AddonRuntime",
) -> "list[McpServerSpec]":
    """Build the merged ``McpServerSpec`` list to hand to the brain.

    Add-on defaults fill gaps; user ``mcp-servers.yaml`` wins on a
    name collision (see module docstring for the full precedence
    rule). Add-on defaults go in first, user yaml second; the dict is
    keyed by ``spec.name`` so a same-named user spec overwrites the
    add-on one — that ordering is what makes user config win.
    """
    # Imported lazily: setup_wizard pulls in heavier deps at module
    # scope and we want this module importable in lean test contexts.
    from vexis_agent.core.brain.base import McpServerSpec
    from vexis_agent.setup_wizard import detect_mcp_servers

    by_name: dict[str, "McpServerSpec"] = {}

    # 2. Add-on defaults first — lowest precedence, fill gaps only.
    #    ``mcp_defaults()`` yields ``McpServerDefaultRegistration``
    #    records; the spec lives on ``.spec`` (already an McpServerSpec).
    for reg in runtime.mcp_defaults():
        spec = reg.spec
        by_name[spec.name] = spec

    # 1. User mcp-servers.yaml — overwrites any add-on default of the
    #    same name (user config wins). ``detect_mcp_servers`` returns
    #    plain dicts; convert each with the SAME field mapping the
    #    wizard's ``write_mcp_config`` uses (setup_wizard.py) so the
    #    startup merge and the CLI/wizard path can't diverge.
    for entry in detect_mcp_servers():
        try:
            spec = McpServerSpec(
                name=entry["name"],
                command=entry.get("command"),
                args=list(entry.get("args", [])),
                env=dict(entry.get("env", {})),
                url=entry.get("url"),
                transport=str(entry.get("transport") or "http"),
                headers=dict(entry.get("headers", {})),
            )
        except Exception as exc:  # malformed user entry; skip loudly
            log.warning("skipping malformed MCP server entry: %s", exc)
            continue
        if spec.name in by_name:
            log.debug(
                "user mcp-servers.yaml entry %r overrides add-on "
                "default of the same name (user config wins)",
                spec.name,
            )
        by_name[spec.name] = spec

    return list(by_name.values())


def merge_addon_mcp_defaults(
    brain: "Brain",
    runtime: "AddonRuntime",
) -> int:
    """Fold add-on MCP defaults into ``brain``'s native MCP config.

    Returns the number of servers written (add-on defaults + user
    yaml, deduped by name). No-op-safe: if there are no add-on
    defaults at all, nothing is written — we don't want to churn the
    native file just to re-emit the user's own yaml; the wizard /
    ``vexis-agent mcp`` path already owns that. Best-effort: a write
    failure is logged, never raised, so a bad MCP config can't keep
    the daemon from starting.

    The brain was constructed with its workspace, and
    ``write_mcp_config(servers)`` writes relative to that
    ``self._workspace`` — so this takes no workspace argument.
    """
    defaults = list(runtime.mcp_defaults())
    if not defaults:
        return 0

    specs = resolve_merged_mcp_specs(runtime)
    try:
        brain.write_mcp_config(specs)
    except NotImplementedError:
        # A brain that hasn't wired native MCP yet (or the null test
        # fake) — registering the default stays harmless.
        log.debug(
            "brain %r has no write_mcp_config; %d add-on MCP "
            "default(s) not applied",
            getattr(brain, "kind", brain),
            len(defaults),
        )
        return 0
    except Exception:  # pragma: no cover - defensive
        log.exception(
            "failed to merge %d add-on MCP default(s) into the brain "
            "config; the brain will start without them",
            len(defaults),
        )
        return 0

    log.info(
        "addons: merged %d add-on MCP server default(s) into the "
        "brain config (%d total servers after user-config merge)",
        len(defaults), len(specs),
    )
    return len(specs)

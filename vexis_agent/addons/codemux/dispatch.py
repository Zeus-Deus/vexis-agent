"""Control-socket dispatch handlers for codemux-specific watch ops.

The watcher control-socket protocol has two flavours of ops:

* **Generic** — register an agent with a known source_type, list,
  unregister, mute, tail. These stay in core (main._build_dispatch)
  because they're transport-agnostic; ``vexis-watch register
  --source <type> --identifier <id>`` works for any source.

* **Codemux-specific** — the ``--workspace <id>`` registration
  flow that runs an MCP lookup to resolve workspace_id →
  session_id before calling the generic register_agent. This
  module owns that resolver path.

The codemux add-on registers a single ``watch_register`` dispatch
handler that delegates to the generic registration when called
with ``source=codemux`` + ``identifier=<session-id>``, and runs
the workspace_id resolver when called with ``source=codemux`` +
``workspace_id=<id>``. Other source types (future PTY, tmux) get
their own add-ons with their own handlers.
"""

from __future__ import annotations

from typing import Any


def build_watch_register_handler(ctx: Any):
    """Return the ``watch_register`` dispatch handler bound to this
    add-on's context.

    Payload shape (subset; full schema in vexis-watch CLI):
        source: "codemux"
        name: str
        agent_kind: str
        chat_id: int
        idle_after_seconds: int
        goal_hint: str | None
        workspace_id: str | None   # if set, run the resolver
        identifier: str | None     # session id; required when
                                   # workspace_id is absent
    """

    async def _watch_register(args: dict[str, Any]) -> dict[str, Any]:
        watcher = ctx.get_service("watcher")
        if watcher is None:
            return {
                "ok": False,
                "error": "watcher subsystem not wired",
                "kind": "WatcherUnavailable",
            }

        source = args.get("source", "codemux")
        if source != "codemux":
            # Generic non-codemux registration; the core dispatch
            # path handles it. We just say "not ours" so the
            # caller falls through.
            return {
                "ok": False,
                "error": f"source {source!r} not handled by codemux add-on",
                "kind": "NotHandled",
            }

        try:
            name = str(args["name"])
            agent_kind = str(args["agent_kind"])
            chat_id = int(args["chat_id"])
        except (KeyError, TypeError, ValueError) as exc:
            return {
                "ok": False,
                "error": f"bad watch_register args: {exc}",
                "kind": "BadRequest",
            }
        idle_after = int(args.get("idle_after_seconds", 30))
        goal_hint = args.get("goal_hint")
        if goal_hint is not None:
            goal_hint = str(goal_hint)
        workspace_id = args.get("workspace_id")
        identifier = args.get("identifier")

        if workspace_id:
            # Workspace-id resolution path: ask the live MCP for the
            # active terminal pane's session id.
            from vexis_agent.addons.codemux.mcp_client import CodemuxMcpClient
            from vexis_agent.addons.codemux.source import (
                resolve_workspace_to_session,
            )
            client = CodemuxMcpClient()
            try:
                resolution = await resolve_workspace_to_session(
                    client, str(workspace_id),
                )
            except Exception as exc:
                msg = str(exc)
                kind = (
                    "WorkspaceNotActive"
                    if "is not the active codemux workspace" in msg
                    else "ResolveFailed"
                )
                return {"ok": False, "error": msg, "kind": kind}
            finally:
                await client.close()
            agent = await watcher.register_agent(
                name=name,
                source_type="codemux",
                identifier=resolution.session_id,
                agent_kind=agent_kind,
                chat_id=chat_id,
                idle_after_seconds=idle_after,
                goal_hint=goal_hint,
                repo_path=resolution.repo_path,
                workspace_id=resolution.workspace_id,
            )
        elif identifier:
            agent = await watcher.register_agent(
                name=name,
                source_type="codemux",
                identifier=str(identifier),
                agent_kind=agent_kind,
                chat_id=chat_id,
                idle_after_seconds=idle_after,
                goal_hint=goal_hint,
            )
        else:
            return {
                "ok": False,
                "error": "watch_register requires workspace_id OR identifier",
                "kind": "BadRequest",
            }
        return {"ok": True, "agent": _agent_to_dict(agent)}

    return _watch_register


def _agent_to_dict(agent: Any) -> dict[str, Any]:
    """Serialise a WatchedAgent for the control-socket reply."""
    return {
        "name": agent.name,
        "source_type": agent.source_type,
        "identifier": agent.identifier,
        "agent_kind": agent.agent_kind,
        "chat_id": agent.chat_id,
        "status": agent.status,
        "muted": agent.muted,
        "workspace_id": agent.workspace_id,
        "repo_path": agent.repo_path,
        "registered_at": agent.registered_at,
    }

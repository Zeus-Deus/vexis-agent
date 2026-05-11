"""Kanban REST + WebSocket endpoints for the dashboard.

Lives in its own module so ``web_server.py`` (already 3700+ lines)
doesn't bloat. The dashboard's :meth:`_build_app` calls
:func:`register_kanban_routes` once during construction; auth flows
through the same Bearer-token dependency the rest of ``/api/v1/*``
uses, plus a ``?token=`` query-param fallback for the WebSocket
handshake (WebSocket can't carry the ``Authorization`` header in
standard browser APIs).

Route inventory
===============

REST (all under ``/api/v1/kanban``):

  GET    /board              → list_board() — summary + tasks
  GET    /board?lane=X       → lane-filtered
  GET    /lanes              → list_lanes_info()
  GET    /tasks/{id}         → show_task()
  GET    /tasks/{id}/events  → list_events for a single task
  POST   /tasks              → create_task()
  POST   /tasks/{id}/status  → update_task(status=...)  — drag-drop
  POST   /tasks/{id}/complete
  POST   /tasks/{id}/block
  POST   /tasks/{id}/unblock
  POST   /tasks/{id}/archive
  POST   /tasks/{id}/assign  → assign_lane()
  POST   /tasks/{id}/comment → comment_on_task()
  POST   /links              → add_link()
  POST   /links/delete       → remove_link()

WebSocket:

  WS     /events?since=<cursor>&token=<bearer>
         → streams task_events rows past ``since`` with ~1s poll cadence

Domain errors (TaskNotFoundError, LaneNotFoundError, etc) come back
as HTTP 4xx with a JSON body matching the action result dict's shape.
The WS path doesn't surface errors via HTTP — protocol-level close
with a clear code is the right shape, but for v1 we just close
cleanly on store-disappeared and let the client reconnect.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import (
    Body,
    Depends,
    FastAPI,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from vexis_agent.core.kanban.db import KanbanStore
from vexis_agent.tools.kanban import api as kanban_api

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Error mapping
# ──────────────────────────────────────────────────────────────────


# Map action-result ``kind`` strings to HTTP status codes. Keep narrow
# — anything unmapped falls through to 400 so the user sees the
# action's error message intact instead of a generic 500.
_KIND_TO_STATUS: dict[str, int] = {
    "TaskNotFoundError": 404,
    "LaneNotFoundError": 400,
    "InvalidStatusError": 400,
    "InvalidStateError": 409,
    "ClaimContentionError": 409,
    "ClaimLost": 409,
    "KanbanError": 400,
}


def _unwrap_or_raise(result: dict[str, Any]) -> dict[str, Any]:
    """Convert a tool-result dict into the data payload or an HTTPException.

    Success → return ``result["data"]`` (or ``{}`` if absent).
    Failure → raise HTTPException with the right status code.
    """
    if result.get("ok"):
        return result.get("data") or {}
    kind = result.get("kind", "Error")
    code = _KIND_TO_STATUS.get(kind, 400)
    raise HTTPException(
        status_code=code,
        detail={"error": result.get("error", "unknown"), "kind": kind},
    )


# ──────────────────────────────────────────────────────────────────
# Route registration
# ──────────────────────────────────────────────────────────────────


StoreProvider = Callable[[], KanbanStore | None]
TokenProvider = Callable[[], str]


def register_kanban_routes(
    app: FastAPI,
    *,
    store_provider: StoreProvider,
    require_auth_dep: Callable[..., Awaitable[None]],
    token_provider: TokenProvider,
) -> None:
    """Wire all kanban REST + WS endpoints onto ``app``.

    ``store_provider`` returns the daemon's :class:`KanbanStore` or
    ``None`` when kanban is disabled. ``require_auth_dep`` is the
    same bearer-token dependency the rest of the dashboard uses.
    ``token_provider`` returns the current dashboard token — used by
    the WS handshake (query param) since WebSocket clients can't set
    Authorization headers from the browser.
    """

    def _store() -> KanbanStore:
        store = store_provider()
        if store is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "kanban is not enabled on this daemon",
                    "kind": "KanbanDisabled",
                },
            )
        return store

    # ── reads ────────────────────────────────────────────────────

    @app.get(
        "/api/v1/kanban/board",
        dependencies=[Depends(require_auth_dep)],
    )
    async def get_board(
        lane: str | None = Query(None),
        status_q: str | None = Query(None, alias="status"),
        archived: bool = Query(False),
        limit: int | None = Query(None, ge=1, le=1000),
    ) -> dict:
        store = _store()
        result = await asyncio.to_thread(
            kanban_api.list_board,
            store, status=status_q, lane=lane,
            include_archived=archived, limit=limit,
        )
        return _unwrap_or_raise(result)

    @app.get(
        "/api/v1/kanban/lanes",
        dependencies=[Depends(require_auth_dep)],
    )
    async def get_lanes() -> dict:
        store = _store()
        result = await asyncio.to_thread(
            kanban_api.list_lanes_info, store,
        )
        return _unwrap_or_raise(result)

    @app.get(
        "/api/v1/kanban/tasks/{task_id}",
        dependencies=[Depends(require_auth_dep)],
    )
    async def get_task(task_id: str) -> dict:
        store = _store()
        result = await asyncio.to_thread(
            kanban_api.show_task, store, task_id,
        )
        return _unwrap_or_raise(result)

    @app.get(
        "/api/v1/kanban/tasks/{task_id}/events",
        dependencies=[Depends(require_auth_dep)],
    )
    async def get_task_events(
        task_id: str,
        limit: int = Query(100, ge=1, le=500),
    ) -> dict:
        store = _store()
        # Use the per-task list_events helper from KanbanStore directly
        # since the api layer's list_events is cursor-paginated across
        # all tasks.
        if await asyncio.to_thread(lambda: store.get_task(task_id)) is None:
            raise HTTPException(status_code=404, detail={
                "error": f"task not found: {task_id}",
                "kind": "TaskNotFoundError",
            })
        events = await asyncio.to_thread(
            lambda: store.list_events(task_id, limit=limit),
        )
        return {"events": [e.to_dict() for e in events]}

    # ── creates / mutations ──────────────────────────────────────

    @app.post(
        "/api/v1/kanban/tasks",
        dependencies=[Depends(require_auth_dep)],
    )
    async def post_task(body: dict = Body(...)) -> dict:
        store = _store()
        try:
            result = await asyncio.to_thread(
                kanban_api.create_task,
                store,
                title=body.get("title", ""),
                body=body.get("body"),
                lane=body.get("lane"),
                status=body.get("status"),
                priority=int(body.get("priority", 0) or 0),
                created_by=body.get("created_by") or "user",
                workspace_path=body.get("workspace_path"),
                max_runtime_seconds=body.get("max_runtime_seconds"),
                skills=body.get("skills"),
                max_retries=body.get("max_retries"),
                parents=body.get("parents"),
            )
        except kanban_api.ToolError as exc:
            raise HTTPException(status_code=400, detail={
                "error": str(exc), "kind": "ToolError",
            })
        return _unwrap_or_raise(result)

    @app.post(
        "/api/v1/kanban/tasks/{task_id}/status",
        dependencies=[Depends(require_auth_dep)],
    )
    async def post_task_status(
        task_id: str, body: dict = Body(...),
    ) -> dict:
        """Generic status flip — used by the dashboard's drag-drop to
        move cards across columns. Tighter than the full action layer
        because drag-drop is a low-risk UX gesture (the dispatcher
        will catch any state-transition surprises)."""
        store = _store()
        new_status = body.get("status")
        if not isinstance(new_status, str) or not new_status:
            raise HTTPException(status_code=400, detail={
                "error": "status is required", "kind": "ToolError",
            })
        # Use update_task indirectly via the store, since the api layer
        # doesn't expose a bare-status mutator (intentional — too easy
        # to misuse from the worker side; drag-drop UX is the only
        # legitimate caller).
        from vexis_agent.core.kanban.constants import VALID_STATUSES
        if new_status not in VALID_STATUSES:
            raise HTTPException(status_code=400, detail={
                "error": f"invalid status {new_status!r}",
                "kind": "InvalidStatusError",
            })
        if await asyncio.to_thread(lambda: store.get_task(task_id)) is None:
            raise HTTPException(status_code=404, detail={
                "error": f"task not found: {task_id}",
                "kind": "TaskNotFoundError",
            })
        await asyncio.to_thread(
            lambda: store.update_task(task_id, status=new_status),
        )
        task = await asyncio.to_thread(lambda: store.require_task(task_id))
        return task.to_dict()

    @app.post(
        "/api/v1/kanban/tasks/{task_id}/complete",
        dependencies=[Depends(require_auth_dep)],
    )
    async def post_complete(task_id: str, body: dict = Body(default={})) -> dict:
        store = _store()
        result = await asyncio.to_thread(
            kanban_api.complete_task, store, task_id,
            summary=body.get("summary"),
            author=body.get("author") or "user",
        )
        return _unwrap_or_raise(result)

    @app.post(
        "/api/v1/kanban/tasks/{task_id}/block",
        dependencies=[Depends(require_auth_dep)],
    )
    async def post_block(task_id: str, body: dict = Body(...)) -> dict:
        store = _store()
        reason = body.get("reason", "")
        if not isinstance(reason, str) or not reason.strip():
            raise HTTPException(status_code=400, detail={
                "error": "reason is required", "kind": "ToolError",
            })
        result = await asyncio.to_thread(
            kanban_api.block_task, store, task_id,
            reason=reason, author=body.get("author") or "user",
        )
        return _unwrap_or_raise(result)

    @app.post(
        "/api/v1/kanban/tasks/{task_id}/unblock",
        dependencies=[Depends(require_auth_dep)],
    )
    async def post_unblock(task_id: str, body: dict = Body(default={})) -> dict:
        store = _store()
        result = await asyncio.to_thread(
            kanban_api.unblock_task, store, task_id,
            new_status=body.get("new_status") or "ready",
            author=body.get("author") or "user",
        )
        return _unwrap_or_raise(result)

    @app.post(
        "/api/v1/kanban/tasks/{task_id}/archive",
        dependencies=[Depends(require_auth_dep)],
    )
    async def post_archive(task_id: str) -> dict:
        store = _store()
        result = await asyncio.to_thread(
            kanban_api.archive_task, store, task_id,
        )
        return _unwrap_or_raise(result)

    @app.post(
        "/api/v1/kanban/tasks/{task_id}/assign",
        dependencies=[Depends(require_auth_dep)],
    )
    async def post_assign(task_id: str, body: dict = Body(...)) -> dict:
        store = _store()
        result = await asyncio.to_thread(
            kanban_api.assign_lane, store, task_id, lane=body.get("lane"),
        )
        return _unwrap_or_raise(result)

    @app.post(
        "/api/v1/kanban/tasks/{task_id}/comment",
        dependencies=[Depends(require_auth_dep)],
    )
    async def post_comment(task_id: str, body: dict = Body(...)) -> dict:
        store = _store()
        comment_body = body.get("body", "")
        if not isinstance(comment_body, str) or not comment_body.strip():
            raise HTTPException(status_code=400, detail={
                "error": "body is required", "kind": "ToolError",
            })
        result = await asyncio.to_thread(
            kanban_api.comment_on_task, store, task_id,
            body=comment_body, author=body.get("author") or "user",
        )
        return _unwrap_or_raise(result)

    @app.post(
        "/api/v1/kanban/links",
        dependencies=[Depends(require_auth_dep)],
    )
    async def post_link(body: dict = Body(...)) -> dict:
        store = _store()
        result = await asyncio.to_thread(
            kanban_api.add_link, store,
            parent_id=body.get("parent_id", ""),
            child_id=body.get("child_id", ""),
        )
        return _unwrap_or_raise(result)

    @app.post(
        "/api/v1/kanban/links/delete",
        dependencies=[Depends(require_auth_dep)],
    )
    async def post_unlink(body: dict = Body(...)) -> dict:
        store = _store()
        result = await asyncio.to_thread(
            kanban_api.remove_link, store,
            parent_id=body.get("parent_id", ""),
            child_id=body.get("child_id", ""),
        )
        return _unwrap_or_raise(result)

    # ── WebSocket — live event stream ────────────────────────────

    @app.websocket("/api/v1/kanban/events")
    async def ws_events(
        websocket: WebSocket,
        since: int = Query(0, ge=0),
        token: str = Query(""),
    ) -> None:
        """Stream task_events past ``since``. Polls the DB every 1s.

        Auth via ``?token=`` query param — WS clients in the browser
        can't set Authorization headers, so we accept the token here.
        The token is compared with ``secrets.compare_digest`` against
        the dashboard's current token.
        """
        expected = token_provider()
        if not token or not secrets.compare_digest(token, expected):
            await websocket.close(code=4401, reason="invalid token")
            return
        store = store_provider()
        if store is None:
            await websocket.close(code=4503, reason="kanban disabled")
            return
        await websocket.accept()
        cursor = since
        try:
            while True:
                events = await asyncio.to_thread(
                    lambda: store.events_since(cursor, limit=100),
                )
                if events:
                    cursor = events[-1].id
                    await websocket.send_json({
                        "events": [e.to_dict() for e in events],
                        "cursor": cursor,
                    })
                await asyncio.sleep(1.0)
        except WebSocketDisconnect:
            return
        except Exception:
            log.exception("kanban ws_events: stream raised")
            try:
                await websocket.close(code=1011)
            except Exception:
                pass


__all__ = ["register_kanban_routes"]

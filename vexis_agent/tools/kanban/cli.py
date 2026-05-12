"""``vexis-kanban`` CLI — shell-callable wrapper around the action layer.

This is the surface a kanban-worker process (spawned subprocess) uses
to declare outcomes back to the daemon. The worker has shell access
via the lane's tool allowlist; it runs ``vexis-kanban complete <id>``
or similar, which opens the kanban DB and applies the action.

Humans also use it for ad-hoc inspection (``vexis-kanban list``,
``vexis-kanban show <id>``) when they don't want to open the dashboard.

Why a CLI and not a proper MCP server for v1
============================================

A real MCP stdio server adds ~200 LoC of JSON-RPC plumbing and a
runtime dependency on the ``mcp`` Python SDK; the worker can call
shell tools just as effectively if it has shell access (which the
default lanes grant). The CLI surface IS the contract — a future MCP
server wrapper would call the same action functions and serialise
their dicts.

When we DO add the MCP server, the CLI stays — it's the human-facing
inspection tool. The MCP server is the brain-facing one. Both wrap
``vexis_agent.tools.kanban.api``.

JSON output mode
================

Every subcommand accepts ``--json`` to emit the raw action result
dict for programmatic consumption (worker subprocess piping the
output to something, scripted automation). Without ``--json`` the
output is short human-readable text. The exit code is 0 on
``ok=True`` and 1 on ``ok=False``; callers can detect failure
without parsing output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer

from vexis_agent.tools.kanban import api

app = typer.Typer(
    add_completion=False,
    help="Kanban work-queue CLI (vexis-agent).",
    no_args_is_help=True,
)


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _emit(result: dict, *, as_json: bool, ok_template: Optional[str] = None) -> None:
    """Render the result and exit with the right code.

    ``as_json=True`` emits raw JSON regardless of ok/err.
    ``as_json=False`` emits a short human line. If the result is
    ``ok=True`` and ``ok_template`` is set, render that template
    against the result's ``data``; otherwise emit a default summary.
    """
    if as_json:
        json.dump(result, sys.stdout, sort_keys=True)
        sys.stdout.write("\n")
        sys.exit(0 if result.get("ok") else 1)
    if not result.get("ok"):
        sys.stderr.write(f"error: {result.get('error', 'unknown')}\n")
        sys.exit(1)
    data = result.get("data") or {}
    if ok_template:
        try:
            sys.stdout.write(ok_template.format(**data) + "\n")
            return
        except (KeyError, IndexError):
            pass
    # Default success line.
    if isinstance(data, dict) and "id" in data and "title" in data:
        sys.stdout.write(f"{data['id']}: {data['title']} [{data.get('status')}]\n")
    else:
        sys.stdout.write("ok\n")


def _store():
    return api.open_default_store()


def _close(store) -> None:
    try:
        store.close()
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────
# Subcommands
# ──────────────────────────────────────────────────────────────────


@app.command()
def create(
    title: str = typer.Argument(..., help="Task title."),
    body: Optional[str] = typer.Option(None, "--body", "-b", help="Task body."),
    lane: Optional[str] = typer.Option(None, "--lane", "-l", help="Lane name."),
    priority: int = typer.Option(0, "--priority", "-p"),
    parent: Optional[list[str]] = typer.Option(
        None, "--parent", help="Parent task id (repeatable).",
    ),
    status: Optional[str] = typer.Option(
        None, "--status",
        help="Initial status (default: triage; forced to todo if parents given).",
    ),
    workspace_path: Optional[str] = typer.Option(None, "--workspace-path"),
    max_runtime_seconds: Optional[int] = typer.Option(None, "--max-runtime"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Create a new task."""
    store = _store()
    try:
        result = api.create_task(
            store,
            title=title,
            body=body,
            lane=lane,
            status=status,
            priority=priority,
            workspace_path=workspace_path,
            max_runtime_seconds=max_runtime_seconds,
            parents=list(parent) if parent else None,
        )
    finally:
        _close(store)
    _emit(result, as_json=as_json,
          ok_template="created: {id}: {title} [{status}]")


@app.command()
def show(
    task_id: str = typer.Argument(..., help="Task id."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show full task detail (body, comments, runs, recent events)."""
    store = _store()
    try:
        result = api.show_task(store, task_id)
    finally:
        _close(store)
    if as_json:
        _emit(result, as_json=True)
        return
    if not result.get("ok"):
        sys.stderr.write(f"error: {result.get('error')}\n")
        sys.exit(1)
    d = result["data"]
    t = d["task"]
    print(f"#{t['id']}  {t['title']}")
    print(f"  status: {t['status']}    lane: {t.get('lane') or '(none)'}    priority: {t.get('priority', 0)}")
    if t.get("body"):
        print(f"  body: {t['body']}")
    if d.get("parents"):
        print(f"  parents: {', '.join(d['parents'])}")
    if d.get("children"):
        print(f"  children: {', '.join(d['children'])}")
    if d.get("comments"):
        print("  comments:")
        for c in d["comments"]:
            print(f"    [{c['author']}] {c['body']}")
    if d.get("runs"):
        print(f"  runs: {len(d['runs'])} (latest outcome: {d['runs'][0].get('outcome') or '(in progress)'})")


@app.command(name="list")
def list_cmd(
    status: Optional[str] = typer.Option(None, "--status", "-s"),
    lane: Optional[str] = typer.Option(None, "--lane", "-l"),
    archived: bool = typer.Option(False, "--archived"),
    limit: Optional[int] = typer.Option(None, "--limit"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List tasks (default: active board)."""
    store = _store()
    try:
        result = api.list_board(
            store, status=status, lane=lane,
            include_archived=archived, limit=limit,
        )
    finally:
        _close(store)
    if as_json:
        _emit(result, as_json=True)
        return
    if not result.get("ok"):
        sys.stderr.write(f"error: {result.get('error')}\n")
        sys.exit(1)
    summary = result["data"]["summary"]
    parts = [f"{k}={v}" for k, v in sorted(summary.items()) if v]
    print("counts: " + ("  ".join(parts) or "(empty board)"))
    for t in result["data"]["tasks"]:
        lane_label = t.get("lane") or "-"
        print(f"  {t['id']}  [{t['status']:11s}]  ({lane_label:14s})  {t['title']}")


@app.command()
def complete(
    task_id: str = typer.Argument(...),
    summary: Optional[str] = typer.Option(None, "--summary", "-m"),
    author: str = typer.Option("agent", "--author"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Mark a task as done. Used by spawned workers."""
    store = _store()
    try:
        result = api.complete_task(
            store, task_id, summary=summary, author=author,
        )
    finally:
        _close(store)
    _emit(result, as_json=as_json,
          ok_template="completed: {id}: {title}")


@app.command()
def block(
    task_id: str = typer.Argument(...),
    reason: str = typer.Argument(..., help="Why is it blocked?"),
    author: str = typer.Option("agent", "--author"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Mark a task as blocked. Reason is required (renders in
    dashboard badge + Telegram notification)."""
    store = _store()
    try:
        result = api.block_task(
            store, task_id, reason=reason, author=author,
        )
    finally:
        _close(store)
    _emit(result, as_json=as_json,
          ok_template="blocked: {id}: {title}")


@app.command()
def unblock(
    task_id: str = typer.Argument(...),
    new_status: str = typer.Option("ready", "--status",
                                   help="Status to flip to (default: ready)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Flip a blocked task back to ready (or any valid status)."""
    store = _store()
    try:
        result = api.unblock_task(store, task_id, new_status=new_status)
    finally:
        _close(store)
    _emit(result, as_json=as_json,
          ok_template="unblocked: {id}: {title} [{status}]")


@app.command()
def comment(
    task_id: str = typer.Argument(...),
    body: str = typer.Argument(..., help="Comment text."),
    author: str = typer.Option("user", "--author"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Add a comment."""
    store = _store()
    try:
        result = api.comment_on_task(
            store, task_id, body=body, author=author,
        )
    finally:
        _close(store)
    _emit(result, as_json=as_json,
          ok_template="comment added")


@app.command()
def heartbeat(
    task_id: str = typer.Argument(...),
    claim_lock: str = typer.Option(..., "--claim-lock"),
    ttl_seconds: int = typer.Option(150, "--ttl"),
    progress: Optional[str] = typer.Option(None, "--progress"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Worker heartbeat. Bumps the claim TTL."""
    store = _store()
    try:
        result = api.heartbeat_task(
            store, task_id, claim_lock=claim_lock,
            ttl_seconds=ttl_seconds, progress=progress,
        )
    finally:
        _close(store)
    _emit(result, as_json=as_json,
          ok_template="heartbeat ok")


@app.command()
def archive(
    task_id: str = typer.Argument(...),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Soft-delete a task (status → archived)."""
    store = _store()
    try:
        result = api.archive_task(store, task_id)
    finally:
        _close(store)
    _emit(result, as_json=as_json,
          ok_template="archived: {task_id}")


@app.command()
def assign(
    task_id: str = typer.Argument(...),
    lane: Optional[str] = typer.Argument(None, help="Lane name (empty to clear)."),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Assign a task to a lane (or clear with empty value)."""
    store = _store()
    try:
        result = api.assign_lane(store, task_id, lane=lane)
    finally:
        _close(store)
    _emit(result, as_json=as_json,
          ok_template="assigned: {id} → {lane}")


@app.command()
def link(
    parent_id: str = typer.Argument(...),
    child_id: str = typer.Argument(...),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Add a parent → child dependency."""
    store = _store()
    try:
        result = api.add_link(store, parent_id=parent_id, child_id=child_id)
    finally:
        _close(store)
    _emit(result, as_json=as_json,
          ok_template="linked: {parent_id} → {child_id}")


@app.command()
def unlink(
    parent_id: str = typer.Argument(...),
    child_id: str = typer.Argument(...),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Remove a parent → child dependency."""
    store = _store()
    try:
        result = api.remove_link(store, parent_id=parent_id, child_id=child_id)
    finally:
        _close(store)
    _emit(result, as_json=as_json,
          ok_template="unlinked: {parent_id} -/-> {child_id}")


@app.command()
def lanes(
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """List available lanes (defaults + user overrides)."""
    store = _store()
    try:
        result = api.list_lanes_info(store)
    finally:
        _close(store)
    if as_json:
        _emit(result, as_json=True)
        return
    if not result.get("ok"):
        sys.stderr.write(f"error: {result.get('error')}\n")
        sys.exit(1)
    for lane in result["data"]["lanes"]:
        tier = lane.get("tier") or "default"
        desc = (lane.get("description") or "")[:60]
        print(f"  {lane['name']:18s} tier={tier:8s}  {desc}")


def main() -> None:
    """Console-script entry."""
    app()


if __name__ == "__main__":
    main()

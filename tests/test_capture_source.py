"""Unit tests for tools/capture_source.py — pure logic, no I/O.

Covers:
* parse_source_modifier across the documented input forms (None, "",
  "host", "sandbox", "sandbox:foo", "sandbox foo", weird whitespace).
* resolve_source rules: explicit host, explicit sandbox-with-id,
  explicit bare sandbox (picks latest active), auto with task-context,
  auto with no task-context, and the four error cases.
* caption_label + caption_hint behaviour (hint only fires for auto-
  routed host while host is locked).

Convention: sync `def test_*` per the test_livestream.py convention;
this module has no async surface so no asyncio.run wrapper is needed.
"""

from __future__ import annotations

import pytest

from vexis_agent.tools.capture_source import (
    CaptureSource,
    CaptureSourceError,
    RouterContext,
    caption_hint,
    caption_label,
    parse_source_modifier,
    resolve_source,
)


# ---------- parse_source_modifier ----------


def test_parse_none_is_auto():
    assert parse_source_modifier(None) == (None, None)


def test_parse_empty_string_is_auto():
    assert parse_source_modifier("") == (None, None)
    assert parse_source_modifier("   ") == (None, None)


def test_parse_host():
    assert parse_source_modifier("host") == ("host", None)
    assert parse_source_modifier("HOST") == ("host", None)
    assert parse_source_modifier("  host  ") == ("host", None)


def test_parse_host_rejects_task_id():
    with pytest.raises(CaptureSourceError, match="takes no task-id"):
        parse_source_modifier("host foo")


def test_parse_bare_sandbox():
    assert parse_source_modifier("sandbox") == ("sandbox", None)
    assert parse_source_modifier("SANDBOX") == ("sandbox", None)


def test_parse_sandbox_colon_form():
    assert parse_source_modifier("sandbox:my-task") == ("sandbox", "my-task")


def test_parse_sandbox_space_form():
    assert parse_source_modifier("sandbox my-task") == ("sandbox", "my-task")
    assert parse_source_modifier("sandbox   my-task") == ("sandbox", "my-task")


def test_parse_sandbox_preserves_task_id_case():
    # docker container names are case-sensitive; the parser must not
    # lowercase the task-id.
    assert parse_source_modifier("sandbox:MyTask") == ("sandbox", "MyTask")


def test_parse_unknown_kind_raises():
    with pytest.raises(CaptureSourceError, match="Unknown source"):
        parse_source_modifier("vm")
    with pytest.raises(CaptureSourceError, match="Unknown source"):
        parse_source_modifier("garbage")


# ---------- CaptureSource invariants ----------


def test_sandbox_kind_requires_task_id():
    with pytest.raises(CaptureSourceError, match="requires a task_id"):
        CaptureSource(kind="sandbox")


def test_host_kind_rejects_task_id():
    with pytest.raises(CaptureSourceError, match="must not carry a task_id"):
        CaptureSource(kind="host", task_id="foo")


# ---------- resolve_source: explicit ----------


def test_resolve_explicit_host():
    ctx = RouterContext(requested="host", current_task_id="task-1")
    src = resolve_source(ctx)
    assert src.kind == "host"
    assert src.task_id is None
    assert src.reason == "user-explicit"


def test_resolve_explicit_sandbox_with_id():
    ctx = RouterContext(
        requested="sandbox:foo",
        active_sandbox_task_ids=("foo", "bar"),
    )
    src = resolve_source(ctx)
    assert src.kind == "sandbox"
    assert src.task_id == "foo"
    assert src.reason == "user-explicit"


def test_resolve_explicit_sandbox_with_unknown_id():
    ctx = RouterContext(
        requested="sandbox:ghost",
        active_sandbox_task_ids=("foo",),
    )
    with pytest.raises(CaptureSourceError, match="No active sandbox with task-id"):
        resolve_source(ctx)


def test_resolve_bare_sandbox_picks_first_active():
    ctx = RouterContext(
        requested="sandbox",
        active_sandbox_task_ids=("recent", "older"),
    )
    src = resolve_source(ctx)
    assert src.kind == "sandbox"
    # The router treats the tuple as most-recent first per docstring.
    assert src.task_id == "recent"


def test_resolve_bare_sandbox_with_none_active_raises():
    ctx = RouterContext(requested="sandbox", active_sandbox_task_ids=())
    with pytest.raises(CaptureSourceError, match="No active sandboxes"):
        resolve_source(ctx)


# ---------- resolve_source: auto ----------


def test_resolve_auto_with_task_context_in_active():
    ctx = RouterContext(
        requested=None,
        current_task_id="task-1",
        active_sandbox_task_ids=("task-1", "other"),
    )
    src = resolve_source(ctx)
    assert src.kind == "sandbox"
    assert src.task_id == "task-1"
    assert src.reason == "task-context"


def test_resolve_auto_with_task_context_not_active_falls_back_to_host():
    ctx = RouterContext(
        requested=None,
        current_task_id="task-1",
        active_sandbox_task_ids=("other",),
    )
    src = resolve_source(ctx)
    assert src.kind == "host"
    assert src.reason == "default-host"


def test_resolve_auto_no_task_context_uses_host():
    ctx = RouterContext(
        requested=None,
        current_task_id=None,
        active_sandbox_task_ids=("foo",),
    )
    src = resolve_source(ctx)
    assert src.kind == "host"
    assert src.reason == "default-host"


# ---------- caption_label ----------


def test_caption_label_host():
    src = CaptureSource(kind="host")
    assert caption_label(src) == "Host"


def test_caption_label_sandbox():
    src = CaptureSource(kind="sandbox", task_id="my-task")
    assert caption_label(src) == "Sandbox my-task"


# ---------- caption_hint ----------


def test_caption_hint_only_for_auto_routed_host_when_locked():
    src = CaptureSource(kind="host", reason="default-host")
    ctx = RouterContext(host_locked=True, active_sandbox_task_ids=("foo",))
    hint = caption_hint(src, ctx)
    assert hint is not None
    assert "locked" in hint.lower()
    assert "foo" in hint


def test_caption_hint_silent_when_user_explicitly_chose_host():
    # If the user asked for host knowing it might be locked, don't nag.
    src = CaptureSource(kind="host", reason="user-explicit")
    ctx = RouterContext(host_locked=True, active_sandbox_task_ids=("foo",))
    assert caption_hint(src, ctx) is None


def test_caption_hint_silent_when_host_not_locked():
    src = CaptureSource(kind="host", reason="default-host")
    ctx = RouterContext(host_locked=False, active_sandbox_task_ids=("foo",))
    assert caption_hint(src, ctx) is None


def test_caption_hint_silent_for_sandbox_source():
    src = CaptureSource(kind="sandbox", task_id="foo", reason="task-context")
    ctx = RouterContext(host_locked=True, active_sandbox_task_ids=("foo",))
    assert caption_hint(src, ctx) is None


def test_caption_hint_no_active_sandboxes_still_helpful():
    src = CaptureSource(kind="host", reason="default-host")
    ctx = RouterContext(host_locked=True, active_sandbox_task_ids=())
    hint = caption_hint(src, ctx)
    assert hint is not None
    assert "kanban" in hint.lower() or "vexis-sandbox" in hint

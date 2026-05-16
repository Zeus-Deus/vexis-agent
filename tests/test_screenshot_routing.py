"""End-to-end tests for /screenshot source routing.

Covers the wire-up between:
* the Telegram modifier extractor in ``transports.telegram``,
* the live router context builder,
* ``tools.desktop.capture_desktop`` dispatching on source,
* the caption formatter.

Each test pins one piece of the pipeline by mocking the layer below
so we exercise the wiring without needing real grim/docker. The pure
router logic itself is covered by ``test_capture_source.py``.

Convention: sync `def test_*` with asyncio.run, matching the
test_livestream.py / test_desktop.py style.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from vexis_agent.tools import desktop
from vexis_agent.tools.capture_source import (
    CaptureSource,
    RouterContext,
)
from vexis_agent.transports.telegram import (
    _extract_screenshot_modifier,
    _format_screenshot_caption,
    _screenshot_help_text,
)


# ---------- modifier extraction ----------


def test_modifier_extraction_bare_command():
    assert _extract_screenshot_modifier("/screenshot") is None


def test_modifier_extraction_with_host():
    assert _extract_screenshot_modifier("/screenshot host") == "host"


def test_modifier_extraction_with_sandbox_and_id():
    assert _extract_screenshot_modifier("/screenshot sandbox foo") == "sandbox foo"


def test_modifier_extraction_strips_botname_suffix():
    assert _extract_screenshot_modifier("/screenshot@vexis_bot sandbox") == "sandbox"


def test_modifier_extraction_handles_extra_whitespace():
    assert _extract_screenshot_modifier("   /screenshot   host  ") == "host"


def test_modifier_extraction_empty_text():
    assert _extract_screenshot_modifier("") is None


def test_modifier_extraction_no_slash_returns_none():
    # Defensive: if somehow the message isn't a slash command, don't
    # invent a modifier.
    assert _extract_screenshot_modifier("just talking") is None


# ---------- caption formatting ----------


def test_caption_host_no_hint():
    src = CaptureSource(kind="host", reason="default-host")
    ctx = RouterContext(host_locked=False)
    caption = _format_screenshot_caption(src, ctx, "Workspace 3 on DP-1 — Brave")
    assert caption is not None
    assert caption.startswith("📺 Host")
    assert "Brave" in caption
    assert "⚠️" not in caption


def test_caption_sandbox():
    src = CaptureSource(kind="sandbox", task_id="my-task", reason="task-context")
    ctx = RouterContext()
    caption = _format_screenshot_caption(src, ctx, "Sandbox my-task (wayland-grim)")
    assert caption is not None
    assert caption.startswith("📦 Sandbox my-task")


def test_caption_includes_lock_hint_when_auto_host_locked():
    src = CaptureSource(kind="host", reason="default-host")
    ctx = RouterContext(host_locked=True, active_sandbox_task_ids=("foo",))
    caption = _format_screenshot_caption(src, ctx, "")
    assert caption is not None
    assert "📺 Host" in caption
    assert "⚠️" in caption
    assert "foo" in caption


def test_caption_no_hint_when_user_picked_host():
    src = CaptureSource(kind="host", reason="user-explicit")
    ctx = RouterContext(host_locked=True, active_sandbox_task_ids=("foo",))
    caption = _format_screenshot_caption(src, ctx, "")
    assert caption is not None
    assert "⚠️" not in caption


def test_caption_empty_summary_still_has_label():
    src = CaptureSource(kind="host", reason="default-host")
    ctx = RouterContext(host_locked=False)
    caption = _format_screenshot_caption(src, ctx, "")
    assert caption == "📺 Host"


# ---------- help text ----------


def test_help_text_mentions_all_modifiers():
    text = _screenshot_help_text()
    for keyword in ("host", "sandbox", "auto", "help"):
        assert keyword in text.lower()


# ---------- capture_desktop dispatch ----------


def test_capture_desktop_with_none_source_uses_host_path():
    """capture_desktop(source=None) must hit the host (_capture_host) branch.

    We pin this by mocking _capture_host and _capture_sandbox and
    asserting only the host one is called.
    """
    host_called = []
    sandbox_called = []

    async def fake_host(scope):
        host_called.append(scope)
        return desktop.CaptureResult(
            image_path=Path("/tmp/fake-host.png"), state={}, summary="host"
        )

    async def fake_sandbox(task_id):
        sandbox_called.append(task_id)
        return desktop.CaptureResult(
            image_path=Path("/tmp/fake-sandbox.png"), state={}, summary="sandbox"
        )

    with patch.object(desktop, "_capture_host", fake_host):
        with patch.object(desktop, "_capture_sandbox", fake_sandbox):
            result = asyncio.run(desktop.capture_desktop(source=None))

    assert host_called == ["focused-monitor"]
    assert sandbox_called == []
    assert result.summary == "host"


def test_capture_desktop_with_host_source_uses_host_path():
    host_called = []

    async def fake_host(scope):
        host_called.append(scope)
        return desktop.CaptureResult(
            image_path=Path("/tmp/fake-host.png"), state={}, summary="host"
        )

    async def fake_sandbox(task_id):
        raise AssertionError("sandbox path must not be invoked")

    src = CaptureSource(kind="host", reason="user-explicit")
    with patch.object(desktop, "_capture_host", fake_host):
        with patch.object(desktop, "_capture_sandbox", fake_sandbox):
            asyncio.run(desktop.capture_desktop(scope="all-monitors", source=src))

    assert host_called == ["all-monitors"]


def test_capture_desktop_with_sandbox_source_uses_sandbox_path():
    sandbox_called = []

    async def fake_host(scope):
        raise AssertionError("host path must not be invoked")

    async def fake_sandbox(task_id):
        sandbox_called.append(task_id)
        return desktop.CaptureResult(
            image_path=Path("/tmp/fake-sandbox.png"),
            state={"source": "sandbox", "task_id": task_id},
            summary=f"Sandbox {task_id}",
        )

    src = CaptureSource(kind="sandbox", task_id="my-task", reason="user-explicit")
    with patch.object(desktop, "_capture_host", fake_host):
        with patch.object(desktop, "_capture_sandbox", fake_sandbox):
            result = asyncio.run(desktop.capture_desktop(source=src))

    assert sandbox_called == ["my-task"]
    assert result.state["source"] == "sandbox"


def test_capture_desktop_sandbox_missing_task_id_raises():
    # CaptureSource validates this at construction time, but the
    # backstop in capture_desktop should also raise if a sandbox source
    # somehow slips through without a task_id (e.g. constructed by a
    # subclass that bypasses __post_init__).
    bad = CaptureSource.__new__(CaptureSource)
    object.__setattr__(bad, "kind", "sandbox")
    object.__setattr__(bad, "task_id", None)
    object.__setattr__(bad, "reason", "")

    with pytest.raises(desktop.CaptureError, match="missing task_id"):
        asyncio.run(desktop.capture_desktop(source=bad))


# ---------- CLI helper: source resolution against live state ----------


def test_cli_source_resolution_auto_with_no_docker_falls_back_to_host():
    """If docker isn't installed (or Sandbox.list_all raises), the CLI
    helper must still resolve `None` (auto) to a CaptureSource —
    specifically host, since no sandboxes are visible."""
    from vexis_agent.tools.desktop_cli import _resolve_source_for_cli

    # We can't easily fake Sandbox.list_all from the import statement
    # inside the helper; instead clear the env var so auto routes to
    # host without depending on docker.
    import os

    env = {k: v for k, v in os.environ.items() if k != "VEXIS_SANDBOX_TASK_ID"}
    with patch.dict("os.environ", env, clear=True):
        src = _resolve_source_for_cli(None)
    assert src.kind == "host"

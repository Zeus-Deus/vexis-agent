"""Browser add-on ``register(ctx)`` wiring — the extraction contract.

These pin that the browser add-on owns its integration end-to-end:

  * the twelve ``browser_*`` dispatch handlers land in the runtime (the
    ten original ops plus the issue-#57 ``browser_tabs`` / ``browser_tab_close``);
  * the ``web-browsing`` capability block (order 13) is registered via
    the add-on hook and assembles into the prompt only when loaded;
  * the ``browser.md`` skill is registered;
  * the live ``BrowserTools`` is attached as the ``"browser"`` service
    so the dashboard can reach it without importing the add-on;
  * a ``browser-session-lifecycle`` background task is registered;
  * the dispatch handlers validate args exactly like the old core
    branches (so the ``vexis-browse`` CLI contract is unchanged).

No browser launches here — ``BrowserTools`` is constructed against a
fake/idle ``SessionManager`` and the handlers are exercised only on
their pure validation paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import vexis_agent.core.capabilities as cap
from vexis_agent.addons.browser import register
from vexis_agent.addons.browser.dispatch import build_browser_handlers
from vexis_agent.core.addons.context import AddonConfig, make_context
from vexis_agent.core.addons.registry import AddonRuntime
from vexis_agent.tools.browser import BrowserTools
from vexis_agent.tools.browser.session import SessionManager

ADDON_DIR = Path(__file__).resolve().parent.parent

_BROWSER_OPS = (
    "browser_navigate",
    "browser_snapshot",
    "browser_click",
    "browser_read",
    "browser_type",
    "browser_press",
    "browser_back",
    "browser_scroll",
    "browser_screenshot",
    "browser_recycle",
    "browser_tabs",
    "browser_tab_close",
)


@pytest.fixture
def restore_registry():
    """Snapshot/restore the process-global core capability registry so the
    add-on's capability-block registration doesn't leak across tests."""
    from vexis_agent.core.capabilities import iter_capability_blocks

    iter_capability_blocks()  # ensure builtins loaded first
    snap = dict(cap._REGISTRY)
    loaded = cap._loaded
    try:
        yield
    finally:
        cap._REGISTRY.clear()
        cap._REGISTRY.update(snap)
        cap._loaded = loaded


@pytest.fixture
def runtime() -> AddonRuntime:
    return AddonRuntime(user_id="test-user")


def _make_ctx(runtime: AddonRuntime, tmp_path: Path):
    # Attach the workspace service the way main.py does so register()
    # resolves it without re-reading config.
    runtime.attach_service("workspace", tmp_path)
    return make_context(
        runtime,
        addon_name="browser",
        addon_dir=ADDON_DIR,
        config=AddonConfig(),
    )


def test_register_wires_dispatch_handlers(restore_registry, runtime, tmp_path):
    register(_make_ctx(runtime, tmp_path))
    handlers = runtime.dispatch_handlers()
    for op in _BROWSER_OPS:
        assert op in handlers, f"{op} not registered"
        assert handlers[op].addon_name == "browser"


def test_register_registers_capability_block(restore_registry, runtime, tmp_path):
    # Absent before load (builtins-only).
    assert "web-browsing" not in cap._REGISTRY
    register(_make_ctx(runtime, tmp_path))
    assert cap._REGISTRY["web-browsing"].order == 13
    assert "web-browsing" in {r.name for r in runtime.capability_blocks()}
    assembled = cap.assemble_capability_docs()
    # The browser is now the vexis-browser MCP server; the block heads
    # with the MCP tools (vexis-browse CLI kept as an equivalent fallback).
    assert "## Web browsing — `vexis-browser`" in assembled


def test_register_attaches_browser_service(restore_registry, runtime, tmp_path):
    register(_make_ctx(runtime, tmp_path))
    browser = runtime.get_service("browser")
    assert isinstance(browser, BrowserTools)
    # state_for_dashboard is the read the dashboard payload uses.
    state = browser.state_for_dashboard()
    assert "recent_navigations" in state


def test_register_registers_skill_and_lifecycle(restore_registry, runtime, tmp_path):
    register(_make_ctx(runtime, tmp_path))
    skills = list(runtime.skills())
    assert any(s.skill_file.name == "browser.md" for s in skills)
    tasks = list(runtime.background_tasks())
    assert any(t.name == "browser-session-lifecycle" for t in tasks)


def test_register_resolves_workspace_from_service(restore_registry, runtime, tmp_path):
    register(_make_ctx(runtime, tmp_path))
    browser = runtime.get_service("browser")
    # BrowserTools writes screenshots under <workspace>/browser/screenshots;
    # the workspace it bound is the one we attached as a service.
    assert browser._workspace == tmp_path


# --- dispatch handler arg-validation parity (no browser launch) ----------


def _handlers():
    mgr = SessionManager()
    return build_browser_handlers(BrowserTools(mgr, Path("/tmp")))


async def _call(op, args):
    return await _handlers()[op](args)


def test_click_rejects_non_int_index():
    import asyncio

    out = asyncio.run(_call("browser_click", {"index": "nope"}))
    assert out == {
        "ok": False,
        "error": "'index' must be an integer",
        "kind": "BadRequest",
    }


def test_type_rejects_non_string_text():
    import asyncio

    out = asyncio.run(_call("browser_type", {"index": 1, "text": 123}))
    assert out["ok"] is False
    assert out["kind"] == "BadRequest"


def test_scroll_rejects_non_numeric_pages():
    import asyncio

    out = asyncio.run(_call("browser_scroll", {"direction": "down", "pages": "x"}))
    assert out["ok"] is False
    assert "pages" in out["error"]


def test_read_rejects_non_string_selector():
    import asyncio

    out = asyncio.run(_call("browser_read", {"selector": 5}))
    assert out["ok"] is False
    assert out["kind"] == "BadRequest"


def test_recycle_handler_present_and_forwards(runtime, tmp_path):
    # browser_recycle (issue #55) is built by build_browser_handlers and, on
    # an idle manager, reports was_running False without launching anything.
    import asyncio

    handlers = build_browser_handlers(BrowserTools(SessionManager(), tmp_path))
    assert "browser_recycle" in handlers
    out = asyncio.run(handlers["browser_recycle"]({}))
    assert out == {"ok": True, "was_running": False}


# --- issue #57 dispatch: new ops + arg coercion (no browser launch) --------


def test_navigate_rejects_non_string_wait_until():
    import asyncio

    out = asyncio.run(_call("browser_navigate", {"url": "http://x", "wait_until": 3}))
    assert out["ok"] is False
    assert out["kind"] == "BadRequest"
    assert "wait_until" in out["error"]


def test_navigate_rejects_non_string_then_read():
    import asyncio

    out = asyncio.run(_call("browser_navigate", {"url": "http://x", "then_read": 5}))
    assert out["ok"] is False
    assert out["kind"] == "BadRequest"
    assert "then_read" in out["error"]


def test_snapshot_rejects_non_string_tab():
    import asyncio

    out = asyncio.run(_call("browser_snapshot", {"tab": 7}))
    assert out["ok"] is False
    assert out["kind"] == "BadRequest"
    assert "tab" in out["error"]


def test_tabs_handler_present_and_lists_empty_on_idle(tmp_path):
    # browser_tabs (issue #57) is a pure read: on an idle manager it returns
    # an empty tab list without launching anything.
    import asyncio

    handlers = build_browser_handlers(BrowserTools(SessionManager(), tmp_path))
    assert "browser_tabs" in handlers
    out = asyncio.run(handlers["browser_tabs"]({}))
    assert out == {"ok": True, "tabs": []}


def test_tab_close_rejects_non_string_tab():
    import asyncio

    out = asyncio.run(_call("browser_tab_close", {"tab": 9}))
    assert out["ok"] is False
    assert out["kind"] == "BadRequest"


def test_tab_close_unknown_tab_errors_with_hint(tmp_path, monkeypatch):
    # An unknown tab on a (fake) running session is a clean error payload
    # with the open-tabs hint, not a BadRequest and not a launch.
    import asyncio

    from vexis_agent.tools.browser.session import SessionManager

    mgr = SessionManager()
    mgr._session = object()  # pretend a session is live; registry stays empty
    handlers = build_browser_handlers(BrowserTools(mgr, tmp_path))
    out = asyncio.run(handlers["browser_tab_close"]({"tab": "ghost"}))
    assert out["ok"] is False
    assert "no tab named 'ghost'" in out["error"]
    assert "hint" in out

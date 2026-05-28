"""``PluginContext`` registration-hook tests.

Each ``ctx.register_*`` method is exercised in isolation, with a
real :class:`AddonRuntime` so we also verify the runtime's
conflict detection. The actual consumers (telegram bot, watcher,
brain) are NOT exercised here — those live in their own tests.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vexis_agent.core.addons import (
    AddonConfig,
    AddonConflictError,
    AddonRuntime,
    DEFAULT_USER_ID,
    PluginContext,
    make_context,
)


def _ctx(
    tmp_path: Path,
    runtime: AddonRuntime,
    name: str = "test-addon",
    user_id: str | None = None,
) -> PluginContext:
    addon_dir = tmp_path / name
    addon_dir.mkdir(exist_ok=True)
    return make_context(
        runtime,
        addon_name=name,
        addon_dir=addon_dir,
        config=AddonConfig(values={}),
        user_id=user_id,
    )


# ---------- context construction --------------------------------------------


def test_context_fields_populated(tmp_path: Path) -> None:
    runtime = AddonRuntime()
    ctx = _ctx(tmp_path, runtime, name="myaddon")
    assert ctx.addon_name == "myaddon"
    assert ctx.addon_dir == tmp_path / "myaddon"
    assert ctx.user_id == DEFAULT_USER_ID
    assert ctx.log.name == "vexis_agent.addons.myaddon"
    assert isinstance(ctx.config, AddonConfig)


def test_context_is_frozen(tmp_path: Path) -> None:
    """Add-ons can't reassign their own context to swap names / dirs."""
    runtime = AddonRuntime()
    ctx = _ctx(tmp_path, runtime)
    with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
        ctx.addon_name = "evil"  # type: ignore[misc]


def test_user_id_defaults_to_runtime_value(tmp_path: Path) -> None:
    """The runtime's user_id flows through unless explicitly overridden —
    this is the multi-user seam."""
    runtime = AddonRuntime(user_id="alice")
    ctx = _ctx(tmp_path, runtime)
    assert ctx.user_id == "alice"


def test_user_id_explicit_override(tmp_path: Path) -> None:
    runtime = AddonRuntime(user_id="default")
    ctx = _ctx(tmp_path, runtime, user_id="bob")
    assert ctx.user_id == "bob"


# ---------- telegram_command -----------------------------------------------


def test_register_telegram_command(tmp_path: Path) -> None:
    runtime = AddonRuntime()
    ctx = _ctx(tmp_path, runtime)

    async def handler(update, context):  # noqa: ARG001
        return None

    ctx.register_telegram_command(
        "hello", handler, menu_description="Say hi"
    )
    cmds = list(runtime.telegram_commands())
    assert len(cmds) == 1
    assert cmds[0].name == "hello"
    assert cmds[0].menu_description == "Say hi"
    assert cmds[0].handler is handler
    assert cmds[0].addon_name == "test-addon"


def test_register_telegram_command_strips_leading_slash(tmp_path: Path) -> None:
    """Both ``/hello`` and ``hello`` register the same command — the
    leading slash is forgiving so add-on authors don't get bitten."""
    runtime = AddonRuntime()
    ctx = _ctx(tmp_path, runtime)

    async def handler(update, context):  # noqa: ARG001
        return None

    ctx.register_telegram_command("/hello", handler)
    cmds = list(runtime.telegram_commands())
    assert cmds[0].name == "hello"


def test_telegram_command_conflict_raises(tmp_path: Path) -> None:
    runtime = AddonRuntime()
    ctx1 = _ctx(tmp_path, runtime, name="first")
    ctx2 = _ctx(tmp_path, runtime, name="second")

    async def h(update, context):  # noqa: ARG001
        return None

    ctx1.register_telegram_command("hello", h)
    with pytest.raises(AddonConflictError, match="hello"):
        ctx2.register_telegram_command("hello", h)


# ---------- dispatch_handler ------------------------------------------------


def test_register_dispatch_handler(tmp_path: Path) -> None:
    runtime = AddonRuntime()
    ctx = _ctx(tmp_path, runtime)

    async def handler(payload: dict) -> dict:
        return {"echoed": payload}

    ctx.register_dispatch_handler("watch_register", handler)
    assert "watch_register" in runtime.dispatch_handlers()
    reg = runtime.dispatch_handlers()["watch_register"]
    assert reg.handler is handler


def test_dispatch_handler_conflict_raises(tmp_path: Path) -> None:
    runtime = AddonRuntime()
    ctx1 = _ctx(tmp_path, runtime, name="first")
    ctx2 = _ctx(tmp_path, runtime, name="second")

    async def h(payload: dict) -> dict:
        return {}

    ctx1.register_dispatch_handler("op", h)
    with pytest.raises(AddonConflictError, match="op"):
        ctx2.register_dispatch_handler("op", h)


# ---------- background_task -------------------------------------------------


def test_register_background_task_additive(tmp_path: Path) -> None:
    """Two add-ons can each register tasks; no name conflict by design."""
    runtime = AddonRuntime()
    ctx1 = _ctx(tmp_path, runtime, name="first")
    ctx2 = _ctx(tmp_path, runtime, name="second")

    async def f1():
        return None

    async def f2():
        return None

    ctx1.register_background_task("worker", f1)
    ctx2.register_background_task("worker", f2)  # same name OK
    tasks = list(runtime.background_tasks())
    assert len(tasks) == 2
    assert {t.addon_name for t in tasks} == {"first", "second"}


# ---------- watcher_source --------------------------------------------------


def test_register_watcher_source(tmp_path: Path) -> None:
    runtime = AddonRuntime()
    ctx = _ctx(tmp_path, runtime)

    sentinel = object()
    ctx.register_watcher_source("codemux", sentinel)
    sources = list(runtime.watcher_sources())
    assert len(sources) == 1
    assert sources[0].source_type == "codemux"
    assert sources[0].source is sentinel


def test_watcher_source_conflict_raises(tmp_path: Path) -> None:
    runtime = AddonRuntime()
    ctx1 = _ctx(tmp_path, runtime, name="first")
    ctx2 = _ctx(tmp_path, runtime, name="second")
    ctx1.register_watcher_source("codemux", object())
    with pytest.raises(AddonConflictError, match="codemux"):
        ctx2.register_watcher_source("codemux", object())


# ---------- system_prompt_block ---------------------------------------------


def test_register_system_prompt_block(tmp_path: Path) -> None:
    runtime = AddonRuntime()
    ctx = _ctx(tmp_path, runtime)

    def provider() -> str | None:
        return "Active codemux: 2 workspaces"

    ctx.register_system_prompt_block("codemux-active", provider)
    blocks = list(runtime.header_blocks())
    assert len(blocks) == 1
    assert blocks[0].provider() == "Active codemux: 2 workspaces"


def test_header_block_conflict_raises(tmp_path: Path) -> None:
    runtime = AddonRuntime()
    ctx1 = _ctx(tmp_path, runtime, name="first")
    ctx2 = _ctx(tmp_path, runtime, name="second")
    ctx1.register_system_prompt_block("active", lambda: "x")
    with pytest.raises(AddonConflictError, match="active"):
        ctx2.register_system_prompt_block("active", lambda: "y")


# ---------- skill -----------------------------------------------------------


def test_register_skill_inside_addon_dir(tmp_path: Path) -> None:
    runtime = AddonRuntime()
    ctx = _ctx(tmp_path, runtime, name="myaddon")
    skill_file = ctx.addon_dir / "skills" / "myskill.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("# my skill", encoding="utf-8")
    ctx.register_skill(skill_file)
    skills = list(runtime.skills())
    assert len(skills) == 1
    assert skills[0].skill_file == skill_file
    assert skills[0].target_subdir == "."


def test_register_skill_outside_addon_dir_rejected(tmp_path: Path) -> None:
    """Add-ons can't install arbitrary files from anywhere on disk —
    the path must be inside ``ctx.addon_dir``."""
    runtime = AddonRuntime()
    ctx = _ctx(tmp_path, runtime, name="myaddon")
    bad = tmp_path / "elsewhere.md"
    bad.write_text("escape attempt", encoding="utf-8")
    with pytest.raises(ValueError, match="not inside addon_dir"):
        ctx.register_skill(bad)


def test_register_skill_requires_absolute_path(tmp_path: Path) -> None:
    runtime = AddonRuntime()
    ctx = _ctx(tmp_path, runtime)
    with pytest.raises(ValueError, match="absolute path"):
        ctx.register_skill(Path("relative/path.md"))


# ---------- dashboard_page --------------------------------------------------


def test_register_dashboard_page_additive(tmp_path: Path) -> None:
    runtime = AddonRuntime()
    ctx1 = _ctx(tmp_path, runtime, name="first")
    ctx2 = _ctx(tmp_path, runtime, name="second")

    ctx1.register_dashboard_page({"label": "First", "tab": {"path": "/f"}})
    ctx2.register_dashboard_page({"label": "Second", "tab": {"path": "/s"}})
    pages = list(runtime.dashboard_pages())
    assert len(pages) == 2
    assert {p.addon_name for p in pages} == {"first", "second"}


def test_register_dashboard_page_defensive_copy(tmp_path: Path) -> None:
    """The runtime stores a copy of the manifest dict so the add-on
    can't mutate it later."""
    runtime = AddonRuntime()
    ctx = _ctx(tmp_path, runtime)
    raw = {"label": "X"}
    ctx.register_dashboard_page(raw)
    raw["label"] = "MUTATED"
    pages = list(runtime.dashboard_pages())
    assert pages[0].manifest["label"] == "X"


# ---------- mcp_server_default ----------------------------------------------


class _FakeMcpSpec:
    """Stand-in for ``McpServerSpec`` — avoids importing core.brain
    just to test the registry's name extraction."""

    def __init__(self, name: str) -> None:
        self.name = name


def test_register_mcp_default(tmp_path: Path) -> None:
    runtime = AddonRuntime()
    ctx = _ctx(tmp_path, runtime)
    spec = _FakeMcpSpec("codemux")
    ctx.register_mcp_server_default(spec)
    defaults = list(runtime.mcp_defaults())
    assert len(defaults) == 1
    assert defaults[0].spec is spec


def test_mcp_default_conflict_raises(tmp_path: Path) -> None:
    runtime = AddonRuntime()
    ctx1 = _ctx(tmp_path, runtime, name="first")
    ctx2 = _ctx(tmp_path, runtime, name="second")
    ctx1.register_mcp_server_default(_FakeMcpSpec("codemux"))
    with pytest.raises(AddonConflictError, match="codemux"):
        ctx2.register_mcp_server_default(_FakeMcpSpec("codemux"))


# ---------- AddonConfig -----------------------------------------------------


def test_addon_config_get_and_contains() -> None:
    cfg = AddonConfig(values={"poll_interval_seconds": 5.0, "enabled": True})
    assert cfg.get("poll_interval_seconds") == 5.0
    assert cfg.get("missing", "fallback") == "fallback"
    assert "enabled" in cfg
    assert "missing" not in cfg
    assert cfg["poll_interval_seconds"] == 5.0


# ---------- async-handler sanity --------------------------------------------


def test_dispatch_handler_invokable(tmp_path: Path) -> None:
    """Handlers we register are stored intact and can be awaited later."""
    runtime = AddonRuntime()
    ctx = _ctx(tmp_path, runtime)

    async def handler(payload: dict) -> dict:
        return {"got": payload["x"]}

    ctx.register_dispatch_handler("op", handler)
    reg = runtime.dispatch_handlers()["op"]

    # ``asyncio.run`` over ``get_event_loop().run_until_complete`` —
    # py3.11+ deprecated the latter when no loop exists, and the
    # full-suite runner doesn't guarantee a fresh loop per test.
    result = asyncio.run(reg.handler({"x": 42}))
    assert result == {"got": 42}

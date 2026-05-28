"""Telegram-side wiring tests for add-on slash commands.

Two surfaces:

  * ``_register_addon_commands`` — runtime registration: every
    ``ctx.register_telegram_command`` should land as a
    ``CommandHandler`` on the bot's PTB ``Application``. Auth gate
    must wrap each handler so add-on authors don't repeat the
    ``is_allowed`` check.

  * ``_register_commands`` (the menu publisher) — addon-supplied
    ``(name, description)`` pairs should append to the canonical
    COMMANDS list and end up in ``set_my_commands``.

Both tests bypass TelegramTransport.__init__ so we don't need a
real Telegram token; that pattern matches ``test_telegram_streaming``
and ``test_schedule_outcome``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from vexis_agent.core.addons import AddonConfig, AddonRuntime, make_context
from vexis_agent.transports.telegram import (
    TelegramTransport,
    _register_commands,
)


# ---------- _register_addon_commands ----------------------------------------


def _make_transport(runtime: AddonRuntime | None) -> TelegramTransport:
    """Bypass __init__ and wire only the attributes
    ``_register_addon_commands`` touches."""
    t = TelegramTransport.__new__(TelegramTransport)
    t._addon_runtime = runtime  # type: ignore[attr-defined]
    t._allowed_user_id = 42  # type: ignore[attr-defined]
    t._app = MagicMock()  # type: ignore[attr-defined]
    t._app.add_handler = MagicMock()
    return t


def test_register_addon_commands_no_runtime() -> None:
    """No runtime → no-op. Defensive against the test-time path that
    constructs TelegramTransport without an addon runtime."""
    t = _make_transport(runtime=None)
    t._register_addon_commands()  # type: ignore[attr-defined]
    t._app.add_handler.assert_not_called()  # type: ignore[attr-defined]


def test_register_addon_commands_no_addons(tmp_path: Path) -> None:
    """Runtime present but empty → still no-op."""
    runtime = AddonRuntime()
    t = _make_transport(runtime=runtime)
    t._register_addon_commands()  # type: ignore[attr-defined]
    t._app.add_handler.assert_not_called()  # type: ignore[attr-defined]


def test_register_addon_commands_wires_each_addon_command(tmp_path: Path) -> None:
    """Each registered slash command becomes a PTB ``CommandHandler``."""
    runtime = AddonRuntime()
    addon_dir = tmp_path / "myaddon"
    addon_dir.mkdir()
    ctx = make_context(
        runtime,
        addon_name="myaddon",
        addon_dir=addon_dir,
        config=AddonConfig(),
    )

    async def hello_handler(update, context):  # noqa: ANN001
        return None

    async def bye_handler(update, context):  # noqa: ANN001
        return None

    ctx.register_telegram_command("hello", hello_handler, menu_description="Hi")
    ctx.register_telegram_command("bye", bye_handler)

    t = _make_transport(runtime=runtime)
    t._register_addon_commands()  # type: ignore[attr-defined]

    # Two handlers added — one per registered slash.
    assert t._app.add_handler.call_count == 2  # type: ignore[attr-defined]


def test_register_addon_commands_wraps_with_auth_gate(tmp_path: Path) -> None:
    """The wrapped handler drops requests from non-allowed users
    without ever calling the add-on's underlying handler."""
    runtime = AddonRuntime()
    addon_dir = tmp_path / "auth-test"
    addon_dir.mkdir()
    ctx = make_context(
        runtime,
        addon_name="auth-test",
        addon_dir=addon_dir,
        config=AddonConfig(),
    )

    underlying = AsyncMock()
    ctx.register_telegram_command("secret", underlying)

    t = _make_transport(runtime=runtime)
    t._register_addon_commands()  # type: ignore[attr-defined]

    # Grab the wrapper PTB would receive. add_handler was called once;
    # the wrapper is its first positional arg's ``callback`` attribute.
    call = t._app.add_handler.call_args_list[0]  # type: ignore[attr-defined]
    cmd_handler = call.args[0]
    wrapped = cmd_handler.callback

    # Rejected request: wrong user_id.
    update_bad = MagicMock()
    update_bad.effective_user.id = 999
    asyncio.run(wrapped(update_bad, MagicMock()))
    underlying.assert_not_awaited()

    # Allowed request: matching user_id.
    update_ok = MagicMock()
    update_ok.effective_user.id = 42
    asyncio.run(wrapped(update_ok, MagicMock()))
    underlying.assert_awaited_once()


# ---------- _register_commands (menu publisher) -----------------------------


def test_register_commands_appends_addon_entries() -> None:
    """The canonical COMMANDS list survives unchanged; addon entries
    append after it. Order matters — users memorise menu positions."""
    app = MagicMock()
    app.bot.set_my_commands = AsyncMock()

    asyncio.run(
        _register_commands(
            app,
            include_codemux=False,
            addon_entries=[
                ("hello", "Say hi"),
                ("bye", "Say bye"),
            ],
        )
    )

    app.bot.set_my_commands.assert_awaited_once()
    bot_commands = app.bot.set_my_commands.call_args.args[0]
    # Last two entries are the addon ones, in registration order.
    assert bot_commands[-2].command == "hello"
    assert bot_commands[-2].description == "Say hi"
    assert bot_commands[-1].command == "bye"
    assert bot_commands[-1].description == "Say bye"


def test_register_commands_no_addon_entries() -> None:
    """Backwards compat: callers that don't pass ``addon_entries`` get
    the original behaviour — canonical commands only."""
    app = MagicMock()
    app.bot.set_my_commands = AsyncMock()

    asyncio.run(_register_commands(app, include_codemux=False))
    app.bot.set_my_commands.assert_awaited_once()


def test_register_commands_codemux_still_works() -> None:
    """``include_codemux`` legacy parameter still appends /codemux,
    alongside any add-on entries. (Phase B retires this path; until
    then both surfaces coexist.)"""
    app = MagicMock()
    app.bot.set_my_commands = AsyncMock()

    asyncio.run(
        _register_commands(
            app,
            include_codemux=True,
            addon_entries=[("hello", "Say hi")],
        )
    )

    bot_commands = app.bot.set_my_commands.call_args.args[0]
    cmd_names = [c.command for c in bot_commands]
    assert "codemux" in cmd_names
    assert "hello" in cmd_names

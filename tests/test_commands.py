"""Tests for core/commands.py and the Telegram command-registration helper.

The COMMANDS tuple is the single source of truth for the bot's slash
menu, so we lock down its shape: each entry must clear Telegram's
validation rules, and the list must contain the commands we ship with.

Tests follow the codebase convention of sync test functions calling
asyncio.run() rather than pytest-asyncio.
"""

from __future__ import annotations

import asyncio
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.commands import COMMANDS, BotCommand
from transports.telegram import _register_commands

_COMMAND_RE = re.compile(r"^[a-z0-9_]{1,32}$")


def test_commands_non_empty():
    assert len(COMMANDS) > 0


def test_commands_match_telegram_validation():
    for cmd in COMMANDS:
        assert _COMMAND_RE.match(cmd.command), (
            f"Command {cmd.command!r} fails Telegram's "
            r"^[a-z0-9_]{1,32}$ rule"
        )
        assert 3 <= len(cmd.description) <= 256, (
            f"Description for /{cmd.command} must be 3-256 chars, "
            f"got {len(cmd.description)}"
        )


def test_command_strings_unique():
    names = [cmd.command for cmd in COMMANDS]
    assert len(names) == len(set(names)), f"Duplicate command in {names}"


def test_expected_commands_present():
    names = {cmd.command for cmd in COMMANDS}
    for expected in (
        "new", "switch", "sessions", "screenshot", "cancel", "model",
    ):
        assert expected in names, f"/{expected} missing from COMMANDS"


def test_confirm_delete_not_in_menu():
    """confirm_delete is only valid right after /delete and shouldn't
    show up in the slash menu — see core/commands.py docstring."""
    names = {cmd.command for cmd in COMMANDS}
    assert "confirm_delete" not in names


def test_botcommand_is_immutable():
    """frozen=True keeps the canonical list from being mutated at runtime."""
    cmd = BotCommand("foo", "bar baz")
    with pytest.raises((AttributeError, TypeError)):
        cmd.command = "bah"  # type: ignore[misc]


def test_register_commands_calls_set_my_commands_with_full_list():
    """Verify the helper hands every COMMANDS entry to the Bot API in
    the right order with the right (command, description) pairs."""
    app = MagicMock()
    app.bot.set_my_commands = AsyncMock()

    asyncio.run(_register_commands(app))

    assert app.bot.set_my_commands.await_count == 1
    (sent,), _kwargs = app.bot.set_my_commands.call_args
    assert len(sent) == len(COMMANDS)
    for tg_cmd, ours in zip(sent, COMMANDS, strict=True):
        assert tg_cmd.command == ours.command
        assert tg_cmd.description == ours.description


def test_register_commands_swallows_api_failure(caplog):
    """API failure must NOT crash startup — the daemon should keep
    booting with whatever menu Telegram already had."""
    app = MagicMock()
    app.bot.set_my_commands = AsyncMock(side_effect=RuntimeError("boom"))

    with caplog.at_level("WARNING"):
        asyncio.run(_register_commands(app))

    assert any(
        "Could not register Telegram commands" in r.message for r in caplog.records
    )

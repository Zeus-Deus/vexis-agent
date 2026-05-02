"""Canonical Telegram slash-command list.

Single source of truth for what shows up in the bot's slash menu.
The Telegram transport mirrors this list to the Bot API on startup
(see TelegramTransport.run). Adding a command here does NOT auto-
create a handler — you still write the handler in transports/telegram.

`confirm_delete` is intentionally excluded: it's only valid in the
60-second window after `/delete <name>` and shouldn't clutter the menu.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BotCommand:
    """A registered Telegram slash command.

    Telegram requires:
      - command: 1-32 chars, lowercase letters/digits/underscores only
      - description: 3-256 chars, shown in the slash menu
    """

    command: str
    description: str


# Order = order shown in the slash menu. Session management first,
# then on-demand actions, then control.
COMMANDS: tuple[BotCommand, ...] = (
    BotCommand("new", "New session, optional name"),
    BotCommand("switch", "Switch to a session"),
    BotCommand("sessions", "List all sessions"),
    BotCommand("rename", "Rename a session"),
    BotCommand("delete", "Delete a session"),
    BotCommand("clear", "Clear current session's history"),
    BotCommand("screenshot", "Take a screenshot of the focused monitor"),
    BotCommand("tasks", "List background tasks"),
    BotCommand("status", "Show what I'm currently working on"),
    BotCommand("cancel", "Stop the current task or a named background task"),
    BotCommand("pin", "Protect a skill from curator and skill_manage edits"),
    BotCommand("unpin", "Allow edits to a previously pinned skill"),
    BotCommand("curator", "Curator status / pause / resume / run / restore"),
    BotCommand("learning", "Learning curator status / pause / resume / run / audit"),
    BotCommand("dashboard", "Open the web dashboard (Tailscale URL + token)"),
)

"""PreToolUse hook verdict logic — pure, no I/O.

Step 6.5 of the safety system (Step 6 is the regex tripwire in
``core.safety``). This module turns a Claude Code PreToolUse hook
payload into a JSON-shaped decision: silent allow (return ``None``)
or hard deny (return a dict that the CLI wrapper prints to stdout).

Wire protocol
-------------
**Input** (Claude Code PreToolUse stdin, JSON)::

    {
        "session_id": "...",
        "transcript_path": "...",
        "cwd": "...",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /", "description": "..."}
    }

**Output** (claude-code reads this from the hook's stdout)::

    # Deny: command is blocked, model sees the reason in its turn.
    {
      "hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "Vexis blocked: recursive/forced rm"
      },
      "systemMessage": "Vexis blocked: recursive/forced rm"
    }

    # Allow: hook emits nothing (the CLI wrapper prints no bytes).
    # Claude Code falls through to its normal permission flow.

We deliberately use the modern ``hookSpecificOutput`` shape rather
than the legacy ``{"decision": "block", "reason": ...}`` form. Both
work, but the modern shape is what Anthropic recommends and what
the SDK's typed bindings target — it future-proofs us.

Non-Bash tools always allow. The regex tripwire only understands
shell commands; gating Edit/Write/etc. would need a different
mechanism and isn't in scope for Step 6.5.
"""

from __future__ import annotations

from typing import Any

from vexis_agent.core.safety import check_command

# The deny reason embeds the safety verdict's reason string so the
# model sees exactly which pattern fired. Kept short — Claude Code
# surfaces this in the tool_result block, where verbosity hurts more
# than it helps.
_REASON_PREFIX = "Vexis safety hook blocked: "

# Hard cap on the command string we'll regex-match. Beyond this we
# bail out and allow — a multi-megabyte "command" is almost certainly
# a model error, not a real destructive invocation, and we don't want
# the regex engine to chew on it.
_MAX_COMMAND_LEN = 64 * 1024


def payload_verdict(payload: Any) -> dict[str, Any] | None:
    """Decide whether to deny a Claude Code PreToolUse invocation.

    Returns a dict suitable for ``json.dumps()`` → stdout when the
    command must be denied. Returns ``None`` for silent allow
    (non-Bash tool, benign command, malformed payload).

    Pure: no logging, no env reads, no disk I/O. The CLI wrapper
    handles all of that.
    """
    if not isinstance(payload, dict):
        return None

    if payload.get("tool_name") != "Bash":
        return None

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None

    command = tool_input.get("command")
    if not isinstance(command, str) or not command:
        return None

    if len(command) > _MAX_COMMAND_LEN:
        return None

    verdict = check_command(command)
    if not verdict.requires_confirmation:
        return None

    reason = f"{_REASON_PREFIX}{verdict.reason}"
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
        # systemMessage is surfaced to the user in claude-code's
        # transcript UI. Duplicating the reason here means the
        # Vexis user sees the same string the model sees, no
        # parsing-the-hookSpecificOutput-by-hand required.
        "systemMessage": reason,
    }

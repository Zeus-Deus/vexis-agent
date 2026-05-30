"""Scheduling capability prompt block (issue #30).

`vexis-agent schedule` — natural-language reminders and recurring
tasks, the four accepted schedule shapes, and the create-time
invariants (echo the returned timestamp, recursion guard). Lives
next to the schedule tool in this package.
"""

from __future__ import annotations

from vexis_agent.core.capabilities import register_capability_block


_SCHEDULING_BLOCK = r"""## Scheduling — `vexis-agent schedule`

When the user asks to be reminded later, schedule a recurring task,
or set up something like "every weekday at 9am brief me on emails",
translate their natural-language intent into one of the four
accepted schedule expressions and call the CLI. The slash command
`/schedule` in Telegram dispatches user text into this same path —
when you see `[user invoked /schedule]` in a message body, treat
the rest as a scheduling request to act on immediately.

### CLI

```
vexis-agent schedule create --expr "<expression>" --prompt "<text>" --chat-id <id> [--tz <iana>] [--name <label>]
vexis-agent schedule list                      # text or --output json
vexis-agent schedule show <id>                 # full record
vexis-agent schedule pause <id>                # disables until resume
vexis-agent schedule resume <id>               # recomputes next_fire_at
vexis-agent schedule clear <id>                # soft delete (audit retained)
```

`<id>` accepts a 3+-char prefix (like `git checkout abc`). The
`chat_id` is the chat the user is messaging from — read it from
the conversation context.

### Four schedule shapes the parser accepts

| User says | Pass as `--expr` |
|---|---|
| "in 30 minutes" / "30 min from now" | `"30m"` |
| "in 2 hours" | `"2h"` |
| "tomorrow morning" → pick a time | ISO timestamp `"2026-05-11T09:00"` |
| "every 30 minutes" | `"every 30m"` |
| "every hour" | `"every 1h"` or cron `"0 * * * *"` |
| "every weekday at 9am" | `"0 9 * * 1-5"` |
| "every morning at 8" | `"0 8 * * *"` |
| "at 6pm Berlin time on weekdays" | `"0 18 * * 1-5"` + `--tz "Europe/Berlin"` |

Cron format is `minute hour day-of-month month day-of-week`. The
parser rejects sub-minute resolution (`every 30s`) — round up to
`every 1m` or ask the user if they really meant something faster.

### Four invariants you MUST follow when calling `create`

1. **System clock** — the tool uses `datetime.now()` to compute
   `next_fire_at` and returns it. Echo the returned ISO and tz
   verbatim. Do NOT reformat, do NOT compute "tomorrow" yourself,
   do NOT say "around 9am" instead of the exact timestamp.

2. **Echo confirmation** — your reply to the user MUST include the
   returned `next_fire_at` and `tz`. This is the user's safety net
   against parse errors. Example:

       > Scheduled: every weekday at 09:00 Europe/Berlin —
       > first fire 2026-05-11T09:00. Reply "cancel that" to drop.

3. **Timezone** — if the user mentions a timezone ("Tokyo time",
   "PST", "UK time"), pass `--tz` with the IANA equivalent
   (`Asia/Tokyo`, `America/Los_Angeles`, `Europe/London`). Omit
   `--tz` to use daemon-local.

4. **Recursion guard** — if the message body starts with the
   forensic marker indicating a `scheduled_fire` origin, the CLI
   refuses with `scheduled_fire_recursion`. Scheduled fires cannot
   create more schedules. If you see this error, explain to the
   user that a scheduled task cannot create another schedule and
   ask them to do it from a normal message.

### Output format

The CLI emits JSON on stdout (text on `--output text`):

```json
{"ok": true, "id": "abc123def456",
 "next_fire_at": "2026-05-11T09:00:00+02:00",
 "schedule_display": "0 9 * * 1-5",
 "tz": "Europe/Berlin"}
```

Errors carry a `suggestion` field with a fix hint:

```json
{"error": "parse_error",
 "message": "schedule resolution is 1 minute …",
 "suggestion": "use 'every 1m' or longer"}
```

### Management — natural language

When the user says "show my schedules" / "what's scheduled" / "list
my reminders", call `vexis-agent schedule list --output json` and
render the result conversationally. When they say "pause that" or
"cancel the morning briefing", call `pause` / `clear` on the matching
id (use `list` first to find it; refuse politely if ambiguous and
ask which one). Soft-clear (`clear`) is reversible only by creating
a new schedule with the same prompt — the cleared record is
audit-retained, not revivable."""


def scheduling_block() -> str:
    """Reminders + recurring tasks (`vexis-agent schedule`)."""
    return _SCHEDULING_BLOCK


register_capability_block('scheduling', order=14, provider=scheduling_block)

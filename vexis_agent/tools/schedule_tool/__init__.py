"""Brain-callable MCP tool surface for the /schedule feature.

Day 1 ships the parser only (``parser.parse_schedule`` /
``parser.compute_next_fire`` / ``parser.compute_grace_seconds``).
Day 2 will add the MCP tool entry points (``schedule_create`` /
``schedule_list`` / ``schedule_pause`` / ``schedule_resume`` /
``schedule_clear``) plus the ``register(mcp)`` hook the main MCP
server module calls during startup.

The subpackage layout intentionally splits parsing (croniter +
regex, no IO) from tool dispatch (IO, store, brain-facing schemas)
so unit tests can exercise the parser in isolation and the Day 2
tool code stays small.

Design citation: ``.plans/scheduling-and-provider-abstraction-research.md``
Day 1.
"""

from .parser import (
    DEFAULT_ONESHOT_GRACE_SECONDS,
    MAX_GRACE_SECONDS,
    MIN_GRACE_SECONDS,
    ScheduleParseError,
    compute_grace_seconds,
    compute_next_fire,
    parse_schedule,
)

__all__ = [
    "DEFAULT_ONESHOT_GRACE_SECONDS",
    "MAX_GRACE_SECONDS",
    "MIN_GRACE_SECONDS",
    "ScheduleParseError",
    "compute_grace_seconds",
    "compute_next_fire",
    "parse_schedule",
]

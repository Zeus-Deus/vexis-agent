"""Brain abstraction package.

Re-exports the canonical names so callers can write
``from core.brain import Brain, ClaudeCodeBrain, BrainNull, BrainCancelled``
instead of reaching into submodules. Submodule imports
(``from core.brain.base import …``) remain valid and are how the
implementations themselves should reach for shared types.
"""

from vexis_agent.core.brain.base import (
    AuxResult,
    Brain,
    BrainAuthRequired,
    BrainCancelled,
    BrainError,
    BrainEvent,
    BrainHealth,
    BrainModelNotFoundError,
    BrainNotInstalled,
    BrainPermanentError,
    BrainTimeoutError,
    BrainTransientError,
    Finished,
    McpServerSpec,
    SessionEstablished,
    SessionLost,
    StreamError,
    TextDelta,
    TextEnd,
    ToolEnd,
    ToolStart,
)
from vexis_agent.core.brain.claude_code import ClaudeCodeBrain
from vexis_agent.core.brain.null import BrainNull
from vexis_agent.core.brain.opencode import OpenCodeBrain

__all__ = [
    "AuxResult",
    "Brain",
    "BrainAuthRequired",
    "BrainCancelled",
    "BrainError",
    "BrainEvent",
    "BrainHealth",
    "BrainModelNotFoundError",
    "BrainNotInstalled",
    "BrainNull",
    "BrainPermanentError",
    "BrainTimeoutError",
    "BrainTransientError",
    "ClaudeCodeBrain",
    "Finished",
    "McpServerSpec",
    "OpenCodeBrain",
    "SessionEstablished",
    "SessionLost",
    "StreamError",
    "TextDelta",
    "TextEnd",
    "ToolEnd",
    "ToolStart",
]

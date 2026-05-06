"""Brain abstraction package.

Re-exports the canonical names so callers can write
``from core.brain import Brain, ClaudeCodeBrain, BrainNull, BrainCancelled``
instead of reaching into submodules. Submodule imports
(``from core.brain.base import …``) remain valid and are how the
implementations themselves should reach for shared types.
"""

from core.brain.base import (
    AuxResult,
    Brain,
    BrainAuthRequired,
    BrainCancelled,
    BrainError,
    BrainEvent,
    BrainHealth,
    BrainNotInstalled,
    BrainTimeoutError,
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
from core.brain.claude_code import ClaudeCodeBrain
from core.brain.null import BrainNull

__all__ = [
    "AuxResult",
    "Brain",
    "BrainAuthRequired",
    "BrainCancelled",
    "BrainError",
    "BrainEvent",
    "BrainHealth",
    "BrainNotInstalled",
    "BrainNull",
    "BrainTimeoutError",
    "ClaudeCodeBrain",
    "Finished",
    "McpServerSpec",
    "SessionEstablished",
    "SessionLost",
    "StreamError",
    "TextDelta",
    "TextEnd",
    "ToolEnd",
    "ToolStart",
]

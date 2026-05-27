"""Source plugin ABC for the watcher.

A source is a thing that can be polled for "did the user-facing agent
emit any new output recently." The watcher core knows nothing about
Codemux, raw PTYs, or tmux — it only knows ``Source.read_recent_output``
returns bytes, ``Source.is_alive`` returns a bool, and ``Source.describe``
returns metadata for the UI.

Adding a new source type (an aider PTY, a tmux pane, an SSH session)
is just: write a subclass, register it via :func:`register_source` in
that file's import side-effect. The registry and polling loop do not
change.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SourceDescription:
    """Static-ish metadata about a watched source for status/header rendering."""

    repo_path: Optional[str] = None
    goal_hint: Optional[str] = None
    title: Optional[str] = None
    # Free-form one-liner shown under "what's it doing right now."
    # The watcher fills this with the last terminal line if available.
    last_line: Optional[str] = None


class Source(abc.ABC):
    """One pollable agent."""

    #: Unique identifier for the source-type plugin. Codemux uses
    #: ``"codemux"``; future PTY/tmux plugins pick their own slug.
    source_type: str = "base"

    @abc.abstractmethod
    async def read_recent_output(self, identifier: str) -> bytes:
        """Return recent PTY/output bytes from this source.

        The polling loop diffs successive reads by content hash to
        decide whether new output landed. Returning the full scrollback
        is fine — the loop never stores the bytes, just their hash.
        Empty bytes is a valid "no output yet" signal; the loop treats
        it the same as the previous read.

        Raises ``SourceUnavailable`` when the underlying transport is
        gone (codemux not running, MCP not configured, pane killed).
        """

    @abc.abstractmethod
    async def is_alive(self, identifier: str) -> bool:
        """True iff the underlying agent process / pane / session still exists.

        Used to transition watched agents to ``status=dead`` so they
        stop spamming idle notifications after the user closes the
        workspace.
        """

    @abc.abstractmethod
    async def describe(self, identifier: str) -> SourceDescription:
        """Return current metadata for the source (repo path, last line, …).

        Called when rendering ``/codemux`` and ``vexis-watch status``.
        Cheap-to-fail — return ``SourceDescription()`` with all fields
        ``None`` if the source can't be reached.
        """


class SourceUnavailable(RuntimeError):
    """Raised when the underlying source transport is gone.

    The watcher catches this and transitions the agent to ``dead``
    rather than letting the polling loop crash.
    """


# ---------- plugin registry --------------------------------------------------

_REGISTRY: dict[str, Source] = {}


def register_source(source: Source) -> None:
    """Make a Source instance available by its ``source_type`` slug.

    Plugins call this at module import time (the watcher controller
    imports its plugins explicitly during startup, so registration
    happens deterministically — not via package walk).
    """
    _REGISTRY[source.source_type] = source


def get_source(source_type: str) -> Optional[Source]:
    """Lookup helper used by the polling loop."""
    return _REGISTRY.get(source_type)


def list_source_types() -> list[str]:
    """For ``vexis-watch register`` argument validation."""
    return sorted(_REGISTRY.keys())


def clear_sources() -> None:
    """Test-only — reset the registry between test cases."""
    _REGISTRY.clear()

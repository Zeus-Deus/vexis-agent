"""Source plugins for the watcher.

Import the base ABC + registry helpers from here. Plugin modules
(``codemux.py``, future ``pty.py``, ``tmux.py``) are imported
explicitly by the watcher controller in ``watcher/__init__.py``;
nothing here walks the directory or auto-imports them.
"""

from vexis_agent.core.watcher.sources.base import (
    Source,
    SourceDescription,
    SourceUnavailable,
    clear_sources,
    get_source,
    list_source_types,
    register_source,
)

__all__ = [
    "Source",
    "SourceDescription",
    "SourceUnavailable",
    "clear_sources",
    "get_source",
    "list_source_types",
    "register_source",
]

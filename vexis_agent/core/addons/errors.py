"""Exception types for the add-on system.

Kept in a leaf module so the loader, manifest parser, and context
facade can all import these without circular-import games. Every
exception carries a human-readable message plus optional structured
fields (addon_name, manifest_path) so the loader can log a clean
``addon X failed because Y`` line without re-formatting.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


class AddonError(Exception):
    """Base for every add-on-system error.

    Caught broadly by the loader so one bad add-on never kills the
    daemon — the loader logs and continues. Specific subclasses exist
    only for tests / log filters to grep by type.
    """

    def __init__(
        self,
        message: str,
        *,
        addon_name: Optional[str] = None,
        manifest_path: Optional[Path] = None,
    ) -> None:
        super().__init__(message)
        self.addon_name = addon_name
        self.manifest_path = manifest_path


class ManifestError(AddonError):
    """``addon.yaml`` missing, unreadable, or schema-invalid.

    Raised by :func:`vexis_agent.core.addons.manifest.parse_manifest`.
    The message is always specific (``required field 'name' missing``,
    ``kind must be one of [...]``, etc.) so users can fix the YAML
    without diving into the parser source.
    """


class AddonLoadError(AddonError):
    """Importing the add-on's ``__init__.py`` or calling ``register()``
    raised an exception.

    Wraps the underlying exception in ``__cause__`` (set via
    ``raise X from Y``) so the original traceback survives. The
    loader's log line shows both the wrapper message AND the cause's
    repr — operators don't need to dig through tracebacks for the
    common case.
    """


class AddonRequirementError(AddonError):
    """An add-on's ``requires:`` block isn't satisfied.

    Examples: missing MCP server in ``~/.vexis/mcp-servers.yaml``,
    missing env var, vexis-agent version below the minimum. Logged
    as a warning (not error) because the add-on is well-formed; it
    just can't run in this environment. ``vexis addons doctor`` exists
    to surface these proactively.
    """


class AddonConflictError(AddonError):
    """Two add-ons want to register the same name.

    Examples: two add-ons both register a ``/codemux`` Telegram
    command, or both claim the ``codemux`` watcher source-type. The
    loader logs the conflict and rejects the SECOND registration
    (first wins, matching the discovery precedence order
    bundled→user→project).
    """

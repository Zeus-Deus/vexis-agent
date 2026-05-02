"""Vexis browser-control package.

Wraps `browser-use` (https://browser-use.com) into a small set of
Vexis tools the brain can call via Bash. The package is structured
around a singleton ``SessionManager`` that holds at most one live
``BrowserSession`` per Vexis daemon process.

Public entry points:

- ``SessionManager`` / ``get_manager``: singleton accessor.
- ``BrowserTools``: the six action methods wired to control-socket ops.

CLI access is via ``scripts/vexis-browse``; daemon registration is in
``main.py``. See ``CAPABILITIES.md`` for the brain-facing docs.
"""

from __future__ import annotations

from tools.browser.session import SessionManager, get_manager
from tools.browser.tools import BrowserTools

__all__ = ["BrowserTools", "SessionManager", "get_manager"]

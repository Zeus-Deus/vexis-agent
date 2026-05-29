"""Vexis browser-control package.

Wraps scrapling's Camoufox-backed ``StealthySession``
(https://github.com/D4Vinci/Scrapling) into a small set of Vexis tools
the brain can call via Bash. The package is structured around a singleton
``SessionManager`` that holds at most one live stealth session — and one
persistent page on top of it — per Vexis daemon process. This is the
browser, not a fallback: it's stealthy by default.

Public entry points:

- ``SessionManager`` / ``get_manager``: singleton accessor.
- ``BrowserTools``: the six action methods wired to control-socket ops.

CLI access is via ``scripts/vexis-browse``; daemon registration is in
``main.py``. See ``CAPABILITIES.md`` for the brain-facing docs.
"""

from __future__ import annotations

from vexis_agent.tools.browser.session import SessionManager, get_manager
from vexis_agent.tools.browser.tools import BrowserTools

__all__ = ["BrowserTools", "SessionManager", "get_manager"]

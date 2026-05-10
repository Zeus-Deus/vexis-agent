"""Shipped data files. Two distinct purposes:

* **Setup-wizard templates** (``config.example.yaml``, ``dotenv.example``):
  copied to ``$VEXIS_HOME`` on first run. Repo-root copies
  (``config.example.yaml``, ``.env.example``) are human-browsable and
  pinned to byte-for-byte parity by
  ``tests/test_data_examples_consistency.py``.

* **Runtime resources** (``CAPABILITIES.md``): read on every brain
  system-prompt build. Lives here — not at the repo root — so
  pipx-installed users (no source checkout) still get the file via
  the wheel.

Read via ``importlib.resources``; never assume a source-checkout path.
"""

from __future__ import annotations

from importlib import resources
from typing import Optional


def read_text(name: str) -> Optional[str]:
    """Read a shipped data file. Returns None if it isn't bundled —
    callers decide whether that's a soft warning (CAPABILITIES.md
    going missing) or a hard error (a template the wizard needs)."""
    try:
        return resources.files(__name__).joinpath(name).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None


def read_capabilities() -> Optional[str]:
    """Single source of truth for the shipped CAPABILITIES.md. Brain
    adapters and ``main.py``'s startup warning both call this rather
    than resolving paths off ``__file__`` (which broke during the
    Phase 2 package move and would break again under any future
    layout change)."""
    return read_text("CAPABILITIES.md")

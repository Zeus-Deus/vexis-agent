"""Bearer token for the web dashboard.

Generated fresh on every daemon start, written to ``~/.vexis/dashboard_token``
with mode 0600, surfaced via the Telegram ``/dashboard`` command. Old
tokens stop working as soon as the daemon restarts — there's no
revocation list because rotation is the revocation.

The token-in-URL pattern (``?token=...``) is acceptable here because the
dashboard is only reachable on the Tailscale-private surface, same
security model as Step 11's livestream URL. The browser redirects the
token into ``localStorage`` on first load and strips it from the URL.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from vexis_agent.core.paths import vexis_dir

log = logging.getLogger(__name__)

_TOKEN_FILENAME = "dashboard_token"
_TOKEN_BYTES = 32


def token_path() -> Path:
    return vexis_dir() / _TOKEN_FILENAME


def issue_token() -> str:
    """Generate a fresh token, persist it with mode 0600, and return it.

    The file is overwritten atomically — the old token is gone the moment
    the daemon restarts. Existing dashboard tabs in a browser see 401 on
    their next request and the user runs ``/dashboard`` again.
    """
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            fh.write(token)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    log.info("Issued fresh dashboard token at %s", path)
    return token


def read_token() -> str | None:
    """Read the live token, or None if no daemon is running.

    The Telegram ``/dashboard`` handler uses this to compose the URL —
    it never re-issues. The web server itself holds the token in memory
    after ``issue_token`` and validates against that, not against this
    file, so a hand-edit here can't widen the auth surface.
    """
    try:
        return token_path().read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        return None
    except OSError:
        log.warning("Could not read dashboard token", exc_info=True)
        return None


def clear_token() -> None:
    """Remove the token file. Called on clean daemon shutdown."""
    try:
        token_path().unlink()
    except FileNotFoundError:
        return
    except OSError:
        log.warning("Could not remove dashboard token file", exc_info=True)

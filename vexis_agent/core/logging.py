"""Root logger configuration: stderr + rotating file under XDG state dir.

Also home to :class:`SensitiveDataFilter` — the scrubber that keeps
text typed into browser form fields (passwords, login emails) out of
the logs. See :func:`redact_sensitive_logs` for the wiring rationale.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler

from vexis_agent.core.paths import state_dir

_FMT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# Matches a browser-use "Typed" log line and captures the quoted payload.
# browser-use emits these at INFO (e.g. `⌨️ Typed "<text>" into element
# with index 12`, also single-quoted on its tools/mcp paths) and only
# masks the text when the caller pre-flagged the field sensitive — which
# Vexis can't do for agent-driven logins. Greedy `.*` + DOTALL is
# deliberate: it runs to the last quote across newlines so a payload
# containing a quote over-redacts rather than risking a partial leak.
_TYPED_REDACT_RE = re.compile(r"(\bTyped\s+)(['\"]).*\2", re.DOTALL)


def _redact_typed(message: str) -> str:
    return _TYPED_REDACT_RE.sub(r"\1\2<redacted>\2", message)


class SensitiveDataFilter(logging.Filter):
    """Scrubs text typed into browser fields out of log records.

    This is a logging *filter*, not a change to the keystrokes — only
    the log record is rewritten, so browser-automation behaviour is
    untouched. Idempotent: re-redacting an already-redacted line is a
    no-op, so it is safe on a record that passes through several
    handlers.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            # A malformed record elsewhere must not take down logging.
            return True
        if "Typed" not in message:
            return True
        redacted = _redact_typed(message)
        if redacted != message:
            # Replace with the fully-formatted, scrubbed string and drop
            # the args so downstream getMessage() calls don't re-expand.
            record.msg = redacted
            record.args = ()
        return True


def redact_sensitive_logs(*logger_names: str) -> None:
    """Attach :class:`SensitiveDataFilter` to the named loggers' handlers.

    Filters go on the *handlers*, not the loggers: a logger-level filter
    only runs for records emitted directly on that logger, never for
    records propagated up from descendants — and browser-use's "Typed"
    lines come from descendant per-watchdog loggers. browser-use also
    sets ``propagate=False`` on its top-level logger and attaches its
    own handler, so those records never reach the root handlers either;
    filtering that handler is what closes the gap.

    Idempotent — skips a handler that already carries the filter — so it
    is safe to call on every browser-session start. That repeat call
    also self-heals if browser-use reconfigures its handlers.
    """
    for name in logger_names:
        for handler in logging.getLogger(name).handlers:
            if not any(
                isinstance(f, SensitiveDataFilter) for f in handler.filters
            ):
                handler.addFilter(SensitiveDataFilter())


def setup_logging(level: str) -> None:
    log_file = state_dir() / "vexis.log"

    root = logging.getLogger()
    root.setLevel(level)
    # Clear handlers in case setup_logging is called twice (tests, reload).
    root.handlers.clear()

    formatter = logging.Formatter(_FMT)
    # Defence in depth: scrub the root handlers too. This covers the
    # import-order path where browser-use's own setup_logging() sees
    # pre-existing root handlers, short-circuits, and leaves its records
    # propagating to root instead of to a handler of its own.
    redactor = SensitiveDataFilter()

    stderr = logging.StreamHandler()
    stderr.setFormatter(formatter)
    stderr.addFilter(redactor)
    root.addHandler(stderr)

    rotating = RotatingFileHandler(
        log_file, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    rotating.setFormatter(formatter)
    rotating.addFilter(redactor)
    root.addHandler(rotating)

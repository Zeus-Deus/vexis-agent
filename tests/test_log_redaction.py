"""Tests for core.logging sensitive-data redaction.

Pins ``SensitiveDataFilter`` / ``redact_sensitive_logs``: browser-use
emits ``Typed "<text>"`` lines at INFO and only masks the text when the
caller pre-flagged the field sensitive. Vexis drives agent logins where
it can't pre-flag, so the filter scrubs the quoted payload at the
handler boundary instead.

Leak reproduced from a real journal line:
    ⌨️ Typed "Magicquinn91!" into element with index 1504
"""

from __future__ import annotations

import logging

from vexis_agent.core.logging import (
    SensitiveDataFilter,
    redact_sensitive_logs,
)


def _record(msg: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord(
        name="browser_use.BrowserSession",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args or None,
        exc_info=None,
    )


def _apply(msg: str, *args: object) -> str:
    """Run the filter over a record and return its final message."""
    record = _record(msg, *args)
    assert SensitiveDataFilter().filter(record) is True
    return record.getMessage()


def test_redacts_double_quoted_typed_line() -> None:
    # Exact browser-use watchdog format for typing into an element.
    out = _apply('⌨️ Typed "Magicquinn91!" into element with index 1504')
    assert "Magicquinn91!" not in out
    assert out == '⌨️ Typed "<redacted>" into element with index 1504'


def test_redacts_single_quoted_typed_line() -> None:
    # browser-use's tools/service + mcp paths quote with single quotes.
    out = _apply("Typed 'hunter2' into element 7")
    assert "hunter2" not in out
    assert out == "Typed '<redacted>' into element 7"


def test_redacts_typed_to_the_page() -> None:
    # The current-focus typing path uses a different suffix.
    out = _apply('⌨️ Typed "s3cret" to the page (current focus)')
    assert "s3cret" not in out
    assert out == '⌨️ Typed "<redacted>" to the page (current focus)'


def test_redacts_login_email_too() -> None:
    out = _apply('⌨️ Typed "quinn@example.com" into element with index 1')
    assert "quinn@example.com" not in out


def test_non_typed_message_untouched() -> None:
    msg = "🔗 Navigated to https://www.tiktok.com/setting"
    assert _apply(msg) == msg


def test_typed_word_without_quotes_untouched() -> None:
    # The already-masked sensitive form (`Typed <sensitive>`) and any
    # other quote-free use of the word must pass through unchanged.
    for msg in ("⌨️ Typed <sensitive> into element with index 1",
                "Typed text dispatch completed"):
        assert _apply(msg) == msg


def test_idempotent() -> None:
    once = _apply('⌨️ Typed "secret" into element with index 1')
    twice = _apply(once)
    assert once == twice
    assert once.count("<redacted>") == 1


def test_payload_with_inner_quote_fully_redacted() -> None:
    # Greedy match runs to the last quote: a quote inside the typed
    # text over-redacts rather than leaking the tail.
    out = _apply('⌨️ Typed "a"b" into element with index 1')
    assert out == '⌨️ Typed "<redacted>" into element with index 1'


def test_percent_arg_record_is_handled() -> None:
    # Vexis' own loggers use %-style args; the filter must format the
    # record first and not choke on the interpolation.
    out = _apply('Typed "%s" into element with index 1', "secret")
    assert "secret" not in out
    assert out == 'Typed "<redacted>" into element with index 1'


def test_redact_sensitive_logs_scrubs_descendant_records() -> None:
    """redact_sensitive_logs must catch records from descendant loggers.

    This is the crux: browser-use's "Typed" lines come from descendant
    per-watchdog loggers and reach the top logger's handler by
    propagation, so the filter has to live on the *handler*.
    """
    logger_name = "browser_use_redaction_test"
    parent = logging.getLogger(logger_name)
    captured: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record.getMessage())

    handler = _Capture()
    parent.addHandler(handler)
    parent.setLevel(logging.INFO)
    parent.propagate = False
    try:
        redact_sensitive_logs(logger_name)
        # Idempotent: a second call must not stack a duplicate filter.
        redact_sensitive_logs(logger_name)
        assert sum(
            isinstance(f, SensitiveDataFilter) for f in handler.filters
        ) == 1

        logging.getLogger(logger_name + ".BrowserSession").info(
            '⌨️ Typed "topsecret" into element with index 9'
        )
        assert captured == [
            '⌨️ Typed "<redacted>" into element with index 9'
        ]
    finally:
        parent.removeHandler(handler)
        parent.handlers.clear()

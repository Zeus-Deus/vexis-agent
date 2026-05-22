"""Tests for transient-network retry on outbound Telegram sends.

Pins ``_send_telegram_with_retry`` in transports.telegram: a long-lived
bot hits occasional connect/read timeouts posting to api.telegram.org,
and python-telegram-bot does not retry outbound sends. The helper
retries ``TimedOut`` / ``NetworkError`` with short linear backoff so a
brief blip self-heals instead of surfacing as "Something broke", while
leaving permanent errors (``BadRequest`` 4xx) and ``RetryAfter``
untouched.

Sync test functions calling ``asyncio.run()`` — matches the style of
test_telegram_streaming.py / test_telegram_transport.py.
"""

from __future__ import annotations

import asyncio

import pytest
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut

from vexis_agent.transports import telegram as tg

_CHAT = 4242


@pytest.fixture(autouse=True)
def _instant_backoff(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Make retry backoff instant and capture the delays requested.

    Patches ``asyncio.sleep`` (the real symbol the helper awaits) so
    the suite never actually waits, and returns the recording list so
    a test can assert the backoff schedule.
    """
    delays: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    return delays


def _factory(*results: object):
    """Build a make_call replaying ``results`` in order.

    Each entry is raised if it is an exception, else returned. The
    returned callable exposes ``.attempts`` (an int counter) so tests
    can assert how many times the helper invoked it.
    """
    seq = iter(results)

    async def _call() -> object:
        _call.attempts += 1  # type: ignore[attr-defined]
        item = next(seq)
        if isinstance(item, BaseException):
            raise item
        return item

    _call.attempts = 0  # type: ignore[attr-defined]
    return _call


def test_succeeds_first_attempt_no_retry(_instant_backoff: list[float]) -> None:
    call = _factory("ok")
    result = asyncio.run(
        tg._send_telegram_with_retry("unit", _CHAT, call)
    )
    assert result == "ok"
    assert call.attempts == 1
    assert _instant_backoff == []  # no backoff when nothing failed


def test_retries_timed_out_then_succeeds(
    _instant_backoff: list[float],
) -> None:
    call = _factory(TimedOut(), TimedOut(), "recovered")
    result = asyncio.run(
        tg._send_telegram_with_retry("unit", _CHAT, call)
    )
    assert result == "recovered"
    assert call.attempts == 3
    # Linear backoff: attempt*base — two sleeps before the 3rd try.
    assert _instant_backoff == [
        tg._TELEGRAM_SEND_BACKOFF_SECONDS * 1,
        tg._TELEGRAM_SEND_BACKOFF_SECONDS * 2,
    ]


def test_bare_network_error_is_retried(
    _instant_backoff: list[float],
) -> None:
    call = _factory(NetworkError("blip"), "recovered")
    result = asyncio.run(
        tg._send_telegram_with_retry("unit", _CHAT, call)
    )
    assert result == "recovered"
    assert call.attempts == 2


def test_exhausts_retries_then_raises_last_error(
    _instant_backoff: list[float],
) -> None:
    call = _factory(TimedOut(), TimedOut(), TimedOut())
    with pytest.raises(TimedOut):
        asyncio.run(tg._send_telegram_with_retry("unit", _CHAT, call))
    # All attempts used; backoff slept only between attempts (not after
    # the final failure).
    assert call.attempts == tg._TELEGRAM_SEND_ATTEMPTS
    assert len(_instant_backoff) == tg._TELEGRAM_SEND_ATTEMPTS - 1


def test_bad_request_is_not_retried(_instant_backoff: list[float]) -> None:
    # BadRequest subclasses NetworkError but is a permanent 4xx — it
    # must re-raise on the first attempt with no backoff.
    call = _factory(BadRequest("message is not modified"))
    with pytest.raises(BadRequest):
        asyncio.run(tg._send_telegram_with_retry("unit", _CHAT, call))
    assert call.attempts == 1
    assert _instant_backoff == []


def test_retry_after_propagates_immediately(
    _instant_backoff: list[float],
) -> None:
    # RetryAfter is rate-limiting, not a transient blip — propagate it
    # untouched rather than hammering through its server-set delay.
    call = _factory(RetryAfter(30))
    with pytest.raises(RetryAfter):
        asyncio.run(tg._send_telegram_with_retry("unit", _CHAT, call))
    assert call.attempts == 1
    assert _instant_backoff == []


def test_non_telegram_exception_propagates(
    _instant_backoff: list[float],
) -> None:
    call = _factory(RuntimeError("not a network fault"))
    with pytest.raises(RuntimeError):
        asyncio.run(tg._send_telegram_with_retry("unit", _CHAT, call))
    assert call.attempts == 1
    assert _instant_backoff == []

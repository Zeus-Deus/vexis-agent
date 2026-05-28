"""Respawn-backoff contract for the Codemux MCP stdio client.

Without backoff, a chronically-failing ``codemux mcp`` (segfault on
init, missing binary mid-uptime, …) would be hammered with one
fork-exec per poll tick × every watched workspace. The contract under
test:

  - First failure → next attempt allowed after ~1s.
  - Each consecutive failure doubles the cooldown.
  - Cap at ~60s; once the cap is hit, log once and stay there.
  - One successful call resets everything to zero.

The schedule is the same shape the docstring at
``_RESPAWN_BACKOFF_BASE_SECONDS`` documents. If you change the
numbers in mcp_client.py, change them here too — drift between
docstring and pin is a bug.
"""

from __future__ import annotations

from vexis_agent.addons.codemux.mcp_client import (
    _RESPAWN_BACKOFF_BASE_SECONDS,
    _RESPAWN_BACKOFF_MAX_SECONDS,
    CodemuxMcpClient,
)


def _client() -> CodemuxMcpClient:
    return CodemuxMcpClient(binary="codemux")


def test_backoff_schedule_doubles_per_failure():
    c = _client()
    assert c._backoff_seconds() == 0.0
    for n, expected in enumerate(
        [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0],
        start=1,
    ):
        c._consecutive_failures = n
        assert c._backoff_seconds() == expected, (
            f"failure #{n}: expected {expected}, got {c._backoff_seconds()}"
        )


def test_backoff_cap_matches_documented_value():
    """Pin: doubling stops at MAX, not above. Drift would surprise users."""
    c = _client()
    c._consecutive_failures = 100
    assert c._backoff_seconds() == _RESPAWN_BACKOFF_MAX_SECONDS


def test_record_success_resets_counter_and_cap_log():
    c = _client()
    c._consecutive_failures = 7
    c._next_attempt_at = 9999.0
    c._cap_logged = True
    c._record_success()
    assert c._consecutive_failures == 0
    assert c._next_attempt_at == 0.0
    assert c._cap_logged is False


def test_record_failure_arms_cooldown_window():
    """The cooldown is the count, doubled, capped, added to the monotonic clock."""
    import time as _time
    c = _client()
    for _ in range(3):
        c._record_failure()
    cooldown = c._backoff_seconds()
    assert cooldown == 4.0  # 1, 2, 4
    # ``_next_attempt_at`` lives in ``time.monotonic`` (the same clock
    # the client gates respawns against); the offset from "now" is
    # the cooldown we just computed. A tiny tolerance covers the
    # microseconds spent in this function.
    now = _time.monotonic()
    assert c._next_attempt_at - now > 3.5
    assert c._next_attempt_at - now <= 4.0 + 0.1


def test_cap_log_latched_so_we_dont_spam_warnings(caplog):
    """Hitting 60s many times in a row must produce ONE warning, not N."""
    import logging
    c = _client()
    with caplog.at_level(logging.WARNING, logger="vexis_agent.addons.codemux.mcp_client"):
        for _ in range(20):
            c._record_failure()
    cap_lines = [
        r for r in caplog.records
        if "respawn backoff hit" in r.getMessage()
    ]
    assert len(cap_lines) == 1, (
        f"cap warning should latch; got {len(cap_lines)} records"
    )


def test_first_failure_baseline_is_base_seconds():
    """Catches a future off-by-one where the first failure jumps to 2s."""
    c = _client()
    c._consecutive_failures = 1
    assert c._backoff_seconds() == _RESPAWN_BACKOFF_BASE_SECONDS

"""``vexis-agent schedule …`` CLI tests — Day 2.

Coverage:

  * Create round-trip — invoke create, parse JSON output, verify
    the schedule landed in the store.
  * List default excludes cleared; `--status all` includes them.
  * Show resolves by full id and by 3+-char prefix.
  * Pause / resume flip status; clear sets cleared.
  * Pause on cleared returns terminal_status error.
  * Create refuses on cap reached, prompt too long, parse error.
  * Recursion guard — VEXIS_SCHEDULED_FIRE=1 makes create refuse.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vexis_agent.cli_schedule import schedule_app
from vexis_agent.core.schedule_state import ScheduleStore


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def schedules_path(tmp_path, monkeypatch) -> Path:
    """Isolated schedules.json per test via VEXIS_SCHEDULES_PATH."""
    path = tmp_path / "schedules.json"
    monkeypatch.setenv("VEXIS_SCHEDULES_PATH", str(path))
    return path


# ──────────────────────────────────────────────────────────────────
# create
# ──────────────────────────────────────────────────────────────────


def test_create_interval_round_trip(runner, schedules_path):
    """Create an interval schedule, verify the store has it."""
    result = runner.invoke(
        schedule_app,
        [
            "create",
            "--expr", "every 30m",
            "--prompt", "remind me to stretch",
            "--chat-id", "12345",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload["ok"] is True
    assert "id" in payload
    assert payload["next_fire_at"] is not None

    # Verify in the store.
    store = ScheduleStore(schedules_path)
    state = store.load(payload["id"])
    assert state is not None
    assert state.prompt == "remind me to stretch"
    assert state.chat_id == 12345
    assert state.schedule["kind"] == "interval"
    assert state.schedule["minutes"] == 30


def test_create_cron_with_tz(runner, schedules_path):
    result = runner.invoke(
        schedule_app,
        [
            "create",
            "--expr", "0 9 * * 1-5",
            "--prompt", "weekday brief",
            "--chat-id", "12345",
            "--tz", "Asia/Tokyo",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload["tz"] == "Asia/Tokyo"


def test_create_parse_error_returns_brain_hint(runner, schedules_path):
    """Invalid expr → error JSON with suggestion field for the brain."""
    result = runner.invoke(
        schedule_app,
        [
            "create",
            "--expr", "every 30s",  # sub-minute reject
            "--prompt", "test",
            "--chat-id", "12345",
        ],
    )
    assert result.exit_code == 6
    err_payload = json.loads(result.output.strip())
    assert err_payload["error"] == "parse_error"
    assert "1m" in err_payload["suggestion"]


def test_create_prompt_too_long_refused(runner, schedules_path, monkeypatch):
    """Cap enforced on the create path."""
    # Force cap to 50 for the test.
    monkeypatch.setattr(
        "vexis_agent.core.yaml_config.schedules_max_prompt_length",
        lambda: 50,
    )
    result = runner.invoke(
        schedule_app,
        [
            "create",
            "--expr", "every 30m",
            "--prompt", "x" * 100,  # exceeds cap
            "--chat-id", "12345",
        ],
    )
    assert result.exit_code == 4
    err = json.loads(result.output.strip())
    assert err["error"] == "prompt_too_long"


def test_create_cap_reached(runner, schedules_path, monkeypatch):
    """``max_total`` cap enforced on create."""
    monkeypatch.setattr(
        "vexis_agent.core.yaml_config.schedules_max_total",
        lambda: 2,
    )
    # Create 2 schedules (at the cap).
    for i in range(2):
        result = runner.invoke(
            schedule_app,
            [
                "create",
                "--expr", "every 30m",
                "--prompt", f"prompt {i}",
                "--chat-id", "12345",
            ],
        )
        assert result.exit_code == 0

    # Third should refuse.
    result = runner.invoke(
        schedule_app,
        [
            "create",
            "--expr", "every 30m",
            "--prompt", "one too many",
            "--chat-id", "12345",
        ],
    )
    assert result.exit_code == 5
    err = json.loads(result.output.strip())
    assert err["error"] == "cap_reached"


def test_create_recursion_guard(runner, schedules_path, monkeypatch):
    """VEXIS_SCHEDULED_FIRE=1 → create refuses."""
    monkeypatch.setenv("VEXIS_SCHEDULED_FIRE", "1")
    result = runner.invoke(
        schedule_app,
        [
            "create",
            "--expr", "every 30m",
            "--prompt", "recursion attempt",
            "--chat-id", "12345",
        ],
    )
    assert result.exit_code == 3
    err = json.loads(result.output.strip())
    assert err["error"] == "scheduled_fire_recursion"


# ──────────────────────────────────────────────────────────────────
# list
# ──────────────────────────────────────────────────────────────────


def test_list_default_excludes_cleared(runner, schedules_path):
    """Cleared schedules don't show in the default list."""
    # Create two, clear one.
    r1 = runner.invoke(
        schedule_app,
        ["create", "--expr", "every 30m", "--prompt", "keep me", "--chat-id", "1"],
    )
    r2 = runner.invoke(
        schedule_app,
        ["create", "--expr", "every 30m", "--prompt", "clear me", "--chat-id", "1"],
    )
    id2 = json.loads(r2.output.strip())["id"]
    runner.invoke(schedule_app, ["clear", id2])

    result = runner.invoke(schedule_app, ["list", "--output", "json"])
    assert result.exit_code == 0
    rows = json.loads(result.output.strip())
    assert len(rows) == 1
    assert rows[0]["prompt"] == "keep me"


def test_list_all_includes_cleared(runner, schedules_path):
    runner.invoke(
        schedule_app,
        ["create", "--expr", "every 30m", "--prompt", "keep", "--chat-id", "1"],
    )
    r2 = runner.invoke(
        schedule_app,
        ["create", "--expr", "every 30m", "--prompt", "clear", "--chat-id", "1"],
    )
    runner.invoke(schedule_app, ["clear", json.loads(r2.output.strip())["id"]])

    result = runner.invoke(
        schedule_app, ["list", "--status", "all", "--output", "json"]
    )
    rows = json.loads(result.output.strip())
    assert len(rows) == 2


def test_list_empty(runner, schedules_path):
    result = runner.invoke(schedule_app, ["list"])
    assert result.exit_code == 0
    assert "No schedules" in result.output


# ──────────────────────────────────────────────────────────────────
# show / pause / resume / clear
# ──────────────────────────────────────────────────────────────────


def test_show_by_prefix(runner, schedules_path):
    r = runner.invoke(
        schedule_app,
        ["create", "--expr", "every 30m", "--prompt", "p", "--chat-id", "1"],
    )
    full_id = json.loads(r.output.strip())["id"]
    prefix = full_id[:6]

    result = runner.invoke(schedule_app, ["show", prefix, "--output", "json"])
    assert result.exit_code == 0
    state = json.loads(result.output.strip())
    assert state["id"] == full_id


def test_show_no_match(runner, schedules_path):
    result = runner.invoke(schedule_app, ["show", "nonexistent"])
    assert result.exit_code == 1
    assert "no schedule matches" in result.output


def test_pause_resume_cycle(runner, schedules_path):
    r = runner.invoke(
        schedule_app,
        ["create", "--expr", "every 30m", "--prompt", "p", "--chat-id", "1"],
    )
    sid = json.loads(r.output.strip())["id"]

    p = runner.invoke(schedule_app, ["pause", sid])
    assert p.exit_code == 0
    assert json.loads(p.output.strip())["status"] == "paused"

    r2 = runner.invoke(schedule_app, ["resume", sid])
    assert r2.exit_code == 0
    payload = json.loads(r2.output.strip())
    assert payload["status"] == "active"
    assert payload["next_fire_at"] is not None  # recomputed


def test_pause_on_cleared_returns_terminal(runner, schedules_path):
    r = runner.invoke(
        schedule_app,
        ["create", "--expr", "every 30m", "--prompt", "p", "--chat-id", "1"],
    )
    sid = json.loads(r.output.strip())["id"]
    runner.invoke(schedule_app, ["clear", sid])

    p = runner.invoke(schedule_app, ["pause", sid])
    assert p.exit_code == 8
    err = json.loads(p.output.strip())
    assert err["error"] == "terminal_status"


def test_clear_is_soft_record_retained(runner, schedules_path):
    r = runner.invoke(
        schedule_app,
        ["create", "--expr", "every 30m", "--prompt", "p", "--chat-id", "1"],
    )
    sid = json.loads(r.output.strip())["id"]
    runner.invoke(schedule_app, ["clear", sid])

    # Record still loadable.
    show = runner.invoke(schedule_app, ["show", sid, "--output", "json"])
    assert show.exit_code == 0
    state = json.loads(show.output.strip())
    assert state["status"] == "cleared"


# ──────────────────────────────────────────────────────────────────
# tick
# ──────────────────────────────────────────────────────────────────


def test_tick_requires_force(runner, schedules_path):
    """Tick without --force refuses (destructive guard)."""
    result = runner.invoke(schedule_app, ["tick"])
    assert result.exit_code == 9
    assert "destructive" in result.output


def test_tick_force_runs(runner, schedules_path):
    """Tick --force runs a tick — fires zero schedules since none due."""
    result = runner.invoke(schedule_app, ["tick", "--force"])
    assert result.exit_code == 0
    payload = json.loads(result.output.strip())
    assert payload["ok"] is True
    assert payload["fired"] == 0

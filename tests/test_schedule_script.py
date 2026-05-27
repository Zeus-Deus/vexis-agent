"""Pre-run script + wake gate tests — Issue #12.

Cross-references:
  * Issue #12 — design + acceptance criteria.
  * ``vexis_agent.core.schedule_manager.run_pre_run_script`` — runner.
  * ``vexis_agent.core.schedule_manager._fire_one`` — integration.

The five acceptance cases from the issue:

  1. Script outputs ``{"wakeAgent": false}`` → no enqueue is called.
  2. Script outputs ``{"wakeAgent": true}`` → enqueue proceeds with
     stdout prepended.
  3. Script outputs nothing → enqueue proceeds (default-to-wake).
  4. Script path outside ``~/.vexis/scripts/`` → rejected, no enqueue,
     error logged + recorded in ``last_error``.
  5. Script exceeds timeout → killed, no enqueue.

Backward-compat case (silent invariant): a schedule with
``script=None`` calls enqueue exactly like today — zero regression
in the existing critical path.

The ``_isolate_vexis_dir`` autofixture in conftest.py points
``vexis_dir()`` at a per-test tmpdir, so ``schedules_scripts_dir()``
returns ``<tmpdir>/scripts/`` and we can drop test scripts there
without polluting the real ~/.vexis/.
"""

from __future__ import annotations

import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from vexis_agent.core.paths import schedules_scripts_dir, vexis_dir
from vexis_agent.core.schedule_manager import (
    ScheduleManager,
    ScriptGatedError,
    ScriptPathError,
    ScriptTimeoutError,
    _parse_wake_gate,
    _strip_gate_line,
    prepend_script_output,
    run_pre_run_script,
)
from vexis_agent.core.schedule_state import (
    ScheduleState,
    ScheduleStore,
    new_schedule_id,
)
from vexis_agent.tools.schedule_tool.parser import parse_schedule


# ──────────────────────────────────────────────────────────────────
# Helpers — mirror tests/test_schedule_manager.py for consistency.
# ──────────────────────────────────────────────────────────────────


class _FakeRunningTasks:
    """Records enqueue calls; tested against without a real asyncio loop."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def enqueue(self, **kwargs) -> int:
        self.calls.append(kwargs)
        return len(self.calls)


def _patch_enqueue(manager: ScheduleManager, fake: _FakeRunningTasks):
    """Replace manager._enqueue_synthetic so the script-execution path
    is exercised but we don't need a real asyncio loop."""

    def _fake_enqueue(
        *, chat_id: int, text: str, schedule_id: str | None = None,
    ) -> bool:
        fake.enqueue(
            chat_id=chat_id,
            text=text,
            schedule_id=schedule_id,
        )
        return True

    return patch.object(manager, "_enqueue_synthetic", side_effect=_fake_enqueue)


def _write_script(name: str, body: str, *, executable: bool = True) -> Path:
    """Place ``body`` at ``~/.vexis/scripts/<name>`` and chmod +x."""
    path = schedules_scripts_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    if executable:
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _make_schedule(
    *,
    store: ScheduleStore,
    script: str | None = None,
    script_timeout_seconds: float = 120.0,
    next_fire_at: datetime,
    id: str | None = None,
    prompt: str = "ping me",
    chat_id: int = 12345,
) -> ScheduleState:
    parsed = parse_schedule("every 5m")
    state = ScheduleState(
        id=id or new_schedule_id(),
        chat_id=chat_id,
        schedule=parsed,
        schedule_display=parsed.get("display", "every 5m"),
        prompt=prompt,
        next_fire_at=next_fire_at,
        status="active",
        script=script,
        script_timeout_seconds=script_timeout_seconds,
    )
    store.save(state)
    return state


def _make_manager(
    store: ScheduleStore, fake: _FakeRunningTasks,
) -> ScheduleManager:
    return ScheduleManager(
        store,
        running_tasks=fake,  # type: ignore[arg-type]
        allowed_user_id=999,
        enabled_fn=lambda: True,
        tick_interval_seconds_fn=lambda: 30,
        max_consecutive_errors_fn=lambda: 5,
    )


# ──────────────────────────────────────────────────────────────────
# Pure unit tests for the wake-gate parser
# ──────────────────────────────────────────────────────────────────


class TestParseWakeGate:
    """``_parse_wake_gate`` decides whether to wake based on the last
    non-empty line of stdout. Default-to-wake on any malformed input.
    """

    def test_explicit_false_skips(self) -> None:
        wake, line = _parse_wake_gate('{"wakeAgent": false}')
        assert wake is False
        assert line == '{"wakeAgent": false}'

    def test_explicit_true_wakes(self) -> None:
        wake, line = _parse_wake_gate('{"wakeAgent": true}')
        assert wake is True
        assert line == '{"wakeAgent": true}'

    def test_empty_stdout_wakes(self) -> None:
        wake, line = _parse_wake_gate("")
        assert wake is True
        assert line == ""

    def test_whitespace_only_wakes(self) -> None:
        wake, line = _parse_wake_gate("   \n  \n")
        assert wake is True
        assert line == ""

    def test_non_json_last_line_wakes(self) -> None:
        wake, line = _parse_wake_gate("hello world\nno gate here")
        assert wake is True
        assert line == ""

    def test_json_without_wakeAgent_key_wakes(self) -> None:
        wake, line = _parse_wake_gate('{"other": "data"}')
        assert wake is True
        assert line == ""

    def test_finds_gate_in_last_non_empty_line(self) -> None:
        stdout = 'preceding payload\nmore stuff\n{"wakeAgent": false}\n\n'
        wake, line = _parse_wake_gate(stdout)
        assert wake is False
        assert line == '{"wakeAgent": false}'

    def test_json_array_at_end_wakes(self) -> None:
        # Array is JSON-valid but not a dict — gate requires a dict.
        wake, line = _parse_wake_gate('["not", "a", "gate"]')
        assert wake is True
        assert line == ""


class TestStripGateLine:
    def test_strips_matching_last_line(self) -> None:
        stdout = 'payload line\n{"wakeAgent": true}\n'
        out = _strip_gate_line(stdout, '{"wakeAgent": true}')
        assert out == "payload line"

    def test_no_gate_returns_unchanged(self) -> None:
        stdout = "payload line\n"
        out = _strip_gate_line(stdout, "")
        assert out == stdout


class TestPrependScriptOutput:
    def test_prepends_with_markers(self) -> None:
        out = prepend_script_output("orig prompt", "5 new emails")
        assert out.startswith("[script output]")
        assert "5 new emails" in out
        assert "[end script output]" in out
        assert out.endswith("orig prompt")

    def test_empty_stdout_returns_original(self) -> None:
        assert prepend_script_output("orig", "") == "orig"
        assert prepend_script_output("orig", "   \n") == "orig"


# ──────────────────────────────────────────────────────────────────
# Path confinement (acceptance case 4)
# ──────────────────────────────────────────────────────────────────


class TestScriptPathConfinement:
    """``run_pre_run_script`` must reject anything outside
    ``~/.vexis/scripts/`` BEFORE running. Path-traversal is the
    primary security gate."""

    def test_absolute_path_rejected(self) -> None:
        with pytest.raises(ScriptPathError):
            run_pre_run_script(
                "abc",
                script="/etc/passwd",
                timeout_seconds=10,
                schedule_name=None,
                tick_ts=datetime.now(timezone.utc),
            )

    def test_parent_traversal_rejected(self, tmp_path: Path) -> None:
        # Even if the file exists, ``..`` traversal lands outside the
        # scripts dir → ScriptPathError before subprocess fires.
        outside = vexis_dir().parent / "outside.sh"
        outside.write_text("#!/bin/bash\necho should not run\n")
        outside.chmod(0o755)
        with pytest.raises(ScriptPathError):
            run_pre_run_script(
                "abc",
                script="../outside.sh",
                timeout_seconds=10,
                schedule_name=None,
                tick_ts=datetime.now(timezone.utc),
            )

    def test_symlink_escape_rejected(self, tmp_path: Path) -> None:
        # A symlink inside the scripts dir pointing OUT must be
        # rejected. ``resolve()`` follows the symlink and
        # ``is_relative_to`` catches the escape.
        outside = tmp_path / "outside.sh"
        outside.write_text("#!/bin/bash\necho leaked\n")
        outside.chmod(0o755)
        link = schedules_scripts_dir() / "evil.sh"
        link.symlink_to(outside)
        with pytest.raises(ScriptPathError):
            run_pre_run_script(
                "abc",
                script="evil.sh",
                timeout_seconds=10,
                schedule_name=None,
                tick_ts=datetime.now(timezone.utc),
            )

    def test_missing_script_rejected(self) -> None:
        with pytest.raises(ScriptPathError):
            run_pre_run_script(
                "abc",
                script="does_not_exist.sh",
                timeout_seconds=10,
                schedule_name=None,
                tick_ts=datetime.now(timezone.utc),
            )

    def test_empty_script_rejected(self) -> None:
        with pytest.raises(ScriptPathError):
            run_pre_run_script(
                "abc",
                script="",
                timeout_seconds=10,
                schedule_name=None,
                tick_ts=datetime.now(timezone.utc),
            )


# ──────────────────────────────────────────────────────────────────
# Subprocess execution
# ──────────────────────────────────────────────────────────────────


class TestScriptExecution:
    """End-to-end exercise of the subprocess runner."""

    def test_wake_false_raises_gated(self) -> None:
        _write_script("gate_no.sh", "#!/bin/bash\necho '{\"wakeAgent\": false}'\n")
        with pytest.raises(ScriptGatedError):
            run_pre_run_script(
                "abc",
                script="gate_no.sh",
                timeout_seconds=10,
                schedule_name=None,
                tick_ts=datetime.now(timezone.utc),
            )

    def test_wake_true_returns_cleaned_stdout(self) -> None:
        body = (
            "#!/bin/bash\n"
            "echo 'found 3 new emails'\n"
            "echo '{\"wakeAgent\": true}'\n"
        )
        _write_script("gate_yes.sh", body)
        stdout, wake = run_pre_run_script(
            "abc",
            script="gate_yes.sh",
            timeout_seconds=10,
            schedule_name=None,
            tick_ts=datetime.now(timezone.utc),
        )
        assert wake is True
        assert "found 3 new emails" in stdout
        # The literal gate line is stripped — the brain should not see it.
        assert "wakeAgent" not in stdout

    def test_no_output_wakes_with_empty_stdout(self) -> None:
        _write_script("silent.sh", "#!/bin/bash\nexit 0\n")
        stdout, wake = run_pre_run_script(
            "abc",
            script="silent.sh",
            timeout_seconds=10,
            schedule_name=None,
            tick_ts=datetime.now(timezone.utc),
        )
        assert wake is True
        assert stdout == ""

    def test_timeout_kills_and_raises(self) -> None:
        # ``sleep 5`` with a 1-second timeout must abort.
        _write_script("hang.sh", "#!/bin/bash\nsleep 5\n")
        with pytest.raises(ScriptTimeoutError):
            run_pre_run_script(
                "abc",
                script="hang.sh",
                timeout_seconds=1,
                schedule_name=None,
                tick_ts=datetime.now(timezone.utc),
            )

    def test_env_vars_injected(self) -> None:
        body = (
            "#!/bin/bash\n"
            "echo \"id=$VEXIS_SCHEDULE_ID name=$VEXIS_SCHEDULE_NAME\"\n"
        )
        _write_script("envtest.sh", body)
        stdout, _wake = run_pre_run_script(
            "sched-abc-123",
            script="envtest.sh",
            timeout_seconds=10,
            schedule_name="my-monitor",
            tick_ts=datetime.now(timezone.utc),
        )
        assert "id=sched-abc-123" in stdout
        assert "name=my-monitor" in stdout

    def test_secrets_not_leaked_to_script_env(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The daemon may have ANTHROPIC_API_KEY in its env. The script
        subprocess must NOT receive it — defense against a buggy
        monitor scripting ``env | curl``.
        """
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret-do-not-leak")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "telegram-secret-do-not-leak")
        body = (
            "#!/bin/bash\n"
            "echo \"key=${ANTHROPIC_API_KEY:-MISSING} tg=${TELEGRAM_BOT_TOKEN:-MISSING}\"\n"
        )
        _write_script("envleak.sh", body)
        stdout, _wake = run_pre_run_script(
            "abc",
            script="envleak.sh",
            timeout_seconds=10,
            schedule_name=None,
            tick_ts=datetime.now(timezone.utc),
        )
        assert "key=MISSING" in stdout
        assert "tg=MISSING" in stdout
        assert "sk-test-secret-do-not-leak" not in stdout

    def test_python_script_runs_with_python3(self) -> None:
        body = (
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "print('hello from python')\n"
            "print(json.dumps({'wakeAgent': True}))\n"
        )
        _write_script("py_check.py", body)
        stdout, wake = run_pre_run_script(
            "abc",
            script="py_check.py",
            timeout_seconds=10,
            schedule_name=None,
            tick_ts=datetime.now(timezone.utc),
        )
        assert wake is True
        assert "hello from python" in stdout

    def test_nonzero_exit_still_wakes(self) -> None:
        """A failing monitor is a real signal — wake the brain so the
        user finds out, don't silently skip."""
        body = (
            "#!/bin/bash\n"
            "echo 'partial output before failure'\n"
            "echo 'something broke' >&2\n"
            "exit 2\n"
        )
        _write_script("crash.sh", body)
        stdout, wake = run_pre_run_script(
            "abc",
            script="crash.sh",
            timeout_seconds=10,
            schedule_name=None,
            tick_ts=datetime.now(timezone.utc),
        )
        assert wake is True
        assert "partial output before failure" in stdout
        # stderr is woven in so the user sees the failure cause.
        assert "something broke" in stdout


# ──────────────────────────────────────────────────────────────────
# End-to-end: _fire_one integration with the manager
# ──────────────────────────────────────────────────────────────────


class TestFireOneScriptIntegration:
    """The acceptance-criteria cases from Issue #12, asserted at the
    manager level — these are what guarantee the wake-gate actually
    skips the brain call."""

    def test_wake_false_skips_enqueue(self, tmp_path: Path) -> None:
        """Acceptance #1 — wakeAgent:false means brain.respond() is
        not called. Proved by mocking the enqueue call and asserting
        zero invocations."""
        _write_script(
            "no_wake.sh",
            "#!/bin/bash\necho '{\"wakeAgent\": false}'\n",
        )
        store = ScheduleStore(vexis_dir() / "schedules.json")
        now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
        state = _make_schedule(
            store=store,
            script="no_wake.sh",
            next_fire_at=now - timedelta(seconds=1),
        )

        fake = _FakeRunningTasks()
        manager = _make_manager(store, fake)
        with _patch_enqueue(manager, fake):
            fired = manager._run_once(now=now)

        # _fire_one returns False on a gated skip, so ``fired`` stays 0.
        assert fired == 0
        # Crucial assertion: NO enqueue call. This is the cost-savings
        # invariant — the brain never wakes up.
        assert fake.calls == []

        reloaded = store.load(state.id)
        assert reloaded is not None
        # Gated fire is recorded as success (no error) so the schedule
        # doesn't accumulate "errors" for steady-state polling.
        assert reloaded.last_status == "ok"
        assert reloaded.consecutive_errors == 0
        # next_fire_at must still have advanced — at-most-once contract.
        assert reloaded.next_fire_at is not None
        assert reloaded.next_fire_at > state.next_fire_at  # type: ignore[operator]

    def test_wake_true_enqueues_with_prepended_stdout(
        self, tmp_path: Path,
    ) -> None:
        """Acceptance #2 — wakeAgent:true means brain wakes AND stdout
        is prepended to the prompt the brain sees."""
        body = (
            "#!/bin/bash\n"
            "echo '7 new PRs in queue'\n"
            "echo '{\"wakeAgent\": true}'\n"
        )
        _write_script("yes_wake.sh", body)
        store = ScheduleStore(vexis_dir() / "schedules.json")
        now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
        _make_schedule(
            store=store,
            script="yes_wake.sh",
            next_fire_at=now - timedelta(seconds=1),
            prompt="summarise the PRs",
        )

        fake = _FakeRunningTasks()
        manager = _make_manager(store, fake)
        with _patch_enqueue(manager, fake):
            fired = manager._run_once(now=now)

        assert fired == 1
        assert len(fake.calls) == 1
        prepended = fake.calls[0]["text"]
        assert "[script output]" in prepended
        assert "7 new PRs in queue" in prepended
        assert "summarise the PRs" in prepended
        # Original prompt comes AFTER the prepended block.
        assert prepended.index("7 new PRs in queue") < prepended.index(
            "summarise the PRs"
        )
        # Gate line stripped — the brain doesn't see the literal JSON.
        assert "wakeAgent" not in prepended

    def test_empty_output_wakes(self, tmp_path: Path) -> None:
        """Acceptance #3 — script with no output is default-wake.
        A typo in the user's script must not silently disable the
        monitor."""
        _write_script("silent.sh", "#!/bin/bash\nexit 0\n")
        store = ScheduleStore(vexis_dir() / "schedules.json")
        now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
        _make_schedule(
            store=store,
            script="silent.sh",
            next_fire_at=now - timedelta(seconds=1),
            prompt="check the thing",
        )

        fake = _FakeRunningTasks()
        manager = _make_manager(store, fake)
        with _patch_enqueue(manager, fake):
            fired = manager._run_once(now=now)

        assert fired == 1
        assert len(fake.calls) == 1
        # No prepended banner since stdout was empty — brain just sees
        # the original prompt.
        assert fake.calls[0]["text"] == "check the thing"

    def test_path_escape_skips_and_records_error(self, tmp_path: Path) -> None:
        """Acceptance #4 — a schedule whose script escapes the scripts
        dir refuses to fire (no enqueue) AND records the rejection in
        ``last_error`` so the user can see what went wrong."""
        store = ScheduleStore(vexis_dir() / "schedules.json")
        now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
        state = _make_schedule(
            store=store,
            script="/etc/passwd",
            next_fire_at=now - timedelta(seconds=1),
        )

        fake = _FakeRunningTasks()
        manager = _make_manager(store, fake)
        with _patch_enqueue(manager, fake):
            fired = manager._run_once(now=now)

        assert fired == 0
        assert fake.calls == []

        reloaded = store.load(state.id)
        assert reloaded is not None
        assert reloaded.last_status == "error"
        assert reloaded.last_error is not None
        assert "script:" in reloaded.last_error
        # Critical: consecutive_errors is NOT incremented for script
        # path failures (would auto-pause a legitimate schedule
        # because the user's gate is buggy).
        assert reloaded.consecutive_errors == 0

    def test_timeout_skips_and_records_error(self, tmp_path: Path) -> None:
        """Acceptance #5 — timeout kills the script, brain skipped,
        error recorded but consecutive_errors NOT incremented."""
        _write_script("hang.sh", "#!/bin/bash\nsleep 5\n")
        store = ScheduleStore(vexis_dir() / "schedules.json")
        now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
        state = _make_schedule(
            store=store,
            script="hang.sh",
            script_timeout_seconds=1.0,
            next_fire_at=now - timedelta(seconds=1),
        )

        fake = _FakeRunningTasks()
        manager = _make_manager(store, fake)
        with _patch_enqueue(manager, fake):
            fired = manager._run_once(now=now)

        assert fired == 0
        assert fake.calls == []

        reloaded = store.load(state.id)
        assert reloaded is not None
        assert reloaded.last_status == "error"
        assert reloaded.last_error is not None
        assert "timed out" in reloaded.last_error
        assert reloaded.consecutive_errors == 0

    def test_backward_compat_no_script(self, tmp_path: Path) -> None:
        """Zero-regression case — schedule with ``script=None`` behaves
        EXACTLY like today: enqueue with the raw prompt, no script
        machinery in the path."""
        store = ScheduleStore(vexis_dir() / "schedules.json")
        now = datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
        _make_schedule(
            store=store,
            script=None,
            next_fire_at=now - timedelta(seconds=1),
            prompt="just the original prompt",
        )

        fake = _FakeRunningTasks()
        manager = _make_manager(store, fake)
        with _patch_enqueue(manager, fake):
            fired = manager._run_once(now=now)

        assert fired == 1
        assert len(fake.calls) == 1
        # Raw prompt, no [script output] banner.
        assert fake.calls[0]["text"] == "just the original prompt"
        assert "[script output]" not in fake.calls[0]["text"]


# ──────────────────────────────────────────────────────────────────
# Round-trip: ScheduleState (de)serialisation of new fields
# ──────────────────────────────────────────────────────────────────


class TestScheduleStateRoundTrip:
    def test_script_round_trips(self, tmp_path: Path) -> None:
        store = ScheduleStore(vexis_dir() / "schedules.json")
        state = _make_schedule(
            store=store,
            script="check_mail.sh",
            script_timeout_seconds=45.0,
            next_fire_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )
        reloaded = store.load(state.id)
        assert reloaded is not None
        assert reloaded.script == "check_mail.sh"
        assert reloaded.script_timeout_seconds == 45.0

    def test_legacy_row_without_script_loads(self, tmp_path: Path) -> None:
        """An on-disk row predating Issue #12 (no ``script`` field)
        must still load — defaults to script=None and timeout=120."""
        store = ScheduleStore(vexis_dir() / "schedules.json")
        # Hand-craft a row without script/script_timeout fields.
        store._mutate(
            lambda schedules: schedules.__setitem__(
                "legacyabcdef",
                {
                    "id": "legacyabcdef",
                    "chat_id": 1,
                    "schedule": {"kind": "interval", "minutes": 5},
                    "schedule_display": "every 5m",
                    "prompt": "test",
                    "status": "active",
                },
            )
        )
        reloaded = store.load("legacyabcdef")
        assert reloaded is not None
        assert reloaded.script is None
        assert reloaded.script_timeout_seconds == 120.0

    def test_negative_timeout_coerced_to_default(self, tmp_path: Path) -> None:
        """A bogus negative timeout on disk must not strand fires —
        the tolerant from_dict coerces back to default."""
        store = ScheduleStore(vexis_dir() / "schedules.json")
        store._mutate(
            lambda schedules: schedules.__setitem__(
                "negabcdef1234",
                {
                    "id": "negabcdef1234",
                    "chat_id": 1,
                    "schedule": {"kind": "interval", "minutes": 5},
                    "schedule_display": "every 5m",
                    "prompt": "test",
                    "status": "active",
                    "script": "foo.sh",
                    "script_timeout_seconds": -10.0,
                },
            )
        )
        reloaded = store.load("negabcdef1234")
        assert reloaded is not None
        assert reloaded.script_timeout_seconds == 120.0


# ──────────────────────────────────────────────────────────────────
# Telegram slash command parser — flag extraction
# ──────────────────────────────────────────────────────────────────


class TestTelegramSlashFlagParser:
    def test_extracts_script_long_form(self) -> None:
        from vexis_agent.transports.telegram import (
            _parse_schedule_script_flags,
        )
        cleaned, script, timeout = _parse_schedule_script_flags(
            "every 5m --script check_mail.sh ping if new mail"
        )
        assert script == "check_mail.sh"
        assert timeout is None
        assert "--script" not in cleaned
        assert "every 5m" in cleaned
        assert "ping if new mail" in cleaned

    def test_extracts_both_flags(self) -> None:
        from vexis_agent.transports.telegram import (
            _parse_schedule_script_flags,
        )
        cleaned, script, timeout = _parse_schedule_script_flags(
            "every 10m --script disk.sh --script-timeout 30 watch disk"
        )
        assert script == "disk.sh"
        assert timeout == "30"
        assert "--script" not in cleaned
        assert "--script-timeout" not in cleaned

    def test_no_flags_returns_text_unchanged(self) -> None:
        from vexis_agent.transports.telegram import (
            _parse_schedule_script_flags,
        )
        cleaned, script, timeout = _parse_schedule_script_flags(
            "every weekday at 9am do standup"
        )
        assert script is None
        assert timeout is None
        assert cleaned == "every weekday at 9am do standup"

    def test_equals_form(self) -> None:
        from vexis_agent.transports.telegram import (
            _parse_schedule_script_flags,
        )
        cleaned, script, timeout = _parse_schedule_script_flags(
            "every 5m --script=foo.sh --script-timeout=60 text"
        )
        assert script == "foo.sh"
        assert timeout == "60"

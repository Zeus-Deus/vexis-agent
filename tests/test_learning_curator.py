"""Tests for core/learning_curator.py.

Day 1 scope: ReviewedStore round-trip, the daemon tick's eligibility
+ cooldown + busy gates, the stubbed shadow-write, recursion guard,
status text, and the /learning telegram dispatcher. The real LLM
review subprocess lands in Day 2.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core import learning_curator as lc
from core.learning_curator import LearningController, ReviewRecord, ReviewedStore
from core.transcripts import claude_session_jsonl_dir, iter_messages


# ``review_fn`` injected into LearningController so tests don't spawn
# real ``claude -p``. Mirrors what Day 1's ``_stub_review`` did.
def _stub_review_fn(workspace, meta):
    n_messages = sum(1 for _ in iter_messages(meta.jsonl_path))
    today = lc._utc_now().strftime("%Y-%m-%d")
    short = meta.session_uuid[:8] if len(meta.session_uuid) >= 8 else meta.session_uuid
    entry = f"[learned {today}] (stub) reviewed session {short} — {n_messages} conversational messages parsed"
    lc._append_shadow_entry(workspace, entry)
    return f"stub: wrote 1 shadow entry ({n_messages} msgs)"


# --------------------------------------------------------------------
# Fixtures + helpers
# --------------------------------------------------------------------


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fresh ``$HOME`` and workspace per test. ``Path.home`` is also
    patched because ``vexis_dir()`` uses it directly."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    workspace = tmp_path / "vexis-workspace"
    (workspace / "memories").mkdir(parents=True)
    return workspace


def _stage_session(
    workspace: Path,
    uuid: str,
    last_ts: str,
    *,
    n_messages: int = 2,
) -> Path:
    """Write a fake JSONL with N user messages, last one at ``last_ts``.
    Earlier messages anchor at 08:00 so the eligibility math is
    obvious in tests."""
    pdir = claude_session_jsonl_dir(workspace)
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / f"{uuid}.jsonl"
    lines = []
    for i in range(n_messages):
        ts = last_ts if i == n_messages - 1 else "2026-05-02T08:00:00Z"
        lines.append(json.dumps({
            "type": "user",
            "uuid": f"m-{uuid}-{i}",
            "timestamp": ts,
            "message": {"role": "user", "content": f"msg {i} in {uuid}"},
        }))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _utc(year=2026, month=5, day=2, hour=11, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------
# ReviewedStore
# --------------------------------------------------------------------


def test_reviewed_store_round_trip(tmp_path):
    path = tmp_path / "reviewed.json"
    store = ReviewedStore(path)
    assert store.load() == {}

    when = _utc(hour=12)
    store.update(
        "abc",
        success=True,
        last_message_at_review_time=when - timedelta(hours=1),
        outcome="stub: wrote 1 entry",
        now=when,
    )

    records = store.load()
    assert "abc" in records
    rec = records["abc"]
    assert rec.last_reviewed_at == when
    assert rec.last_review_attempt_at == when
    assert rec.last_message_at_review_time == when - timedelta(hours=1)
    assert rec.outcome == "stub: wrote 1 entry"


def test_reviewed_store_failure_leaves_success_field_alone(tmp_path):
    """Failure path must NOT advance ``last_reviewed_at`` or
    ``last_message_at_review_time`` — that's how the eligibility
    gate stays open for retry after the cooldown."""
    path = tmp_path / "reviewed.json"
    store = ReviewedStore(path)
    when = _utc(hour=12)
    msg_ts = _utc(hour=11)
    store.update(
        "abc",
        success=False,
        last_message_at_review_time=msg_ts,
        outcome="error: spawn failed",
        now=when,
    )
    rec = store.load()["abc"]
    assert rec.last_reviewed_at is None
    assert rec.last_message_at_review_time is None
    assert rec.last_review_attempt_at == when
    assert rec.outcome == "error: spawn failed"


def test_reviewed_store_corrupt_file_treated_as_empty(tmp_path):
    path = tmp_path / "reviewed.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert ReviewedStore(path).load() == {}


# --------------------------------------------------------------------
# Daemon tick — happy path
# --------------------------------------------------------------------


def test_run_once_eligible_session_writes_shadow(env, monkeypatch):
    """An abandoned session 60 min idle gets reviewed; the stub
    writes one §-delimited entry to MEMORY-SHADOW.md."""
    workspace = env
    _stage_session(workspace, "abandoned", "2026-05-02T10:00:00Z")
    now = _utc(hour=11)
    monkeypatch.setattr(lc, "_utc_now", lambda: now)

    controller = LearningController(workspace=workspace, review_fn=_stub_review_fn)
    result = controller.run_now()

    assert "abandoned" in result.eligible
    assert "abandoned" in result.reviewed

    shadow = workspace / "memories" / "MEMORY-SHADOW.md"
    assert shadow.exists()
    content = shadow.read_text(encoding="utf-8")
    assert "[learned" in content
    assert "abandoned"[:8] in content
    assert "2 conversational messages parsed" in content


def test_run_once_writes_reviewed_json_with_message_snapshot(env, monkeypatch):
    """Successful review records both ``last_reviewed_at`` (= now)
    and ``last_message_at_review_time`` (= the session's actual
    last_message_timestamp). The latter is what makes resume detection
    work on the next tick."""
    workspace = env
    _stage_session(workspace, "abc", "2026-05-02T10:00:00Z")
    now = _utc(hour=11)
    monkeypatch.setattr(lc, "_utc_now", lambda: now)

    controller = LearningController(workspace=workspace, review_fn=_stub_review_fn)
    controller.run_now()

    records = controller._reviewed.load()
    assert "abc" in records
    rec = records["abc"]
    assert rec.last_reviewed_at == now
    # Snapshot is the JSONL's actual last-message timestamp, not now.
    assert rec.last_message_at_review_time == _utc(hour=10)


def test_run_once_skips_already_reviewed_no_new_messages(env, monkeypatch):
    workspace = env
    _stage_session(workspace, "abc", "2026-05-02T10:00:00Z")
    now = _utc(hour=11)
    monkeypatch.setattr(lc, "_utc_now", lambda: now)

    controller = LearningController(workspace=workspace, review_fn=_stub_review_fn)
    controller.run_now()

    # Second tick — same content, same timestamp — should not
    # re-enter eligibility.
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=12))
    result = controller.run_now()
    assert "abc" not in result.eligible


def test_run_once_re_eligible_after_user_resumes_session(env, monkeypatch):
    """User adds a new message after the last review — eligibility
    flips back on once the second idle period elapses."""
    workspace = env
    _stage_session(workspace, "abc", "2026-05-02T10:00:00Z")
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))

    controller = LearningController(workspace=workspace, review_fn=_stub_review_fn)
    controller.run_now()

    # User resumes; new message at 12:00.
    _stage_session(workspace, "abc", "2026-05-02T12:00:00Z", n_messages=3)
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=13))

    result = controller.run_now()
    assert "abc" in result.eligible
    assert "abc" in result.reviewed


# --------------------------------------------------------------------
# Daemon tick — failure cooldown
# --------------------------------------------------------------------


def test_failure_cooldown_skips_recent_failures(env, monkeypatch):
    """First tick: review raises, recorded as failure. Second tick
    within the cooldown window: skipped with reason 'cooldown'."""
    workspace = env
    _stage_session(workspace, "broken", "2026-05-02T10:00:00Z")

    def boom(*_a, **_kw):
        raise RuntimeError("simulated review failure")

    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))
    controller = LearningController(workspace=workspace, review_fn=boom)
    result1 = controller.run_now()

    assert "broken" in result1.eligible
    assert any(uuid == "broken" for uuid, _ in result1.skipped)

    # 10 minutes later — well within the 1-hour cooldown.
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11, minute=10))
    result2 = controller.run_now()
    assert any(reason == "cooldown" for _, reason in result2.skipped)


def test_failure_cooldown_lifts_after_window(env, monkeypatch):
    """After the cooldown window passes, a previously-failed session
    becomes eligible again."""
    workspace = env
    _stage_session(workspace, "broken", "2026-05-02T10:00:00Z")

    raised = {"count": 0}

    def boom(*_a, **_kw):
        raised["count"] += 1
        raise RuntimeError("simulated review failure")

    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))
    controller = LearningController(workspace=workspace, review_fn=boom)
    controller.run_now()

    # 2 hours later (cooldown is 1h) — retry attempted.
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=13))
    controller.run_now()
    assert raised["count"] == 2


# --------------------------------------------------------------------
# Recursion guard + lifecycle
# --------------------------------------------------------------------


def test_recursion_guard_prevents_start(env, monkeypatch):
    """When the env var is set (we're inside a review fork), start()
    refuses to launch the daemon thread."""
    monkeypatch.setenv("VEXIS_LEARNING_REVIEW", "1")
    controller = LearningController(workspace=env)
    controller.start()
    assert controller._thread is None


def test_disabled_via_config_prevents_start(env, monkeypatch):
    monkeypatch.setattr(lc, "learning_enabled", lambda: False)
    controller = LearningController(workspace=env)
    controller.start()
    assert controller._thread is None


# --------------------------------------------------------------------
# Status + telegram dispatcher
# --------------------------------------------------------------------


def test_status_text_reports_after_one_review(env, monkeypatch):
    workspace = env
    _stage_session(workspace, "abc", "2026-05-02T10:00:00Z")
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))
    controller = LearningController(workspace=workspace, review_fn=_stub_review_fn)
    controller.run_now()

    text = controller._status_text()
    assert "Learning curator: enabled" in text
    assert "Tracked sessions: 1" in text
    assert "Successful reviews: 1" in text


def test_handle_telegram_status(env):
    controller = LearningController(workspace=env)
    out = asyncio.run(controller.handle_telegram("status", []))
    assert "Learning curator" in out


def test_handle_telegram_pause_and_resume(env):
    controller = LearningController(workspace=env)
    paused = asyncio.run(controller.handle_telegram("pause", []))
    assert "paused" in paused.lower()
    assert lc.is_paused()
    resumed = asyncio.run(controller.handle_telegram("resume", []))
    assert "resumed" in resumed.lower()
    assert not lc.is_paused()


def test_handle_telegram_run_executes_tick(env, monkeypatch):
    workspace = env
    _stage_session(workspace, "abc", "2026-05-02T10:00:00Z")
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))
    controller = LearningController(workspace=workspace, review_fn=_stub_review_fn)
    out = asyncio.run(controller.handle_telegram("run", []))
    assert "Eligible: 1" in out
    assert "reviewed: 1" in out


def test_handle_telegram_unknown_subcommand(env):
    controller = LearningController(workspace=env)
    out = asyncio.run(controller.handle_telegram("nonsense", []))
    assert "Usage:" in out


# --------------------------------------------------------------------
# Day 3: recursion guard scan-diff
# --------------------------------------------------------------------


def test_review_one_tracks_spawned_uuids(env, monkeypatch):
    """When the review fork creates a new JSONL in the projects dir,
    the controller's _review_one wrapper should pick it up and add
    to _spawned_uuids."""
    workspace = env
    _stage_session(workspace, "abandoned", "2026-05-02T10:00:00Z")
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))

    pdir = claude_session_jsonl_dir(workspace)

    def fake_review_with_spawn(workspace_arg, meta):
        # Simulate claude -p creating a new session JSONL during review.
        new_path = pdir / "claude-spawned-session.jsonl"
        new_path.write_text('{"type":"user","timestamp":"2026-05-02T11:00:00Z","message":{"role":"user","content":"review prompt"}}\n', encoding="utf-8")
        return _stub_review_fn(workspace_arg, meta)

    controller = LearningController(workspace=workspace, review_fn=fake_review_with_spawn)
    controller.run_now()

    assert "claude-spawned-session" in controller._spawned_uuids


def test_review_one_no_new_uuid_means_empty_set(env, monkeypatch):
    """A review that doesn't spawn anything (e.g. a stub) leaves
    _spawned_uuids empty."""
    workspace = env
    _stage_session(workspace, "abc", "2026-05-02T10:00:00Z")
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))

    controller = LearningController(workspace=workspace, review_fn=_stub_review_fn)
    controller.run_now()
    assert controller._spawned_uuids == set()


def test_spawned_uuid_excluded_on_next_tick(env, monkeypatch):
    """The full chain: scan-diff captures a spawned UUID → next tick's
    list_eligible_sessions excludes it even though it would otherwise
    look eligible."""
    workspace = env
    pdir = claude_session_jsonl_dir(workspace)

    # Pre-stage a JSONL the curator pretends it spawned. Make it 60
    # min old so the idle gate would let it through.
    _stage_session(workspace, "ours", "2026-05-02T10:00:00Z")

    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))

    controller = LearningController(workspace=workspace, review_fn=_stub_review_fn)
    # Manually install the recursion guard as if a previous review
    # had already spawned this UUID.
    controller._spawned_uuids.add("ours")

    result = controller.run_now()
    assert "ours" not in result.eligible
    assert "ours" not in result.reviewed


# --------------------------------------------------------------------
# Day 3: per-tick REPORT.md + run.json
# --------------------------------------------------------------------


def test_tick_report_written_when_eligible(env, monkeypatch):
    workspace = env
    _stage_session(workspace, "abc", "2026-05-02T10:00:00Z")
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))

    controller = LearningController(workspace=workspace, review_fn=_stub_review_fn)
    controller.run_now()

    logs_root = lc.learning_logs_dir()
    runs = list(logs_root.iterdir())
    assert len(runs) == 1
    folder = runs[0]
    assert (folder / "REPORT.md").exists()
    assert (folder / "run.json").exists()

    md = (folder / "REPORT.md").read_text()
    assert "Learning curator tick" in md
    assert "abc" in md
    assert "stub: wrote" in md

    payload = json.loads((folder / "run.json").read_text())
    assert payload["eligible"] == ["abc"]
    assert payload["reviewed"] == ["abc"]
    assert payload["outcomes"][0]["session_uuid"] == "abc"


def test_tick_report_skipped_for_noop_tick(env, monkeypatch):
    """Empty tick (no eligible sessions) doesn't produce a report —
    log spam reduction. The heartbeat still updates state.json."""
    workspace = env
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))
    controller = LearningController(workspace=workspace, review_fn=_stub_review_fn)
    controller.run_now()

    logs_root = lc.learning_logs_dir()
    runs = list(logs_root.iterdir()) if logs_root.exists() else []
    assert runs == []


def test_tick_report_records_failure(env, monkeypatch):
    workspace = env
    _stage_session(workspace, "broken", "2026-05-02T10:00:00Z")
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))

    def boom(*_a, **_kw):
        raise RuntimeError("simulated failure")

    controller = LearningController(workspace=workspace, review_fn=boom)
    controller.run_now()

    logs_root = lc.learning_logs_dir()
    runs = list(logs_root.iterdir())
    assert len(runs) == 1
    payload = json.loads((runs[0] / "run.json").read_text())
    assert any("error" in entry["outcome"] for entry in payload["outcomes"])


# --------------------------------------------------------------------
# Day 3: /learning audit subcommand
# --------------------------------------------------------------------


def test_audit_text_no_data(env):
    controller = LearningController(workspace=env)
    out = asyncio.run(controller.handle_telegram("audit", []))
    assert "shadow" in out.lower() or "Nothing to audit" in out


def test_audit_text_after_one_review(env, monkeypatch):
    workspace = env
    _stage_session(workspace, "abc", "2026-05-02T10:00:00Z")
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))
    controller = LearningController(workspace=workspace, review_fn=_stub_review_fn)
    controller.run_now()

    out = asyncio.run(controller.handle_telegram("audit", []))
    assert "Shadow entries" in out
    assert "[learned" in out
    assert "MEMORY-SHADOW.md" in out


def test_audit_surfaces_skip_rate(env, monkeypatch):
    """When skipped sessions accumulate, the audit shows the rate
    plus the >10% warning band."""
    workspace = env
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))

    def declining_review(workspace_arg, meta):
        # Mimic what _real_review returns when transcript is too large.
        return f"skipped: transcript too large (300000 chars)"

    controller = LearningController(workspace=workspace, review_fn=declining_review)

    # Stage 3 abandoned sessions; all 3 will get the decline outcome.
    for i in range(3):
        _stage_session(workspace, f"big-{i}", "2026-05-02T10:00:00Z")
    controller.run_now()

    out = asyncio.run(controller.handle_telegram("audit", []))
    assert "Skip rate" in out
    assert "3/3" in out
    assert "100%" in out
    assert "Above 10%" in out


def test_audit_skip_rate_below_threshold_no_warning(env, monkeypatch):
    workspace = env
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))

    # 1 declined + 9 successful → 10% — exactly at threshold, no
    # warning fires (we want >10%, not >=10%).
    counter = {"i": 0}

    def mixed_review(workspace_arg, meta):
        counter["i"] += 1
        if counter["i"] == 1:
            return "skipped: transcript too large (250000 chars)"
        return _stub_review_fn(workspace_arg, meta)

    controller = LearningController(workspace=workspace, review_fn=mixed_review)

    for i in range(10):
        _stage_session(workspace, f"sess-{i}", "2026-05-02T10:00:00Z")
    controller.run_now()

    out = asyncio.run(controller.handle_telegram("audit", []))
    assert "Skip rate" in out
    assert "1/10" in out
    assert "10%" in out
    assert "Above 10%" not in out


# --------------------------------------------------------------------
# Day 3: outcome string for transcript-too-large
# --------------------------------------------------------------------


def test_real_review_returns_skipped_outcome_for_oversized(env, monkeypatch):
    """End-to-end: when run_review declines for size, _real_review
    returns the canonical skipped string and the controller advances
    last_reviewed_at (no exception, no cooldown).

    Uses many ~5KB messages so the tail-read still finds a recent
    timestamp (an individual message larger than the tail-read window
    would hide it — see test_oversized_single_message_caveat below)."""
    from core import learning_review as lr

    workspace = env
    pdir = claude_session_jsonl_dir(workspace)
    pdir.mkdir(parents=True, exist_ok=True)

    # ~50 messages × 5KB ≈ 250KB transcript after formatter overhead.
    chunk = "y" * 5_000
    lines = []
    for i in range(50):
        lines.append(json.dumps({
            "type": "user",
            "uuid": f"m-{i}",
            "timestamp": f"2026-05-02T10:{i:02d}:00Z",
            "message": {"role": "user", "content": f"chunk {i}: {chunk}"},
        }))
    (pdir / "huge.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Block the actual subprocess (we should never reach it).
    def spawn_should_not_run(argv, env_dict):
        raise AssertionError("LLM was called for an oversized transcript")
    monkeypatch.setattr(lr.subprocess, "run", spawn_should_not_run)

    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=12))
    controller = LearningController(workspace=workspace)
    result = controller.run_now()

    assert "huge" in result.reviewed  # success path advances reviewed.json
    records = controller._reviewed.load()
    assert "huge" in records
    assert records["huge"].outcome.startswith("skipped: transcript too large")
    assert records["huge"].last_reviewed_at is not None


def test_oversized_single_message_invisible_to_tail_read(env):
    """Known limitation: a single message larger than the tail-read
    window (8 KiB) hides ``last_message_timestamp`` because the
    tail-read fragment contains no parseable timestamp. The session
    is silently skipped from eligibility — not a hot loop, just
    invisible. Documented here so the limitation is tracked; future
    work could fall back to a full parse when the tail produces no
    timestamps."""
    workspace = env
    pdir = claude_session_jsonl_dir(workspace)
    pdir.mkdir(parents=True, exist_ok=True)

    massive_text = "z" * 50_000  # >> _TAIL_READ_BYTES of 8192
    (pdir / "monomsg.jsonl").write_text(json.dumps({
        "type": "user", "uuid": "u1",
        "timestamp": "2026-05-02T10:00:00Z",
        "message": {"role": "user", "content": massive_text},
    }) + "\n", encoding="utf-8")

    from core.transcripts import iter_session_metas
    metas = list(iter_session_metas(workspace))
    assert len(metas) == 1
    # Last message timestamp is None — tail-read missed it.
    assert metas[0].last_message_timestamp is None

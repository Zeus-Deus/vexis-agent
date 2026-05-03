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


# --------------------------------------------------------------------
# Day 2: dispatcher routing — class-based write paths
# --------------------------------------------------------------------


def _make_review_output_with(lessons: list[dict]):
    """Build a minimal ReviewOutput carrying ``lessons`` as the
    verified set. Lets us drive ``_write_verified`` directly without
    spinning up the full review subprocess."""
    from core.learning_review import ReviewOutput
    return ReviewOutput(verified_lessons=lessons)


def _meta(uuid: str = "sess-1") -> "SessionMeta":
    """Minimal SessionMeta for dispatcher tests. The dispatcher only
    reads ``session_uuid`` (Day 3 onwards, for the IDENTITY queue)."""
    from core.transcripts import SessionMeta
    return SessionMeta(
        session_uuid=uuid,
        jsonl_path=Path(f"/tmp/{uuid}.jsonl"),
        last_message_timestamp=_utc(hour=10),
        message_count_estimate=2,
    )


def test_dispatcher_routes_procedural_s3_to_staging(env):
    """Day 2: a verified PROCEDURAL/S3 lesson stages a new skill in
    .shadow/ (NOT MEMORY-SHADOW.md as a content target) and writes
    an audit-trail entry into MEMORY-SHADOW.md with the staging path."""
    workspace = env
    lesson = {
        "class": "PROCEDURAL",
        "lesson": "When listing time-bound options, filter ahead of now.",
        "evidence": "filter to upcoming items only please",
        "scope": "time-bound listings",
        "tier": "S3",
        "target": {
            "skill_name": "time-bound-listings",
            "new_skill_body": (
                "---\nname: time-bound-listings\n"
                "description: Filter time-bound options.\n"
                "origin: learning-curator\n---\n\n# Body\n"
            ),
        },
    }
    written = lc._write_verified(
        workspace, _make_review_output_with([lesson]), meta=_meta(), shadow=True
    )
    assert written.written == 1
    # The actual content lives in the staging tree:
    from core.learning_writes import shadow_skills_root
    staged = shadow_skills_root(workspace) / "time-bound-listings" / "SKILL.md"
    assert staged.exists()
    assert "origin: learning-curator" in staged.read_text(encoding="utf-8")
    # MEMORY-SHADOW.md gets the audit record with the Staged: pointer:
    audit = (workspace / "memories" / "MEMORY-SHADOW.md").read_text(encoding="utf-8")
    assert "Class: PROCEDURAL" in audit
    assert "Tier: S3" in audit
    assert "Staged:" in audit
    assert "time-bound-listings" in audit


def test_dispatcher_routes_procedural_s1_to_staging(env):
    """A PROCEDURAL/S1 patch stages a SKILL.md mutation in .shadow/."""
    workspace = env
    # Set up a live skill the patch can target:
    from core.skills import create_skill
    from core.paths import skills_dir
    create_skill(
        skills_dir(workspace),
        "comm-style",
        (
            "---\nname: comm-style\ndescription: Communication.\n---\n\n"
            "## Tone\nDefault is formal.\n"
        ),
    )
    lesson = {
        "class": "PROCEDURAL",
        "lesson": "Direct factual answers default to one line.",
        "evidence": "filter to upcoming items only please",
        "scope": "communication",
        "tier": "S1",
        "target": {
            "skill_name": "comm-style",
            "patch_old_string": "Default is formal.",
            "patch_new_string": "Default is formal.\n\n## Brevity\nDirect Q→one line.\n",
        },
    }
    written = lc._write_verified(
        workspace, _make_review_output_with([lesson]), meta=_meta(), shadow=True
    )
    assert written.written == 1
    from core.learning_writes import shadow_skills_root
    staged = shadow_skills_root(workspace) / "comm-style" / "SKILL.md"
    assert staged.exists()
    body = staged.read_text(encoding="utf-8")
    assert "## Brevity" in body
    # Live skill is unchanged (Day 2 never writes live):
    from core.paths import skills_dir as sd
    live = (sd(workspace) / "comm-style" / "SKILL.md").read_text(encoding="utf-8")
    assert "## Brevity" not in live
    # Audit shadow records both the lesson and the staging path:
    audit = (workspace / "memories" / "MEMORY-SHADOW.md").read_text(encoding="utf-8")
    assert "Tier: S1" in audit
    assert "comm-style" in audit


def test_dispatcher_routes_situational_to_memory_shadow(env):
    """SITUATIONAL goes to MEMORY-SHADOW.md as before — Day 2 does
    NOT route this class to the staging tree."""
    workspace = env
    lesson = {
        "class": "SITUATIONAL",
        "lesson": "Vexis runs on Hetzner VPS at 203.0.113.42.",
        "evidence": "filter to upcoming items only please",  # any user msg
        "scope": "environment",
    }
    written = lc._write_verified(
        workspace, _make_review_output_with([lesson]), meta=_meta(), shadow=True
    )
    assert written.written == 1
    audit = (workspace / "memories" / "MEMORY-SHADOW.md").read_text(encoding="utf-8")
    assert "Class: SITUATIONAL" in audit
    assert "Hetzner" in audit
    # No skill staging happened:
    from core.learning_writes import list_staged_skills
    assert list_staged_skills(workspace) == []


# --------------------------------------------------------------------
# Day 3: IDENTITY routes through the USER candidate queue
# --------------------------------------------------------------------


def test_dispatcher_identity_first_observation_queues_no_write(env):
    """Day 3 checkpoint #1: a one-shot IDENTITY signal queues the
    claim but does NOT write to USER-SHADOW.md. The cross-session
    threshold (≥2 distinct sessions in 30d) hasn't been met."""
    workspace = env
    lesson = {
        "class": "IDENTITY",
        "lesson": "User prefers concise responses.",
        "evidence": "filter to upcoming items only please",
        "scope": "communication preferences",
    }
    written = lc._write_verified(
        workspace, _make_review_output_with([lesson]), meta=_meta("sess-1"), shadow=True
    )
    assert written.written == 1
    # Audit shows the queued status:
    audit = (workspace / "memories" / "MEMORY-SHADOW.md").read_text(encoding="utf-8")
    assert "Class: IDENTITY" in audit
    assert "queued (1/2 session(s))" in audit
    # USER-SHADOW.md does NOT exist yet (no promotion):
    user_shadow = workspace / "memories" / "USER-SHADOW.md"
    assert not user_shadow.exists()
    # Queue file got the candidate:
    from core.user_candidates import UserCandidateStore
    from core.paths import user_candidates_path
    queue = UserCandidateStore(user_candidates_path()).list_all()
    assert len(queue) == 1
    assert queue[0].claim == "User prefers concise responses."
    assert queue[0].promoted_to_user_md is False


def test_dispatcher_identity_same_session_repeat_does_not_promote(env):
    """A second observation of the same claim from the SAME session
    must not promote — the threshold is by distinct session UUIDs,
    not occurrence count."""
    workspace = env
    lesson = {
        "class": "IDENTITY",
        "lesson": "User prefers concise responses.",
        "evidence": "filter to upcoming items only please",
        "scope": "x",
    }
    same_meta = _meta("sess-1")
    lc._write_verified(workspace, _make_review_output_with([lesson]), meta=same_meta, shadow=True)
    lc._write_verified(workspace, _make_review_output_with([lesson]), meta=same_meta, shadow=True)
    user_shadow = workspace / "memories" / "USER-SHADOW.md"
    assert not user_shadow.exists()


def test_dispatcher_identity_second_session_promotes(env):
    """Day 3 checkpoint #2: second observation in a DIFFERENT session
    crosses the threshold and promotes to USER-SHADOW.md."""
    workspace = env
    lesson = {
        "class": "IDENTITY",
        "lesson": "User prefers concise responses.",
        "evidence": "filter to upcoming items only please",
        "scope": "x",
    }
    # First session — queued, no promotion.
    lc._write_verified(workspace, _make_review_output_with([lesson]), meta=_meta("sess-1"), shadow=True)
    user_shadow = workspace / "memories" / "USER-SHADOW.md"
    assert not user_shadow.exists()
    # Second session — distinct UUID, threshold met, promotion.
    lc._write_verified(workspace, _make_review_output_with([lesson]), meta=_meta("sess-2"), shadow=True)
    assert user_shadow.exists()
    body = user_shadow.read_text(encoding="utf-8")
    assert "User prefers concise responses." in body
    assert "Sessions: 2" in body
    # Audit reflects the promotion on the second pass:
    audit = (workspace / "memories" / "MEMORY-SHADOW.md").read_text(encoding="utf-8")
    assert "promoted to USER-SHADOW.md" in audit
    # Queue marks the claim promoted:
    from core.user_candidates import UserCandidateStore
    from core.paths import user_candidates_path
    queue = UserCandidateStore(user_candidates_path()).get(
        "User prefers concise responses."
    )
    assert queue is not None
    assert queue.promoted_to_user_md is True


def test_dispatcher_identity_alias_path_attaches_to_existing_claim(env):
    """LLM emits target.user_claim_alias to dedupe paraphrases. A
    second-session observation aliased to an existing queue claim
    must promote that claim (not create a fresh one) when the
    threshold is met."""
    workspace = env
    # First session: fresh claim.
    fresh = {
        "class": "IDENTITY",
        "lesson": "User prefers concise responses for direct factual questions.",
        "evidence": "filter to upcoming items only please",
        "scope": "x",
    }
    lc._write_verified(workspace, _make_review_output_with([fresh]), meta=_meta("sess-1"), shadow=True)
    # Second session: paraphrased claim aliased to the first.
    aliased = {
        "class": "IDENTITY",
        "lesson": "User wants tight, no-preamble answers to direct queries.",
        "evidence": "filter to upcoming items only please",
        "scope": "x",
        "target": {
            "user_claim_alias": "User prefers concise responses for direct factual questions.",
        },
    }
    lc._write_verified(workspace, _make_review_output_with([aliased]), meta=_meta("sess-2"), shadow=True)
    # The original claim got promoted (not the paraphrase):
    user_shadow = workspace / "memories" / "USER-SHADOW.md"
    assert user_shadow.exists()
    body = user_shadow.read_text(encoding="utf-8")
    assert "User prefers concise responses for direct factual questions." in body
    assert "User wants tight, no-preamble answers" not in body
    # Queue has only one entry:
    from core.user_candidates import UserCandidateStore
    from core.paths import user_candidates_path
    queue = UserCandidateStore(user_candidates_path()).list_all()
    assert len(queue) == 1


def test_housekeeping_expires_stale_user_candidates_periodically(env, monkeypatch):
    """Day 3.5: every _HOUSEKEEPING_TICKS ticks the controller calls
    expire_stale on the queue. Fresh observations stay; observations
    older than the window are removed. Promoted claims are retained
    even when stale (audit trail)."""
    workspace = env
    from core.paths import user_candidates_path
    from core.user_candidates import UserCandidateStore, DEFAULT_WINDOW
    # Seed a stale unpromoted claim and a stale-but-promoted claim.
    store = UserCandidateStore(user_candidates_path())
    far_past = _utc(hour=10) - DEFAULT_WINDOW - timedelta(days=1)
    store.add_occurrence("stale-pending", "old-sess", "ev", now=far_past)
    store.add_occurrence("stale-promoted", "old-sess", "ev", now=far_past)
    store.add_occurrence("stale-promoted", "other-old", "ev", now=far_past)
    store.mark_promoted("stale-promoted", now=far_past)
    # Pre-flight: both exist.
    assert store.get("stale-pending") is not None
    assert store.get("stale-promoted") is not None

    # Run the controller's tick a few times — each call to _run_once
    # increments the tick counter; housekeeping fires when count %
    # _HOUSEKEEPING_TICKS == 0.
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=12))
    controller = LearningController(workspace=workspace, review_fn=_stub_review_fn)
    for _ in range(controller._HOUSEKEEPING_TICKS):
        controller._run_once()

    # Stale unpromoted claim removed; promoted claim retained.
    after = UserCandidateStore(user_candidates_path())
    assert after.get("stale-pending") is None
    assert after.get("stale-promoted") is not None


def test_tick_report_includes_write_summary_fields(env, monkeypatch):
    """Day 5: per-tick REPORT.md and run.json carry the new
    classification / tier / dedup / queue counts so the user can
    audit what the curator did this tick."""
    workspace = env
    _stage_session(workspace, "abc", "2026-05-02T10:00:00Z")
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))

    # Stub returns a full WriteSummary so we know exactly what
    # numbers should land in the report.
    summary = lc.WriteSummary(
        written=2,
        by_class={"PROCEDURAL": 1, "SITUATIONAL": 1},
        by_tier={"S3": 1, "MEM": 1},
        dedup_skipped=1,
        queue_added=0,
        queue_promoted=0,
        stage_refused=0,
    )

    def stub(workspace, meta):
        return ("stub: 2 entries", summary)

    controller = LearningController(workspace=workspace, review_fn=stub)
    controller.run_now()

    # Most-recent log dir
    from core.paths import learning_logs_dir
    tick_dirs = sorted(learning_logs_dir().iterdir(), reverse=True)
    assert tick_dirs, "expected at least one tick log dir"
    report_md = (tick_dirs[0] / "REPORT.md").read_text(encoding="utf-8")
    assert "Write summary (Day 5)" in report_md
    assert "total written: 2" in report_md
    assert "PROCEDURAL=1" in report_md
    assert "SITUATIONAL=1" in report_md
    assert "S3=1" in report_md
    assert "MEM=1" in report_md
    assert "dedup-skipped (memory): 1" in report_md
    # And run.json carries the structured form:
    run_json = json.loads((tick_dirs[0] / "run.json").read_text(encoding="utf-8"))
    assert run_json["summary"]["written"] == 2
    assert run_json["summary"]["by_class"]["PROCEDURAL"] == 1
    assert run_json["summary"]["dedup_skipped"] == 1


def test_audit_text_surfaces_curator_authored_skills(env, monkeypatch):
    """Day 5: /learning audit lists skills carrying
    ``origin: learning-curator*`` in their YAML frontmatter so the
    user can see what's been promoted vs hand-authored."""
    workspace = env
    from core.skills import create_skill
    from core.paths import skills_dir
    create_skill(
        skills_dir(workspace),
        "curator-made",
        (
            "---\n"
            "name: curator-made\n"
            "description: D.\n"
            "origin: learning-curator\n"
            "---\n\nB\n"
        ),
    )
    create_skill(
        skills_dir(workspace),
        "hand-made",
        "---\nname: hand-made\ndescription: D.\n---\n\nB\n",
    )
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))
    controller = LearningController(workspace=workspace, review_fn=_stub_review_fn)
    audit = controller._audit_text()
    assert "Curator-authored skills:" in audit
    assert "curator-made" in audit
    assert "hand-made" not in audit  # only the origin-tagged one shows


def test_audit_text_surfaces_user_candidate_queue(env, monkeypatch):
    """Day 5: /learning audit shows pending USER claims with
    distinct-session count and days-until-expiry."""
    workspace = env
    from core.user_candidates import UserCandidateStore
    from core.paths import user_candidates_path
    store = UserCandidateStore(user_candidates_path())
    store.add_occurrence("User prefers terse responses.", "sess-1", "ev",
                         now=_utc(hour=10))
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))
    controller = LearningController(workspace=workspace, review_fn=_stub_review_fn)
    audit = controller._audit_text()
    assert "USER candidate queue:" in audit
    assert "User prefers terse responses." in audit
    assert "1/2 sessions" in audit
    assert "until expiry" in audit


def test_audit_text_surfaces_recent_dedup_count(env, monkeypatch):
    """Day 5: /learning audit aggregates the dedup_skipped count
    from recent tick reports so the user can spot when the dedup
    gate is firing often (signal to retune the prompt)."""
    workspace = env
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))

    # Run two ticks with a stub that simulates dedup-skipped counts.
    summary1 = lc.WriteSummary(written=1, dedup_skipped=2)
    summary2 = lc.WriteSummary(written=0, dedup_skipped=1)

    def stub_with(s):
        def _stub(workspace, meta):
            return ("stub", s)
        return _stub

    _stage_session(workspace, "tick-a", "2026-05-02T10:00:00Z")
    LearningController(workspace=workspace, review_fn=stub_with(summary1)).run_now()
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=12))
    _stage_session(workspace, "tick-b", "2026-05-02T11:30:00Z")
    LearningController(workspace=workspace, review_fn=stub_with(summary2)).run_now()

    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=13))
    audit = LearningController(
        workspace=workspace, review_fn=_stub_review_fn,
    )._audit_text()
    assert "Dedup gate" in audit
    # 2 + 1 = 3 dedup skips across both tick reports
    assert "3 candidate(s) skipped" in audit


def test_dispatcher_identity_alias_to_unknown_claim_falls_back_to_fresh(env):
    """If the LLM hallucinates an alias target that doesn't exist in
    the queue, the dispatcher falls back to fresh insertion under
    the lesson's own text rather than dropping the observation."""
    workspace = env
    lesson = {
        "class": "IDENTITY",
        "lesson": "User prefers concise responses.",
        "evidence": "filter to upcoming items only please",
        "scope": "x",
        "target": {"user_claim_alias": "Nonexistent claim that was never seen."},
    }
    lc._write_verified(workspace, _make_review_output_with([lesson]), meta=_meta("sess-1"), shadow=True)
    from core.user_candidates import UserCandidateStore
    from core.paths import user_candidates_path
    queue = UserCandidateStore(user_candidates_path()).list_all()
    # Fresh claim under the lesson's own text:
    assert len(queue) == 1
    assert queue[0].claim == "User prefers concise responses."


def test_dispatcher_records_failed_stage_in_audit(env):
    """If skill staging refuses (e.g. live-tree collision for an S3),
    the dispatcher records the refusal in MEMORY-SHADOW.md so the
    user can see what the curator tried and why — but does NOT
    increment the written count."""
    workspace = env
    # Set up a live collision so the S3 stage will refuse:
    from core.skills import create_skill
    from core.paths import skills_dir
    create_skill(
        skills_dir(workspace),
        "occupied-name",
        "---\nname: occupied-name\ndescription: D.\n---\n\nB\n",
    )
    lesson = {
        "class": "PROCEDURAL",
        "lesson": "Some procedural rule.",
        "evidence": "filter to upcoming items only please",
        "scope": "x",
        "tier": "S3",
        "target": {
            "skill_name": "occupied-name",
            "new_skill_body": (
                "---\nname: occupied-name\ndescription: D.\n"
                "origin: learning-curator\n---\n\nB\n"
            ),
        },
    }
    written = lc._write_verified(
        workspace, _make_review_output_with([lesson]), meta=_meta(), shadow=True
    )
    assert written.written == 0  # the collision means nothing got staged
    assert written.stage_refused == 1  # … and the refusal was tallied
    audit = (workspace / "memories" / "MEMORY-SHADOW.md").read_text(encoding="utf-8")
    assert "Stage refused:" in audit
    assert "occupied-name" in audit


def test_format_lesson_entry_v2_metadata():
    """The audit entry surfaces Class + Tier (and skill-name / paths
    for the readable variant) so a user reviewing MEMORY-SHADOW.md
    can see what the curator INTENDED to write."""
    lesson = {
        "class": "PROCEDURAL",
        "lesson": "X",
        "evidence": "Y",
        "scope": "Z",
        "tier": "S3",
        "target": {"skill_name": "fresh-skill", "new_skill_body": "..."},
    }
    rendered = lc._format_lesson_entry(lesson)
    assert "Class: PROCEDURAL" in rendered
    assert "Tier: S3" in rendered
    assert "fresh-skill" in rendered


def test_format_lesson_entry_legacy_v1_shape_renders():
    """v1-shape entries (no class) still render legibly via the
    legacy three-line layout — defense-in-depth for reading any
    pre-v2 entries already in the shadow file."""
    lesson = {"lesson": "old style", "evidence": "ev", "scope": "sc"}
    rendered = lc._format_lesson_entry(lesson)
    assert "old style" in rendered
    assert "Scope: sc" in rendered
    assert "Evidence: ev" in rendered
    assert "Class:" not in rendered
    assert "Tier:" not in rendered

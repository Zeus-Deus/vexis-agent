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


# --------------------------------------------------------------------
# METRIC (audit A2 deferred): tier distribution surface
#
# /learning audit aggregates by_tier counts from run.json across the
# last N ticks so we can see "did S1 actually get picked or stay at
# 0?" — the metric the v2-hermes-verification audit named as the
# trigger for deferring A2 (LLM cannot read SKILL.md bodies → S1
# patches systematically lose to S2/S3 fallback).
# --------------------------------------------------------------------


def test_recent_tier_distribution_aggregates_by_tier_unit(env, monkeypatch):
    """Direct unit test of the aggregation helper."""
    workspace = env
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))

    summary1 = lc.WriteSummary(written=2, by_tier={"S2": 1, "MEM": 1})
    summary2 = lc.WriteSummary(written=2, by_tier={"S2": 1, "S3": 1})
    summary3 = lc.WriteSummary(written=1, by_tier={"USER": 1})

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
    _stage_session(workspace, "tick-c", "2026-05-02T12:30:00Z")
    LearningController(workspace=workspace, review_fn=stub_with(summary3)).run_now()

    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=14))
    controller = LearningController(workspace=workspace, review_fn=_stub_review_fn)
    counts, scanned = controller._recent_tier_distribution(window_ticks=72)
    assert scanned == 3
    assert counts == {"S2": 2, "MEM": 1, "S3": 1, "USER": 1}
    # No S1 picked — stays absent from the dict.
    assert "S1" not in counts


def test_audit_text_surfaces_tier_distribution(env, monkeypatch):
    """The audit surface renders S1/S2/S3/MEM/USER counts in stable
    order so a soak-week reader can scan for the S1=0 signal at a
    glance."""
    workspace = env
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))

    summary = lc.WriteSummary(written=3, by_tier={"S2": 2, "MEM": 1})

    def stub(workspace, meta):
        return ("stub", summary)

    _stage_session(workspace, "tick-a", "2026-05-02T10:00:00Z")
    LearningController(workspace=workspace, review_fn=stub).run_now()

    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=12))
    audit = LearningController(
        workspace=workspace, review_fn=_stub_review_fn,
    )._audit_text()
    assert "Tier distribution" in audit
    assert "S1=0" in audit
    assert "S2=2" in audit
    assert "S3=0" in audit
    assert "MEM=1" in audit
    assert "USER=0" in audit
    assert "3 writes" in audit


def test_audit_text_flags_s1_zero_when_procedural_volume_is_high(env, monkeypatch):
    """When ≥5 procedural writes have landed and S1 is still at 0,
    surface the audit-A2 warning so the user knows the deferred
    two-pass-review fix is empirically warranted."""
    workspace = env
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))

    # 6 procedural writes across 3 ticks, all S2/S3 — no S1.
    s1 = lc.WriteSummary(written=2, by_tier={"S2": 1, "S3": 1})
    s2 = lc.WriteSummary(written=2, by_tier={"S2": 2})
    s3 = lc.WriteSummary(written=2, by_tier={"S3": 2})

    def stub_with(s):
        def _stub(workspace, meta):
            return ("stub", s)
        return _stub

    _stage_session(workspace, "tick-a", "2026-05-02T10:00:00Z")
    LearningController(workspace=workspace, review_fn=stub_with(s1)).run_now()
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=12))
    _stage_session(workspace, "tick-b", "2026-05-02T11:30:00Z")
    LearningController(workspace=workspace, review_fn=stub_with(s2)).run_now()
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=13))
    _stage_session(workspace, "tick-c", "2026-05-02T12:30:00Z")
    LearningController(workspace=workspace, review_fn=stub_with(s3)).run_now()

    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=14))
    audit = LearningController(
        workspace=workspace, review_fn=_stub_review_fn,
    )._audit_text()
    assert "S1 at 0" in audit
    assert "two-pass review" in audit
    assert "v2-hermes-verification" in audit


def test_audit_text_does_not_flag_s1_zero_when_volume_is_low(env, monkeypatch):
    """The S1=0 warning must NOT fire on small samples — too noisy.
    Threshold is ≥5 procedural writes; below that we wait for more
    data before flagging."""
    workspace = env
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))

    # Only 2 procedural writes — below the warning threshold.
    summary = lc.WriteSummary(written=2, by_tier={"S2": 1, "S3": 1})

    def stub(workspace, meta):
        return ("stub", summary)

    _stage_session(workspace, "tick-a", "2026-05-02T10:00:00Z")
    LearningController(workspace=workspace, review_fn=stub).run_now()

    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=12))
    audit = LearningController(
        workspace=workspace, review_fn=_stub_review_fn,
    )._audit_text()
    # Distribution surfaces, but warning does not:
    assert "Tier distribution" in audit
    assert "S1 at 0" not in audit


def test_run_json_persists_by_tier_for_aggregation(env, monkeypatch):
    """Schema check: run.json's summary.by_tier is what the aggregation
    helper reads. Confirm the field is round-tripped through the tick
    report so future readers (dashboards, scripts/) can rely on it."""
    workspace = env
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))

    summary = lc.WriteSummary(written=1, by_tier={"S3": 1, "MEM": 0})

    def stub(workspace, meta):
        return ("stub", summary)

    _stage_session(workspace, "tick-a", "2026-05-02T10:00:00Z")
    LearningController(workspace=workspace, review_fn=stub).run_now()

    from core.paths import learning_logs_dir
    tick_dirs = sorted(learning_logs_dir().iterdir())
    assert len(tick_dirs) == 1
    payload = json.loads((tick_dirs[0] / "run.json").read_text(encoding="utf-8"))
    by_tier = payload["summary"]["by_tier"]
    assert by_tier.get("S3") == 1
    # by_tier keys are strings — defensive check for downstream
    # consumers that key on the schema.
    assert all(isinstance(k, str) for k in by_tier.keys())


# --------------------------------------------------------------------
# C2: in-process IDENTITY substring-overlap gate
#
# When the LLM doesn't emit ``target.user_claim_alias`` but the
# proposed claim text overlaps an existing queue claim (substring
# match either direction), the dispatcher folds the new occurrence
# into the existing claim rather than splitting the queue across
# paraphrases. Mirrors the SITUATIONAL exact-evidence dedup gate in
# ``learning_review._check_evidence_overlap``.
# --------------------------------------------------------------------


def test_check_claim_overlap_unit():
    """Direct unit test of the substring gate in both directions plus
    the no-overlap case.

    Note: the gate is strict substring (no punctuation normalization),
    matching the behavior of ``_check_evidence_overlap`` in
    learning_review.py. A trailing period on one side and not the
    other will NOT match — paraphrases differing in punctuation
    fall through to fresh insertion. The underlying assumption is
    that LLM-emitted claim text is consistent enough (and the
    ``target.user_claim_alias`` path remains for explicit aliases).
    """
    existing = [
        "User prefers terse responses to direct questions",
        "User runs Vexis on Hetzner",
    ]
    # New claim is a substring of an existing one → match the longer:
    assert lc._check_claim_overlap(
        "User prefers terse responses", existing
    ) == "User prefers terse responses to direct questions"
    # New claim contains an existing one → also match:
    assert lc._check_claim_overlap(
        "User runs Vexis on Hetzner behind Tailscale", existing
    ) == "User runs Vexis on Hetzner"
    # No overlap → None:
    assert lc._check_claim_overlap(
        "User works in Python primarily.", existing
    ) is None
    # Empty inputs → None:
    assert lc._check_claim_overlap("", existing) is None
    assert lc._check_claim_overlap("anything", []) is None
    # Exact-equal returns the matched text (caller may use it for
    # logging even when add_occurrence's dict-key collision would
    # also handle the dedup):
    assert lc._check_claim_overlap(
        "User runs Vexis on Hetzner", existing
    ) == "User runs Vexis on Hetzner"


def test_dispatcher_identity_overlap_gate_collapses_paraphrase_shorter_to_longer(env):
    """LLM didn't emit user_claim_alias; new claim is a substring of
    an existing queue claim. C2 gate folds the second occurrence into
    the existing accumulator so the threshold counts both sessions
    against the same claim."""
    workspace = env
    longer = {
        "class": "IDENTITY",
        "lesson": "User prefers terse responses to direct factual questions.",
        "evidence": "filter to upcoming items only please",
        "scope": "x",
    }
    lc._write_verified(workspace, _make_review_output_with([longer]),
                       meta=_meta("sess-1"), shadow=True)
    # Second session emits a shorter paraphrase — substring of the first.
    shorter = {
        "class": "IDENTITY",
        "lesson": "User prefers terse responses",
        "evidence": "filter to upcoming items only please",
        "scope": "x",
    }
    lc._write_verified(workspace, _make_review_output_with([shorter]),
                       meta=_meta("sess-2"), shadow=True)
    from core.user_candidates import UserCandidateStore
    from core.paths import user_candidates_path
    queue = UserCandidateStore(user_candidates_path()).list_all()
    # Single accumulator under the longer (existing) claim text:
    assert len(queue) == 1, (
        f"overlap gate should fold paraphrases; got {[c.claim for c in queue]}"
    )
    assert queue[0].claim == longer["lesson"]
    # Threshold met (2 distinct sessions) → promoted to USER-SHADOW.md.
    user_shadow = workspace / "memories" / "USER-SHADOW.md"
    assert user_shadow.exists()
    assert longer["lesson"] in user_shadow.read_text(encoding="utf-8")


def test_dispatcher_identity_overlap_gate_collapses_paraphrase_longer_to_shorter(env):
    """Reverse direction: existing queue claim is the shorter form;
    new claim contains it. The gate is bidirectional — fold into the
    existing accumulator regardless of which is the substring of
    which."""
    workspace = env
    shorter = {
        "class": "IDENTITY",
        "lesson": "User prefers terse responses",
        "evidence": "filter to upcoming items only please",
        "scope": "x",
    }
    lc._write_verified(workspace, _make_review_output_with([shorter]),
                       meta=_meta("sess-1"), shadow=True)
    longer = {
        "class": "IDENTITY",
        "lesson": "User prefers terse responses to direct factual questions.",
        "evidence": "filter to upcoming items only please",
        "scope": "x",
    }
    lc._write_verified(workspace, _make_review_output_with([longer]),
                       meta=_meta("sess-2"), shadow=True)
    from core.user_candidates import UserCandidateStore
    from core.paths import user_candidates_path
    queue = UserCandidateStore(user_candidates_path()).list_all()
    # Single accumulator under the SHORTER (existing) claim text —
    # the gate folds into whichever was already in the queue:
    assert len(queue) == 1
    assert queue[0].claim == shorter["lesson"]


def test_dispatcher_identity_overlap_gate_audit_message(env):
    """The audit trail in MEMORY-SHADOW.md distinguishes overlap-gate
    aliasing from LLM-emitted aliasing so the user can spot when the
    LLM is producing paraphrases the gate is folding (signal: tune
    the prompt's alias instructions)."""
    workspace = env
    first = {
        "class": "IDENTITY",
        "lesson": "User prefers concise responses for direct questions.",
        "evidence": "filter to upcoming items only please",
        "scope": "x",
    }
    lc._write_verified(workspace, _make_review_output_with([first]),
                       meta=_meta("sess-1"), shadow=True)
    paraphrase = {
        "class": "IDENTITY",
        "lesson": "User prefers concise responses",
        "evidence": "filter to upcoming items only please",
        "scope": "x",
    }
    lc._write_verified(workspace, _make_review_output_with([paraphrase]),
                       meta=_meta("sess-2"), shadow=True)
    audit = (workspace / "memories" / "MEMORY-SHADOW.md").read_text(encoding="utf-8")
    # Promoted on the second observation (threshold met via the gate):
    assert "promoted to USER-SHADOW.md" in audit


def test_dispatcher_identity_no_overlap_creates_separate_claims(env):
    """Two genuinely-distinct IDENTITY claims must NOT collapse into
    one queue entry — the overlap gate is a sieve, not a wall."""
    workspace = env
    a = {
        "class": "IDENTITY",
        "lesson": "User prefers terse responses.",
        "evidence": "filter to upcoming items only please",
        "scope": "x",
    }
    b = {
        "class": "IDENTITY",
        "lesson": "User works primarily in Python and TypeScript.",
        "evidence": "filter to upcoming items only please",
        "scope": "x",
    }
    lc._write_verified(workspace, _make_review_output_with([a]),
                       meta=_meta("sess-1"), shadow=True)
    lc._write_verified(workspace, _make_review_output_with([b]),
                       meta=_meta("sess-2"), shadow=True)
    from core.user_candidates import UserCandidateStore
    from core.paths import user_candidates_path
    queue = UserCandidateStore(user_candidates_path()).list_all()
    assert len(queue) == 2
    assert {c.claim for c in queue} == {a["lesson"], b["lesson"]}
    # Neither got promoted (each has only 1 session):
    user_shadow = workspace / "memories" / "USER-SHADOW.md"
    assert not user_shadow.exists()


def test_dispatcher_identity_overlap_does_not_override_explicit_alias(env):
    """When the LLM explicitly sets target.user_claim_alias, the
    overlap gate is bypassed — the explicit alias is canonical. This
    preserves the LLM's judgment when it picked a specific alias
    target rather than letting the substring gate second-guess it."""
    workspace = env
    a = {
        "class": "IDENTITY",
        "lesson": "User prefers concise responses.",
        "evidence": "filter to upcoming items only please",
        "scope": "x",
    }
    lc._write_verified(workspace, _make_review_output_with([a]),
                       meta=_meta("sess-1"), shadow=True)
    # The LLM emits an explicit alias whose text DIFFERS from any
    # substring overlap candidate. Substring gate must not fire —
    # explicit alias wins.
    aliased = {
        "class": "IDENTITY",
        "lesson": "User prefers concise responses",  # would substring-match
        "evidence": "filter to upcoming items only please",
        "scope": "x",
        "target": {
            "user_claim_alias": "User prefers concise responses.",
        },
    }
    lc._write_verified(workspace, _make_review_output_with([aliased]),
                       meta=_meta("sess-2"), shadow=True)
    from core.user_candidates import UserCandidateStore
    from core.paths import user_candidates_path
    queue = UserCandidateStore(user_candidates_path()).list_all()
    # One claim, threshold met, promoted (the alias won):
    assert len(queue) == 1
    assert queue[0].claim == a["lesson"]
    user_shadow = workspace / "memories" / "USER-SHADOW.md"
    assert user_shadow.exists()


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


# --------------------------------------------------------------------
# Day 6 (v3a): coherence judge integration
# --------------------------------------------------------------------
#
# Verifies the live shadow hook:
#   - run_coherence_judge fires per verified lesson when messages is
#     non-empty
#   - the verdict gets attached to the lesson dict
#   - the shadow file gets the inline Coherence: annotation per §3.4
#   - the WriteSummary aggregates per-flag detail + counts
#   - the per-tick REPORT.md surfaces the ## Coherence flags section
#   - /learning audit row mirrors the dedup row shape


from datetime import datetime as _datetime, timezone as _timezone
from unittest import mock

from core.coherence_judge import CoherenceVerdict
from core.transcripts import TranscriptMessage as _TranscriptMessage


def _tmsg(role: str, text: str, *, ts: str = "2026-05-02T10:00:00Z",
          uuid: str = "m1") -> _TranscriptMessage:
    return _TranscriptMessage(
        role=role,
        text=text,
        timestamp=_datetime.fromisoformat(ts.replace("Z", "+00:00")),
        uuid=uuid,
        tool_calls=(),
        raw={},
    )


def _proc_lesson(lesson_text: str = "When X, do Y.",
                 evidence: str = "do Y please") -> dict:
    return {
        "class": "PROCEDURAL",
        "lesson": lesson_text,
        "evidence": evidence,
        "scope": "X-related tasks",
        "tier": "S3",
        "target": {
            "skill_name": "x-handling",
            "new_skill_body": (
                "---\nname: x-handling\ndescription: Handle X.\n"
                "origin: learning-curator\n---\n\n# Body\n"
            ),
        },
    }


def test_coherence_coherent_verdict_silent_in_shadow(env):
    """A COHERENT verdict produces NO Coherence: line in the shadow
    file — silent on clean lessons keeps the file uncluttered."""
    workspace = env
    lesson = _proc_lesson()
    messages = [_tmsg("user", "do Y please")]
    with mock.patch(
        "core.learning_curator.run_coherence_judge",
        return_value=CoherenceVerdict.coherent(),
    ) as judge_mock:
        summary = lc._write_verified(
            workspace,
            _make_review_output_with([lesson]),
            meta=_meta(),
            shadow=True,
            messages=messages,
        )

    judge_mock.assert_called_once()
    # Verdict was attached to the lesson dict
    assert lesson["coherence"].verdict == "COHERENT"
    # No flag counts
    assert summary.coherence_flagged == 0
    assert summary.coherence_near_miss == 0
    assert summary.coherence_by_reason == {}
    assert summary.coherence_flags == []
    # Shadow file: lesson present but no Coherence: line
    audit = (workspace / "memories" / "MEMORY-SHADOW.md").read_text(encoding="utf-8")
    assert lesson["lesson"] in audit
    assert "Coherence:" not in audit


def test_coherence_incoherent_verdict_annotates_shadow(env):
    """An INCOHERENT verdict surfaces a FLAGGED line with the reason
    name (greppable: ``grep 'FLAGGED (mismatched-attribution)'``)."""
    workspace = env
    lesson = _proc_lesson()
    messages = [_tmsg("user", "do Y please")]
    verdict = CoherenceVerdict.incoherent(
        reason="mismatched-attribution",
        explanation="evidence is about Tailscale; lesson is about Python",
    )
    with mock.patch(
        "core.learning_curator.run_coherence_judge",
        return_value=verdict,
    ):
        summary = lc._write_verified(
            workspace,
            _make_review_output_with([lesson]),
            meta=_meta("sess-bad-1"),
            shadow=True,
            messages=messages,
        )

    assert summary.coherence_flagged == 1
    assert summary.coherence_near_miss == 0
    assert summary.coherence_by_reason == {"mismatched-attribution": 1}
    # Per-flag detail captured for the REPORT.md narrative
    assert len(summary.coherence_flags) == 1
    flag = summary.coherence_flags[0]
    assert flag["session_uuid"] == "sess-bad-1"
    assert flag["verdict"] == "INCOHERENT"
    assert flag["reason"] == "mismatched-attribution"
    assert "Tailscale" in flag["explanation"]
    # Shadow file has the FLAGGED line
    audit = (workspace / "memories" / "MEMORY-SHADOW.md").read_text(encoding="utf-8")
    assert "Coherence: FLAGGED (mismatched-attribution)" in audit
    assert "Tailscale" in audit


def test_coherence_near_miss_verdict_annotates_shadow(env):
    """NEAR_MISS_REVIEW gets the soft NEAR_MISS label."""
    workspace = env
    lesson = _proc_lesson()
    messages = [_tmsg("user", "do Y please")]
    verdict = CoherenceVerdict.near_miss(
        reason="narrow-one-shot",
        explanation="evidence is one tactical exchange",
    )
    with mock.patch(
        "core.learning_curator.run_coherence_judge",
        return_value=verdict,
    ):
        summary = lc._write_verified(
            workspace,
            _make_review_output_with([lesson]),
            meta=_meta(),
            shadow=True,
            messages=messages,
        )

    assert summary.coherence_flagged == 0
    assert summary.coherence_near_miss == 1
    assert summary.coherence_by_reason == {"narrow-one-shot": 1}
    audit = (workspace / "memories" / "MEMORY-SHADOW.md").read_text(encoding="utf-8")
    assert "Coherence: NEAR_MISS (narrow-one-shot)" in audit


def test_coherence_skipped_when_messages_empty(env):
    """Backward compat: callers that pass messages=None or [] don't
    invoke the judge. Keeps every legacy _write_verified call site
    working unchanged."""
    workspace = env
    lesson = _proc_lesson()
    with mock.patch(
        "core.learning_curator.run_coherence_judge"
    ) as judge_mock:
        # No messages kwarg → judge not called
        lc._write_verified(
            workspace,
            _make_review_output_with([lesson]),
            meta=_meta(),
            shadow=True,
        )
        assert judge_mock.call_count == 0
        # Empty messages list → also skipped
        lc._write_verified(
            workspace,
            _make_review_output_with([_proc_lesson("X2", "ev2")]),
            meta=_meta(),
            shadow=True,
            messages=[],
        )
        assert judge_mock.call_count == 0


def test_coherence_summary_aggregates_across_lessons(env):
    """Two verified lessons in one session → two judge calls; counts
    sum correctly."""
    workspace = env
    lessons = [
        _proc_lesson("first lesson", "evidence one"),
        _proc_lesson("second lesson", "evidence two"),
    ]
    # Make second lesson's skill name distinct so staging doesn't collide
    lessons[1]["target"]["skill_name"] = "x-handling-2"
    lessons[1]["target"]["new_skill_body"] = (
        "---\nname: x-handling-2\ndescription: Handle X variant.\n"
        "origin: learning-curator\n---\n\n# Body\n"
    )
    messages = [
        _tmsg("user", "evidence one", uuid="m1"),
        _tmsg("user", "evidence two", uuid="m2"),
    ]
    verdicts = [
        CoherenceVerdict.coherent(),
        CoherenceVerdict.incoherent(
            reason="hallucinated-inference",
            explanation="not in evidence",
        ),
    ]
    with mock.patch(
        "core.learning_curator.run_coherence_judge",
        side_effect=verdicts,
    ):
        summary = lc._write_verified(
            workspace,
            _make_review_output_with(lessons),
            meta=_meta("sess-mixed"),
            shadow=True,
            messages=messages,
        )

    assert summary.coherence_flagged == 1
    assert summary.coherence_near_miss == 0
    assert summary.coherence_by_reason == {"hallucinated-inference": 1}
    assert len(summary.coherence_flags) == 1
    assert summary.coherence_flags[0]["lesson_preview"].startswith("second")


def test_writesummary_to_dict_includes_coherence_block():
    """run.json contract: summary.coherence = {flagged, near_miss,
    by_reason}. Per the §3.4 brief shape."""
    s = lc.WriteSummary(
        coherence_flagged=2,
        coherence_near_miss=1,
        coherence_by_reason={"mismatched-attribution": 2,
                             "narrow-one-shot": 1},
    )
    out = s.to_dict()
    assert out["coherence"] == {
        "flagged": 2,
        "near_miss": 1,
        "by_reason": {"mismatched-attribution": 2,
                       "narrow-one-shot": 1},
    }


def test_writesummary_merge_aggregates_coherence_fields():
    """Per-session summaries roll up into the tick total via merge()."""
    a = lc.WriteSummary(
        coherence_flagged=1,
        coherence_by_reason={"mismatched-attribution": 1},
        coherence_flags=[{"session_uuid": "s1", "lesson_preview": "x",
                          "verdict": "INCOHERENT",
                          "reason": "mismatched-attribution",
                          "explanation": "..."}],
    )
    b = lc.WriteSummary(
        coherence_flagged=1,
        coherence_near_miss=2,
        coherence_by_reason={"mismatched-attribution": 1,
                             "narrow-one-shot": 2},
        coherence_flags=[{"session_uuid": "s2", "lesson_preview": "y",
                          "verdict": "INCOHERENT",
                          "reason": "mismatched-attribution",
                          "explanation": "..."}],
    )
    a.merge(b)
    assert a.coherence_flagged == 2
    assert a.coherence_near_miss == 2
    assert a.coherence_by_reason == {
        "mismatched-attribution": 2, "narrow-one-shot": 2,
    }
    assert len(a.coherence_flags) == 2


def test_format_coherence_line_silent_on_coherent():
    assert lc._format_coherence_line(None) is None
    assert lc._format_coherence_line(CoherenceVerdict.coherent()) is None


def test_format_coherence_line_renders_incoherent_and_near_miss():
    inc = CoherenceVerdict.incoherent("scope-overflow", "scope too broad")
    line = lc._format_coherence_line(inc)
    assert "FLAGGED (scope-overflow)" in line
    assert "scope too broad" in line

    nm = CoherenceVerdict.near_miss("other", "thin grounding")
    line = lc._format_coherence_line(nm)
    assert "NEAR_MISS (other)" in line
    assert "thin grounding" in line

    # NEAR_MISS_REVIEW with no reason — prefix omitted
    nm_no_reason = CoherenceVerdict.near_miss(None, "borderline")
    line = lc._format_coherence_line(nm_no_reason)
    assert "NEAR_MISS" in line
    assert "borderline" in line
    assert "()" not in line  # no empty parens for missing reason


def test_tick_report_includes_coherence_section_when_flags(env):
    """Per §3.4 #2: REPORT.md gets a ## Coherence flags section
    listing per-session flag detail."""
    workspace = env
    controller = LearningController(workspace, review_fn=_stub_review_fn)
    summary = lc.WriteSummary(
        written=2,
        coherence_flagged=1,
        coherence_near_miss=1,
        coherence_by_reason={"mismatched-attribution": 1, "other": 1},
        coherence_flags=[
            {"session_uuid": "sess-aaaa1234",
             "lesson_preview": "When invoking vexis-agent tools...",
             "verdict": "INCOHERENT",
             "reason": "mismatched-attribution",
             "explanation": "evidence is about Tailscale"},
            {"session_uuid": "sess-bbbb5678",
             "lesson_preview": "Always use ydotool fallback",
             "verdict": "NEAR_MISS_REVIEW",
             "reason": "other",
             "explanation": "thin grounding"},
        ],
    )
    result = lc.TickResult(
        eligible=["sess-aaaa1234", "sess-bbbb5678"],
        reviewed=["sess-aaaa1234", "sess-bbbb5678"],
        outcomes=[
            ("sess-aaaa1234", "wrote 1"),
            ("sess-bbbb5678", "wrote 1"),
        ],
        summary=summary,
    )
    started = _utc(hour=10)
    finished = _utc(hour=10, minute=1)
    folder = controller._write_tick_report(started, finished, result)
    report = (folder / "REPORT.md").read_text(encoding="utf-8")
    assert "## Coherence flags" in report
    assert "1 INCOHERENT, 1 NEAR_MISS_REVIEW" in report
    assert "by reason: mismatched-attribution=1, other=1" in report
    assert "FLAGGED (mismatched-attribution)" in report
    assert "NEAR_MISS (other)" in report
    assert "evidence is about Tailscale" in report
    # Run.json carries the aggregate per the brief shape
    run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    assert run["summary"]["coherence"] == {
        "flagged": 1, "near_miss": 1,
        "by_reason": {"mismatched-attribution": 1, "other": 1},
    }


def test_tick_report_omits_coherence_section_when_no_flags(env):
    """No flags this tick → no ## Coherence flags section. Keeps the
    report clean during normal operation when v3a is silent."""
    workspace = env
    controller = LearningController(workspace, review_fn=_stub_review_fn)
    result = lc.TickResult(
        eligible=["sess-clean"],
        reviewed=["sess-clean"],
        outcomes=[("sess-clean", "nothing to save")],
        summary=lc.WriteSummary(),  # all zeros
    )
    folder = controller._write_tick_report(
        _utc(hour=10), _utc(hour=10, minute=1), result,
    )
    report = (folder / "REPORT.md").read_text(encoding="utf-8")
    assert "## Coherence flags" not in report
    # Run.json still carries the empty coherence block (always present
    # in summary.to_dict — schema stability)
    run = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    assert run["summary"]["coherence"] == {
        "flagged": 0, "near_miss": 0, "by_reason": {},
    }


def test_audit_text_surfaces_coherence_row_when_flags(env):
    """`/learning audit` row mirrors the existing dedup row shape."""
    workspace = env
    controller = LearningController(workspace, review_fn=_stub_review_fn)
    # Synthesize a tick report with non-zero coherence counts so the
    # audit's _recent_coherence_flags helper picks them up.
    summary = lc.WriteSummary(
        written=2,
        coherence_flagged=2,
        coherence_near_miss=1,
        coherence_by_reason={"mismatched-attribution": 2, "narrow-one-shot": 1},
        coherence_flags=[
            {"session_uuid": "s1", "lesson_preview": "x",
             "verdict": "INCOHERENT", "reason": "mismatched-attribution",
             "explanation": "..."},
            {"session_uuid": "s2", "lesson_preview": "y",
             "verdict": "INCOHERENT", "reason": "mismatched-attribution",
             "explanation": "..."},
            {"session_uuid": "s3", "lesson_preview": "z",
             "verdict": "NEAR_MISS_REVIEW", "reason": "narrow-one-shot",
             "explanation": "..."},
        ],
    )
    result = lc.TickResult(
        eligible=["s1", "s2", "s3"],
        reviewed=["s1", "s2", "s3"],
        outcomes=[(u, "ok") for u in ("s1", "s2", "s3")],
        summary=summary,
    )
    controller._write_tick_report(
        _utc(hour=10), _utc(hour=10, minute=1), result,
    )
    audit = controller._audit_text()
    assert "Coherence flags (last 1 tick reports)" in audit
    assert "2 INCOHERENT, 1 NEAR_MISS_REVIEW" in audit
    assert "mismatched-attribution: 2" in audit
    assert "narrow-one-shot: 1" in audit


def test_audit_text_shows_zero_coherence_row_when_clean(env):
    """When tick reports exist but contain no flags, audit shows a
    'Coherence flags ... 0 entries flagged.' row — confirms the
    surface is alive even when v3a has nothing to flag."""
    workspace = env
    controller = LearningController(workspace, review_fn=_stub_review_fn)
    result = lc.TickResult(
        eligible=["s1"],
        reviewed=["s1"],
        outcomes=[("s1", "ok")],
        summary=lc.WriteSummary(written=1),  # no coherence flags
    )
    controller._write_tick_report(
        _utc(hour=10), _utc(hour=10, minute=1), result,
    )
    audit = controller._audit_text()
    assert "Coherence flags (last 1 tick reports): 0 entries flagged" in audit


def test_recent_coherence_flags_aggregates_across_ticks(env):
    """Helper sums INCOHERENT + NEAR_MISS_REVIEW + by_reason across
    the most recent N tick reports."""
    workspace = env
    controller = LearningController(workspace, review_fn=_stub_review_fn)
    # Tick 1: 1 INCOHERENT (mismatched), 0 near-miss
    s1 = lc.WriteSummary(
        coherence_flagged=1,
        coherence_by_reason={"mismatched-attribution": 1},
        coherence_flags=[{"session_uuid": "a", "lesson_preview": "x",
                          "verdict": "INCOHERENT",
                          "reason": "mismatched-attribution",
                          "explanation": "..."}],
    )
    controller._write_tick_report(
        _utc(hour=10), _utc(hour=10, minute=1),
        lc.TickResult(eligible=["a"], reviewed=["a"],
                       outcomes=[("a", "ok")], summary=s1),
    )
    # Tick 2: 1 INCOHERENT (narrow-one-shot), 2 NEAR_MISS (other, narrow)
    s2 = lc.WriteSummary(
        coherence_flagged=1,
        coherence_near_miss=2,
        coherence_by_reason={"narrow-one-shot": 2, "other": 1},
        coherence_flags=[{"session_uuid": "b", "lesson_preview": "y",
                          "verdict": "NEAR_MISS_REVIEW",
                          "reason": "narrow-one-shot",
                          "explanation": "..."}],
    )
    controller._write_tick_report(
        _utc(hour=11), _utc(hour=11, minute=1),
        lc.TickResult(eligible=["b"], reviewed=["b"],
                       outcomes=[("b", "ok")], summary=s2),
    )
    flagged, near_miss, by_reason, scanned = (
        controller._recent_coherence_flags(window_ticks=24)
    )
    assert flagged == 2
    assert near_miss == 2
    assert by_reason == {"mismatched-attribution": 1,
                         "narrow-one-shot": 2, "other": 1}
    assert scanned == 2


# --------------------------------------------------------------------
# Day 3 — manual /learning coherence-audit command
# --------------------------------------------------------------------


def test_parse_curator_entries_extracts_structured_dicts(env):
    """The shadow-file parser must round-trip the standard layout:
    [learned ...] header → lesson, then Class/Tier/Scope/Evidence
    metadata lines into matching dict keys."""
    workspace = env
    shadow = workspace / "memories" / "MEMORY-SHADOW.md"
    shadow.write_text(
        "[learned 2026-05-12] When listing time-bound options, filter ahead.\n"
        "  Class: PROCEDURAL\n"
        "  Tier: S3 (would create new skill: time-bound-listings)\n"
        "  Scope: time-bound listings\n"
        "  Evidence: filter to upcoming items only please\n"
        "\n§\n"
        "[learned 2026-05-13] User runs Vexis on Hetzner.\n"
        "  Class: SITUATIONAL\n"
        "  Scope: environment\n"
        "  Evidence: yeah this is on the Hetzner box\n"
        "\n§\n"
        "Hand-written entry without [learned tag — should be skipped\n",
        encoding="utf-8",
    )
    entries = lc._parse_curator_entries(shadow)
    assert len(entries) == 2
    assert entries[0]["lesson"].startswith("When listing time-bound")
    assert entries[0]["class"] == "PROCEDURAL"
    assert entries[0]["tier"] == "S3"
    assert entries[0]["scope"] == "time-bound listings"
    assert entries[0]["evidence"] == "filter to upcoming items only please"
    assert entries[1]["class"] == "SITUATIONAL"
    assert "tier" not in entries[1]


def test_parse_curator_entries_skips_entries_without_evidence(env):
    """Entries missing scope OR evidence can't be judged — skip them
    silently rather than passing partial data to the judge."""
    workspace = env
    shadow = workspace / "memories" / "MEMORY-SHADOW.md"
    shadow.write_text(
        "[learned 2026-05-12] half-baked entry without metadata\n"
        "\n§\n"
        "[learned 2026-05-13] Complete entry.\n"
        "  Scope: thing\n"
        "  Evidence: ev\n",
        encoding="utf-8",
    )
    entries = lc._parse_curator_entries(shadow)
    assert len(entries) == 1
    assert entries[0]["lesson"] == "Complete entry."


def test_coherence_audit_text_judges_shadow_entries(env):
    """Manual /learning coherence-audit walks shadow files, calls
    the judge in degraded mode (no transcript), and returns a chat-
    friendly summary plus a structured log."""
    workspace = env
    controller = LearningController(workspace, review_fn=_stub_review_fn)
    shadow = workspace / "memories" / "MEMORY-SHADOW.md"
    shadow.write_text(
        "[learned 2026-05-12] Clean rule about X.\n"
        "  Class: PROCEDURAL\n"
        "  Tier: S3\n"
        "  Scope: X-related\n"
        "  Evidence: do X please\n"
        "\n§\n"
        "[learned 2026-05-13] Bad lesson with mismatched evidence.\n"
        "  Class: PROCEDURAL\n"
        "  Tier: S3\n"
        "  Scope: Z-related\n"
        "  Evidence: completely unrelated quote\n",
        encoding="utf-8",
    )
    verdicts = [
        CoherenceVerdict.coherent(),
        CoherenceVerdict.incoherent(
            "mismatched-attribution",
            "lesson is about Z; evidence is unrelated",
        ),
    ]
    with mock.patch(
        "core.learning_curator.run_coherence_judge",
        side_effect=verdicts,
    ) as judge_mock:
        out = controller._coherence_audit_text(shadow_only=True)

    # Two judge calls (one per parsed entry)
    assert judge_mock.call_count == 2
    # Both were called with empty messages list (degraded mode)
    for call in judge_mock.call_args_list:
        _args, _ = call.args, call.kwargs
        assert _args[2] == []  # messages is positional arg [2]
    # Reply summary mentions counts and the flagged entry
    assert "1" in out  # at least one of the counts
    assert "COHERENT: 1" in out
    assert "INCOHERENT: 1" in out
    assert "FLAGGED" in out
    assert "mismatched-attribution" in out
    assert "Bad lesson" in out
    # Structured log persisted
    log_dir = lc.learning_logs_dir() / "coherence-audit"
    assert log_dir.exists()
    log_files = list(log_dir.glob("*.json"))
    assert len(log_files) == 1
    log = json.loads(log_files[0].read_text(encoding="utf-8"))
    assert log["shadow_only"] is True
    assert len(log["results"]) == 2
    assert log["results"][1]["verdict"] == "INCOHERENT"
    assert log["results"][1]["reason"] == "mismatched-attribution"


def test_coherence_audit_handles_empty_targets(env):
    """No shadow files → friendly empty-state message rather than
    trying to judge nothing."""
    workspace = env
    controller = LearningController(workspace, review_fn=_stub_review_fn)
    out = controller._coherence_audit_text(shadow_only=True)
    assert "No shadow" in out


def test_coherence_audit_skips_live_when_shadow_only(env):
    """--shadow-only restricts to shadow files; live MEMORY.md /
    USER.md curator-authored entries are skipped."""
    workspace = env
    controller = LearningController(workspace, review_fn=_stub_review_fn)
    # Live MEMORY.md with curator-authored entry
    live = workspace / "memories" / "MEMORY.md"
    live.write_text(
        "[learned 2026-05-01] Live curator entry.\n"
        "  Class: SITUATIONAL\n"
        "  Scope: env\n"
        "  Evidence: live ev\n",
        encoding="utf-8",
    )
    # Shadow MEMORY-SHADOW.md
    shadow = workspace / "memories" / "MEMORY-SHADOW.md"
    shadow.write_text(
        "[learned 2026-05-12] Shadow entry.\n"
        "  Class: SITUATIONAL\n"
        "  Scope: env\n"
        "  Evidence: shadow ev\n",
        encoding="utf-8",
    )
    with mock.patch(
        "core.learning_curator.run_coherence_judge",
        return_value=CoherenceVerdict.coherent(),
    ) as judge_mock:
        controller._coherence_audit_text(shadow_only=True)
    # Only the shadow entry got judged (1 call), not the live one
    assert judge_mock.call_count == 1


def test_coherence_audit_includes_live_when_not_shadow_only(env):
    """Default (shadow_only=False) walks live MEMORY.md and USER.md
    in addition to shadow files."""
    workspace = env
    controller = LearningController(workspace, review_fn=_stub_review_fn)
    live = workspace / "memories" / "MEMORY.md"
    live.write_text(
        "[learned 2026-05-01] Live entry.\n"
        "  Class: SITUATIONAL\n"
        "  Scope: env\n"
        "  Evidence: live ev\n",
        encoding="utf-8",
    )
    shadow = workspace / "memories" / "MEMORY-SHADOW.md"
    shadow.write_text(
        "[learned 2026-05-12] Shadow entry.\n"
        "  Class: SITUATIONAL\n"
        "  Scope: env\n"
        "  Evidence: shadow ev\n",
        encoding="utf-8",
    )
    with mock.patch(
        "core.learning_curator.run_coherence_judge",
        return_value=CoherenceVerdict.coherent(),
    ) as judge_mock:
        controller._coherence_audit_text(shadow_only=False)
    assert judge_mock.call_count == 2


def test_handle_telegram_dispatches_coherence_audit_subcommand(env):
    """The /learning coherence-audit command surface dispatches into
    the controller's helper."""
    workspace = env
    controller = LearningController(workspace, review_fn=_stub_review_fn)
    shadow = workspace / "memories" / "MEMORY-SHADOW.md"
    shadow.write_text(
        "[learned 2026-05-12] X.\n"
        "  Scope: y\n  Evidence: ev\n",
        encoding="utf-8",
    )
    with mock.patch(
        "core.learning_curator.run_coherence_judge",
        return_value=CoherenceVerdict.coherent(),
    ):
        out = asyncio.run(
            controller.handle_telegram("coherence-audit", ["--shadow-only"])
        )
    assert "Coherence audit" in out
    assert "COHERENT: 1" in out


def test_handle_telegram_unknown_subcommand_lists_coherence_audit(env):
    """Usage text mentions the new command so users discover it."""
    workspace = env
    controller = LearningController(workspace, review_fn=_stub_review_fn)
    out = asyncio.run(controller.handle_telegram("nonsense", []))
    assert "coherence-audit" in out


def test_end_to_end_coherent_then_incoherent_through_pipeline(env):
    """End-to-end Day 2 checkpoint: two ticks, one COHERENT lesson
    and one INCOHERENT, both flow through _write_verified, the
    shadow file gets the right annotations, and /learning audit
    surfaces the flag count.
    """
    workspace = env
    controller = LearningController(workspace, review_fn=_stub_review_fn)

    coherent_lesson = _proc_lesson("clean lesson", "actual evidence")
    incoherent_lesson = _proc_lesson("bad lesson", "different evidence string")
    incoherent_lesson["target"]["skill_name"] = "x-handling-bad"
    incoherent_lesson["target"]["new_skill_body"] = (
        "---\nname: x-handling-bad\ndescription: variant.\n"
        "origin: learning-curator\n---\n\n# Body\n"
    )

    messages_a = [_tmsg("user", "actual evidence")]
    messages_b = [_tmsg("user", "different evidence string")]

    # Tick A: COHERENT
    with mock.patch(
        "core.learning_curator.run_coherence_judge",
        return_value=CoherenceVerdict.coherent(),
    ):
        s_a = lc._write_verified(
            workspace,
            _make_review_output_with([coherent_lesson]),
            meta=_meta("sess-aaa11111"),
            shadow=True,
            messages=messages_a,
        )
    controller._write_tick_report(
        _utc(hour=10), _utc(hour=10, minute=1),
        lc.TickResult(eligible=["sess-aaa11111"],
                       reviewed=["sess-aaa11111"],
                       outcomes=[("sess-aaa11111", "ok")],
                       summary=s_a),
    )

    # Tick B: INCOHERENT
    inc_verdict = CoherenceVerdict.incoherent(
        "mismatched-attribution",
        "lesson is about Z; evidence cites W",
    )
    with mock.patch(
        "core.learning_curator.run_coherence_judge",
        return_value=inc_verdict,
    ):
        s_b = lc._write_verified(
            workspace,
            _make_review_output_with([incoherent_lesson]),
            meta=_meta("sess-bbb22222"),
            shadow=True,
            messages=messages_b,
        )
    controller._write_tick_report(
        _utc(hour=11), _utc(hour=11, minute=1),
        lc.TickResult(eligible=["sess-bbb22222"],
                       reviewed=["sess-bbb22222"],
                       outcomes=[("sess-bbb22222", "ok")],
                       summary=s_b),
    )

    # Shadow file: has the COHERENT entry (no annotation) AND the
    # INCOHERENT entry (with FLAGGED annotation).
    audit_md = (workspace / "memories" / "MEMORY-SHADOW.md").read_text(encoding="utf-8")
    assert "clean lesson" in audit_md
    assert "bad lesson" in audit_md
    # Annotation appears exactly once (only the bad lesson)
    assert audit_md.count("Coherence:") == 1
    assert "Coherence: FLAGGED (mismatched-attribution)" in audit_md

    # Per-tick REPORT.md for tick B has the section; tick A doesn't.
    log_dirs = sorted((p for p in lc.learning_logs_dir().iterdir() if p.is_dir()),
                      key=lambda p: p.name)
    assert len(log_dirs) == 2
    report_a = (log_dirs[0] / "REPORT.md").read_text(encoding="utf-8")
    report_b = (log_dirs[1] / "REPORT.md").read_text(encoding="utf-8")
    assert "## Coherence flags" not in report_a
    assert "## Coherence flags" in report_b
    assert "FLAGGED (mismatched-attribution)" in report_b

    # /learning audit row: 1 INCOHERENT across the 2 ticks
    audit = controller._audit_text()
    assert "Coherence flags (last 2 tick reports): 1 INCOHERENT" in audit
    assert "mismatched-attribution: 1" in audit

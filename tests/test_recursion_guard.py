"""Tests for the recursion-fix landed alongside the May 2026 audit.

Covers:
  - Persistent SpawnedStore (~/.vexis/learning/spawned.json) survives
    daemon restart.
  - Content-prefix filter in list_eligible_sessions excludes curator-
    owned JSONLs and admits real-conversation JSONLs.
  - The rendered review prompt actually starts with
    CURATOR_REVIEW_PROMPT_PREFIX (invariant for the content filter).
  - eligibility_map / failure_count behaviour: cooldown-bounded retry
    until MAX_REVIEW_FAILURES, then eligibility gate pins.
  - reviewed.json round-trips old-shape records (without
    failure_count) as failure_count=0.
  - PID lock blocks duplicate startups and self-cleans stale locks.
  - scripts/clean_curator_jsonls.py dry-run + apply + idempotence.

Each test gets an isolated $HOME via the ``env`` fixture so writes
land under tmp_path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vexis_agent import main
from vexis_agent.core import learning_curator as lc
from vexis_agent.core import transcripts as t
from vexis_agent.core.learning_curator import (
    MAX_REVIEW_FAILURES,
    LearningController,
    ReviewedStore,
    SpawnedStore,
)
from vexis_agent.core.learning_review import CURATOR_REVIEW_PROMPT_PREFIX
from vexis_agent.core.paths import (
    daemon_pid_path,
    learning_spawned_path,
    learning_state_path,
)
from vexis_agent.core.transcripts import claude_session_jsonl_dir, iter_messages


# --------------------------------------------------------------------
# Fixtures + helpers
# --------------------------------------------------------------------


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fresh $HOME and workspace per test. Same pattern as the other
    test files — Path.home is patched in addition to $HOME because
    paths.py uses Path.home directly."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    workspace = tmp_path / "vexis-workspace"
    (workspace / "memories").mkdir(parents=True)
    return workspace


def _utc(year=2026, month=5, day=2, hour=11, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, 0, tzinfo=timezone.utc)


def _stage_jsonl(
    pdir: Path,
    uuid: str,
    *,
    first_user_text: str,
    last_ts: str = "2026-05-02T10:00:00Z",
) -> Path:
    """Write a minimal JSONL with one user message + one assistant turn.
    ``first_user_text`` controls whether the JSONL trips the content
    filter (curator prefix → filtered) or not (real conversation)."""
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / f"{uuid}.jsonl"
    lines = [
        json.dumps({
            "type": "user",
            "uuid": f"u-{uuid}",
            "timestamp": last_ts,
            "message": {"role": "user", "content": first_user_text},
        }),
        json.dumps({
            "type": "assistant",
            "uuid": f"a-{uuid}",
            "timestamp": last_ts,
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "ok"}],
            },
        }),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------
# 1. Persistent guard survives restart
# --------------------------------------------------------------------


def test_persistent_guard_survives_restart(env, monkeypatch):
    """A spawned UUID written to disk by one controller is honoured by
    a fresh controller (simulates daemon restart) — even though the
    in-memory ``_spawned_uuids`` of the new controller starts empty."""
    workspace = env
    pdir = claude_session_jsonl_dir(workspace)
    # Real conversation, would normally be eligible.
    _stage_jsonl(
        pdir, "real",
        first_user_text="hi, can you help me with a thing?",
        last_ts="2026-05-02T10:00:00Z",
    )
    # A "previously spawned" curator fork — pretend an old daemon
    # recorded its UUID before crashing.
    _stage_jsonl(
        pdir, "spawned-by-old-daemon",
        first_user_text="ordinary text, NOT the curator prefix",
        last_ts="2026-05-02T10:00:00Z",
    )
    SpawnedStore(learning_spawned_path()).add_many(
        {"spawned-by-old-daemon"},
        parent_session="some-prior-uuid",
    )

    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))
    fresh = LearningController(
        workspace=workspace,
        review_fn=lambda *a, **kw: ("ok", lc.WriteSummary()),
    )
    # Sanity: the in-memory set is empty (new controller).
    assert fresh._spawned_uuids == set()

    result = fresh.run_now()
    assert "real" in result.eligible
    assert "spawned-by-old-daemon" not in result.eligible


def test_spawned_store_atomic_round_trip(tmp_path):
    """SpawnedStore writes via the same flock+rename pattern as
    ReviewedStore — idempotent add, parent_session preserved, stable
    re-load."""
    path = tmp_path / "spawned.json"
    store = SpawnedStore(path)
    assert store.load() == {}

    store.add_many({"a", "b"}, parent_session="p1")
    store.add_many({"b", "c"}, parent_session="p2")  # b is duplicate

    data = store.load()
    assert set(data.keys()) == {"a", "b", "c"}
    # b kept its first parent_session (idempotent add).
    assert data["b"]["parent_session"] == "p1"
    assert data["a"]["parent_session"] == "p1"
    assert data["c"]["parent_session"] == "p2"

    # File on disk has the expected schema version.
    raw = json.loads(path.read_text())
    assert raw["version"] == SpawnedStore.SCHEMA_VERSION


def test_spawned_store_corrupt_treated_as_empty(tmp_path):
    path = tmp_path / "spawned.json"
    path.write_text("{not valid json", encoding="utf-8")
    assert SpawnedStore(path).load() == {}
    assert SpawnedStore(path).load_uuids() == set()


def test_review_one_persists_spawned_uuid_immediately(env, monkeypatch):
    """``_review_one`` runs the review_fn between scan-diff
    snapshots; a JSONL created by the review_fn lands in the persistent
    SpawnedStore on the same call (so a crash before tick completion
    leaves the disk authoritative).

    Phase C Day 6: scan-diff routes through
    ``brain.iter_session_metas`` — must use a real
    ``ClaudeCodeBrain`` rather than the default ``BrainNull`` so
    the seeded JSONLs are actually visible to the snapshot.
    """
    from vexis_agent.core.brain.claude_code import ClaudeCodeBrain
    from vexis_agent.core.running_tasks import RunningTasks
    from vexis_agent.core.sessions import SessionStore

    workspace = env
    pdir = claude_session_jsonl_dir(workspace)
    _stage_jsonl(
        pdir, "real",
        first_user_text="real conversation",
        last_ts="2026-05-02T10:00:00Z",
    )

    # Capture the workspace projects dir so the fake review_fn can
    # write a child JSONL into it (simulating a claude -p subprocess).
    def fake_review(ws, meta):
        # Emulate claude -p's side effect: a new JSONL appears in the
        # projects dir while the review is running.
        _stage_jsonl(
            pdir, "child-of-real",
            first_user_text="this is a curator review fork",
            last_ts="2026-05-02T10:30:00Z",
        )
        return ("ok", lc.WriteSummary())

    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(hour=11))
    cc_brain = ClaudeCodeBrain(
        workspace=workspace,
        session=SessionStore(workspace / "sessions.json"),
        running_tasks=RunningTasks(),
    )
    controller = LearningController(
        workspace=workspace,
        review_fn=fake_review,
        brain=cc_brain,
    )
    controller.run_now()

    # In-memory set captured the child.
    assert "child-of-real" in controller._spawned_uuids
    # AND the persistent store has it too.
    on_disk = SpawnedStore(learning_spawned_path()).load_uuids()
    assert "child-of-real" in on_disk


# --------------------------------------------------------------------
# 2. Content filter
# --------------------------------------------------------------------


def test_content_filter_excludes_curator_jsonls(env):
    """A JSONL whose first user turn is the curator prompt is filtered
    out even when not in spawned_by_curator and not in reviewed."""
    workspace = env
    pdir = claude_session_jsonl_dir(workspace)
    _stage_jsonl(
        pdir, "curator-fork",
        first_user_text=CURATOR_REVIEW_PROMPT_PREFIX + " for session abc...",
    )
    _stage_jsonl(
        pdir, "real-user",
        first_user_text="hi, please do a thing",
    )

    eligible = t.list_eligible_sessions(
        workspace=workspace,
        reviewed={},
        idle_threshold=timedelta(minutes=25),
        now=_utc(hour=11),
    )
    uuids = {m.session_uuid for m in eligible}
    assert "real-user" in uuids
    assert "curator-fork" not in uuids


def test_content_filter_admits_real_conversation(env):
    """Sanity: a regular user-conversation JSONL passes the filter."""
    workspace = env
    pdir = claude_session_jsonl_dir(workspace)
    _stage_jsonl(
        pdir, "ordinary",
        first_user_text="What's the weather like?",
    )
    eligible = t.list_eligible_sessions(
        workspace=workspace,
        reviewed={},
        idle_threshold=timedelta(minutes=25),
        now=_utc(hour=11),
    )
    assert {m.session_uuid for m in eligible} == {"ordinary"}


def test_content_filter_skips_jsonl_with_no_user_messages(env):
    """A JSONL containing only assistant messages (or none at all) is
    NOT classified as curator-owned — the filter is positive, not
    default-deny. Eligibility is decided by the other gates."""
    workspace = env
    pdir = claude_session_jsonl_dir(workspace)
    path = pdir / "assistant-only.jsonl"
    pdir.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "type": "assistant",
            "uuid": "a1",
            "timestamp": "2026-05-02T10:00:00Z",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "hello"}],
            },
        }) + "\n",
        encoding="utf-8",
    )
    eligible = t.list_eligible_sessions(
        workspace=workspace,
        reviewed={},
        idle_threshold=timedelta(minutes=25),
        now=_utc(hour=11),
    )
    assert {m.session_uuid for m in eligible} == {"assistant-only"}


# --------------------------------------------------------------------
# 3. Curator prompt invariant
# --------------------------------------------------------------------


def test_curator_prompt_invariant():
    """The rendered review prompt must start with the prefix the
    content filter looks for. If the prompt is edited and breaks this
    invariant, the filter would silently regress — this assertion
    forces the edit to update the constant in the same diff."""
    from vexis_agent.core.learning_review import _build_review_prompt

    prompt = _build_review_prompt(
        "(transcript here)",
        skill_index_text="(skill index)",
        existing_memory_text="(existing memory)",
        user_queue_text="(user queue)",
    )
    assert prompt.startswith(CURATOR_REVIEW_PROMPT_PREFIX), (
        "Curator prompt edit broke the content-filter prefix invariant. "
        "Update CURATOR_REVIEW_PROMPT_PREFIX in core/learning_review.py "
        "to match the new prompt opening."
    )


# --------------------------------------------------------------------
# 4. eligibility_map + failure_count
# --------------------------------------------------------------------


def test_failure_count_increments_and_pins_at_max(tmp_path):
    """ReviewedStore.update increments failure_count on each failure;
    on hitting MAX_REVIEW_FAILURES it pins last_message_at_review_time
    so the eligibility gate filters the session."""
    path = tmp_path / "reviewed.json"
    store = ReviewedStore(path)
    msg_ts = _utc(hour=10)

    for n in range(MAX_REVIEW_FAILURES - 1):
        when = _utc(hour=11 + n)
        store.update(
            "broken",
            success=False,
            last_message_at_review_time=msg_ts,
            outcome=f"error #{n+1}",
            now=when,
        )
        rec = store.load()["broken"]
        assert rec.failure_count == n + 1
        # Not yet pinned — eligibility gate stays open for cooldown-
        # bounded retry.
        assert rec.last_message_at_review_time is None

    # The threshold-hitting failure pins.
    store.update(
        "broken",
        success=False,
        last_message_at_review_time=msg_ts,
        outcome="error #MAX",
        now=_utc(hour=11 + MAX_REVIEW_FAILURES),
    )
    rec = store.load()["broken"]
    assert rec.failure_count == MAX_REVIEW_FAILURES
    assert rec.last_message_at_review_time == msg_ts


def test_failure_count_resets_on_success(tmp_path):
    path = tmp_path / "reviewed.json"
    store = ReviewedStore(path)
    msg_ts = _utc(hour=10)
    store.update(
        "abc",
        success=False,
        last_message_at_review_time=msg_ts,
        outcome="error",
        now=_utc(hour=11),
    )
    assert store.load()["abc"].failure_count == 1

    store.update(
        "abc",
        success=True,
        last_message_at_review_time=msg_ts,
        outcome="wrote 1",
        now=_utc(hour=12),
    )
    rec = store.load()["abc"]
    assert rec.failure_count == 0
    assert rec.last_reviewed_at == _utc(hour=12)


def test_pinned_session_filtered_until_transcript_advances(env, monkeypatch):
    """After MAX failures the session is filtered by the eligibility
    gate (last_message_timestamp <= pinned snapshot). Once the user
    adds new content (advancing the JSONL's last_message_timestamp),
    the session re-enters eligibility."""
    workspace = env
    pdir = claude_session_jsonl_dir(workspace)
    _stage_jsonl(
        pdir, "broken",
        first_user_text="real but the verifier hates it",
        last_ts="2026-05-02T10:00:00Z",
    )

    def boom(*_a, **_kw):
        raise RuntimeError("simulated failure")

    controller = LearningController(workspace=workspace, review_fn=boom)
    # Drive MAX_REVIEW_FAILURES failures, each beyond the previous
    # cooldown so the gate doesn't skip them as cooldown'd.
    for i in range(MAX_REVIEW_FAILURES):
        # First attempt at hour=11; each subsequent retry 2h later
        # (well beyond the 1h cooldown default).
        monkeypatch.setattr(lc, "_utc_now", lambda i=i: _utc(hour=11 + i * 2))
        controller.run_now()

    rec = controller._reviewed.load()["broken"]
    assert rec.failure_count == MAX_REVIEW_FAILURES
    assert rec.last_message_at_review_time == _utc(hour=10)

    # Next tick: still pinned, NOT eligible even with no cooldown
    # remaining. (Cooldown is 1h; we wait 24h.)
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(day=3, hour=11))
    result = controller.run_now()
    assert "broken" not in result.eligible

    # User adds a new message — JSONL's last_message_timestamp moves
    # past the pinned snapshot.
    _stage_jsonl(
        pdir, "broken",
        first_user_text="real but the verifier hates it",
        last_ts="2026-05-03T11:00:00Z",
    )
    monkeypatch.setattr(lc, "_utc_now", lambda: _utc(day=3, hour=12))
    result = controller.run_now()
    assert "broken" in result.eligible


# --------------------------------------------------------------------
# 5. Old-shape reviewed.json migrates cleanly
# --------------------------------------------------------------------


def test_reviewed_store_old_shape_loads_failure_count_zero(tmp_path):
    """Records written before the failure_count field landed must
    round-trip with failure_count=0 (and not crash on load)."""
    path = tmp_path / "reviewed.json"
    payload = {
        "by_session": {
            "abc": {
                "last_reviewed_at": "2026-05-02T10:00:00Z",
                "last_review_attempt_at": "2026-05-02T10:00:00Z",
                "last_message_at_review_time": "2026-05-02T09:30:00Z",
                "outcome": "wrote 1 entry",
                # No failure_count key.
            }
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    rec = ReviewedStore(path).load()["abc"]
    assert rec.failure_count == 0
    assert rec.outcome == "wrote 1 entry"


def test_reviewed_store_garbage_failure_count_clamped(tmp_path):
    """Defensive: a corrupted record with non-int failure_count loads
    as 0 rather than crashing on parse."""
    path = tmp_path / "reviewed.json"
    payload = {
        "by_session": {
            "abc": {
                "outcome": "x",
                "failure_count": "three",
            }
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    rec = ReviewedStore(path).load()["abc"]
    assert rec.failure_count == 0


# --------------------------------------------------------------------
# 6. PID lock
# --------------------------------------------------------------------


def test_pid_lock_acquires_when_unheld(env):
    fd = main.acquire_daemon_lock()
    assert daemon_pid_path().read_text().strip() == str(os.getpid())
    os.close(fd)


def test_pid_lock_blocks_when_alive_pid_present(env):
    """Simulate an alive incumbent by writing the test process's own
    PID into the file (we know we're alive). The new acquire raises
    DaemonAlreadyRunning and names the incumbent."""
    pid_path = daemon_pid_path()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(f"{os.getpid()}\n")

    # Patch getpid so the lock thinks "we" are a different process.
    real_getpid = os.getpid
    fake_pid = real_getpid() + 1_000_000  # almost certainly unused
    # Make sure the fake PID isn't actually alive on this box.
    while True:
        try:
            os.kill(fake_pid, 0)
        except ProcessLookupError:
            break
        fake_pid += 1

    import unittest.mock as mock
    with mock.patch.object(main.os, "getpid", return_value=fake_pid):
        with pytest.raises(main.DaemonAlreadyRunning) as excinfo:
            main.acquire_daemon_lock()
    assert str(real_getpid()) in str(excinfo.value)


def test_pid_lock_clears_stale_and_reacquires(env):
    """File contains a definitely-dead PID; the new acquire treats it
    as stale, overwrites with our own PID, and proceeds."""
    pid_path = daemon_pid_path()
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    # Find a definitely-dead PID. PID 1 is init (alive), so start higher.
    candidate = 999_999
    while True:
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            break
        candidate += 1
    pid_path.write_text(f"{candidate}\n")

    fd = main.acquire_daemon_lock()
    assert pid_path.read_text().strip() == str(os.getpid())
    os.close(fd)


# --------------------------------------------------------------------
# 7. clean_curator_jsonls.py — dry-run + apply + idempotence
# --------------------------------------------------------------------


def _run_cleanup(workspace: Path, *args: str) -> subprocess.CompletedProcess:
    """Subprocess the script (vs imported main) so it exercises the
    ``if __name__ == '__main__'`` path and the argparse wiring."""
    script = Path(__file__).resolve().parent.parent / "scripts" / "clean_curator_jsonls.py"
    return subprocess.run(
        [sys.executable, str(script), "--workspace", str(workspace), *args],
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": os.environ["HOME"]},
        check=True,
    )


def test_cleanup_dry_run_counts_curator_owned(env):
    """Dry-run reports curator-owned count without moving anything."""
    workspace = env
    pdir = claude_session_jsonl_dir(workspace)
    for i in range(5):
        _stage_jsonl(
            pdir, f"curator-{i}",
            first_user_text=CURATOR_REVIEW_PROMPT_PREFIX + " ...",
        )
    for i in range(2):
        _stage_jsonl(pdir, f"real-{i}", first_user_text=f"hello {i}")

    cp = _run_cleanup(workspace)
    assert "Curator-owned:   5 JSONL" in cp.stdout
    assert "Real-user / other: 2 JSONL" in cp.stdout
    # No archive subdirs created.
    archive_root = Path.home() / ".vexis" / "learning" / "curator-jsonl-archive"
    assert not archive_root.exists() or not any(archive_root.iterdir())
    # Files all still in place.
    assert len(list(pdir.glob("*.jsonl"))) == 7


def test_cleanup_apply_moves_only_curator_owned_then_idempotent(env):
    workspace = env
    pdir = claude_session_jsonl_dir(workspace)
    for i in range(3):
        _stage_jsonl(
            pdir, f"curator-{i}",
            first_user_text=CURATOR_REVIEW_PROMPT_PREFIX + " ...",
        )
    for i in range(2):
        _stage_jsonl(pdir, f"real-{i}", first_user_text=f"hello {i}")

    cp = _run_cleanup(workspace, "--apply")
    assert "Moved:           3/3" in cp.stdout
    # Real-user JSONLs untouched.
    remaining_uuids = {p.stem for p in pdir.glob("*.jsonl")}
    assert remaining_uuids == {"real-0", "real-1"}
    # Archive subdir exists with all 3.
    archive_root = Path.home() / ".vexis" / "learning" / "curator-jsonl-archive"
    archived = sorted(archive_root.iterdir())
    assert len(archived) == 1
    assert sorted(p.stem for p in archived[0].glob("*.jsonl")) == [
        "curator-0", "curator-1", "curator-2",
    ]

    # Second --apply is a no-op (count=0, no work).
    cp2 = _run_cleanup(workspace, "--apply")
    assert "Curator-owned:   0 JSONL" in cp2.stdout
    assert "Nothing to do." in cp2.stdout

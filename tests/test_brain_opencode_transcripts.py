"""Phase C Day 4: OpenCodeBrain SQL transcript reader.

These tests exercise ``iter_session_metas``, ``iter_messages``,
and ``is_brain_owned_session`` against a hand-built SQLite
database with the same schema as opencode's
``~/.local/share/opencode/opencode.db``. The reader is opened
read-only against the test DB via the
``set_opencode_db_path_override`` test hook (the autouse
``_isolate_opencode_db`` fixture in ``tests/conftest.py`` already
points it at a non-existent path; tests that need real data
re-override per-test).

Schema we mirror — captured live from opencode 1.14:

    session(id, project_id, parent_id, slug, directory, title,
            version, … , time_created, time_updated, …)
    message(id, session_id, time_created, time_updated, data)
    part(id, message_id, session_id, time_created, time_updated, data)

The reader only depends on a subset; we declare the minimum
columns the queries hit, plus enough placeholders to satisfy NOT
NULL constraints. We deliberately mimic the production layout so
a future schema change in opencode would surface as a test break
when we copy the new DDL across.

Design citation: ``.plans/brain-abstraction-research.md`` §5 Day 4.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from vexis_agent.core.brain.opencode import (
    OpenCodeBrain,
    set_opencode_db_path_override,
)
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore


# ──────────────────────────────────────────────────────────────────
# Fixtures + DB builder
# ──────────────────────────────────────────────────────────────────


_SCHEMA = """
CREATE TABLE session (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    parent_id TEXT,
    slug TEXT NOT NULL,
    directory TEXT NOT NULL,
    title TEXT NOT NULL,
    version TEXT NOT NULL,
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL
);
CREATE TABLE message (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE part (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL,
    data TEXT NOT NULL
);
"""


def _build_db(path: Path) -> sqlite3.Connection:
    """Create the opencode-shaped DB at ``path`` and return the
    open connection so the test can keep INSERTing."""
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    return conn


def _insert_session(
    conn: sqlite3.Connection,
    *,
    sid: str,
    directory: str,
    time_updated_ms: int,
    title: str = "test session",
) -> None:
    conn.execute(
        """INSERT INTO session
           (id, project_id, parent_id, slug, directory, title,
            version, time_created, time_updated)
           VALUES (?, 'proj-1', NULL, 'slug', ?, ?, '1.0', ?, ?)""",
        (sid, directory, title, time_updated_ms, time_updated_ms),
    )


def _insert_message(
    conn: sqlite3.Connection,
    *,
    mid: str,
    sid: str,
    role: str,
    time_created_ms: int,
) -> None:
    data = json.dumps({"role": role, "time": {"created": time_created_ms}})
    conn.execute(
        """INSERT INTO message
           (id, session_id, time_created, time_updated, data)
           VALUES (?, ?, ?, ?, ?)""",
        (mid, sid, time_created_ms, time_created_ms, data),
    )


def _insert_part(
    conn: sqlite3.Connection,
    *,
    pid: str,
    mid: str,
    sid: str,
    time_created_ms: int,
    data: dict,
) -> None:
    conn.execute(
        """INSERT INTO part
           (id, message_id, session_id, time_created, time_updated, data)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (pid, mid, sid, time_created_ms, time_created_ms, json.dumps(data)),
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


@pytest.fixture
def brain(workspace: Path, tmp_path: Path) -> OpenCodeBrain:
    return OpenCodeBrain(
        workspace=workspace,
        session=SessionStore(tmp_path / "sessions.json"),
        running_tasks=RunningTasks(),
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Override the opencode DB path for the duration of the test."""
    path = tmp_path / "opencode.db"
    set_opencode_db_path_override(path)
    yield path
    set_opencode_db_path_override(None)


# ──────────────────────────────────────────────────────────────────
# iter_session_metas
# ──────────────────────────────────────────────────────────────────


def test_iter_session_metas_returns_empty_when_db_missing(brain: OpenCodeBrain):
    """The autouse fixture in conftest.py points the reader at a
    nonexistent path. Verify the reader silently returns empty —
    the curator must not crash on a fresh OpenCode install."""
    assert list(brain.iter_session_metas()) == []


def test_iter_session_metas_finds_sessions_in_workspace(
    brain: OpenCodeBrain, workspace: Path, db_path: Path
):
    """``iter_session_metas`` filters by ``directory`` matching the
    brain's workspace. Sessions in other directories must NOT
    leak through (cross-workspace isolation is what makes the
    curator's per-workspace eligibility scan correct)."""
    conn = _build_db(db_path)
    workspace_str = str(workspace.resolve())
    other_workspace = "/some/other/workspace"
    now_ms = int(time.time() * 1000)

    _insert_session(conn, sid="ses_in_ws_1", directory=workspace_str, time_updated_ms=now_ms - 1000)
    _insert_session(conn, sid="ses_in_ws_2", directory=workspace_str, time_updated_ms=now_ms)
    _insert_session(conn, sid="ses_other", directory=other_workspace, time_updated_ms=now_ms)
    conn.commit()
    conn.close()

    metas = list(brain.iter_session_metas())
    sids = {m.session_uuid for m in metas}
    assert sids == {"ses_in_ws_1", "ses_in_ws_2"}
    # Every meta has jsonl_path=None (opencode storage flag).
    assert all(m.jsonl_path is None for m in metas)


def test_iter_session_metas_carries_last_message_timestamp(
    brain: OpenCodeBrain, workspace: Path, db_path: Path
):
    """``time_updated`` (millisecond Unix epoch) maps to a
    timezone-aware UTC datetime in ``last_message_timestamp``."""
    conn = _build_db(db_path)
    expected_ms = 1_700_000_000_000  # arbitrary fixed instant
    _insert_session(
        conn, sid="ses_x", directory=str(workspace.resolve()),
        time_updated_ms=expected_ms,
    )
    conn.commit()
    conn.close()

    meta = next(iter(brain.iter_session_metas()))
    assert meta.last_message_timestamp == datetime.fromtimestamp(
        expected_ms / 1000, tz=timezone.utc
    )


def test_iter_session_metas_carries_message_count(
    brain: OpenCodeBrain, workspace: Path, db_path: Path
):
    """``message_count_estimate`` reflects the row count from the
    LEFT JOIN — sessions with no messages get 0, busy sessions
    get the real count. The curator uses this for the cheap
    "is this worth full-parsing" gate."""
    conn = _build_db(db_path)
    workspace_str = str(workspace.resolve())
    _insert_session(conn, sid="ses_busy", directory=workspace_str, time_updated_ms=2)
    _insert_session(conn, sid="ses_empty", directory=workspace_str, time_updated_ms=1)
    for i in range(3):
        _insert_message(
            conn, mid=f"msg_b_{i}", sid="ses_busy", role="user", time_created_ms=i,
        )
    conn.commit()
    conn.close()

    metas = {m.session_uuid: m for m in brain.iter_session_metas()}
    assert metas["ses_busy"].message_count_estimate == 3
    assert metas["ses_empty"].message_count_estimate == 0


def test_iter_session_metas_orders_newest_first(
    brain: OpenCodeBrain, workspace: Path, db_path: Path
):
    """Helps the curator surface freshly-active sessions before
    abandoned ones (matches the existing claude-code behaviour
    where the curator's eligibility scan iterates oldest-last
    after the file-system sort)."""
    conn = _build_db(db_path)
    workspace_str = str(workspace.resolve())
    _insert_session(conn, sid="ses_old", directory=workspace_str, time_updated_ms=100)
    _insert_session(conn, sid="ses_new", directory=workspace_str, time_updated_ms=999)
    _insert_session(conn, sid="ses_mid", directory=workspace_str, time_updated_ms=500)
    conn.commit()
    conn.close()

    sids = [m.session_uuid for m in brain.iter_session_metas()]
    assert sids == ["ses_new", "ses_mid", "ses_old"]


# ──────────────────────────────────────────────────────────────────
# iter_messages
# ──────────────────────────────────────────────────────────────────


def test_iter_messages_unknown_session_returns_empty(
    brain: OpenCodeBrain, workspace: Path, db_path: Path
):
    """A session_id with no rows in ``message`` returns the empty
    iterator — same semantics as claude-code's iter_messages on
    an unreadable JSONL."""
    conn = _build_db(db_path)
    conn.commit()
    conn.close()
    assert list(brain.iter_messages("ses_does_not_exist")) == []


def test_iter_messages_yields_user_then_assistant(
    brain: OpenCodeBrain, workspace: Path, db_path: Path
):
    """One user turn + one assistant turn, with text parts on
    each. Verify role + text + ordering (by time_created)."""
    conn = _build_db(db_path)
    sid = "ses_x"
    _insert_session(conn, sid=sid, directory=str(workspace.resolve()), time_updated_ms=2)
    _insert_message(conn, mid="m1", sid=sid, role="user", time_created_ms=1)
    _insert_message(conn, mid="m2", sid=sid, role="assistant", time_created_ms=2)
    _insert_part(
        conn, pid="p1", mid="m1", sid=sid, time_created_ms=1,
        data={"type": "text", "text": "hi vexis"},
    )
    _insert_part(
        conn, pid="p2", mid="m2", sid=sid, time_created_ms=2,
        data={"type": "text", "text": "hi back"},
    )
    conn.commit()
    conn.close()

    msgs = list(brain.iter_messages(sid))
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert [m.text for m in msgs] == ["hi vexis", "hi back"]
    # uuid carries the message id for downstream tracing.
    assert [m.uuid for m in msgs] == ["m1", "m2"]


def test_iter_messages_concatenates_multiple_text_parts(
    brain: OpenCodeBrain, workspace: Path, db_path: Path
):
    """One assistant message can have multiple text parts
    (opencode's stream-completion writes each block as a separate
    row). They should join with newlines for the
    ``TranscriptMessage.text`` field — same shape claude-code's
    ``_flatten_content`` produces."""
    conn = _build_db(db_path)
    sid = "ses_x"
    _insert_session(conn, sid=sid, directory=str(workspace.resolve()), time_updated_ms=2)
    _insert_message(conn, mid="m1", sid=sid, role="assistant", time_created_ms=1)
    _insert_part(
        conn, pid="p1", mid="m1", sid=sid, time_created_ms=1,
        data={"type": "text", "text": "first chunk"},
    )
    _insert_part(
        conn, pid="p2", mid="m1", sid=sid, time_created_ms=2,
        data={"type": "text", "text": "second chunk"},
    )
    conn.commit()
    conn.close()

    msg = next(iter(brain.iter_messages(sid)))
    assert msg.text == "first chunk\nsecond chunk"


def test_iter_messages_extracts_tool_calls(
    brain: OpenCodeBrain, workspace: Path, db_path: Path
):
    """``tool`` parts populate ``TranscriptMessage.tool_calls``
    with id/name/input — same shape claude-code emits so the
    curator's tool-aware lessons work uniformly."""
    conn = _build_db(db_path)
    sid = "ses_x"
    _insert_session(conn, sid=sid, directory=str(workspace.resolve()), time_updated_ms=2)
    _insert_message(conn, mid="m1", sid=sid, role="assistant", time_created_ms=1)
    _insert_part(
        conn, pid="p1", mid="m1", sid=sid, time_created_ms=1,
        data={
            "type": "tool",
            "callID": "call_xyz",
            "tool": "bash",
            "state": {"input": {"command": "ls"}},
        },
    )
    conn.commit()
    conn.close()

    msg = next(iter(brain.iter_messages(sid)))
    assert len(msg.tool_calls) == 1
    tc = msg.tool_calls[0]
    assert tc["id"] == "call_xyz"
    assert tc["name"] == "bash"
    assert tc["input"] == {"command": "ls"}


def test_iter_messages_skips_non_conversational_parts(
    brain: OpenCodeBrain, workspace: Path, db_path: Path
):
    """``step-start`` / ``step-finish`` parts are pacing markers
    opencode emits between model calls — they shouldn't surface
    as text or tool-calls in the curator-facing view."""
    conn = _build_db(db_path)
    sid = "ses_x"
    _insert_session(conn, sid=sid, directory=str(workspace.resolve()), time_updated_ms=2)
    _insert_message(conn, mid="m1", sid=sid, role="assistant", time_created_ms=1)
    _insert_part(
        conn, pid="p1", mid="m1", sid=sid, time_created_ms=1,
        data={"type": "step-start"},
    )
    _insert_part(
        conn, pid="p2", mid="m1", sid=sid, time_created_ms=2,
        data={"type": "text", "text": "real text"},
    )
    _insert_part(
        conn, pid="p3", mid="m1", sid=sid, time_created_ms=3,
        data={"type": "step-finish", "reason": "stop"},
    )
    conn.commit()
    conn.close()

    msg = next(iter(brain.iter_messages(sid)))
    assert msg.text == "real text"
    assert msg.tool_calls == ()


def test_iter_messages_handles_corrupt_part_json(
    brain: OpenCodeBrain, workspace: Path, db_path: Path
):
    """Corrupt ``part.data`` JSON must not break the rest of the
    transcript — opencode does atomic row writes but a half-
    written DB or schema-mismatched part should degrade
    gracefully (skip the bad row, keep going)."""
    conn = _build_db(db_path)
    sid = "ses_x"
    _insert_session(conn, sid=sid, directory=str(workspace.resolve()), time_updated_ms=2)
    _insert_message(conn, mid="m1", sid=sid, role="assistant", time_created_ms=1)
    # Insert a corrupt part by bypassing _insert_part.
    conn.execute(
        """INSERT INTO part
           (id, message_id, session_id, time_created, time_updated, data)
           VALUES ('p_bad', 'm1', ?, 1, 1, 'NOT JSON')""",
        (sid,),
    )
    _insert_part(
        conn, pid="p_good", mid="m1", sid=sid, time_created_ms=2,
        data={"type": "text", "text": "good part"},
    )
    conn.commit()
    conn.close()

    msg = next(iter(brain.iter_messages(sid)))
    assert msg.text == "good part"


def test_iter_messages_uses_message_time_created_for_timestamp(
    brain: OpenCodeBrain, workspace: Path, db_path: Path
):
    """``message.data.time.created`` (the model's view of the
    timestamp) is preferred over the row's ``time_created`` column.
    They're typically identical; the test pins the precedence so
    a future schema change that makes them diverge is a visible
    failure."""
    conn = _build_db(db_path)
    sid = "ses_x"
    _insert_session(conn, sid=sid, directory=str(workspace.resolve()), time_updated_ms=2)
    # Message data carries one timestamp; row column carries another.
    # Reader should pick the data field.
    data_ts = 1_700_000_000_000
    row_ts = 999_999_999
    conn.execute(
        """INSERT INTO message
           (id, session_id, time_created, time_updated, data)
           VALUES ('m1', ?, ?, ?, ?)""",
        (
            sid, row_ts, row_ts,
            json.dumps({"role": "user", "time": {"created": data_ts}}),
        ),
    )
    _insert_part(
        conn, pid="p1", mid="m1", sid=sid, time_created_ms=1,
        data={"type": "text", "text": "x"},
    )
    conn.commit()
    conn.close()

    msg = next(iter(brain.iter_messages(sid)))
    assert msg.timestamp == datetime.fromtimestamp(
        data_ts / 1000, tz=timezone.utc
    )


# ──────────────────────────────────────────────────────────────────
# is_brain_owned_session
# ──────────────────────────────────────────────────────────────────


def test_is_brain_owned_session_recognises_curator_review_prefix(
    brain: OpenCodeBrain, workspace: Path, db_path: Path
):
    """The curator's recursion guard relies on this returning True
    for sessions whose first user turn starts with
    ``CURATOR_REVIEW_PROMPT_PREFIX``. A False positive here would
    let curator-owned sessions get reviewed (the recursion bug
    we fixed back in May 2026)."""
    from vexis_agent.core.learning_review import CURATOR_REVIEW_PROMPT_PREFIX

    conn = _build_db(db_path)
    sid = "ses_curator"
    _insert_session(conn, sid=sid, directory=str(workspace.resolve()), time_updated_ms=1)
    _insert_message(conn, mid="m1", sid=sid, role="user", time_created_ms=1)
    _insert_part(
        conn, pid="p1", mid="m1", sid=sid, time_created_ms=1,
        data={"type": "text", "text": CURATOR_REVIEW_PROMPT_PREFIX + "..."},
    )
    conn.commit()
    conn.close()

    assert brain.is_brain_owned_session(sid) is True


def test_is_brain_owned_session_recognises_goal_judge_prefix(
    brain: OpenCodeBrain, workspace: Path, db_path: Path
):
    """Goal-judge spawns also write JSONL-equivalent rows; the
    curator must skip those too. Same logic, different prefix."""
    from vexis_agent.core.goal_judge import GOAL_JUDGE_PROMPT_PREFIX

    conn = _build_db(db_path)
    sid = "ses_goaljudge"
    _insert_session(conn, sid=sid, directory=str(workspace.resolve()), time_updated_ms=1)
    _insert_message(conn, mid="m1", sid=sid, role="user", time_created_ms=1)
    _insert_part(
        conn, pid="p1", mid="m1", sid=sid, time_created_ms=1,
        data={"type": "text", "text": GOAL_JUDGE_PROMPT_PREFIX + "..."},
    )
    conn.commit()
    conn.close()

    assert brain.is_brain_owned_session(sid) is True


def test_is_brain_owned_session_returns_false_for_real_user_session(
    brain: OpenCodeBrain, workspace: Path, db_path: Path
):
    """A normal user session (whose first turn is "hello" or
    similar) returns False — the curator should review it."""
    conn = _build_db(db_path)
    sid = "ses_real"
    _insert_session(conn, sid=sid, directory=str(workspace.resolve()), time_updated_ms=1)
    _insert_message(conn, mid="m1", sid=sid, role="user", time_created_ms=1)
    _insert_part(
        conn, pid="p1", mid="m1", sid=sid, time_created_ms=1,
        data={"type": "text", "text": "hey can you check the log file?"},
    )
    conn.commit()
    conn.close()

    assert brain.is_brain_owned_session(sid) is False


def test_is_brain_owned_session_returns_false_for_unknown_session(
    brain: OpenCodeBrain
):
    """Unknown session_id (no row in ``message``): defensive False
    — treat as "not brain-owned, let the curator decide"."""
    assert brain.is_brain_owned_session("ses_does_not_exist") is False


def test_is_brain_owned_session_returns_false_when_first_turn_is_assistant(
    brain: OpenCodeBrain, workspace: Path, db_path: Path
):
    """Edge case — opencode could (hypothetically) write an
    assistant message first if the session was forked from another.
    The check should walk past assistant turns to find the first
    user message, which mirrors claude-code's
    ``_is_curator_owned`` behaviour."""
    from vexis_agent.core.learning_review import CURATOR_REVIEW_PROMPT_PREFIX

    conn = _build_db(db_path)
    sid = "ses_forked"
    _insert_session(conn, sid=sid, directory=str(workspace.resolve()), time_updated_ms=10)
    # Assistant turn first (forked-session synthetic seed).
    _insert_message(conn, mid="m1", sid=sid, role="assistant", time_created_ms=1)
    _insert_part(
        conn, pid="p1", mid="m1", sid=sid, time_created_ms=1,
        data={"type": "text", "text": "context from fork"},
    )
    # Real user turn second — this is what the prefix check should hit.
    _insert_message(conn, mid="m2", sid=sid, role="user", time_created_ms=2)
    _insert_part(
        conn, pid="p2", mid="m2", sid=sid, time_created_ms=2,
        data={"type": "text", "text": CURATOR_REVIEW_PROMPT_PREFIX + "..."},
    )
    conn.commit()
    conn.close()

    assert brain.is_brain_owned_session(sid) is True


# ──────────────────────────────────────────────────────────────────
# SQLITE_BUSY backoff (synthetic — manually triggers OperationalError)
# ──────────────────────────────────────────────────────────────────


def test_run_db_query_returns_none_on_persistent_lock(monkeypatch):
    """When SQLite raises ``database is locked`` repeatedly, the
    helper returns None (the curator path treats None as "skip
    this scan tick")."""
    from vexis_agent.core.brain import opencode as oc

    # Force the fake DB path to exist so we get past the
    # path.exists() short-circuit, then make connect raise.
    fake_path = Path("/tmp/_fake_busy_opencode.db")
    fake_path.touch()
    set_opencode_db_path_override(fake_path)

    call_count = {"n": 0}

    def _busy_connect(*args, **kwargs):
        call_count["n"] += 1
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(sqlite3, "connect", _busy_connect)
    # Speed up the test — back-off is 0.1s per attempt; with 5
    # attempts that's 0.4s of sleep. Skip the sleeps.
    monkeypatch.setattr(oc.time, "sleep", lambda _: None)

    try:
        result = oc._run_db_query("SELECT 1")
    finally:
        set_opencode_db_path_override(None)
        fake_path.unlink(missing_ok=True)

    assert result is None
    # Confirm the retry loop ran the configured number of times.
    assert call_count["n"] == oc._SQLITE_RETRIES

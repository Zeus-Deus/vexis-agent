"""Cross-brain transcript seeding for parity tests.

A subsystem under test reads session transcripts through
``brain.iter_messages(session_uuid)`` — never by touching storage
directly. To exercise a subsystem against *both* real brains, a test
needs to lay down a transcript in whatever store that brain reads:

- ``ClaudeCodeBrain`` reads a JSONL at
  ``<encoded-cwd>/<session_uuid>.jsonl``.
- ``OpenCodeBrain`` reads rows from ``opencode.db`` (session / message
  / part tables), located via ``opencode_db_path()`` — which the
  autouse ``_isolate_opencode_db`` conftest fixture already points at a
  tmp path.

``seed_transcript`` hides that split. Give it a brain, a session id,
and a list of ``(role, text)`` turns; it writes a transcript the
brain's own ``iter_messages`` will read back. Re-seeding the same
session id replaces the prior transcript (so a test can "grow" a
session across turns).

The opencode JSONL/DB shapes mirror the ones pinned by
``tests/test_brain_opencode_transcripts.py`` and the claude-code shape
mirrors ``tests/relationships/test_telegram_handoff.py`` — if opencode
or claude-code changes its on-disk layout, those suites break first
and this helper gets updated alongside them.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Sequence

from vexis_agent.core.brain.claude_code import ClaudeCodeBrain
from vexis_agent.core.brain.null import BrainNull
from vexis_agent.core.brain.opencode import OpenCodeBrain, opencode_db_path
from vexis_agent.core.transcripts import claude_session_jsonl_dir

Turn = tuple[str, str]  # (role, text) — role in {"user", "assistant"}

# Schema mirrors tests/test_brain_opencode_transcripts.py::_SCHEMA, which
# in turn mirrors opencode 1.14's live opencode.db DDL.
_OPENCODE_SCHEMA = """
CREATE TABLE IF NOT EXISTS session (
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
CREATE TABLE IF NOT EXISTS message (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS part (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    time_created INTEGER NOT NULL,
    time_updated INTEGER NOT NULL,
    data TEXT NOT NULL
);
"""


def supports_seeding(brain: object) -> bool:
    """True when ``seed_transcript`` can lay down a readable transcript
    for this brain. BrainNull has no backing store — its
    ``iter_messages`` always yields empty — so seeding is a no-op there
    and parity tests that need transcript content should skip the null
    parametrisation."""
    return isinstance(brain, (ClaudeCodeBrain, OpenCodeBrain))


def seed_transcript(
    brain: object,
    session_uuid: str,
    turns: Sequence[Turn],
    *,
    last_updated_ms: int | None = None,
) -> None:
    """Write ``turns`` as a transcript that ``brain.iter_messages``
    will read back. Replaces any prior transcript for ``session_uuid``.

    ``last_updated_ms`` (Unix epoch milliseconds) stamps the session's
    last-activity time — what the learning curator's eligibility scan
    reads via ``last_message_timestamp``. When omitted, turns are
    stamped at a fixed early instant (fine for tests that only read
    message *content*, not recency). Pass it for recency-sensitive
    tests so both brains land on the same instant.

    ``BrainNull`` is a silent no-op (it has no store; ``iter_messages``
    yields empty by design). Unknown brain types raise ``TypeError`` so
    a new brain implementation can't quietly skip parity coverage.
    """
    if isinstance(brain, BrainNull):
        return
    if isinstance(brain, ClaudeCodeBrain):
        _seed_claude_code(brain, session_uuid, turns, last_updated_ms)
        return
    if isinstance(brain, OpenCodeBrain):
        _seed_opencode(brain, session_uuid, turns, last_updated_ms)
        return
    raise TypeError(
        f"seed_transcript: unsupported brain type {type(brain).__name__!r}. "
        "Add a seeding branch when introducing a new brain."
    )


def _iso_from_ms(ms: int) -> str:
    from datetime import datetime, timezone
    return (
        datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _seed_claude_code(
    brain: ClaudeCodeBrain,
    session_uuid: str,
    turns: Sequence[Turn],
    last_updated_ms: int | None,
) -> None:
    pdir = claude_session_jsonl_dir(brain._workspace)
    pdir.mkdir(parents=True, exist_ok=True)
    jsonl = pdir / f"{session_uuid}.jsonl"
    base = "2026-05-04T12:00:00Z"
    last_ts = _iso_from_ms(last_updated_ms) if last_updated_ms else base
    lines: list[str] = []
    for i, (role, text) in enumerate(turns):
        # Stamp the final turn at last_updated_ms so claude-code's
        # iter_session_metas (which derives recency from the last
        # message timestamp) agrees with opencode's session row.
        ts = last_ts if i == len(turns) - 1 else base
        if role == "user":
            lines.append(json.dumps({
                "type": "user",
                "uuid": f"u-{i}",
                "timestamp": ts,
                "message": {"role": "user", "content": text},
            }))
        else:
            lines.append(json.dumps({
                "type": "assistant",
                "uuid": f"a-{i}",
                "timestamp": ts,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": text}],
                },
            }))
    jsonl.write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
    )


def _seed_opencode(
    brain: OpenCodeBrain,
    session_uuid: str,
    turns: Sequence[Turn],
    last_updated_ms: int | None,
) -> None:
    db_path = opencode_db_path()
    _ensure_opencode_db(db_path)
    directory = str(brain._workspace.resolve())
    # time_updated drives the curator's recency gate; per-turn
    # timestamps only need to be monotonic for ordering.
    session_updated = last_updated_ms if last_updated_ms else (len(turns) or 1)
    conn = sqlite3.connect(db_path)
    try:
        # Idempotent re-seed: drop any prior rows for this session.
        conn.execute("DELETE FROM part WHERE session_id = ?", (session_uuid,))
        conn.execute("DELETE FROM message WHERE session_id = ?", (session_uuid,))
        conn.execute("DELETE FROM session WHERE id = ?", (session_uuid,))
        conn.execute(
            """INSERT INTO session
               (id, project_id, parent_id, slug, directory, title,
                version, time_created, time_updated)
               VALUES (?, 'proj-test', NULL, 'slug', ?, 'test session',
                       '1.0', 1, ?)""",
            (session_uuid, directory, session_updated),
        )
        for i, (role, text) in enumerate(turns, start=1):
            mid = f"{session_uuid}-m{i}"
            conn.execute(
                """INSERT INTO message
                   (id, session_id, time_created, time_updated, data)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    mid, session_uuid, i, i,
                    json.dumps({"role": role, "time": {"created": i}}),
                ),
            )
            conn.execute(
                """INSERT INTO part
                   (id, message_id, session_id, time_created,
                    time_updated, data)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    f"{mid}-p1", mid, session_uuid, i, i,
                    json.dumps({"type": "text", "text": text}),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _ensure_opencode_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_OPENCODE_SCHEMA)
        conn.commit()
    finally:
        conn.close()

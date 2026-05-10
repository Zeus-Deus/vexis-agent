"""Phase C Day 4: ``OpenCodeBrain`` session resume + harvest +
``SessionLost`` recovery.

These tests exercise the new session-token plumbing without
spawning a real ``opencode`` binary. We mock
``asyncio.create_subprocess_exec`` to return a fake process whose
stdout streams a pre-built JSON event log. The brain's read-side
code (``_read_opencode_event_stream``) parses real opencode shape;
only the subprocess transport is faked.

Test plan:

- **First call (fresh)**: spawns without ``--session``, with
  ``--title vexis-chat-<id>``. Harvests ``sessionID`` from the
  first event, persists via ``SessionStore.set``, marks
  initialised. Subsequent ``session_token()`` returns the
  harvested id.
- **Second call (resume)**: spawns with ``--session <stored_id>``
  (no ``--title``). The brain's stream reader locks onto the
  stored id so cross-session bus events are filtered.
- **SessionLost**: when ``--session`` references a dead id,
  opencode exits 1 with ``"Session not found"`` on stderr. The
  brain rotates the session (clearing the dead id and the
  initialised flag) and raises ``SessionLost`` so the transport-
  layer recovery can retry.

Design citation: ``.plans/brain-abstraction-research.md`` §5 Day 4.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from vexis_agent.core.brain.base import SessionLost
from vexis_agent.core.brain.opencode import OpenCodeBrain
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_yaml_config(monkeypatch, tmp_path):
    """Tier resolution reads ``~/.vexis/config.yaml`` — keep tests
    insulated from the user's real config."""
    from vexis_agent.core import yaml_config
    cfg_dir = tmp_path / "vexis-config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        yaml_config, "_config_path", lambda: cfg_dir / "config.yaml"
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


@pytest.fixture
def session_store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "sessions.json")


@pytest.fixture
def brain(workspace: Path, session_store: SessionStore) -> OpenCodeBrain:
    return OpenCodeBrain(
        workspace=workspace,
        session=session_store,
        running_tasks=RunningTasks(),
    )


# ──────────────────────────────────────────────────────────────────
# Subprocess fake
# ──────────────────────────────────────────────────────────────────


class _FakeStream:
    """Async stream that yields pre-loaded byte chunks, then EOF."""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)

    async def read(self) -> bytes:
        # ``proc.stderr.read()`` is awaited as one big slurp.
        out = b"".join(self._lines)
        self._lines = []
        return out


class _FakeProc:
    """Minimal async-subprocess stand-in for the brain's respond
    loop. Records argv + env so tests can assert on the spawn
    shape, and exposes a configurable returncode + stdout/stderr."""

    def __init__(
        self,
        *,
        stdout_lines: list[bytes],
        stderr_lines: list[bytes],
        returncode: int = 0,
        argv: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.stdout = _FakeStream(stdout_lines)
        self.stderr = _FakeStream(stderr_lines)
        self.returncode = returncode
        self.pid = 99999
        self.argv = argv or []
        self.env = env or {}

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        return None


def _evt(type_: str, session_id: str, **kw) -> bytes:
    """Serialise one opencode JSON event (newline-terminated)."""
    payload = {"type": type_, "timestamp": 0, "sessionID": session_id}
    payload.update(kw)
    return (json.dumps(payload) + "\n").encode("utf-8")


def _idle_event(session_id: str) -> bytes:
    """Terminal session.status idle event the stream reader breaks on."""
    return _evt(
        "session.status",
        session_id,
        properties={"sessionID": session_id, "status": {"type": "idle"}},
    )


def _build_fake_spawner(
    *,
    stdout_lines: list[bytes],
    stderr_lines: list[bytes] | None = None,
    returncode: int = 0,
    captured: dict | None = None,
):
    """Return an async fn that captures argv/env + returns a FakeProc."""

    async def _spawn(*argv, cwd=None, stdout=None, stderr=None,
                    start_new_session=False, env=None, limit=None):
        if captured is not None:
            captured["argv"] = list(argv)
            captured["env"] = dict(env or {})
            captured["cwd"] = cwd
        return _FakeProc(
            stdout_lines=stdout_lines,
            stderr_lines=stderr_lines or [],
            returncode=returncode,
            argv=list(argv),
            env=dict(env or {}),
        )

    return _spawn


# ──────────────────────────────────────────────────────────────────
# First call — fresh session, harvest + persist
# ──────────────────────────────────────────────────────────────────


def test_first_call_omits_session_flag_and_harvests_id(
    brain: OpenCodeBrain, session_store: SessionStore, monkeypatch
):
    """A SessionStore that has never been initialized:
      - argv must include ``--title vexis-chat-<id>`` (creating)
      - argv must NOT include ``--session``
      - ``sessionID`` from the first event must land in
        ``session_store.get()`` after the call.
      - ``initialized`` must flip to True so the next call resumes.
    """
    harvested_id = "ses_2HARVESTEDfrombrain"
    captured: dict = {}

    spawner = _build_fake_spawner(
        stdout_lines=[
            _evt("text", harvested_id, part={"text": "hello"}),
            _idle_event(harvested_id),
        ],
        captured=captured,
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.opencode.asyncio.create_subprocess_exec", spawner
    )

    reply = asyncio.run(brain.respond("hi", chat_id=42))

    assert reply == "hello"
    # argv shape — fresh session creates with --title, never --session.
    argv = captured["argv"]
    assert "--title" in argv
    title_idx = argv.index("--title")
    assert argv[title_idx + 1] == "vexis-chat-42"
    assert "--session" not in argv
    # The harvested id is now persisted.
    assert session_store.get() == harvested_id
    assert session_store.is_initialized() is True
    # session_token reads through to the same id.
    assert brain.session_token() == harvested_id


def test_first_call_raises_brain_error_on_empty_stream(
    brain: OpenCodeBrain, session_store: SessionStore, monkeypatch
):
    """Day 5 stream-EOF resilience: an opencode that exits 0 but
    emitted no events at all is a degraded state (transient SDK
    glitch, model produced nothing, or worse). Raise BrainError
    with diagnostic stderr so the transport surfaces something
    actionable rather than echoing a blank reply.

    Day 4 had this case silently warn-and-return-empty; Day 5
    promotes it to a hard error per the research doc's stream-EOF
    resilience requirement.
    """
    from vexis_agent.core.brain.base import BrainError

    spawner = _build_fake_spawner(
        stdout_lines=[],  # empty — no events
        stderr_lines=[b"some diagnostic from opencode\n"],
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.opencode.asyncio.create_subprocess_exec", spawner
    )

    with pytest.raises(BrainError) as ei:
        asyncio.run(brain.respond("hi", chat_id=1))
    # Error message includes the stderr context for debugging.
    assert "no events" in str(ei.value)
    assert "diagnostic from opencode" in str(ei.value)
    # Initialisation flag NOT flipped — the call failed.
    assert session_store.is_initialized() is False


def test_first_call_warns_when_events_but_no_session_id(
    brain: OpenCodeBrain, session_store: SessionStore, monkeypatch, caplog
):
    """Distinct from the empty-stream case above. If opencode
    emits events (so saw_any_event=True) but every event lacks a
    ``sessionID`` field (e.g. malformed events that still parse as
    JSON dicts), the harvest fails. Warn + mark initialised so the
    next call doesn't infinitely re-create — same trade-off Day 4
    chose. The reply text is still returned to the user.
    """
    import logging

    # An event without sessionID — saw_any_event=True, but
    # locked_session_id stays None.
    spawner = _build_fake_spawner(
        stdout_lines=[
            (json.dumps({"type": "text", "part": {"text": "hello"}}) + "\n").encode(),
        ],
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.opencode.asyncio.create_subprocess_exec", spawner
    )
    caplog.set_level(logging.WARNING)

    reply = asyncio.run(brain.respond("hi", chat_id=1))

    assert reply == "hello"
    assert session_store.is_initialized() is True
    # Warning surfaces the degraded path so a real bug doesn't slip
    # past silently.
    assert any(
        "no sessionID was harvested" in rec.message
        for rec in caplog.records
    )


# ──────────────────────────────────────────────────────────────────
# Subsequent call — resume via --session, lock target id
# ──────────────────────────────────────────────────────────────────


def test_second_call_passes_stored_session_id(
    brain: OpenCodeBrain, session_store: SessionStore, monkeypatch
):
    """After the first call has harvested + persisted, a second
    ``respond`` must spawn with ``--session <stored_id>`` and NOT
    ``--title`` (we don't rename the session on every resume)."""
    sid = "ses_2HARVESTEDfrombrain"
    # Manually pre-seed the store as if a first call already
    # happened — equivalent to the post-state of the first-call
    # test above.
    session_store.set(sid)
    session_store.mark_initialized()
    captured: dict = {}

    spawner = _build_fake_spawner(
        stdout_lines=[
            _evt("text", sid, part={"text": "follow-up"}),
            _idle_event(sid),
        ],
        captured=captured,
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.opencode.asyncio.create_subprocess_exec", spawner
    )

    reply = asyncio.run(brain.respond("hi again", chat_id=42))

    assert reply == "follow-up"
    argv = captured["argv"]
    assert "--session" in argv
    s_idx = argv.index("--session")
    assert argv[s_idx + 1] == sid
    # No --title on resume — opencode keeps the existing title.
    assert "--title" not in argv


def test_second_call_filters_cross_session_events(
    brain: OpenCodeBrain, session_store: SessionStore, monkeypatch
):
    """When the stream emits events with mixed sessionID values
    (opencode can multiplex), the reader must keep only events
    matching the target. Belt-and-braces — extracts shouldn't bleed
    in even though ``opencode run`` filters at its end too."""
    sid = "ses_target"
    other = "ses_OTHER"
    session_store.set(sid)
    session_store.mark_initialized()

    spawner = _build_fake_spawner(
        stdout_lines=[
            _evt("text", other, part={"text": "WRONG SESSION"}),
            _evt("text", sid, part={"text": "right session"}),
            _idle_event(sid),
        ],
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.opencode.asyncio.create_subprocess_exec", spawner
    )

    reply = asyncio.run(brain.respond("hi", chat_id=1))

    assert reply == "right session"
    assert "WRONG SESSION" not in reply


# ──────────────────────────────────────────────────────────────────
# SessionLost recovery
# ──────────────────────────────────────────────────────────────────


def test_session_not_found_stderr_raises_session_lost_and_rotates(
    brain: OpenCodeBrain, session_store: SessionStore, monkeypatch
):
    """When ``opencode run --session <dead_id>`` exits 1 with
    ``Session not found`` on stderr, the brain rotates the session
    (clears the dead id + flips initialised back to False) and
    raises ``SessionLost``. The transport-layer recovery is
    expected to retry — that's the existing brain-agnostic
    contract; we only test the rotation + raise here."""
    dead_id = "ses_DEADdeadDEAD"
    session_store.set(dead_id)
    session_store.mark_initialized()

    spawner = _build_fake_spawner(
        stdout_lines=[],
        stderr_lines=[b"Session not found\n"],
        returncode=1,
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.opencode.asyncio.create_subprocess_exec", spawner
    )

    with pytest.raises(SessionLost):
        asyncio.run(brain.respond("anyone home?", chat_id=7))

    # Dead id cleared, store rotated to a fresh placeholder, and
    # initialised flipped back to False so the next respond spawns
    # without --session.
    new_token = session_store.get()
    assert new_token != dead_id
    assert session_store.is_initialized() is False


def test_non_zero_exit_without_session_marker_is_brain_error(
    brain: OpenCodeBrain, session_store: SessionStore, monkeypatch
):
    """An opencode failure that ISN'T session-not-found (e.g. auth
    error, model timeout) must NOT trigger SessionLost rotation
    — that would discard a perfectly valid session id.
    """
    from vexis_agent.core.brain.base import BrainError

    sid = "ses_alive"
    session_store.set(sid)
    session_store.mark_initialized()

    spawner = _build_fake_spawner(
        stdout_lines=[],
        stderr_lines=[b"some other error: model timed out\n"],
        returncode=1,
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.opencode.asyncio.create_subprocess_exec", spawner
    )

    with pytest.raises(BrainError) as ei:
        asyncio.run(brain.respond("hi", chat_id=1))

    assert "session" not in str(ei.value).lower() or "lost" not in str(ei.value).lower()
    # Session id preserved — we only rotate on the canonical
    # session-not-found stderr marker.
    assert session_store.get() == sid
    assert session_store.is_initialized() is True


def test_session_not_found_on_first_call_does_not_rotate(
    brain: OpenCodeBrain, session_store: SessionStore, monkeypatch
):
    """SessionLost rotation only fires when ``is_initialized`` was
    True at spawn time. A fresh-session call that happens to
    surface ``Session not found`` (unlikely — fresh spawns don't
    pass --session) is treated as a generic BrainError, not a
    rotation trigger. Defends against accidentally rotating the
    placeholder UUID and losing it for a follow-up that COULD
    have succeeded with a retry on the same (uninitialised)
    state."""
    from vexis_agent.core.brain.base import BrainError

    # Fresh state — never initialised.
    initial_token = session_store.get()

    spawner = _build_fake_spawner(
        stdout_lines=[],
        stderr_lines=[b"Session not found\n"],
        returncode=1,
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.opencode.asyncio.create_subprocess_exec", spawner
    )

    with pytest.raises(BrainError):
        asyncio.run(brain.respond("hi", chat_id=1))

    # No rotation happened.
    assert session_store.get() == initial_token
    assert session_store.is_initialized() is False


# ──────────────────────────────────────────────────────────────────
# rotate_session contract
# ──────────────────────────────────────────────────────────────────


def test_rotate_session_clears_initialized(
    brain: OpenCodeBrain, session_store: SessionStore
):
    """``rotate_session`` (called by the SessionLost recovery path
    or by ``/clear``) must reset ``initialized`` to False so the
    next ``respond`` spawns fresh and harvests a new id."""
    session_store.set("ses_old")
    session_store.mark_initialized()
    assert session_store.is_initialized() is True

    new_token = brain.rotate_session()

    assert new_token != "ses_old"
    assert session_store.is_initialized() is False


# ──────────────────────────────────────────────────────────────────
# OPENCODE_CONFIG_CONTENT env injection always present
# ──────────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────────
# Day 5 — session.error typed-event detection (belt-and-braces
# alongside the stderr substring path Day 4 landed)
# ──────────────────────────────────────────────────────────────────


def test_session_error_event_triggers_session_lost(
    brain: OpenCodeBrain, session_store: SessionStore, monkeypatch
):
    """When opencode's JSON event stream contains an ``error`` event
    whose typed payload matches the session-not-found marker, the
    brain rotates the session and raises SessionLost — same
    contract as the stderr-substring path.

    This is the case where the session was deleted mid-stream
    (e.g. a ``opencode`` cleanup ran while our turn was in flight)
    rather than at spawn time. The proc may still exit 0 in this
    case because opencode's loop drains the bus before exiting.
    """
    sid = "ses_DEAD_mid_stream"
    session_store.set(sid)
    session_store.mark_initialized()

    error_event = {
        "type": "error",
        "sessionID": sid,
        "error": {
            "name": "NotFoundError",
            "data": {"message": f"Session not found: {sid}"},
        },
    }
    spawner = _build_fake_spawner(
        stdout_lines=[
            (json.dumps(error_event) + "\n").encode(),
        ],
        stderr_lines=[],
        returncode=0,  # opencode may exit 0 even with the typed error
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.opencode.asyncio.create_subprocess_exec", spawner
    )

    with pytest.raises(SessionLost):
        asyncio.run(brain.respond("anyone home?", chat_id=7))

    # Same rotation outcome as the stderr path.
    assert session_store.get() != sid
    assert session_store.is_initialized() is False


def test_session_error_unrelated_does_not_trigger_session_lost(
    brain: OpenCodeBrain, session_store: SessionStore, monkeypatch
):
    """An ``error`` event that's NOT session-not-found (e.g.
    model timeout, tool failure) must not trigger SessionLost.
    The brain logs it and continues — the underlying turn may
    still produce text, and opencode emits soft errors mid-stream
    without aborting the turn."""
    sid = "ses_alive"
    session_store.set(sid)
    session_store.mark_initialized()

    spawner = _build_fake_spawner(
        stdout_lines=[
            (json.dumps({
                "type": "error",
                "sessionID": sid,
                "error": {
                    "name": "ModelError",
                    "data": {"message": "model timed out"},
                },
            }) + "\n").encode(),
            (json.dumps({
                "type": "text",
                "sessionID": sid,
                "part": {"text": "recovered"},
            }) + "\n").encode(),
        ],
        returncode=0,
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.opencode.asyncio.create_subprocess_exec", spawner
    )

    # Should NOT raise SessionLost — proceeds and returns text.
    reply = asyncio.run(brain.respond("hi", chat_id=1))
    assert reply == "recovered"
    # Session preserved.
    assert session_store.get() == sid


def test_session_error_event_on_uninitialised_does_not_rotate(
    brain: OpenCodeBrain, session_store: SessionStore, monkeypatch
):
    """SessionLost rotation gates on ``is_initialized=True`` —
    matches the stderr path (Day 4). A NotFoundError event on a
    fresh-call respond is treated as a generic error rather than
    a rotation trigger.

    The event in this test omits ``sessionID`` so the brain doesn't
    accidentally harvest "ses_fresh" as the new session id — the
    isolated assertion here is "rotation didn't fire", separate
    from the harvest path tested elsewhere.
    """
    initial_token = session_store.get()
    # Fresh state: never initialised.
    assert session_store.is_initialized() is False

    error_event = {
        "type": "error",
        # No sessionID — so locked_session_id stays None and the
        # "first-call success path" doesn't write anything.
        "error": {
            "name": "NotFoundError",
            "data": {"message": "Session not found: ses_fresh"},
        },
    }
    spawner = _build_fake_spawner(
        stdout_lines=[
            (json.dumps(error_event) + "\n").encode(),
        ],
        returncode=0,
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.opencode.asyncio.create_subprocess_exec", spawner
    )

    # Must NOT raise SessionLost — we don't have a session to
    # rotate yet. Reply is empty (no text events).
    reply = asyncio.run(brain.respond("hi", chat_id=1))
    assert reply == ""
    # Token preserved (no harvest happened — saw an event but no
    # sessionID).
    assert session_store.get() == initial_token
    # First-call success path still flips initialised so the next
    # call doesn't infinitely re-create — same trade-off as the
    # warn-only "no sessionID" path.
    assert session_store.is_initialized() is True


def test_respond_sets_opencode_config_content_env(
    brain: OpenCodeBrain, session_store: SessionStore, monkeypatch
):
    """Whether resume or fresh, every respond spawn carries the
    OPENCODE_CONFIG_CONTENT env var so opencode picks up vexis's
    agent definition (system prompt, model, tool permission)."""
    sid = "ses_x"
    session_store.set(sid)
    session_store.mark_initialized()
    captured: dict = {}

    spawner = _build_fake_spawner(
        stdout_lines=[
            _evt("text", sid, part={"text": "ok"}),
            _idle_event(sid),
        ],
        captured=captured,
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.opencode.asyncio.create_subprocess_exec", spawner
    )

    asyncio.run(brain.respond("hi", chat_id=1))

    assert "OPENCODE_CONFIG_CONTENT" in captured["env"]
    raw = captured["env"]["OPENCODE_CONFIG_CONTENT"]
    parsed = json.loads(raw)
    assert "agent" in parsed
    assert "vexis" in parsed["agent"]
    assert "prompt" in parsed["agent"]["vexis"]

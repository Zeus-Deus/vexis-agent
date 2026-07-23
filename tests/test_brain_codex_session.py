"""``CodexBrain`` session resume + harvest + ``SessionLost`` recovery.

Exercises the session-token plumbing without spawning a real
``codex`` binary: ``asyncio.create_subprocess_exec`` is mocked with a
fake process whose stdout streams a pre-built codex JSONL event log.
Only the subprocess transport is faked — the read-side parser
(``_read_codex_event_stream``) sees real codex event shapes.

Test plan:

- **Fresh call**: spawns WITHOUT ``resume``, prompt last,
  ``developer_instructions`` present, no ``--profile`` when no
  vexis.config.toml. Harvests ``thread.started.thread_id``, persists
  via ``SessionStore.set``, marks initialised.
- **Resume call**: options precede ``resume <id> <message>``.
- **SessionLost**: a resume against an unknown id exits non-zero with
  ``no rollout found for thread id`` on stderr → rotate + raise.
- **Last-of-multiple agent_message wins**; empty stream → BrainError;
  per-turn model / reasoning overrides; ``session`` kwarg rerouting.

Design lock: ``.plans/codex-brain-research.md`` §2.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from vexis_agent.core.brain.base import BrainError, SessionLost
from vexis_agent.core.brain.codex import CodexBrain
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_yaml_config(monkeypatch, tmp_path):
    from vexis_agent.core import yaml_config
    cfg_dir = tmp_path / "vexis-config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        yaml_config, "_config_path", lambda: cfg_dir / "config.yaml"
    )


@pytest.fixture(autouse=True)
def _isolated_codex_home(monkeypatch, tmp_path):
    """Empty tmp CODEX_HOME so the ``--profile vexis`` existence check
    is False (no vexis.config.toml) unless a test writes one."""
    home = tmp_path / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CODEX_HOME", str(home))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


@pytest.fixture
def session_store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "sessions.json")


@pytest.fixture
def brain(workspace: Path, session_store: SessionStore) -> CodexBrain:
    return CodexBrain(
        workspace=workspace,
        session=session_store,
        running_tasks=RunningTasks(),
    )


# ──────────────────────────────────────────────────────────────────
# Subprocess fake
# ──────────────────────────────────────────────────────────────────


class _FakeStream:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    async def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)

    async def read(self) -> bytes:
        out = b"".join(self._lines)
        self._lines = []
        return out


class _FakeProc:
    def __init__(
        self,
        *,
        stdout_lines: list[bytes],
        stderr_lines: list[bytes],
        returncode: int = 0,
    ) -> None:
        self.stdout = _FakeStream(stdout_lines)
        self.stderr = _FakeStream(stderr_lines)
        self.returncode = returncode
        self.pid = 99999

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        return None


def _codex_event(type_: str, **kw) -> bytes:
    payload = {"type": type_}
    payload.update(kw)
    return (json.dumps(payload) + "\n").encode("utf-8")


def _build_fake_spawner(
    *,
    stdout_lines: list[bytes],
    stderr_lines: list[bytes] | None = None,
    returncode: int = 0,
    captured: dict | None = None,
):
    async def _spawn(*argv, cwd=None, stdin=None, stdout=None, stderr=None,
                    start_new_session=False, env=None, limit=None):
        if captured is not None:
            captured["argv"] = list(argv)
            captured["env"] = dict(env or {})
            captured["cwd"] = cwd
            captured["stdin"] = stdin
        return _FakeProc(
            stdout_lines=stdout_lines,
            stderr_lines=stderr_lines or [],
            returncode=returncode,
        )

    return _spawn


# ──────────────────────────────────────────────────────────────────
# Fresh call — harvest + persist thread id
# ──────────────────────────────────────────────────────────────────


def test_first_call_omits_resume_and_harvests_thread_id(
    brain: CodexBrain, session_store: SessionStore, monkeypatch
):
    thread_id = "019f8d9b-3338-7d33-9861-dd63e92718de"
    captured: dict = {}

    spawner = _build_fake_spawner(
        stdout_lines=[
            _codex_event("thread.started", thread_id=thread_id),
            _codex_event("turn.started"),
            _codex_event(
                "item.completed",
                item={"id": "item_0", "type": "agent_message", "text": "pong"},
            ),
            _codex_event("turn.completed"),
        ],
        captured=captured,
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.codex.asyncio.create_subprocess_exec", spawner
    )

    reply = asyncio.run(brain.respond("ping", chat_id=42))

    assert reply == "pong"
    argv = captured["argv"]
    # Fresh spawn: no resume subcommand; prompt is the last positional.
    assert "resume" not in argv
    assert argv[-1] == "ping"
    # developer_instructions injected (one argv element with the value).
    assert any(
        a.startswith("developer_instructions=") for a in argv
    )
    # No --profile when no vexis.config.toml exists in CODEX_HOME.
    assert "--profile" not in argv
    # stdin piped from DEVNULL.
    assert captured["stdin"] == asyncio.subprocess.DEVNULL
    # Harvested id persisted + initialised.
    assert session_store.get() == thread_id
    assert session_store.is_initialized() is True
    assert brain.session_token() == thread_id


def test_first_call_empty_stream_raises_brain_error(
    brain: CodexBrain, session_store: SessionStore, monkeypatch
):
    spawner = _build_fake_spawner(
        stdout_lines=[],
        stderr_lines=[b"some diagnostic from codex\n"],
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.codex.asyncio.create_subprocess_exec", spawner
    )
    with pytest.raises(BrainError) as ei:
        asyncio.run(brain.respond("hi", chat_id=1))
    assert "no events" in str(ei.value)
    assert "diagnostic from codex" in str(ei.value)
    assert session_store.is_initialized() is False


def test_first_call_warns_when_no_thread_id(
    brain: CodexBrain, session_store: SessionStore, monkeypatch, caplog
):
    """Events arrived but no thread.started — warn + mark initialised
    so the next call doesn't infinitely re-create. Reply still returned."""
    import logging

    spawner = _build_fake_spawner(
        stdout_lines=[
            _codex_event(
                "item.completed",
                item={"type": "agent_message", "text": "hello"},
            ),
        ],
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.codex.asyncio.create_subprocess_exec", spawner
    )
    caplog.set_level(logging.WARNING)

    reply = asyncio.run(brain.respond("hi", chat_id=1))
    assert reply == "hello"
    assert session_store.is_initialized() is True
    assert any(
        "no thread_id was harvested" in rec.message for rec in caplog.records
    )


def test_last_agent_message_is_final_text(
    brain: CodexBrain, monkeypatch
):
    """A turn narrates between tools then answers — the LAST
    agent_message is the canonical reply."""
    spawner = _build_fake_spawner(
        stdout_lines=[
            _codex_event("thread.started", thread_id="t1"),
            _codex_event(
                "item.completed",
                item={"type": "agent_message", "text": "let me check..."},
            ),
            _codex_event(
                "item.completed",
                item={
                    "type": "command_execution",
                    "command": "/bin/ls",
                    "status": "completed",
                },
            ),
            _codex_event(
                "item.completed",
                item={"type": "agent_message", "text": "the answer is 42"},
            ),
            _codex_event("turn.completed"),
        ],
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.codex.asyncio.create_subprocess_exec", spawner
    )
    reply = asyncio.run(brain.respond("q", chat_id=1))
    assert reply == "the answer is 42"


def test_per_turn_model_and_reasoning_overrides(
    brain: CodexBrain, monkeypatch
):
    captured: dict = {}
    spawner = _build_fake_spawner(
        stdout_lines=[
            _codex_event("thread.started", thread_id="t1"),
            _codex_event(
                "item.completed",
                item={"type": "agent_message", "text": "ok"},
            ),
        ],
        captured=captured,
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.codex.asyncio.create_subprocess_exec", spawner
    )
    asyncio.run(
        brain.respond(
            "hi", chat_id=1, model="gpt-5.6-sol", reasoning_level="high",
        )
    )
    argv = captured["argv"]
    assert argv[argv.index("-m") + 1] == "gpt-5.6-sol"
    assert 'model_reasoning_effort="high"' in " ".join(argv)


def test_profile_flag_present_when_vexis_config_exists(
    brain: CodexBrain, monkeypatch
):
    """When ``$CODEX_HOME/vexis.config.toml`` exists (write_mcp_config
    has run), every spawn adds ``--profile vexis``."""
    captured: dict = {}
    # Create the profile file so _profile_args() fires.
    from vexis_agent.core.brain.codex import codex_home, _VEXIS_PROFILE_FILE
    (codex_home() / _VEXIS_PROFILE_FILE).write_text("", encoding="utf-8")

    spawner = _build_fake_spawner(
        stdout_lines=[
            _codex_event("thread.started", thread_id="t1"),
            _codex_event(
                "item.completed",
                item={"type": "agent_message", "text": "ok"},
            ),
        ],
        captured=captured,
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.codex.asyncio.create_subprocess_exec", spawner
    )
    asyncio.run(brain.respond("hi", chat_id=1))
    argv = captured["argv"]
    assert "--profile" in argv
    assert argv[argv.index("--profile") + 1] == "vexis"


# ──────────────────────────────────────────────────────────────────
# Resume call — options precede `resume <id> <message>`
# ──────────────────────────────────────────────────────────────────


def test_resume_argv_ordering(
    brain: CodexBrain, session_store: SessionStore, monkeypatch
):
    tid = "019f8d9b-3338-7d33-9861-dd63e92718de"
    session_store.set(tid)
    session_store.mark_initialized()
    captured: dict = {}

    spawner = _build_fake_spawner(
        stdout_lines=[
            _codex_event(
                "item.completed",
                item={"type": "agent_message", "text": "follow-up"},
            ),
            _codex_event("turn.completed"),
        ],
        captured=captured,
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.codex.asyncio.create_subprocess_exec", spawner
    )

    reply = asyncio.run(brain.respond("again", chat_id=42))
    assert reply == "follow-up"
    argv = captured["argv"]
    # `resume <id> <message>` at the very end, options before it.
    r = argv.index("resume")
    assert argv[r + 1] == tid
    assert argv[r + 2] == "again"
    assert argv[-2:] == [tid, "again"]
    # OPTIONS must precede resume — the flags all appear before r.
    assert argv.index("--json") < r
    assert argv.index("-C") < r


# ──────────────────────────────────────────────────────────────────
# SessionLost recovery
# ──────────────────────────────────────────────────────────────────


def test_rollout_not_found_stderr_raises_session_lost_and_rotates(
    brain: CodexBrain, session_store: SessionStore, monkeypatch
):
    dead_id = "019f8d9b-DEAD-7d33-9861-dd63e92718de"
    session_store.set(dead_id)
    session_store.mark_initialized()

    spawner = _build_fake_spawner(
        stdout_lines=[],
        stderr_lines=[
            b"Error: thread/resume: thread/resume failed: no rollout "
            b"found for thread id " + dead_id.encode() + b" (code -32600)\n"
        ],
        returncode=1,
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.codex.asyncio.create_subprocess_exec", spawner
    )

    with pytest.raises(SessionLost):
        asyncio.run(brain.respond("anyone home?", chat_id=7))

    assert session_store.get() != dead_id
    assert session_store.is_initialized() is False


def test_non_session_error_is_brain_error_not_rotation(
    brain: CodexBrain, session_store: SessionStore, monkeypatch
):
    tid = "019f8d9b-alive-7d33-9861-dd63e92718de"
    session_store.set(tid)
    session_store.mark_initialized()

    spawner = _build_fake_spawner(
        stdout_lines=[],
        stderr_lines=[b"some other error: model timed out\n"],
        returncode=1,
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.codex.asyncio.create_subprocess_exec", spawner
    )

    with pytest.raises(BrainError):
        asyncio.run(brain.respond("hi", chat_id=1))
    # Session preserved — only the rollout-not-found marker rotates.
    assert session_store.get() == tid
    assert session_store.is_initialized() is True


def test_rollout_not_found_on_first_call_does_not_rotate(
    brain: CodexBrain, session_store: SessionStore, monkeypatch
):
    """SessionLost gates on ``is_initialized`` — a fresh call that
    somehow surfaces the marker is a generic BrainError, not a
    rotation trigger."""
    initial_token = session_store.get()

    spawner = _build_fake_spawner(
        stdout_lines=[],
        stderr_lines=[b"no rollout found for thread id whatever\n"],
        returncode=1,
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.codex.asyncio.create_subprocess_exec", spawner
    )

    with pytest.raises(BrainError):
        asyncio.run(brain.respond("hi", chat_id=1))
    assert session_store.get() == initial_token
    assert session_store.is_initialized() is False


# ──────────────────────────────────────────────────────────────────
# session kwarg (SessionLike) reroute
# ──────────────────────────────────────────────────────────────────


def test_session_kwarg_reroutes_harvest(
    brain: CodexBrain, session_store: SessionStore, tmp_path, monkeypatch
):
    """A non-None ``session`` handle (issue #48) routes harvest/persist
    through THAT session, leaving the bound active store untouched."""
    other = SessionStore(tmp_path / "other-session.json")
    thread_id = "019f8d9b-OTHER-7d33-9861-dd63e92718de"

    spawner = _build_fake_spawner(
        stdout_lines=[
            _codex_event("thread.started", thread_id=thread_id),
            _codex_event(
                "item.completed",
                item={"type": "agent_message", "text": "ok"},
            ),
        ],
    )
    monkeypatch.setattr(
        "vexis_agent.core.brain.codex.asyncio.create_subprocess_exec", spawner
    )

    asyncio.run(brain.respond("hi", chat_id=1, session=other))

    # The passed handle got the harvested id; the bound store did not.
    assert other.get() == thread_id
    assert other.is_initialized() is True
    assert session_store.get() != thread_id
    assert session_store.is_initialized() is False

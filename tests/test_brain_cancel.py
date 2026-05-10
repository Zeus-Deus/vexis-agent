"""Tests for ClaudeCodeBrain integration with RunningTasks.

We patch asyncio.create_subprocess_exec to return a fake proc instead of
spawning real `claude -p`. The fake proc lets us drive the lifecycle:
normal exit, timeout, cancel-mid-call, or cancel-during-spawn.

Tests follow the codebase convention of sync test functions calling
asyncio.run() rather than pytest-asyncio.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path

import pytest

from vexis_agent.core.brain import claude_code as brain_module
from vexis_agent.core.brain.claude_code import (
    BrainCancelled,
    BrainError,
    BrainTimeoutError,
    ClaudeCodeBrain,
)
from vexis_agent.core import paths, status as status_module
from vexis_agent.core.running_tasks import RunningTasks, TaskAlreadyRunning


def _stream_json_result(text: str) -> bytes:
    """Build a minimal stream-json stdout sequence for a successful turn:
    one system/init then one result event with the given final text."""
    return (
        json.dumps(
            {"type": "system", "subtype": "init", "session_id": "test"}
        ).encode()
        + b"\n"
        + json.dumps(
            {"type": "result", "subtype": "success", "result": text}
        ).encode()
        + b"\n"
    )


class _FakeStream:
    """asyncio.StreamReader stand-in.

    Pre-loaded `data` is consumed by readline()/read(); after that
    they return EOF (b""). For 'hang' mode tests we never load data —
    the brain only reads once the proc exits, by which time finish()
    has flagged that there's nothing to read.
    """

    def __init__(self, data: bytes = b"") -> None:
        self._buf = data
        self._exhausted = not data

    async def readline(self) -> bytes:
        if not self._buf:
            self._exhausted = True
            return b""
        nl = self._buf.find(b"\n")
        if nl < 0:
            line, self._buf = self._buf, b""
            self._exhausted = True
            return line
        line, self._buf = self._buf[: nl + 1], self._buf[nl + 1 :]
        if not self._buf:
            self._exhausted = True
        return line

    async def read(self) -> bytes:
        out, self._buf = self._buf, b""
        self._exhausted = True
        return out


class FakeProc:
    """Programmable stand-in for asyncio.subprocess.Process.

    Tests script the behavior by setting `mode`:
      - "ok":   wait() returns rc=0 immediately; stdout pre-loaded.
      - "fail": wait() returns rc>0 immediately; stderr pre-loaded.
      - "hang": wait() blocks until finish() is called.
    """

    def __init__(
        self,
        pid: int = 4242,
        mode: str = "ok",
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
    ) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self._mode = mode
        self._success_rc = returncode
        self._exit = asyncio.Event()
        self.signals: list[int] = []
        self.stdout = _FakeStream(stdout if mode != "hang" else b"")
        self.stderr = _FakeStream(stderr if mode != "hang" else b"")

    async def wait(self) -> int:
        if self._mode == "ok":
            self.returncode = self._success_rc
            return self.returncode
        if self._mode == "fail":
            self.returncode = self._success_rc or 1
            return self.returncode
        await self._exit.wait()
        assert self.returncode is not None
        return self.returncode

    def finish(self, returncode: int = -signal.SIGTERM) -> None:
        if self.returncode is None:
            self.returncode = returncode
            self._exit.set()


@pytest.fixture
def patch_runtime_dir(monkeypatch, tmp_path):
    """Redirect status.runtime_dir() at a tmpdir so the brain's status
    file writes don't touch /run/user/$UID."""
    monkeypatch.setattr(paths, "runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(status_module, "runtime_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def patch_killpg(monkeypatch):
    procs: dict[int, FakeProc] = {}

    def _getpgid(pid: int) -> int:
        return pid

    def _killpg(pgid: int, sig: int) -> None:
        proc = procs.get(pgid)
        if proc is None:
            raise ProcessLookupError(pgid)
        proc.signals.append(sig)
        if sig in (signal.SIGTERM, signal.SIGKILL):
            proc.finish(-sig)

    monkeypatch.setattr("vexis_agent.core.running_tasks.os.getpgid", _getpgid)
    monkeypatch.setattr("vexis_agent.core.running_tasks.os.killpg", _killpg)
    monkeypatch.setattr("vexis_agent.core.brain.claude_code.os.getpgid", _getpgid)
    monkeypatch.setattr("vexis_agent.core.brain.claude_code.os.killpg", _killpg)
    return procs


class FakeSession:
    """Minimal SessionStore stand-in. Always 'initialized' so we go down
    the --resume path without triggering rotate."""

    def __init__(self, uid: str = "00000000-0000-0000-0000-000000000001") -> None:
        self._uid = uid
        self._initialized = True

    def get(self) -> str:
        return self._uid

    def is_initialized(self) -> bool:
        return self._initialized

    def mark_initialized(self) -> None:
        self._initialized = True

    def rotate(self) -> str:
        self._uid = "00000000-0000-0000-0000-000000000002"
        return self._uid


def _build_brain(running_tasks: RunningTasks, tmp_path: Path) -> ClaudeCodeBrain:
    return ClaudeCodeBrain(
        workspace=tmp_path,
        session=FakeSession(),
        running_tasks=running_tasks,
    )


def _patch_spawn(monkeypatch, proc: FakeProc) -> None:
    async def _fake_spawn(*_argv, **_kwargs) -> FakeProc:
        return proc

    monkeypatch.setattr(brain_module.asyncio, "create_subprocess_exec", _fake_spawn)


def test_brain_registers_and_unregisters_on_normal_exit(
    monkeypatch, tmp_path, patch_killpg
):
    proc = FakeProc(pid=111, mode="ok", stdout=_stream_json_result("hello sir"))
    patch_killpg[111] = proc
    _patch_spawn(monkeypatch, proc)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> str:
        return await brain.respond("ping", chat_id=10)

    reply = asyncio.run(scenario())
    assert reply == "hello sir"
    assert not reg.is_running(10)


def test_brain_unregisters_on_brain_error(monkeypatch, tmp_path, patch_killpg):
    proc = FakeProc(pid=112, mode="fail", stdout=b"", stderr=b"boom", returncode=2)
    patch_killpg[112] = proc
    _patch_spawn(monkeypatch, proc)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> None:
        with pytest.raises(BrainError):
            await brain.respond("ping", chat_id=11)

    asyncio.run(scenario())
    assert not reg.is_running(11)


def test_brain_timeout_kills_proc_and_unregisters(monkeypatch, tmp_path, patch_killpg):
    proc = FakeProc(pid=113, mode="hang")
    patch_killpg[113] = proc
    _patch_spawn(monkeypatch, proc)
    monkeypatch.setattr(brain_module, "BRAIN_TIMEOUT_SECONDS", 0.05)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> None:
        with pytest.raises(BrainTimeoutError):
            await brain.respond("hang please", chat_id=12)

    asyncio.run(scenario())
    assert not reg.is_running(12)
    assert signal.SIGTERM in proc.signals


def test_brain_cancel_after_attach_raises_brain_cancelled(
    monkeypatch, tmp_path, patch_killpg
):
    proc = FakeProc(pid=114, mode="hang", stdout=b"partial", stderr=b"")
    patch_killpg[114] = proc
    _patch_spawn(monkeypatch, proc)
    monkeypatch.setattr(brain_module, "BRAIN_TIMEOUT_SECONDS", 5)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> None:
        respond_task = asyncio.create_task(brain.respond("work", chat_id=13))
        # Wait for the brain to reserve + attach the proc.
        for _ in range(100):
            if reg.is_running(13) and signal.SIGTERM not in proc.signals:
                # Reserved; small extra spin to ensure attach happened
                # before we cancel — otherwise we'd be testing the
                # reservation-window path instead.
                await asyncio.sleep(0.01)
                break
            await asyncio.sleep(0.01)
        cancelled = await reg.cancel(13)
        assert cancelled is True
        with pytest.raises(BrainCancelled):
            await respond_task

    asyncio.run(scenario())
    assert not reg.is_running(13)
    assert signal.SIGTERM in proc.signals


def test_brain_cancel_during_reservation_window_kills_spawned_proc(
    monkeypatch, tmp_path, patch_killpg
):
    """The race fix: /cancel arrives after reserve() but before the
    spawn returns. Brain's attach() gets False, kills the freshly-
    spawned proc, and raises BrainCancelled."""
    proc = FakeProc(pid=300, mode="hang")
    patch_killpg[300] = proc

    spawn_gate = asyncio.Event()
    cancel_done = asyncio.Event()

    async def _gated_spawn(*_argv, **_kwargs) -> FakeProc:
        # Tell the test the brain has reached spawn (slot is reserved),
        # then wait until the test fires /cancel before returning the proc.
        spawn_gate.set()
        await cancel_done.wait()
        return proc

    monkeypatch.setattr(brain_module.asyncio, "create_subprocess_exec", _gated_spawn)
    monkeypatch.setattr(brain_module, "BRAIN_TIMEOUT_SECONDS", 5)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> None:
        respond_task = asyncio.create_task(brain.respond("work", chat_id=20))
        await asyncio.wait_for(spawn_gate.wait(), timeout=1.0)
        # Brain has reserve()d but hasn't attach()ed yet.
        assert reg.is_running(20)
        cancelled = await reg.cancel(20)
        assert cancelled is True
        # Now release the spawn so attach() happens with cancelled=True.
        cancel_done.set()
        with pytest.raises(BrainCancelled):
            await respond_task

    asyncio.run(scenario())
    assert not reg.is_running(20)
    # Brain killed the orphan proc itself once attach returned False.
    assert signal.SIGTERM in proc.signals


def test_brain_concurrent_call_for_same_chat_raises_before_spawn(
    monkeypatch, tmp_path, patch_killpg
):
    """Reserve-first means the second respond() for a chat_id raises
    TaskAlreadyRunning before it even spawns a subprocess — the fix's
    side benefit: no leak to clean up because nothing was spawned."""
    first = FakeProc(pid=201, mode="hang")
    patch_killpg[201] = first

    spawn_calls = 0

    async def _fake_spawn(*_argv, **_kwargs) -> FakeProc:
        nonlocal spawn_calls
        spawn_calls += 1
        return first

    monkeypatch.setattr(brain_module.asyncio, "create_subprocess_exec", _fake_spawn)
    monkeypatch.setattr(brain_module, "BRAIN_TIMEOUT_SECONDS", 5)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> None:
        first_task = asyncio.create_task(brain.respond("a", chat_id=14))
        for _ in range(100):
            if reg.is_running(14):
                break
            await asyncio.sleep(0.01)
        assert reg.is_running(14)

        with pytest.raises(TaskAlreadyRunning):
            await brain.respond("b", chat_id=14)

        # Only the first call should have spawned a subprocess.
        assert spawn_calls == 1

        # Tear down the first call so the test exits cleanly.
        await reg.cancel(14)
        with pytest.raises(BrainCancelled):
            await first_task

    asyncio.run(scenario())
    assert not reg.is_running(14)


# --- StatusFile lifecycle inside the brain ---------------------------------


def _stream_json_with_tools(text: str, tool_uses: list[tuple[str, dict]]) -> bytes:
    """Build a stream-json sequence with one or more tool_use blocks
    in an assistant event, followed by a successful result event."""
    events = [{"type": "system", "subtype": "init", "session_id": "test"}]
    for name, tool_input in tool_uses:
        events.append(
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"toolu_{name}_{len(events)}",
                            "name": name,
                            "input": tool_input,
                        }
                    ],
                },
            }
        )
    events.append({"type": "result", "subtype": "success", "result": text})
    return b"".join(json.dumps(e).encode() + b"\n" for e in events)


def test_brain_writes_tool_events_to_status_file(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir
):
    """Stream-json with two tool_use events should land in the status
    file as tool_count=2 with last_tool/last_target reflecting the
    most recent one. We disable delete() so we can inspect the file
    after respond() returns; a separate test covers delete-in-finally.
    """
    stdout = _stream_json_with_tools(
        "done sir",
        [
            ("Edit", {"file_path": "core/foo.py"}),
            ("Bash", {"command": "git status"}),
        ],
    )
    proc = FakeProc(pid=120, mode="ok", stdout=stdout)
    patch_killpg[120] = proc
    _patch_spawn(monkeypatch, proc)
    # Suppress delete so the file survives for inspection.
    monkeypatch.setattr(status_module.StatusFile, "delete", lambda self: None)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> str:
        return await brain.respond("ping", chat_id=70)

    reply = asyncio.run(scenario())
    assert reply == "done sir"
    snap = status_module.read_status(70)
    assert snap is not None
    assert snap.tool_count == 2
    assert snap.last_tool == "Bash"
    assert snap.last_target == "git status"


def test_brain_deletes_status_file_on_normal_exit(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir
):
    proc = FakeProc(
        pid=121, mode="ok", stdout=_stream_json_result("ok")
    )
    patch_killpg[121] = proc
    _patch_spawn(monkeypatch, proc)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> None:
        await brain.respond("hi", chat_id=71)

    asyncio.run(scenario())
    assert not (patch_runtime_dir / "status-71.json").exists()


def test_brain_deletes_status_file_on_brain_error(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir
):
    proc = FakeProc(
        pid=122, mode="fail", stdout=b"", stderr=b"boom", returncode=2
    )
    patch_killpg[122] = proc
    _patch_spawn(monkeypatch, proc)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> None:
        with pytest.raises(BrainError):
            await brain.respond("hi", chat_id=72)

    asyncio.run(scenario())
    assert not (patch_runtime_dir / "status-72.json").exists()


def test_brain_deletes_status_file_on_timeout(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir
):
    proc = FakeProc(pid=123, mode="hang")
    patch_killpg[123] = proc
    _patch_spawn(monkeypatch, proc)
    monkeypatch.setattr(brain_module, "BRAIN_TIMEOUT_SECONDS", 0.05)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> None:
        with pytest.raises(BrainTimeoutError):
            await brain.respond("hang", chat_id=73)

    asyncio.run(scenario())
    assert not (patch_runtime_dir / "status-73.json").exists()


def test_brain_drains_high_volume_stream_without_stalling(
    monkeypatch, tmp_path, patch_runtime_dir
):
    """Regression: the previous proc.communicate() flow buffered all of
    stdout in memory, which masked the question of whether a slow
    reader could block the subprocess on a full pipe. We now read
    incrementally, so this test pumps ~3500 stream-json events
    (>500 KiB total, well past the 64 KiB pipe buffer) through a real
    subprocess and verifies every event lands in the status file.

    A reader that didn't keep up would either deadlock (subprocess
    blocked on write while we wait on its exit) or drop events. Either
    failure is caught here.
    """
    import sys

    n_events = 3500
    emitter = (
        "import json\n"
        "print(json.dumps({'type':'system','subtype':'init','session_id':'x'}))\n"
        f"for i in range({n_events}):\n"
        "    print(json.dumps({"
        "'type':'assistant','message':{'role':'assistant','content':["
        "{'type':'tool_use','name':'Edit',"
        "'input':{'file_path': f'src/file_{i}.py'}}"
        "]}}))\n"
        "print(json.dumps({'type':'result','subtype':'success','result':'done'}))\n"
    )

    real_create = asyncio.create_subprocess_exec

    async def fake_create(*_argv, **kwargs):
        # Replace `claude -p ...` with a Python emitter that produces
        # the same stream-json shape. -u is critical: line-buffered
        # stdout means events flush as they're written, which is the
        # condition we want to stress-test.
        return await real_create(
            sys.executable,
            "-u",
            "-c",
            emitter,
            stdout=kwargs.get("stdout"),
            stderr=kwargs.get("stderr"),
        )

    monkeypatch.setattr(
        brain_module.asyncio, "create_subprocess_exec", fake_create
    )
    # Keep the status file around so we can verify tool_count after exit.
    monkeypatch.setattr(status_module.StatusFile, "delete", lambda self: None)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> str:
        return await asyncio.wait_for(
            brain.respond("ping", chat_id=300), timeout=30
        )

    reply = asyncio.run(scenario())
    assert reply == "done"
    snap = status_module.read_status(300)
    assert snap is not None
    assert snap.tool_count == n_events
    assert snap.last_tool == "Edit"
    assert snap.last_target == f"src/file_{n_events - 1}.py"


def test_brain_deletes_status_file_on_cancel(
    monkeypatch, tmp_path, patch_killpg, patch_runtime_dir
):
    proc = FakeProc(pid=124, mode="hang")
    patch_killpg[124] = proc
    _patch_spawn(monkeypatch, proc)
    monkeypatch.setattr(brain_module, "BRAIN_TIMEOUT_SECONDS", 5)

    reg = RunningTasks()
    brain = _build_brain(reg, tmp_path)

    async def scenario() -> None:
        respond_task = asyncio.create_task(brain.respond("work", chat_id=74))
        for _ in range(100):
            if reg.is_running(74) and signal.SIGTERM not in proc.signals:
                await asyncio.sleep(0.01)
                break
            await asyncio.sleep(0.01)
        await reg.cancel(74)
        with pytest.raises(BrainCancelled):
            await respond_task

    asyncio.run(scenario())
    assert not (patch_runtime_dir / "status-74.json").exists()


# ──────────────────────────────────────────────────────────────────
# Orphaned session-lock recovery (post-cancel "Session ID already
# in use" bug).
#
# When ``claude -p --resume <uuid>`` is SIGKILLed, claude-code
# leaves a ``~/.claude/tasks/<uuid>/.lock`` file behind. The next
# spawn against the same UUID exits 1 with::
#
#     Error: Session ID <uuid> is already in use.
#
# Fix: ``_scrub_orphaned_session_lock`` runs before each spawn in
# both :meth:`respond` and :meth:`astream`. These tests pin the
# helper's behavior + the spawn-site integration.
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def patch_claude_home(monkeypatch, tmp_path):
    """Redirect ``Path.home()`` in claude_code.py to a tmpdir so
    the JSONL existence check (used to decide --session-id vs
    --resume) reads from a sandbox, not the real ``~/.claude``.
    Yields the fake home so tests can pre-seed transcript files."""
    fake_home = tmp_path / "fake-home"
    (fake_home / ".claude" / "projects").mkdir(parents=True)
    real_path_cls = brain_module.Path

    class _PatchedPath(type(brain_module.Path())):
        @classmethod
        def home(cls):
            return fake_home

    monkeypatch.setattr(brain_module, "Path", _PatchedPath)
    yield fake_home
    monkeypatch.setattr(brain_module, "Path", real_path_cls)


def _seed_jsonl(home: Path, workspace: Path, session_id: str) -> Path:
    """Pre-create ``<home>/.claude/projects/<encoded-cwd>/<uuid>.jsonl``
    matching claude-code's on-disk layout. Returns the path so
    tests can verify it stayed put / got read."""
    encoded = "-" + str(workspace).strip("/").replace("/", "-")
    proj = home / ".claude" / "projects" / encoded
    proj.mkdir(parents=True, exist_ok=True)
    jsonl = proj / f"{session_id}.jsonl"
    jsonl.write_text('{"role":"system"}\n', encoding="utf-8")
    return jsonl


def test_session_jsonl_exists_returns_true_when_present(
    patch_claude_home, tmp_path,
):
    """Disk check returns True when the transcript file is on
    disk — what claude-code uses to decide 'in use'."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    uuid = "11111111-2222-3333-4444-555555555555"
    _seed_jsonl(patch_claude_home, workspace, uuid)
    assert brain_module._session_jsonl_exists(workspace, uuid) is True


def test_session_jsonl_exists_returns_false_when_absent(
    patch_claude_home, tmp_path,
):
    """No transcript = fresh session — caller will use
    ``--session-id`` to pin the UUID for the first time."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    uuid = "22222222-2222-3333-4444-555555555555"
    assert brain_module._session_jsonl_exists(workspace, uuid) is False


def test_session_jsonl_exists_idempotent_when_projects_dir_missing(
    patch_claude_home, tmp_path,
):
    """First-ever-claude-run case: the projects dir doesn't even
    exist yet. Helper must return False without raising."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # Remove the projects dir entirely.
    import shutil as _shutil
    _shutil.rmtree(patch_claude_home / ".claude" / "projects")
    uuid = "33333333-2222-3333-4444-555555555555"
    assert brain_module._session_jsonl_exists(workspace, uuid) is False


def test_session_jsonl_exists_uses_correct_workspace_encoding(
    patch_claude_home, tmp_path,
):
    """Workspace path encoding: ``/foo/bar`` → ``-foo-bar``. Test
    that the encoding matches what claude actually uses on disk —
    a mismatch would make us always say 'doesn't exist' and the
    fix would silently be a no-op."""
    workspace = tmp_path / "deep" / "nested" / "workspace"
    workspace.mkdir(parents=True)
    uuid = "44444444-2222-3333-4444-555555555555"
    seeded = _seed_jsonl(patch_claude_home, workspace, uuid)
    # The seeded path is the truth; check our helper finds it.
    assert seeded.exists()
    assert brain_module._session_jsonl_exists(workspace, uuid) is True


class _FakeUninitializedSession(FakeSession):
    """Session whose in-memory flag says NOT initialized — even
    though the transcript may already exist on disk. Reproduces
    the post-cancel scenario where ``mark_initialized`` was
    never called because the cancelled turn raised before
    reaching the success path."""
    def __init__(self, uid: str = "00000000-0000-0000-0000-000000000001") -> None:
        super().__init__(uid)
        self._initialized = False


def test_respond_uses_resume_when_jsonl_exists_despite_uninitialized(
    monkeypatch, tmp_path, patch_killpg, patch_claude_home,
):
    """The actual fix: even if ``is_initialized()`` returns False
    (cancel-mid-turn left the in-memory flag stale), the disk
    check sees the JSONL and we use ``--resume``. Without this,
    claude exits 1 with 'Session ID already in use'."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    uuid = "00000000-0000-0000-0000-000000000001"
    # Pre-seed the transcript as if the previous (cancelled)
    # turn already wrote partial content.
    _seed_jsonl(patch_claude_home, workspace, uuid)

    # Capture argv that goes to subprocess so we can assert which
    # session flag the brain chose.
    captured_argv: list[list[str]] = []

    proc = FakeProc(
        pid=300, mode="ok",
        stdout=_stream_json_result("recovered after cancel"),
    )
    patch_killpg[300] = proc

    async def _fake_spawn(*argv, **_kwargs) -> FakeProc:
        captured_argv.append(list(argv))
        return proc

    monkeypatch.setattr(
        brain_module.asyncio, "create_subprocess_exec", _fake_spawn,
    )

    reg = RunningTasks()
    brain = ClaudeCodeBrain(
        workspace=workspace,
        session=_FakeUninitializedSession(uid=uuid),
        running_tasks=reg,
    )

    async def scenario() -> str:
        return await brain.respond("hello again", chat_id=80)

    reply = asyncio.run(scenario())
    assert reply == "recovered after cancel"

    # The decisive assertion: argv contains --resume (not
    # --session-id) because the JSONL existed on disk.
    assert len(captured_argv) == 1
    argv = captured_argv[0]
    assert "--resume" in argv, (
        f"expected --resume on post-cancel respawn, got argv={argv}. "
        "claude-code rejects --session-id when the JSONL exists."
    )
    assert "--session-id" not in argv, (
        "must not pass --session-id when the JSONL already exists "
        "— claude exits 1 with 'Session ID already in use'"
    )


def test_astream_uses_resume_when_jsonl_exists_despite_uninitialized(
    monkeypatch, tmp_path, patch_killpg, patch_claude_home,
):
    """Same fix on the streaming path — the *hottest* path for
    the bug because the web chat's Stop button fires here."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    uuid = "00000000-0000-0000-0000-000000000001"
    _seed_jsonl(patch_claude_home, workspace, uuid)

    captured_argv: list[list[str]] = []

    proc = FakeProc(
        pid=301, mode="ok",
        stdout=_stream_json_result("streamed after cancel"),
    )
    patch_killpg[301] = proc

    async def _fake_spawn(*argv, **_kwargs) -> FakeProc:
        captured_argv.append(list(argv))
        return proc

    monkeypatch.setattr(
        brain_module.asyncio, "create_subprocess_exec", _fake_spawn,
    )

    reg = RunningTasks()
    brain = ClaudeCodeBrain(
        workspace=workspace,
        session=_FakeUninitializedSession(uid=uuid),
        running_tasks=reg,
    )

    async def scenario() -> list[str]:
        out: list[str] = []
        async for chunk in brain.astream("hi", chat_id=81):
            out.append(chunk)
        return out

    chunks = asyncio.run(scenario())
    assert "".join(chunks) == "streamed after cancel"
    argv = captured_argv[0]
    assert "--resume" in argv, (
        f"astream must use --resume when JSONL exists; got {argv}"
    )
    assert "--session-id" not in argv


def test_respond_uses_session_id_when_no_jsonl(
    monkeypatch, tmp_path, patch_killpg, patch_claude_home,
):
    """Regression guard: the very first turn (no JSONL on disk,
    flag still False) MUST use ``--session-id`` to pin the UUID.
    Switching unconditionally to ``--resume`` would make claude
    error with 'No conversation found' on first send."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    uuid = "00000000-0000-0000-0000-000000000001"
    # No JSONL seeded — fresh session.

    captured_argv: list[list[str]] = []

    proc = FakeProc(
        pid=302, mode="ok",
        stdout=_stream_json_result("first turn ok"),
    )
    patch_killpg[302] = proc

    async def _fake_spawn(*argv, **_kwargs) -> FakeProc:
        captured_argv.append(list(argv))
        return proc

    monkeypatch.setattr(
        brain_module.asyncio, "create_subprocess_exec", _fake_spawn,
    )

    reg = RunningTasks()
    brain = ClaudeCodeBrain(
        workspace=workspace,
        session=_FakeUninitializedSession(uid=uuid),
        running_tasks=reg,
    )

    async def scenario() -> str:
        return await brain.respond("first message", chat_id=82)

    reply = asyncio.run(scenario())
    assert reply == "first turn ok"
    argv = captured_argv[0]
    assert "--session-id" in argv, (
        "first turn must use --session-id to pin the UUID; "
        f"got argv={argv}"
    )
    assert "--resume" not in argv

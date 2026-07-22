"""Issue #66 — regression coverage against the REAL ``opencode``
binary for the ``--dir`` duplication crash.

Background (verified against opencode 1.18.4):
- A single ``--dir`` works in every argv shape Vexis emits.
- Passing ``--dir`` TWICE crashes opencode 1.18.4 with
  ``The "paths[1]" property must be of type string, got array``
  — yargs collapses a repeated ``type: "string"`` flag into an
  array and opencode's project-dir resolution rejects it. That
  resolution runs during CLI startup, BEFORE any model call.
- Vexis passes ``--dir`` exactly once at both spawn sites
  (``respond`` foreground + ``spawn_aux``). ``--dir`` must stay
  (issue #64 — cwd alone is not authoritative for opencode's
  project-dir resolution inside containers).

These tests capture the genuine argv the brain emits (via the
same monkeypatched-spawn technique the mock-only suites use, so
they stay in sync with the real code) and then execute that argv
against the real binary under a fully isolated environment.

Isolation contract:
- ``HOME`` + all ``XDG_*`` dirs point at throwaway tmp dirs, so
  opencode never reads the user's real config, session db, or
  credentials, and never writes to them.
- ``VEXIS_HOME`` points at a tmp dir — the live Vexis deployment
  (``~/.vexis`` / ``~/vexis-workspace``) is never touched.
- ``MISE_DATA_DIR`` is pinned at the user's real mise data dir so
  the opencode launcher resolves its already-installed node
  runtime instead of re-downloading one per run (speed only — not
  a correctness dependency).
- Every ``*_API_KEY`` is stripped and the agent's model is
  rewritten to a non-resolvable ``<provider>/<model>`` id, so the
  run fails at model resolution and NEVER completes a model turn.
  No tokens are spent; the metadata registry fetch opencode does
  on its own is the only network traffic, which is why the
  subprocess timeout is generous.

The regression assertion is specifically "argv survives CLI
parsing + project-dir resolution": combined stdout+stderr must
not contain the ``paths[1]`` / ``must be of type string`` crash
signature. A nonzero exit (model-resolution failure) is expected
and fine.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from vexis_agent.core.brain.opencode import OpenCodeBrain
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore

pytestmark = pytest.mark.skipif(
    shutil.which("opencode") is None,
    reason="opencode binary not on PATH — real-binary regression skipped",
)

# The #66 crash signature opencode 1.18.4 prints when ``--dir`` is
# duplicated. The regression run must never produce either fragment.
_CRASH_SIGNATURE_FRAGMENTS = ("paths[1]", "must be of type string")

# A deliberately non-resolvable model id. It forces opencode to
# fail at model resolution — after arg-parse + project-dir
# resolution (where #66 fires) but before any inference — so no
# tokens are ever spent by these tests.
_UNRESOLVABLE_MODEL = (
    "__vexis_regression_no_provider__/__vexis_regression_no_model__"
)

# Generous ceiling: the first isolated run pays for opencode's
# metadata registry fetch (models.dev) plus launcher warm-up on a
# clean XDG cache. Comfortably fast in practice (~1-6s), but the
# margin keeps a slow probe from flaking.
_REAL_RUN_TIMEOUT_SECONDS = 90.0


# ──────────────────────────────────────────────────────────────────
# Subprocess fakes — capture the genuine argv without launching
# ──────────────────────────────────────────────────────────────────


class _FakeStream:
    """Async stream yielding pre-loaded byte lines, then EOF."""

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
    """Minimal async-subprocess stand-in for the foreground capture."""

    def __init__(self, stdout_lines: list[bytes]) -> None:
        self.stdout = _FakeStream(stdout_lines)
        self.stderr = _FakeStream([])
        self.returncode = 0
        self.pid = 99999

    async def wait(self) -> int:
        return self.returncode

    def kill(self) -> None:
        return None


def _brain(tmp_path: Path) -> OpenCodeBrain:
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / "skills").mkdir(exist_ok=True)
    return OpenCodeBrain(
        workspace=ws,
        session=SessionStore(tmp_path / "sessions.json"),
        running_tasks=RunningTasks(),
    )


def _idle_line(session_id: str) -> bytes:
    return (
        json.dumps(
            {
                "type": "session.status",
                "timestamp": 0,
                "sessionID": session_id,
                "properties": {
                    "sessionID": session_id,
                    "status": {"type": "idle"},
                },
            }
        )
        + "\n"
    ).encode("utf-8")


def _text_line(session_id: str, text: str) -> bytes:
    return (
        json.dumps(
            {
                "type": "text",
                "timestamp": 0,
                "sessionID": session_id,
                "part": {"text": text},
            }
        )
        + "\n"
    ).encode("utf-8")


def _capture_foreground_argv(brain: OpenCodeBrain, monkeypatch) -> list[str]:
    """Drive ``respond`` with a faked spawn to record the exact argv
    the foreground fresh-session path emits."""
    sid = "ses_realbinaryFG"
    captured: dict = {}

    async def _spawn(*argv, cwd=None, stdout=None, stderr=None,
                     start_new_session=False, env=None, limit=None):
        captured["argv"] = list(argv)
        return _FakeProc([_text_line(sid, "ok"), _idle_line(sid)])

    monkeypatch.setattr(
        "vexis_agent.core.brain.opencode.asyncio.create_subprocess_exec",
        _spawn,
    )
    asyncio.run(brain.respond("hi", chat_id=7))
    return captured["argv"]


def _capture_aux_argv(brain: OpenCodeBrain, monkeypatch) -> list[str]:
    """Drive ``spawn_aux`` with a faked spawn to record the exact
    argv the aux path emits."""
    captured: dict = {}

    class _CP:
        stdout = b'{"type": "text", "sessionID": "s1", "part": {"text": "v"}}\n'
        stderr = b""
        returncode = 0

    def _fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        return _CP()

    monkeypatch.setattr(
        "vexis_agent.core.brain.opencode.subprocess.run", _fake_run
    )
    asyncio.run(brain.spawn_aux("regression prompt", model_tier="small"))
    return captured["argv"]


# ──────────────────────────────────────────────────────────────────
# Isolated real-binary invocation
# ──────────────────────────────────────────────────────────────────


def _isolated_env(tmp_path: Path, agent_name: str) -> dict[str, str]:
    """Synthetic environment: throwaway HOME/XDG/VEXIS_HOME, no
    credentials, and an ``OPENCODE_CONFIG_CONTENT`` that pins the
    referenced agent to a non-resolvable model."""
    home = tmp_path / "iso_home"
    for sub in ("iso_home", "iso_config", "iso_data", "iso_cache", "iso_vexis"):
        (tmp_path / sub).mkdir(exist_ok=True)

    env = {
        k: v for k, v in os.environ.items()
        if not k.endswith("_API_KEY") and not k.endswith("_AUTH_TOKEN")
    }
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(tmp_path / "iso_config")
    env["XDG_DATA_HOME"] = str(tmp_path / "iso_data")
    env["XDG_CACHE_HOME"] = str(tmp_path / "iso_cache")
    env["VEXIS_HOME"] = str(tmp_path / "iso_vexis")
    # Keep the launcher's node runtime resolvable without a fresh
    # download — this is the user's real, already-populated mise
    # data dir (speed only; not read for any opencode state).
    env["MISE_DATA_DIR"] = env.get(
        "MISE_DATA_DIR", str(Path.home() / ".local" / "share" / "mise")
    )
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(
        {
            "agent": {
                agent_name: {"model": _UNRESOLVABLE_MODEL, "prompt": "x"}
            }
        }
    )
    return env


def _agent_name_from_argv(argv: list[str]) -> str:
    return argv[argv.index("--agent") + 1]


def _run_real(
    argv: list[str], env: dict[str, str], cwd: Path
) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        env=env,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=_REAL_RUN_TIMEOUT_SECONDS,
        check=False,
    )


def _combined_output(cp: subprocess.CompletedProcess) -> str:
    out = (cp.stdout or b"").decode("utf-8", errors="replace")
    err = (cp.stderr or b"").decode("utf-8", errors="replace")
    return out + err


# ──────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────


def test_real_binary_foreground_argv_survives_dir_resolution(
    tmp_path: Path, monkeypatch
):
    """Issue #66 — the genuine foreground argv (single ``--dir``)
    parses cleanly against the real opencode binary: no
    ``paths[1]`` / ``must be of type string`` crash. A nonzero exit
    from the forced model-resolution failure is expected and fine —
    the point is that argv survives CLI parsing + project-dir
    resolution."""
    brain = _brain(tmp_path)
    argv = _capture_foreground_argv(brain, monkeypatch)
    assert argv.count("--dir") == 1

    ws = tmp_path / "real_ws"
    ws.mkdir()
    env = _isolated_env(tmp_path, _agent_name_from_argv(argv))
    cp = _run_real(argv, env, ws)

    output = _combined_output(cp)
    for fragment in _CRASH_SIGNATURE_FRAGMENTS:
        assert fragment not in output, (
            f"issue #66 crash signature {fragment!r} in foreground "
            f"output:\n{output}"
        )


def test_real_binary_aux_argv_survives_dir_resolution(
    tmp_path: Path, monkeypatch
):
    """Issue #66 — same real-binary check for the ``spawn_aux``
    argv shape (single ``--dir``). No ``paths[1]`` crash; nonzero
    exit from model-resolution failure is fine."""
    brain = _brain(tmp_path)
    argv = _capture_aux_argv(brain, monkeypatch)
    assert argv.count("--dir") == 1

    ws = tmp_path / "real_ws"
    ws.mkdir()
    env = _isolated_env(tmp_path, _agent_name_from_argv(argv))
    cp = _run_real(argv, env, ws)

    output = _combined_output(cp)
    for fragment in _CRASH_SIGNATURE_FRAGMENTS:
        assert fragment not in output, (
            f"issue #66 crash signature {fragment!r} in aux "
            f"output:\n{output}"
        )


def test_real_binary_duplicate_dir_fails_fast(tmp_path: Path, monkeypatch):
    """Canary documenting the #66 trigger: a duplicated ``--dir``
    must fail fast rather than reach a successful model turn.

    As of opencode 1.18.4 the observed output is
    ``The "paths[1]" property must be of type string, got array``
    (yargs collapses the repeated ``type: "string"`` flag into an
    array). That exact string is NOT hard-asserted — upstream may
    change how it handles duplicate flags — so this asserts only
    the tolerant invariant: the process exits nonzero and never
    emits a successful step-finish. If opencode ever starts
    de-duplicating ``--dir``, the run still fails on the forced
    unresolvable model, keeping this canary green."""
    brain = _brain(tmp_path)
    argv = _capture_foreground_argv(brain, monkeypatch)
    assert argv.count("--dir") == 1

    ws = tmp_path / "real_ws"
    ws.mkdir()
    other = tmp_path / "real_ws_other"
    other.mkdir()
    # Inject the environment-level duplication the #66 reporter hit
    # (a second --dir from an opencode wrapper), right after Vexis's.
    dup_argv = list(argv)
    dir_idx = dup_argv.index("--dir")
    dup_argv[dir_idx + 2:dir_idx + 2] = ["--dir", str(other)]
    assert dup_argv.count("--dir") == 2

    env = _isolated_env(tmp_path, _agent_name_from_argv(argv))
    cp = _run_real(dup_argv, env, ws)

    output = _combined_output(cp)
    # Tolerant: no successful model turn reached.
    assert cp.returncode != 0
    assert '"reason":"stop"' not in output

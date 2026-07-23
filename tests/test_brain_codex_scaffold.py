"""Scaffold tests for ``core.brain.codex`` (mock-only).

Coverage:
- ABC-method shapes: ``CodexBrain`` instantiates as a ``Brain``,
  inspection methods are callable.
- instruction files (AGENTS.md + $CODEX_HOME/AGENTS.md).
- ``build_system_prompt`` INCLUDES vexis's skills index (codex's
  native skill discovery uses $CODEX_HOME/skills, not the workspace,
  so vexis must render its own index — the opposite of opencode).
- ``healthcheck``: missing binary, ``codex login status`` non-zero,
  and the ok path — via monkeypatched ``shutil.which`` + ``subprocess.run``.
- ``write_mcp_config``: TOML round-trip (parsed back with ``tomllib``)
  covering stdio env tables, remote ``bearer_token_env_var``, atomic
  write, idempotency.
- ``spawn_aux`` argv shape: ``--ephemeral``, ``--skip-git-repo-check``,
  the allowlist→sandbox flag mapping, ``-m`` only when the tier
  resolves, ``-c model_reasoning_effort`` when reasoning is set,
  ``stdin=DEVNULL``.
- ``BrainTimeoutError`` / ``BrainNotInstalled`` / structured
  ``BrainModelNotFoundError`` on spawn.

Real-binary smoke tests are marked ``@pytest.mark.brain_smoke_codex``
and live in a separate file. This file is mock-only.

Design lock: ``.plans/codex-brain-research.md`` §2.
"""

from __future__ import annotations

import asyncio
import subprocess
import tomllib
from pathlib import Path

import pytest

from vexis_agent.core.brain.base import (
    Brain,
    BrainError,
    BrainHealth,
    BrainModelNotFoundError,
    BrainNotInstalled,
    BrainPermanentError,
    BrainTimeoutError,
    BrainTransientError,
    McpServerSpec,
)
from vexis_agent.core.brain.codex import (
    CodexBrain,
    _classify_brain_failure,
    _sandbox_flags,
    codex_home,
)
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_yaml_config(monkeypatch, tmp_path):
    """Tier resolution reads ``~/.vexis/config.yaml``; keep tests off
    the user's real config."""
    from vexis_agent.core import yaml_config
    cfg_dir = tmp_path / "vexis-config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        yaml_config, "_config_path", lambda: cfg_dir / "config.yaml"
    )


@pytest.fixture(autouse=True)
def _isolated_codex_home(monkeypatch, tmp_path):
    """Point ``$CODEX_HOME`` at a tmp dir so ``write_mcp_config`` and
    the ``--profile vexis`` existence check never touch the real
    ``~/.codex`` (codex may be installed on the dev machine)."""
    home = tmp_path / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CODEX_HOME", str(home))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "skills").mkdir()
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
# ABC contract + inspection methods
# ──────────────────────────────────────────────────────────────────


def test_codex_brain_implements_abc(brain: CodexBrain):
    assert isinstance(brain, Brain)


def test_instruction_file_name(brain: CodexBrain):
    assert brain.instruction_file_name() == "AGENTS.md"


def test_instruction_search_paths_includes_workspace_and_codex_home(
    brain: CodexBrain, workspace: Path
):
    paths = brain.instruction_search_paths(workspace)
    assert workspace / "AGENTS.md" in paths
    assert codex_home() / "AGENTS.md" in paths


def test_session_token_returns_session_uuid(
    brain: CodexBrain, session_store: SessionStore
):
    assert brain.session_token() == session_store.get()


def test_rotate_session_returns_new_token(brain: CodexBrain):
    before = brain.session_token()
    after = brain.rotate_session()
    assert before != after


def test_build_system_prompt_includes_skills_index(
    brain: CodexBrain, workspace: Path
):
    """codex discovers skills from $CODEX_HOME/skills, NOT the
    workspace, so CodexBrain.build_system_prompt MUST render vexis's
    own index (the opposite of opencode, which drops it). A seeded
    workspace skill should appear as a ``- name: desc`` bullet."""
    skill_dir = workspace / "skills" / "test-codex-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test-codex-skill\n"
        "description: A unique test marker for the codex index.\n"
        "origin: hand-written\n---\nbody",
        encoding="utf-8",
    )
    prompt = brain.build_system_prompt()
    assert "- test-codex-skill: A unique test marker for the codex index" in prompt
    # Sanity: SOUL.md / DEFAULT_SOUL still present.
    assert "Vexis" in prompt


def test_iter_methods_do_not_crash_without_sessions(brain: CodexBrain):
    """The curator tick calls these every few minutes; a fresh install
    (no rollout dir — the autouse isolation points it at a nonexistent
    path) must return empty, not raise."""
    assert list(brain.iter_session_metas()) == []
    assert list(brain.iter_messages("anything")) == []
    assert brain.is_brain_owned_session("anything") is False


# ──────────────────────────────────────────────────────────────────
# healthcheck
# ──────────────────────────────────────────────────────────────────


def test_healthcheck_brain_not_installed_when_binary_missing(
    brain: CodexBrain, monkeypatch
):
    monkeypatch.setattr("shutil.which", lambda name: None)
    result = asyncio.run(brain.healthcheck())
    assert isinstance(result, BrainHealth)
    assert result.ok is False
    assert "not on PATH" in (result.error or "")
    assert any("install" in h.lower() for h in result.hints)


def test_healthcheck_auth_required_when_login_status_fails(
    brain: CodexBrain, monkeypatch
):
    import vexis_agent.core.brain.codex as cx

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/codex")

    class _CP:
        returncode = 1
        stdout = b""
        stderr = b"not logged in"

    monkeypatch.setattr(cx.subprocess, "run", lambda *a, **k: _CP())
    result = asyncio.run(brain.healthcheck())
    assert result.ok is False
    assert "not authenticated" in (result.error or "")
    assert any("codex login" in h for h in result.hints)


def test_healthcheck_ok_when_binary_and_login_present(
    brain: CodexBrain, monkeypatch
):
    import vexis_agent.core.brain.codex as cx

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/codex")

    class _CP:
        returncode = 0
        stdout = b"Logged in using ChatGPT\n"
        stderr = b""

    monkeypatch.setattr(cx.subprocess, "run", lambda *a, **k: _CP())
    result = asyncio.run(brain.healthcheck())
    assert result.ok is True
    assert result.error is None


# ──────────────────────────────────────────────────────────────────
# write_mcp_config — TOML round-trip (parsed back with tomllib)
# ──────────────────────────────────────────────────────────────────


def test_write_mcp_config_stdio_round_trip(brain: CodexBrain):
    spec = McpServerSpec(
        name="codemux",
        command="/usr/bin/codemux",
        args=["mcp", "--flag"],
        env={"CODEMUX_WORKSPACE_ID": "workspace-1"},
    )
    path = brain.write_mcp_config([spec])
    assert path == codex_home() / "vexis.config.toml"
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    entry = parsed["mcp_servers"]["codemux"]
    assert entry["command"] == "/usr/bin/codemux"
    assert entry["args"] == ["mcp", "--flag"]
    # env lands in a nested table.
    assert entry["env"] == {"CODEMUX_WORKSPACE_ID": "workspace-1"}


def test_write_mcp_config_remote_bearer_env(brain: CodexBrain):
    """A remote server whose Authorization header is the canonical
    ``Bearer ${VAR}`` shape collapses to codex's
    ``bearer_token_env_var`` (a NAME, not the resolved token)."""
    spec = McpServerSpec(
        name="ticktick",
        url="https://mcp.ticktick.com/",
        transport="http",
        headers={"Authorization": "Bearer ${VX_TICK_TOKEN}"},
    )
    path = brain.write_mcp_config([spec])
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    entry = parsed["mcp_servers"]["ticktick"]
    assert entry["url"] == "https://mcp.ticktick.com/"
    assert entry["bearer_token_env_var"] == "VX_TICK_TOKEN"
    # No literal token in the file.
    assert "command" not in entry


def test_write_mcp_config_remote_non_bearer_header_url_only(
    brain: CodexBrain,
):
    """A header shape codex can't express (not ``Bearer ${VAR}``) is
    dropped — url only, no bearer key."""
    spec = McpServerSpec(
        name="oddserver",
        url="https://example.com/mcp",
        transport="http",
        headers={"X-Custom": "value"},
    )
    path = brain.write_mcp_config([spec])
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    entry = parsed["mcp_servers"]["oddserver"]
    assert entry == {"url": "https://example.com/mcp"}


def test_write_mcp_config_atomic_no_tmp_lingers(brain: CodexBrain):
    brain.write_mcp_config([McpServerSpec(name="x", command="/x", args=[])])
    assert not (codex_home() / "vexis.config.toml.tmp").exists()


def test_write_mcp_config_idempotent(brain: CodexBrain):
    spec = McpServerSpec(name="x", command="/x", args=["a"])
    path = brain.write_mcp_config([spec])
    first = path.read_text(encoding="utf-8")
    brain.write_mcp_config([spec])
    second = path.read_text(encoding="utf-8")
    assert first == second


def test_write_mcp_config_replace_all_drops_old_servers(brain: CodexBrain):
    """The vexis profile is vexis-owned — replace-all, no merge. A
    later write with a different server list drops the old one."""
    brain.write_mcp_config([McpServerSpec(name="old", command="/old", args=[])])
    path = brain.write_mcp_config(
        [McpServerSpec(name="new", command="/new", args=[])]
    )
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert "new" in parsed["mcp_servers"]
    assert "old" not in parsed["mcp_servers"]


def test_write_mcp_config_empty_list(brain: CodexBrain):
    """An empty server list writes an empty (still parseable) file."""
    path = brain.write_mcp_config([])
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert parsed == {}


def test_write_mcp_config_quotes_non_bare_names_and_env_keys(
    brain: CodexBrain,
):
    """Server names / env keys outside TOML's bare-key charset must be
    quoted, not interpolated raw: a dotted name would otherwise become
    nested tables, and a spaced key would corrupt the file — breaking
    every spawn that layers ``--profile vexis``."""
    specs = [
        McpServerSpec(
            name="my.dotted server",
            command="/srv",
            args=[],
            env={"WEIRD KEY": "v", "OK_KEY": "w"},
        ),
        McpServerSpec(name="plain", command="/p", args=[]),
    ]
    path = brain.write_mcp_config(specs)
    parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    assert set(parsed["mcp_servers"]) == {"my.dotted server", "plain"}
    entry = parsed["mcp_servers"]["my.dotted server"]
    assert entry["env"] == {"WEIRD KEY": "v", "OK_KEY": "w"}
    # Bare-safe names stay unquoted (byte-stable vs prior output).
    assert "[mcp_servers.plain]" in path.read_text(encoding="utf-8")


# ──────────────────────────────────────────────────────────────────
# sandbox flag mapping (allowlist → -s / bypass)
# ──────────────────────────────────────────────────────────────────


def test_sandbox_flags_text_only():
    assert _sandbox_flags(False, None) == ["-s", "read-only"]
    assert _sandbox_flags(False, []) == ["-s", "read-only"]


def test_sandbox_flags_file_edit_allowlist():
    assert _sandbox_flags(False, ["Read", "Write", "Edit"]) == [
        "-s", "workspace-write",
    ]


def test_sandbox_flags_shell_web_allowlist_bypasses():
    assert _sandbox_flags(False, ["Read", "Bash"]) == [
        "--dangerously-bypass-approvals-and-sandbox",
    ]
    assert _sandbox_flags(False, ["WebFetch"]) == [
        "--dangerously-bypass-approvals-and-sandbox",
    ]


def test_sandbox_flags_allow_tools_true_bypasses():
    assert _sandbox_flags(True, None) == [
        "--dangerously-bypass-approvals-and-sandbox",
    ]


# ──────────────────────────────────────────────────────────────────
# spawn_aux — argv shape, stdin=DEVNULL, env
# ──────────────────────────────────────────────────────────────────


def _capture_run(monkeypatch, stdout=b"", stderr=b"", returncode=0):
    captured: dict = {}

    class _CP:
        pass

    def _fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["env"] = dict(kwargs.get("env") or {})
        captured["cwd"] = kwargs.get("cwd")
        captured["stdin"] = kwargs.get("stdin")
        cp = _CP()
        cp.stdout = stdout
        cp.stderr = stderr
        cp.returncode = returncode
        return cp

    monkeypatch.setattr(
        "vexis_agent.core.brain.codex.subprocess.run", _fake_run
    )
    return captured


def test_spawn_aux_argv_shape_with_tier(
    brain: CodexBrain, workspace: Path, monkeypatch
):
    captured = _capture_run(
        monkeypatch,
        stdout=(
            b'{"type":"item.completed","item":'
            b'{"type":"agent_message","text":"verdict"}}\n'
        ),
    )
    result = asyncio.run(
        brain.spawn_aux(
            "test prompt", model_tier="small",
            env_overrides={"X_TEST_FLAG": "1"},
        )
    )
    assert result.returncode == 0
    argv = captured["argv"]
    assert argv[:5] == ["codex", "exec", "--json", "--skip-git-repo-check", "--ephemeral"]
    # -C <workspace>
    assert argv[argv.index("-C") + 1] == str(workspace)
    # Text-only default (allow_tools False, no allowlist) → read-only.
    assert "-s" in argv and argv[argv.index("-s") + 1] == "read-only"
    # Tier small → gpt-5.4-mini via -m.
    assert argv[argv.index("-m") + 1] == "gpt-5.4-mini"
    # Prompt is the last positional.
    assert argv[-1] == "test prompt"
    # stdin piped from /dev/null so codex never reads a stray stdin.
    assert captured["stdin"] == subprocess.DEVNULL
    # env_overrides merged.
    assert captured["env"]["X_TEST_FLAG"] == "1"


def test_spawn_aux_no_model_flag_when_tier_unset(
    brain: CodexBrain, monkeypatch
):
    captured = _capture_run(monkeypatch)
    asyncio.run(brain.spawn_aux("p"))
    assert "-m" not in captured["argv"]


def test_spawn_aux_reasoning_effort_flag(brain: CodexBrain, monkeypatch):
    captured = _capture_run(monkeypatch)
    asyncio.run(brain.spawn_aux("p", model_tier="small", reasoning_level="high"))
    argv = captured["argv"]
    # -c model_reasoning_effort="high"
    assert "-c" in argv
    joined = " ".join(argv)
    assert 'model_reasoning_effort="high"' in joined


def test_spawn_aux_allowlist_maps_to_sandbox(brain: CodexBrain, monkeypatch):
    captured = _capture_run(monkeypatch)
    asyncio.run(brain.spawn_aux("p", allowed_tools=["Read", "Grep"]))
    argv = captured["argv"]
    # A read/grep allowlist (no shell/web tool) → workspace-write.
    assert argv[argv.index("-s") + 1] == "workspace-write"


def test_spawn_aux_extracts_agent_message_text(brain: CodexBrain, monkeypatch):
    _capture_run(
        monkeypatch,
        stdout=(
            b'{"type":"item.completed","item":'
            b'{"type":"agent_message","text":"first "}}\n'
            b'{"type":"item.completed","item":'
            b'{"type":"agent_message","text":"second"}}\n'
            b'{"type":"turn.completed"}\n'
        ),
    )
    result = asyncio.run(brain.spawn_aux("p"))
    assert result.stdout == "first second"


def test_spawn_aux_falls_back_to_raw_stdout(brain: CodexBrain, monkeypatch):
    _capture_run(monkeypatch, stdout=b"unexpected non-json output\n")
    result = asyncio.run(brain.spawn_aux("p"))
    assert "unexpected non-json output" in result.stdout


def test_spawn_aux_timeout_raises(brain: CodexBrain, monkeypatch):
    def _fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1.0)

    monkeypatch.setattr(
        "vexis_agent.core.brain.codex.subprocess.run", _fake_run
    )
    with pytest.raises(BrainTimeoutError, match="timed out"):
        asyncio.run(brain.spawn_aux("p", timeout_seconds=1.0))


def test_spawn_aux_missing_binary_raises_not_installed(
    brain: CodexBrain, monkeypatch
):
    def _fake_run(argv, **kwargs):
        raise FileNotFoundError("[Errno 2] No such file: 'codex'")

    monkeypatch.setattr(
        "vexis_agent.core.brain.codex.subprocess.run", _fake_run
    )
    with pytest.raises(BrainNotInstalled, match="not on PATH"):
        asyncio.run(brain.spawn_aux("p"))


def test_spawn_aux_model_not_found_raises_structured_error(
    brain: CodexBrain, monkeypatch
):
    """A bad ``-m`` id exits non-zero with a model-not-supported /
    model-metadata error event. spawn_aux must surface a structured
    BrainModelNotFoundError, not a silent empty reply."""
    _capture_run(
        monkeypatch,
        stdout=(
            b'{"type":"item.completed","item":{"type":"error",'
            b'"message":"Model metadata for `gpt-bogus` not found."}}\n'
            b'{"type":"turn.failed","error":{"message":'
            b'"The gpt-bogus model is not supported when using Codex."}}\n'
        ),
        returncode=1,
    )
    with pytest.raises(BrainModelNotFoundError) as exc_info:
        asyncio.run(
            brain.spawn_aux(
                "p", model_tier="large", subsystem="coherence_judge"
            )
        )
    err = exc_info.value
    assert err.brain_kind == "codex"
    assert err.subsystem == "coherence_judge"
    # Tier large → gpt-5.6-sol resolved before the spawn.
    assert err.model_id == "gpt-5.6-sol"
    assert err.suggested_fix


# ──────────────────────────────────────────────────────────────────
# Error classification — codex's JSON wire shapes
# ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("error_text", "expected_cls"),
    [
        # The HTTP status arrives JSON-encoded ("status":NNN) — the
        # verified turn.failed shape, not prose.
        ('{"type":"error","status":500,"error":{"message":"boom"}}',
         BrainTransientError),
        ('{"type":"error","status":429,"error":{"message":"slow down"}}',
         BrainTransientError),
        ("stream disconnected before completion", BrainTransientError),
        # Verified bad-model 400 dump (spec §1).
        ('{"type":"error","status":400,"error":{"type":'
         '"invalid_request_error","message":"The \'x\' model is not '
         'supported when using Codex with a ChatGPT account."}}',
         BrainPermanentError),
        ("Please run codex login to authenticate", BrainPermanentError),
        ("something unrecognised happened", BrainError),
    ],
)
def test_classify_brain_failure_wire_shapes(error_text, expected_cls):
    cls, msg = _classify_brain_failure(stderr_text="", error_text=error_text)
    assert cls is expected_cls
    assert msg

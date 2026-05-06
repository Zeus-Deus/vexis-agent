"""Phase C Day 3 scaffold tests for ``core.brain.opencode``.

Coverage:
- ABC-method shapes: ``OpenCodeBrain`` instantiates as a ``Brain``,
  every abstract method is callable, the deferred transcript-
  readback methods return empty / False (graceful no-op, NOT
  ``NotImplementedError``).
- ``healthcheck`` raises actionable errors when ``opencode`` is
  missing from PATH or unauthenticated.
- ``write_mcp_config`` round-trip: vexis-prefixed entries appear
  under ``mcp:``; user-owned non-prefixed entries are preserved
  byte-for-byte across multiple writes; the writer doesn't clobber
  unrelated top-level keys.
- ``OPENCODE_CONFIG_CONTENT`` env var is set on every spawn with
  the right shape (agent definition + system prompt + optional
  model + permission ruleset matching ``allow_tools``).
- JSON event-stream parsing: ``text`` events accumulate into the
  final reply, ``session.status.idle`` terminates, malformed lines
  are silently skipped.
- ``BrainNotInstalled`` raised when binary missing on spawn.

Real-binary smoke tests (Day 5) are marked
``@pytest.mark.brain_smoke_opencode`` and live in a separate file.
This file is mock-only.

Design citation: ``.plans/brain-abstraction-research.md`` §5 Day 3
+ §4 "BrainOpenCode system-prompt injection" + "MCP-config-writer
merge strategy".
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from core.brain.base import (
    Brain,
    BrainHealth,
    BrainNotInstalled,
    BrainTimeoutError,
    McpServerSpec,
)
from core.brain.opencode import (
    VEXIS_AUX_AGENT_NAME,
    VEXIS_MCP_PREFIX,
    OpenCodeBrain,
    _build_opencode_config_content,
    _extract_text_from_event_stream,
)
from core.running_tasks import RunningTasks
from core.sessions import SessionStore


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_yaml_config(monkeypatch, tmp_path):
    """Tier resolution reads ``~/.vexis/config.yaml``. Tests must
    not see the user's real config."""
    from core import yaml_config
    cfg_dir = tmp_path / "vexis-config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        yaml_config, "_config_path", lambda: cfg_dir / "config.yaml"
    )


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
def brain(workspace: Path, session_store: SessionStore) -> OpenCodeBrain:
    return OpenCodeBrain(
        workspace=workspace,
        session=session_store,
        running_tasks=RunningTasks(),
    )


# ──────────────────────────────────────────────────────────────────
# ABC contract — instantiation + inspection methods
# ──────────────────────────────────────────────────────────────────


def test_opencode_brain_implements_abc(brain: OpenCodeBrain):
    """Construction succeeds → every abstract method is implemented
    (Python ABC enforcement is at instantiation time)."""
    assert isinstance(brain, Brain)


def test_instruction_file_name(brain: OpenCodeBrain):
    assert brain.instruction_file_name() == "AGENTS.md"


def test_instruction_search_paths_includes_workspace_and_global(
    brain: OpenCodeBrain, workspace: Path
):
    paths = brain.instruction_search_paths(workspace)
    assert workspace / "AGENTS.md" in paths
    # OpenCode reads CLAUDE.md as a fallback unless flag-disabled.
    assert workspace / "CLAUDE.md" in paths
    # Global at ~/.config/opencode/AGENTS.md surfaces too.
    assert any("opencode" in str(p) and "AGENTS.md" in p.name for p in paths)


def test_session_token_returns_session_uuid(
    brain: OpenCodeBrain, session_store: SessionStore
):
    """Day 3 placeholder: returns whatever SessionStore generates.
    Day 4 will swap for the OpenCode-harvested id."""
    assert brain.session_token() == session_store.get()


def test_rotate_session_returns_new_token(brain: OpenCodeBrain):
    before = brain.session_token()
    after = brain.rotate_session()
    assert before != after


def test_build_system_prompt_omits_skills_block(
    brain: OpenCodeBrain, workspace: Path
):
    """OpenCode auto-discovers ``<workspace>/skills/**/SKILL.md``
    natively and emits its own ``<available_skills>`` block, so
    BrainOpenCode.build_system_prompt MUST omit vexis's index to
    avoid double-injection. (See module docstring + §2 of the
    research doc.)

    Loose check: the SEEDED skill name must not appear as a
    bulleted index entry. We can't grep on ``<available_skills>``
    alone because CAPABILITIES.md mentions the literal phrase as
    documentation; but vexis's actual index renders as
    ``- skill-name: description`` lines (per
    ``core.skills.build_skills_index_block``), so the test skill's
    name + description in that exact shape would only appear if
    vexis injected the block.
    """
    skill_dir = workspace / "skills" / "test-opencode-skip-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test-opencode-skip-skill\n"
        "description: A unique test marker for the skip assertion.\n"
        "origin: hand-written\n---\nbody",
        encoding="utf-8",
    )
    prompt = brain.build_system_prompt()
    # The skill name + description in the bulleted index shape must
    # not appear. Equivalent claude-code prompt WOULD contain
    # "- test-opencode-skip-skill: A unique test marker..." here.
    assert "- test-opencode-skip-skill: A unique test marker" not in prompt
    # Sanity: SOUL.md / DEFAULT_SOUL still present.
    assert "Vexis" in prompt


# ──────────────────────────────────────────────────────────────────
# Day-3 transcript-readback stubs return empty (graceful no-op)
# ──────────────────────────────────────────────────────────────────


def test_iter_session_metas_returns_empty_iterator_day_3(
    brain: OpenCodeBrain,
):
    """Day 3 graceful no-op: the curator's eligibility scan sees
    no sessions when running under OpenCode. Correct because
    OpenCode hasn't run any vexis sessions yet at Day 3."""
    assert list(brain.iter_session_metas()) == []


def test_iter_messages_returns_empty_iterator_day_3(brain: OpenCodeBrain):
    assert list(brain.iter_messages("any-session-id")) == []


def test_is_brain_owned_session_returns_false_day_3(brain: OpenCodeBrain):
    """Defensive False — treat unknown sessions as not brain-owned
    so the recursion guard doesn't accidentally skip a real session."""
    assert brain.is_brain_owned_session("any-session-id") is False


def test_curator_can_invoke_iter_methods_without_crash(brain: OpenCodeBrain):
    """The load-bearing assertion: the curator's tick path calls
    these methods every 5 minutes. A NotImplementedError here
    would crash the daemon. Verify they all return cleanly."""
    list(brain.iter_session_metas())  # must not raise
    list(brain.iter_messages("anything"))  # must not raise
    brain.is_brain_owned_session("anything")  # must not raise


# ──────────────────────────────────────────────────────────────────
# healthcheck — actionable errors when binary or auth missing
# ──────────────────────────────────────────────────────────────────


def test_healthcheck_brain_not_installed_when_binary_missing(
    brain: OpenCodeBrain, monkeypatch
):
    monkeypatch.setattr("shutil.which", lambda name: None)
    result = asyncio.run(brain.healthcheck())
    assert isinstance(result, BrainHealth)
    assert result.ok is False
    assert "not on PATH" in (result.error or "")
    # Hint mentions the install command.
    assert any("install" in h.lower() for h in result.hints)


def test_healthcheck_brain_auth_required_when_auth_list_fails(
    brain: OpenCodeBrain, monkeypatch
):
    """opencode is installed but ``opencode auth list`` returns
    non-zero — surface as actionable BrainHealth(ok=False)."""
    import core.brain.opencode as oc

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/opencode")

    class _CP:
        returncode = 1
        stdout = b""
        stderr = b"no auth configured"

    def _fake_run(*a, **kw):
        return _CP()

    monkeypatch.setattr(oc.subprocess, "run", _fake_run)
    result = asyncio.run(brain.healthcheck())
    assert result.ok is False
    assert "not authenticated" in (result.error or "")
    # Hint includes at least one ``opencode auth login`` example.
    assert any("opencode auth login" in h for h in result.hints)


def test_healthcheck_ok_when_binary_and_auth_present(
    brain: OpenCodeBrain, monkeypatch
):
    import core.brain.opencode as oc

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/opencode")

    class _CP:
        returncode = 0
        stdout = b"anthropic: oauth\n"
        stderr = b""

    def _fake_run(*a, **kw):
        return _CP()

    monkeypatch.setattr(oc.subprocess, "run", _fake_run)
    result = asyncio.run(brain.healthcheck())
    assert result.ok is True
    assert result.error is None


# ──────────────────────────────────────────────────────────────────
# write_mcp_config — namespace-prefix merge round-trip
# ──────────────────────────────────────────────────────────────────


def test_write_mcp_config_emits_vexis_prefixed_entries(
    brain: OpenCodeBrain, workspace: Path
):
    """Each McpServerSpec gets serialised under ``mcp.vexis-<name>``
    in OpenCode's local-MCP shape (type=local, command as one list)."""
    spec = McpServerSpec(
        name="codemux",
        command="/usr/bin/codemux",
        args=["mcp"],
        env={"CODEMUX_WORKSPACE_ID": "workspace-1"},
    )
    path = brain.write_mcp_config([spec])
    assert path == workspace / "opencode.json"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert "mcp" in on_disk
    key = f"{VEXIS_MCP_PREFIX}codemux"
    assert key in on_disk["mcp"]
    entry = on_disk["mcp"][key]
    assert entry["type"] == "local"
    # OpenCode merges command+args into one list.
    assert entry["command"] == ["/usr/bin/codemux", "mcp"]
    assert entry["environment"] == {"CODEMUX_WORKSPACE_ID": "workspace-1"}
    assert entry["enabled"] is True


def test_write_mcp_config_preserves_user_owned_non_prefixed_entries(
    brain: OpenCodeBrain, workspace: Path
):
    """The load-bearing invariant: user-added MCP servers (any key
    NOT starting with ``vexis-``) must round-trip byte-for-byte."""
    # User has hand-written a ``filesystem`` MCP server in opencode.json.
    user_initial = {
        "mcp": {
            "filesystem": {
                "type": "local",
                "command": ["mcp-filesystem", "/home/zeus/notes"],
                "enabled": True,
            },
        },
        # Plus an unrelated top-level key vexis must also preserve.
        "agent": {
            "build": {"prompt": "you are a build agent"},
        },
    }
    (workspace / "opencode.json").write_text(
        json.dumps(user_initial, indent=2), encoding="utf-8"
    )

    # Now vexis writes its server.
    spec = McpServerSpec(
        name="codemux",
        command="/usr/bin/codemux",
        args=["mcp"],
    )
    brain.write_mcp_config([spec])

    on_disk = json.loads(
        (workspace / "opencode.json").read_text(encoding="utf-8")
    )
    # User's filesystem entry preserved, byte-for-byte:
    assert on_disk["mcp"]["filesystem"] == user_initial["mcp"]["filesystem"]
    # Vexis's entry added under prefix:
    assert f"{VEXIS_MCP_PREFIX}codemux" in on_disk["mcp"]
    # User's unrelated top-level key preserved:
    assert on_disk["agent"] == user_initial["agent"]


def test_write_mcp_config_replaces_only_vexis_prefixed_entries(
    brain: OpenCodeBrain, workspace: Path
):
    """Successive calls with different vexis server lists overwrite
    the prefixed half but never touch user entries."""
    user_entry = {
        "type": "local",
        "command": ["my-tool"],
        "enabled": True,
    }
    initial = {
        "mcp": {
            "user-tool": user_entry,
            "vexis-old-name": {
                "type": "local",
                "command": ["old"],
                "enabled": True,
            },
        }
    }
    (workspace / "opencode.json").write_text(
        json.dumps(initial), encoding="utf-8"
    )

    new_spec = McpServerSpec(
        name="new-name", command="/usr/bin/new", args=[]
    )
    brain.write_mcp_config([new_spec])

    on_disk = json.loads(
        (workspace / "opencode.json").read_text(encoding="utf-8")
    )
    # User entry untouched.
    assert on_disk["mcp"]["user-tool"] == user_entry
    # Old vexis entry GONE — replaced by the new spec.
    assert "vexis-old-name" not in on_disk["mcp"]
    # New vexis entry present.
    assert f"{VEXIS_MCP_PREFIX}new-name" in on_disk["mcp"]


def test_write_mcp_config_round_trips_with_no_changes(
    brain: OpenCodeBrain, workspace: Path
):
    """Calling write_mcp_config twice with the same spec list
    produces byte-identical files (modulo possible re-ordering of
    keys, which Python's dict insertion order makes deterministic)."""
    spec = McpServerSpec(
        name="codemux", command="/usr/bin/codemux", args=["mcp"]
    )
    brain.write_mcp_config([spec])
    first = (workspace / "opencode.json").read_text(encoding="utf-8")
    brain.write_mcp_config([spec])
    second = (workspace / "opencode.json").read_text(encoding="utf-8")
    assert first == second


def test_write_mcp_config_handles_empty_server_list(
    brain: OpenCodeBrain, workspace: Path
):
    """Empty server list with no existing user entries → no
    ``mcp:`` block emitted (cleaner than an empty object)."""
    brain.write_mcp_config([])
    on_disk = json.loads(
        (workspace / "opencode.json").read_text(encoding="utf-8")
    )
    assert "mcp" not in on_disk


def test_write_mcp_config_empty_server_list_preserves_user_entries(
    brain: OpenCodeBrain, workspace: Path
):
    """Empty vexis list + existing user entries → user entries
    survive, no ``mcp:`` block deletion."""
    initial = {
        "mcp": {
            "user-tool": {"type": "local", "command": ["x"], "enabled": True}
        }
    }
    (workspace / "opencode.json").write_text(
        json.dumps(initial), encoding="utf-8"
    )
    brain.write_mcp_config([])
    on_disk = json.loads(
        (workspace / "opencode.json").read_text(encoding="utf-8")
    )
    assert on_disk["mcp"] == initial["mcp"]


def test_write_mcp_config_atomic_uses_tempfile_rename(
    brain: OpenCodeBrain, workspace: Path
):
    """The writer goes via ``opencode.json.tmp`` + rename so a crash
    mid-write can't corrupt the file. Sanity: after a successful
    call, no .tmp lingers."""
    spec = McpServerSpec(name="x", command="/x", args=[])
    brain.write_mcp_config([spec])
    assert not (workspace / "opencode.json.tmp").exists()


def test_write_mcp_config_handles_corrupt_existing_file(
    brain: OpenCodeBrain, workspace: Path
):
    """If opencode.json is malformed (truncated mid-write, hand-
    edited and broken), the writer falls through to a fresh file
    rather than crashing the daemon. User loses any unparseable
    data but keeps a working config."""
    (workspace / "opencode.json").write_text(
        "{this is not valid json", encoding="utf-8"
    )
    spec = McpServerSpec(name="x", command="/x", args=[])
    brain.write_mcp_config([spec])  # must not raise
    on_disk = json.loads(
        (workspace / "opencode.json").read_text(encoding="utf-8")
    )
    assert f"{VEXIS_MCP_PREFIX}x" in on_disk["mcp"]


# ──────────────────────────────────────────────────────────────────
# OPENCODE_CONFIG_CONTENT shape
# ──────────────────────────────────────────────────────────────────


def test_config_content_includes_agent_definition_with_prompt():
    raw = _build_opencode_config_content(
        agent_name="vexis",
        system_prompt="be concise",
        model="anthropic/claude-sonnet-4",
        allow_tools=True,
    )
    parsed = json.loads(raw)
    assert "agent" in parsed
    assert "vexis" in parsed["agent"]
    agent = parsed["agent"]["vexis"]
    assert agent["prompt"] == "be concise"
    assert agent["model"] == "anthropic/claude-sonnet-4"


def test_config_content_omits_model_when_none():
    """``model=None`` → no ``model`` key in the agent definition;
    OpenCode uses its native default."""
    raw = _build_opencode_config_content(
        agent_name="vexis",
        system_prompt="x",
        model=None,
        allow_tools=True,
    )
    parsed = json.loads(raw)
    assert "model" not in parsed["agent"]["vexis"]


def test_config_content_allow_tools_false_emits_deny_permissions():
    """Judges/extractors pass ``allow_tools=False`` so the spawn
    can't accidentally use a tool. Verify the deny ruleset is
    present."""
    raw = _build_opencode_config_content(
        agent_name="vexis-aux",
        system_prompt="",
        model=None,
        allow_tools=False,
    )
    parsed = json.loads(raw)
    perm = parsed["agent"]["vexis-aux"].get("permission")
    assert perm is not None
    # All four tool categories denied.
    assert perm.get("edit") == "deny"
    assert perm.get("write") == "deny"
    assert perm.get("shell") == "deny"
    assert perm.get("webfetch") == "deny"


def test_config_content_allow_tools_true_omits_permissions():
    """``allow_tools=True`` (curator) omits the deny ruleset so
    the model can use tools normally."""
    raw = _build_opencode_config_content(
        agent_name="vexis",
        system_prompt="x",
        model=None,
        allow_tools=True,
    )
    parsed = json.loads(raw)
    assert "permission" not in parsed["agent"]["vexis"]


# ──────────────────────────────────────────────────────────────────
# Event-stream extractor (used by spawn_aux)
# ──────────────────────────────────────────────────────────────────


def test_extract_text_concatenates_text_events():
    raw = (
        '{"type": "text", "sessionID": "s1", "part": {"text": "hello "}}\n'
        '{"type": "text", "sessionID": "s1", "part": {"text": "world"}}\n'
        '{"type": "session.status", "sessionID": "s1", '
        '"properties": {"status": {"type": "idle"}}}\n'
    )
    assert _extract_text_from_event_stream(raw) == "hello world"


def test_extract_text_ignores_non_text_events():
    raw = (
        '{"type": "tool_use", "sessionID": "s1", "part": {"tool": "read"}}\n'
        '{"type": "text", "sessionID": "s1", "part": {"text": "answer"}}\n'
        '{"type": "step_finish", "sessionID": "s1"}\n'
    )
    assert _extract_text_from_event_stream(raw) == "answer"


def test_extract_text_skips_malformed_lines():
    raw = (
        "this line is not json\n"
        '{"type": "text", "sessionID": "s1", "part": {"text": "kept"}}\n'
        "[]\n"  # valid JSON but not a dict
        "\n"  # empty line
    )
    assert _extract_text_from_event_stream(raw) == "kept"


def test_extract_text_returns_empty_when_no_text_events():
    raw = '{"type": "tool_use", "sessionID": "s1"}\n'
    assert _extract_text_from_event_stream(raw) == ""


# ──────────────────────────────────────────────────────────────────
# spawn_aux — argv shape, env_overrides, BrainNotInstalled
# ──────────────────────────────────────────────────────────────────


def test_spawn_aux_argv_shape_with_tier(
    brain: OpenCodeBrain, monkeypatch
):
    """spawn_aux composes ``opencode run --format json --agent
    vexis-aux`` plus the tier-resolved model in the
    OPENCODE_CONFIG_CONTENT env var. The model flag is NOT in argv
    — it's in the agent definition inside the env var."""
    captured: dict = {}

    class _CP:
        stdout = (
            b'{"type": "text", "sessionID": "s1", '
            b'"part": {"text": "verdict"}}\n'
        )
        stderr = b""
        returncode = 0

    def _fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["env"] = dict(kwargs.get("env") or {})
        return _CP()

    monkeypatch.setattr("core.brain.opencode.subprocess.run", _fake_run)
    result = asyncio.run(
        brain.spawn_aux(
            "test prompt", model_tier="small",
            env_overrides={"X_TEST_FLAG": "1"},
        )
    )
    assert result.returncode == 0
    # Argv: opencode run --format json --agent vexis-aux <prompt>
    assert captured["argv"][:5] == [
        "opencode", "run", "--format", "json", "--agent",
    ]
    assert captured["argv"][5] == VEXIS_AUX_AGENT_NAME
    assert "test prompt" in captured["argv"]
    # Model is NOT a CLI flag — lives in OPENCODE_CONFIG_CONTENT.
    assert "--model" not in captured["argv"]
    # OPENCODE_CONFIG_CONTENT carries the agent definition with
    # the tier-resolved model.
    config_blob = json.loads(captured["env"]["OPENCODE_CONFIG_CONTENT"])
    aux_agent = config_blob["agent"][VEXIS_AUX_AGENT_NAME]
    # Default tier map: small → anthropic/claude-haiku-3-5.
    assert aux_agent["model"] == "anthropic/claude-haiku-3-5"
    # env_overrides merged with os.environ.
    assert captured["env"]["X_TEST_FLAG"] == "1"


def test_spawn_aux_allow_tools_false_sets_deny_permissions(
    brain: OpenCodeBrain, monkeypatch
):
    """Default ``allow_tools=False`` means the agent definition
    carries a deny-by-default permission ruleset."""
    captured: dict = {}

    class _CP:
        stdout = b""
        stderr = b""
        returncode = 0

    def _fake_run(argv, **kwargs):
        captured["env"] = dict(kwargs.get("env") or {})
        return _CP()

    monkeypatch.setattr("core.brain.opencode.subprocess.run", _fake_run)
    asyncio.run(brain.spawn_aux("p"))  # allow_tools defaults to False
    config_blob = json.loads(captured["env"]["OPENCODE_CONFIG_CONTENT"])
    perm = config_blob["agent"][VEXIS_AUX_AGENT_NAME].get("permission")
    assert perm is not None
    assert perm["edit"] == "deny"


def test_spawn_aux_allow_tools_true_omits_permissions(
    brain: OpenCodeBrain, monkeypatch
):
    captured: dict = {}

    class _CP:
        stdout = b""
        stderr = b""
        returncode = 0

    def _fake_run(argv, **kwargs):
        captured["env"] = dict(kwargs.get("env") or {})
        return _CP()

    monkeypatch.setattr("core.brain.opencode.subprocess.run", _fake_run)
    asyncio.run(brain.spawn_aux("p", allow_tools=True))
    config_blob = json.loads(captured["env"]["OPENCODE_CONFIG_CONTENT"])
    assert (
        "permission"
        not in config_blob["agent"][VEXIS_AUX_AGENT_NAME]
    )


def test_spawn_aux_extracts_text_from_json_event_stream(
    brain: OpenCodeBrain, monkeypatch
):
    """spawn_aux's stdout is OpenCode's JSON event stream — the
    extractor pulls out concatenated ``text`` events so callers
    see the same final-reply shape claude-code's ``result`` event
    produced."""

    class _CP:
        stdout = (
            b'{"type": "text", "sessionID": "s1", '
            b'"part": {"text": "first "}}\n'
            b'{"type": "text", "sessionID": "s1", '
            b'"part": {"text": "second"}}\n'
            b'{"type": "session.status", "sessionID": "s1", '
            b'"properties": {"status": {"type": "idle"}}}\n'
        )
        stderr = b""
        returncode = 0

    def _fake_run(argv, **kwargs):
        return _CP()

    monkeypatch.setattr("core.brain.opencode.subprocess.run", _fake_run)
    result = asyncio.run(brain.spawn_aux("p"))
    assert result.stdout == "first second"


def test_spawn_aux_falls_back_to_raw_stdout_when_no_text_events(
    brain: OpenCodeBrain, monkeypatch
):
    """If the JSON stream contains no ``text`` events (malformed
    spawn, all-error stream), we hand the caller the raw stdout
    so they can decide what to do — better noisy than empty."""

    class _CP:
        stdout = b'unexpected non-json output\n'
        stderr = b""
        returncode = 0

    def _fake_run(argv, **kwargs):
        return _CP()

    monkeypatch.setattr("core.brain.opencode.subprocess.run", _fake_run)
    result = asyncio.run(brain.spawn_aux("p"))
    assert "unexpected non-json output" in result.stdout


def test_spawn_aux_timeout_raises_brain_timeout(
    brain: OpenCodeBrain, monkeypatch
):
    import subprocess as subprocess_module

    def _fake_run(argv, **kwargs):
        raise subprocess_module.TimeoutExpired(cmd=argv, timeout=1.0)

    monkeypatch.setattr("core.brain.opencode.subprocess.run", _fake_run)
    with pytest.raises(BrainTimeoutError, match="timed out"):
        asyncio.run(brain.spawn_aux("p", timeout_seconds=1.0))


def test_spawn_aux_missing_binary_raises_not_installed(
    brain: OpenCodeBrain, monkeypatch
):
    def _fake_run(argv, **kwargs):
        raise FileNotFoundError("[Errno 2] No such file: 'opencode'")

    monkeypatch.setattr("core.brain.opencode.subprocess.run", _fake_run)
    with pytest.raises(BrainNotInstalled, match="not on PATH"):
        asyncio.run(brain.spawn_aux("p"))
    # Hint mentions the install command in the exception message.


def test_spawn_aux_passes_workspace_as_default_cwd(
    brain: OpenCodeBrain, workspace: Path, monkeypatch
):
    captured: dict = {}

    class _CP:
        stdout = b""
        stderr = b""
        returncode = 0

    def _fake_run(argv, **kwargs):
        captured["cwd"] = kwargs.get("cwd")
        return _CP()

    monkeypatch.setattr("core.brain.opencode.subprocess.run", _fake_run)
    asyncio.run(brain.spawn_aux("p"))
    assert captured["cwd"] == str(workspace)


def test_spawn_aux_explicit_cwd_overrides_workspace(
    brain: OpenCodeBrain, tmp_path: Path, monkeypatch
):
    captured: dict = {}

    class _CP:
        stdout = b""
        stderr = b""
        returncode = 0

    def _fake_run(argv, **kwargs):
        captured["cwd"] = kwargs.get("cwd")
        return _CP()

    monkeypatch.setattr("core.brain.opencode.subprocess.run", _fake_run)
    other = tmp_path / "other"
    other.mkdir()
    asyncio.run(brain.spawn_aux("p", cwd=other))
    assert captured["cwd"] == str(other)

"""Issue #10 — defense-in-depth tool-allowlist on ``Brain.spawn_aux``.

The kwarg ``allowed_tools: list[str] | None`` is the per-call seam
that closes a class of bug we can't rule out by reading prompts: a
poisoned transcript that argues the curator-review LLM into calling
``Bash``/``Write``/``WebFetch``.

What this file pins down:

  * **Argv-level** on claude-code: ``allowed_tools=['Read']`` emits
    ``--allowedTools Read`` + ``--permission-mode bypassPermissions``
    so the model can call ``Read`` headless without a permission
    prompt deadlock. ``DISALLOWED_TOOLS`` continues to apply on top.
  * **Text-only-explicit**: ``allowed_tools=[]`` emits neither
    ``--allowedTools`` nor the bypass flag, and the spawn still
    completes without deadlock (the model produces text only;
    if it tried a tool, headless ``-p`` has no UI to answer).
  * **Back-compat**: a caller that doesn't pass ``allowed_tools``
    and uses ``allow_tools=True`` still sees the bypass flag (no
    ``--allowedTools``) so existing skill-curator behaviour is
    preserved bit-for-bit.
  * **Precedence**: when both are set, ``allowed_tools`` wins —
    ``allow_tools=True, allowed_tools=[]`` is text-only.
  * **Cross-brain parity**: every brain (``claude-code``,
    ``opencode``, ``null``) accepts the kwarg without error.
  * **opencode mapping**: an explicit allowlist gets translated into
    opencode's per-category permission block (the categories opencode
    actually exposes are coarser than Claude's per-tool names so the
    mapping is one-to-many but defaults to deny).
  * **Caller wiring**: every shipping aux-spawn site declares an
    allowlist via :class:`BrainNull`'s call recorder — proving the
    Issue #10 acceptance criterion that all six callsites carry the
    explicit form.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vexis_agent.core.brain.base import AuxResult, BrainError
from vexis_agent.core.brain.claude_code import ClaudeCodeBrain
from vexis_agent.core.brain.null import BrainNull
from vexis_agent.core.brain.opencode import (
    OpenCodeBrain,
    _build_opencode_config_content,
    _permission_from_allowlist,
)
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "skills").mkdir()
    return tmp_path


@pytest.fixture
def claude_brain(workspace: Path, tmp_path: Path) -> ClaudeCodeBrain:
    return ClaudeCodeBrain(
        workspace=workspace,
        session=SessionStore(tmp_path / "sessions.json"),
        running_tasks=RunningTasks(),
    )


@pytest.fixture
def opencode_brain(workspace: Path, tmp_path: Path) -> OpenCodeBrain:
    return OpenCodeBrain(
        workspace=workspace,
        session=SessionStore(tmp_path / "opencode-sessions.json"),
        running_tasks=RunningTasks(),
    )


def _capture_argv(monkeypatch) -> dict:
    """Patch claude-code's subprocess.run with a recorder. Returns
    a dict that the caller inspects after the spawn for the argv."""
    captured: dict = {}

    class _FakeCP:
        stdout = b""
        stderr = b""
        returncode = 0

    def _fake_run(argv, **kwargs):
        captured["argv"] = list(argv)
        captured["env"] = kwargs.get("env")
        return _FakeCP()

    monkeypatch.setattr(
        "vexis_agent.core.brain.claude_code.subprocess.run", _fake_run
    )
    return captured


# ──────────────────────────────────────────────────────────────────
# Claude-code argv
# ──────────────────────────────────────────────────────────────────


def test_claude_code_allowed_tools_list_emits_allowed_tools_flag(
    claude_brain: ClaudeCodeBrain, monkeypatch
):
    """``allowed_tools=['Read', 'Grep']`` → ``--allowedTools Read Grep``
    followed by ``--permission-mode bypassPermissions`` (headless -p
    must skip the permission prompt or the call deadlocks)."""
    captured = _capture_argv(monkeypatch)
    asyncio.run(
        claude_brain.spawn_aux(
            "p",
            model_tier=None,
            allowed_tools=["Read", "Grep"],
        )
    )
    argv = captured["argv"]
    assert "--allowedTools" in argv
    flag_idx = argv.index("--allowedTools")
    # The two tool names follow the flag in order.
    assert argv[flag_idx + 1] == "Read"
    assert argv[flag_idx + 2] == "Grep"
    # bypassPermissions present so the spawned brain doesn't try to
    # surface an interactive prompt that headless -p can't answer.
    assert "--permission-mode" in argv
    perm_idx = argv.index("--permission-mode")
    assert argv[perm_idx + 1] == "bypassPermissions"


def test_claude_code_allowed_tools_empty_list_emits_no_flags(
    claude_brain: ClaudeCodeBrain, monkeypatch
):
    """``allowed_tools=[]`` → text-only spawn. No ``--allowedTools``,
    no ``--permission-mode bypassPermissions``. Spawn still completes;
    a stray tool attempt would fail loud rather than hang the call."""
    captured = _capture_argv(monkeypatch)
    asyncio.run(
        claude_brain.spawn_aux(
            "p",
            model_tier=None,
            allowed_tools=[],
        )
    )
    argv = captured["argv"]
    assert "--allowedTools" not in argv
    assert "bypassPermissions" not in argv


def test_claude_code_allowed_tools_wins_over_allow_tools(
    claude_brain: ClaudeCodeBrain, monkeypatch
):
    """Precedence: ``allowed_tools=[]`` beats ``allow_tools=True`` so
    a caller that sets both explicit-text-only and the legacy
    permissive boolean ends up text-only — the safer default."""
    captured = _capture_argv(monkeypatch)
    asyncio.run(
        claude_brain.spawn_aux(
            "p",
            model_tier=None,
            allow_tools=True,
            allowed_tools=[],
        )
    )
    argv = captured["argv"]
    assert "bypassPermissions" not in argv


def test_claude_code_allow_tools_true_back_compat(
    claude_brain: ClaudeCodeBrain, monkeypatch
):
    """Back-compat for the pre-#10 callers: ``allow_tools=True``
    without ``allowed_tools`` still emits ``bypassPermissions`` and
    no ``--allowedTools`` flag — bit-identical to the old behaviour
    so existing skill-curator integration code keeps working."""
    captured = _capture_argv(monkeypatch)
    asyncio.run(
        claude_brain.spawn_aux(
            "p",
            model_tier=None,
            allow_tools=True,
        )
    )
    argv = captured["argv"]
    assert "--allowedTools" not in argv
    assert "--permission-mode" in argv
    perm_idx = argv.index("--permission-mode")
    assert argv[perm_idx + 1] == "bypassPermissions"


def test_claude_code_default_kwargs_remain_text_only(
    claude_brain: ClaudeCodeBrain, monkeypatch
):
    """Bare ``spawn_aux(prompt)`` defaults to text-only and emits
    neither the bypass flag nor an allowlist. Same shape pre- and
    post-Issue-#10."""
    captured = _capture_argv(monkeypatch)
    asyncio.run(claude_brain.spawn_aux("p"))
    argv = captured["argv"]
    assert "--allowedTools" not in argv
    assert "bypassPermissions" not in argv


# ──────────────────────────────────────────────────────────────────
# Opencode permission block
# ──────────────────────────────────────────────────────────────────


def test_opencode_permission_empty_allowlist_denies_everything():
    """``[]`` → all four categories ``deny``. Same effective behaviour
    as ``allow_tools=False`` but the caller declared the intent."""
    perm = _permission_from_allowlist([])
    assert perm == {
        "edit": "deny",
        "write": "deny",
        "shell": "deny",
        "webfetch": "deny",
    }


def test_opencode_permission_read_write_allows_only_write_category():
    """``Read`` is not in opencode's permission map (read-only ops
    aren't gated). ``Write`` maps to the ``write`` category. So an
    allowlist of ``['Read', 'Write']`` allows ``write`` and denies
    the other three."""
    perm = _permission_from_allowlist(["Read", "Write"])
    assert perm["write"] == "allow"
    assert perm["edit"] == "deny"
    assert perm["shell"] == "deny"
    assert perm["webfetch"] == "deny"


def test_opencode_permission_edit_maps_to_edit_category():
    perm = _permission_from_allowlist(["Edit", "MultiEdit"])
    assert perm["edit"] == "allow"
    assert perm["write"] == "deny"
    assert perm["shell"] == "deny"


def test_opencode_permission_bash_maps_to_shell():
    perm = _permission_from_allowlist(["Bash"])
    assert perm["shell"] == "allow"
    assert perm["edit"] == "deny"


def test_opencode_config_content_uses_allowlist_when_set():
    """When ``allowed_tools`` is non-None it wins over ``allow_tools``
    in the per-spawn config JSON. The agent definition's
    ``permission`` block reflects the per-category map."""
    import json

    content = _build_opencode_config_content(
        agent_name="aux",
        system_prompt="",
        model=None,
        allow_tools=True,  # would otherwise leave permissions open
        allowed_tools=["Read", "Write"],
    )
    parsed = json.loads(content)
    perm = parsed["agent"]["aux"]["permission"]
    # write category allowed (Write is in the list); edit/shell/webfetch denied.
    assert perm["write"] == "allow"
    assert perm["edit"] == "deny"
    assert perm["shell"] == "deny"
    assert perm["webfetch"] == "deny"


def test_opencode_config_content_back_compat_allow_tools_true():
    """``allowed_tools=None, allow_tools=True`` keeps the pre-#10
    shape: no ``permission`` block at all (opencode falls through to
    its run-mode defaults, which permit tools when paired with
    ``--dangerously-skip-permissions``)."""
    import json

    content = _build_opencode_config_content(
        agent_name="aux",
        system_prompt="",
        model=None,
        allow_tools=True,
    )
    parsed = json.loads(content)
    assert "permission" not in parsed["agent"]["aux"]


def test_opencode_config_content_default_is_text_only():
    """``allowed_tools=None, allow_tools=False`` (the default) still
    emits the deny-everything permission block — same as before."""
    import json

    content = _build_opencode_config_content(
        agent_name="aux",
        system_prompt="",
        model=None,
        allow_tools=False,
    )
    parsed = json.loads(content)
    perm = parsed["agent"]["aux"]["permission"]
    assert perm == {
        "edit": "deny",
        "write": "deny",
        "shell": "deny",
        "webfetch": "deny",
    }


# ──────────────────────────────────────────────────────────────────
# Cross-brain ABC parity
# ──────────────────────────────────────────────────────────────────


def test_all_brains_accept_allowed_tools_kwarg(
    claude_brain: ClaudeCodeBrain,
    opencode_brain: OpenCodeBrain,
    monkeypatch,
):
    """Every brain (``claude-code``, ``opencode``, ``null``) accepts
    the kwarg without raising. Pins the ABC contract: an aux caller
    can safely pass ``allowed_tools`` regardless of which brain the
    user picked. Subprocesses are mocked so this runs offline."""
    # claude-code path
    _capture_argv(monkeypatch)
    asyncio.run(
        claude_brain.spawn_aux("p", allowed_tools=["Read"])
    )

    # opencode path — patch subprocess.run inside the opencode module.
    captured_opencode: dict = {}

    class _FakeCP:
        stdout = b'{"text":"ok"}'
        stderr = b""
        returncode = 0

    def _fake_run(argv, **kwargs):
        captured_opencode["argv"] = list(argv)
        captured_opencode["env"] = kwargs.get("env")
        return _FakeCP()

    monkeypatch.setattr(
        "vexis_agent.core.brain.opencode.subprocess.run", _fake_run
    )
    asyncio.run(
        opencode_brain.spawn_aux("p", allowed_tools=["Read", "Write"])
    )
    # The agent permission block lives in OPENCODE_CONFIG_CONTENT
    # in the env so we don't need to scrape argv. The presence of
    # the env var itself is enough to confirm the call wired through.
    assert "OPENCODE_CONFIG_CONTENT" in captured_opencode["env"]
    import json
    parsed = json.loads(captured_opencode["env"]["OPENCODE_CONFIG_CONTENT"])
    perm = parsed["agent"]["vexis-aux"]["permission"]
    assert perm["write"] == "allow"

    # null brain path — records the kwarg verbatim.
    null = BrainNull(
        aux_results=[AuxResult(stdout="ok", stderr="", returncode=0)],
    )
    asyncio.run(
        null.spawn_aux("p", allowed_tools=["Read", "Grep"])
    )
    records = null.aux_call_records()
    assert records[-1]["allowed_tools"] == ["Read", "Grep"]


def test_null_brain_records_allowed_tools_none_distinct_from_empty():
    """A caller that doesn't set ``allowed_tools`` (back-compat) is
    distinguishable from one that explicitly passed ``[]`` (Issue
    #10's text-only-explicit form). Tests assert on this contract."""
    null = BrainNull(
        aux_results=[
            AuxResult(stdout="a", stderr="", returncode=0),
            AuxResult(stdout="b", stderr="", returncode=0),
        ],
    )
    asyncio.run(null.spawn_aux("p1"))  # no kwarg → None
    asyncio.run(null.spawn_aux("p2", allowed_tools=[]))  # explicit
    records = null.aux_call_records()
    assert records[0]["allowed_tools"] is None
    assert records[1]["allowed_tools"] == []


# ──────────────────────────────────────────────────────────────────
# Caller-site wiring — every shipping site declares an allowlist
# ──────────────────────────────────────────────────────────────────


def test_learning_review_callsites_pass_empty_allowlist(
    workspace: Path, tmp_path: Path, monkeypatch
):
    """The learning curator's triage + full review are both text-only
    judges. Both must declare ``allowed_tools=[]``. Asserted via
    BrainNull's call recorder — the spawn never touches subprocess."""
    from vexis_agent.core import learning_review

    # Stub the subsystem_tier / subsystem_reasoning so the test
    # doesn't depend on the user's config.yaml.
    monkeypatch.setattr(
        learning_review, "subsystem_tier", lambda _name: "small"
    )
    monkeypatch.setattr(
        learning_review, "subsystem_reasoning", lambda _name: None
    )
    monkeypatch.setattr(
        learning_review, "learning_triage_enabled", lambda: True
    )

    brain = BrainNull(
        aux_results=[
            # Triage returns NO so the review skips the full pass.
            AuxResult(stdout="NO", stderr="", returncode=0),
        ],
    )

    from datetime import datetime, timezone

    from vexis_agent.core.transcripts import SessionMeta, TranscriptMessage

    meta = SessionMeta(
        session_uuid="test-session",
        jsonl_path=tmp_path / "fake.jsonl",
        last_message_timestamp=None,
        message_count_estimate=2,
    )
    now = datetime.now(timezone.utc)
    messages = [
        TranscriptMessage(
            role="user", text="hi",
            timestamp=now, uuid="u1", tool_calls=(), raw={},
        ),
        TranscriptMessage(
            role="assistant", text="hello",
            timestamp=now, uuid="a1", tool_calls=(), raw={},
        ),
    ]

    learning_review.run_review(workspace, meta, messages, brain)

    records = brain.aux_call_records()
    assert records, "expected at least one spawn_aux call"
    # Both triage AND review must be text-only. We only got triage
    # in this test because the stub returned NO, but the assertion
    # generalises to every recorded call.
    for record in records:
        assert record["allowed_tools"] == [], (
            f"learning_review callsite missed allowed_tools=[]: "
            f"{record!r}"
        )


def test_coherence_judge_callsite_passes_empty_allowlist(
    workspace: Path, monkeypatch
):
    """coherence judge is advisory-only; must declare text-only."""
    from vexis_agent.core import coherence_judge

    monkeypatch.setattr(
        coherence_judge, "subsystem_tier", lambda _name: "small"
    )
    monkeypatch.setattr(
        coherence_judge, "subsystem_reasoning", lambda _name: None
    )

    brain = BrainNull(
        aux_results=[
            AuxResult(stdout='{"verdict":"COHERENT"}', stderr="", returncode=0),
        ],
    )
    from datetime import datetime, timezone

    from vexis_agent.core.transcripts import TranscriptMessage

    lesson = {"evidence": "user said: hi", "body": "user greeted us"}
    now = datetime.now(timezone.utc)
    messages = [
        TranscriptMessage(
            role="user", text="user said: hi",
            timestamp=now, uuid="u1", tool_calls=(), raw={},
        ),
    ]

    coherence_judge.run_coherence_judge(workspace, lesson, messages, brain)

    records = brain.aux_call_records()
    assert len(records) == 1
    assert records[0]["allowed_tools"] == []


def test_goal_judge_callsite_passes_empty_allowlist(
    workspace: Path, monkeypatch
):
    """goal judge produces a done/continue verdict; no tools."""
    from vexis_agent.core import goal_judge

    monkeypatch.setattr(goal_judge, "subsystem_tier", lambda _name: "large")
    monkeypatch.setattr(
        goal_judge, "subsystem_reasoning", lambda _name: None
    )

    brain = BrainNull(
        aux_results=[
            AuxResult(
                stdout='{"done": false, "reason": "more work"}',
                stderr="", returncode=0,
            ),
        ],
    )

    asyncio.run(goal_judge.judge_goal(
        workspace,
        "ship feature",
        "I'm working on it.",
        brain,
        files_changed=[],
    ))

    records = brain.aux_call_records()
    assert len(records) == 1
    assert records[0]["allowed_tools"] == []


def test_relationships_extractor_callsite_passes_empty_allowlist(
    workspace: Path, tmp_path: Path, monkeypatch
):
    """relationships extractor emits JSON facts; no tools."""
    from vexis_agent.core.relationships import extractor as extractor_mod
    from vexis_agent.core.relationships.candidate_store import (
        RelationshipsCandidateStore,
    )
    from vexis_agent.core.transcripts import TranscriptMessage

    monkeypatch.setattr(
        extractor_mod, "subsystem_tier", lambda _name: "small"
    )
    monkeypatch.setattr(
        extractor_mod, "subsystem_reasoning", lambda _name: None
    )

    brain = BrainNull(
        aux_results=[AuxResult(stdout="[]", stderr="", returncode=0)],
    )

    from datetime import datetime, timezone

    candidate_store = RelationshipsCandidateStore(tmp_path / "cands.json")
    now = datetime.now(timezone.utc)
    messages = [
        TranscriptMessage(
            role="user", text="my mom likes apples",
            timestamp=now, uuid="u1", tool_calls=(), raw={},
        ),
    ]

    asyncio.run(extractor_mod.extract_relationships(
        messages,
        "test-session",
        workspace=workspace,
        candidate_store=candidate_store,
        brain=brain,
    ))

    records = brain.aux_call_records()
    assert len(records) == 1
    assert records[0]["allowed_tools"] == []


def test_relationships_classifier_callsite_passes_empty_allowlist(
    workspace: Path, monkeypatch
):
    """relationships classifier returns JSON; no tools."""
    from vexis_agent.core.relationships import triggers

    monkeypatch.setattr(
        triggers, "subsystem_tier", lambda _name: "small"
    )
    monkeypatch.setattr(
        triggers, "subsystem_reasoning", lambda _name: None
    )

    brain = BrainNull(
        aux_results=[
            AuxResult(
                stdout='{"trigger":"none","reason":"no cue"}',
                stderr="", returncode=0,
            ),
        ],
    )

    asyncio.run(triggers._classifier_call(
        "hello",
        session_uuid="test-session",
        turn_index=0,
        workspace=workspace,
        brain=brain,
    ))

    records = brain.aux_call_records()
    assert len(records) == 1
    assert records[0]["allowed_tools"] == []


def test_curator_consolidation_callsite_passes_read_write_edit(
    tmp_path: Path,
):
    """Skill curator's consolidation pass needs Read + Write + Edit
    + Glob + Grep — the narrowest legitimate surface for moving and
    merging SKILL.md trees. Bash and WebFetch are NOT in the
    allowlist; a poisoned candidate skill must not be able to coax
    the curator into running shell."""
    from vexis_agent.core import curator

    # Seed a candidate skill so phase2 actually runs (matches the
    # setup pattern in tests/test_aux_spawn_routing.py).
    skills_root = tmp_path / "skills"
    (skills_root / "alpha").mkdir(parents=True)
    (skills_root / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: A test skill\n"
        "origin: learning-curator\n---\n# Body\n",
        encoding="utf-8",
    )

    brain = BrainNull(
        aux_results=[
            AuxResult(
                stdout="CURATOR-SUMMARY:\nNo changes needed.\n",
                stderr="",
                returncode=0,
            )
        ],
    )

    curator.run_phase2(tmp_path, brain)

    records = brain.aux_call_records()
    assert len(records) == 1
    allowlist = records[0]["allowed_tools"]
    assert allowlist is not None
    assert "Read" in allowlist
    assert "Write" in allowlist
    assert "Edit" in allowlist
    # Crucially Bash and WebFetch are NOT in the allowlist.
    assert "Bash" not in allowlist
    assert "WebFetch" not in allowlist
    # The legacy ``allow_tools`` boolean is still True (back-compat
    # safety net for code paths that haven't migrated).
    assert records[0]["allow_tools"] is True

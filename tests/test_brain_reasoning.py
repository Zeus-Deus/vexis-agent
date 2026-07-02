"""Brain.spawn_aux reasoning_level + context_window passthrough.

Pin that each shipping brain implementation translates
``reasoning_level`` to its native CLI flag (``--effort`` on
claude-code, ``--variant`` on opencode) and that the inert
``context_window`` kwarg is accepted without affecting the argv
(no CLI flag exists on either brain — see Brain.spawn_aux's
docstring).

Mocks ``subprocess.run`` and inspects the captured argv. Same
infrastructure shape as ``tests/test_brain_model_not_found.py``.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from vexis_agent.core.brain.claude_code import ClaudeCodeBrain
from vexis_agent.core.brain.opencode import OpenCodeBrain
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore


@pytest.fixture(autouse=True)
def _isolated_yaml_config(monkeypatch, tmp_path):
    from vexis_agent.core import yaml_config
    cfg_dir = tmp_path / "vexis-config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        yaml_config, "_config_path", lambda: cfg_dir / "config.yaml"
    )


@pytest.fixture
def cc_brain(tmp_path: Path) -> ClaudeCodeBrain:
    ws = tmp_path / "ws"
    ws.mkdir()
    return ClaudeCodeBrain(
        workspace=ws,
        session=SessionStore(tmp_path / "cc-sessions.json"),
        running_tasks=RunningTasks(),
    )


@pytest.fixture
def oc_brain(tmp_path: Path) -> OpenCodeBrain:
    ws = tmp_path / "ws"
    ws.mkdir()
    return OpenCodeBrain(
        workspace=ws,
        session=SessionStore(tmp_path / "oc-sessions.json"),
        running_tasks=RunningTasks(),
    )


def _ok_completed_process() -> MagicMock:
    """Mimic subprocess.CompletedProcess. claude-code's spawn_aux
    consumes stdout as text; opencode's consumes it as bytes. We
    can't tell ahead of time which brain's calling — return text
    by default, individual tests override stdout if they want
    bytes."""
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.returncode = 0
    cp.stdout = "ok"
    cp.stderr = ""
    return cp


def _ok_bytes_completed_process() -> MagicMock:
    """opencode reads stdout as bytes (then .decode'd). Tests
    against opencode brain need bytes here or `.decode` raises."""
    cp = MagicMock(spec=subprocess.CompletedProcess)
    cp.returncode = 0
    cp.stdout = b'{"type":"finished","reason":"end_turn","content":"ok"}\n'
    cp.stderr = b""
    return cp


# ──────────────────────────────────────────────────────────────────
# claude-code: --effort flag
# ──────────────────────────────────────────────────────────────────


def test_cc_passes_reasoning_level_via_effort_flag(cc_brain):
    """``reasoning_level="high"`` translates to ``--effort high``
    in the spawned argv. No flag when reasoning_level is None."""
    captured: list[list[str]] = []

    def _spy(argv, **_kw):
        captured.append(list(argv))
        return _ok_bytes_completed_process()

    with patch("subprocess.run", side_effect=_spy):
        asyncio.run(cc_brain.spawn_aux(
            "test prompt",
            reasoning_level="high",
        ))
    assert captured, "subprocess.run was not called"
    argv = captured[0]
    assert "--effort" in argv
    idx = argv.index("--effort")
    assert argv[idx + 1] == "high"


def test_cc_no_effort_flag_when_reasoning_level_none(cc_brain):
    """No reasoning_level → no --effort flag (brain picks default)."""
    captured: list[list[str]] = []
    with patch(
        "subprocess.run",
        side_effect=lambda argv, **_kw: (
            captured.append(list(argv)) or _ok_bytes_completed_process()
        ),
    ):
        asyncio.run(cc_brain.spawn_aux("test prompt"))
    assert "--effort" not in captured[0]


def test_cc_context_window_is_inert(cc_brain):
    """``context_window`` is accepted for ABC stability but the
    claude CLI has no runtime context flag — argv must be
    unchanged from the no-context-window case."""
    captured: list[list[str]] = []
    with patch(
        "subprocess.run",
        side_effect=lambda argv, **_kw: (
            captured.append(list(argv)) or _ok_bytes_completed_process()
        ),
    ):
        asyncio.run(cc_brain.spawn_aux(
            "test prompt", context_window=1000000,
        ))
    argv = captured[0]
    # No surprise flags introduced. Probe specifically: no --context,
    # --max-input, --max-tokens-input, etc.
    for flag in ("--context", "--max-input", "--max-input-tokens",
                 "--context-window"):
        assert flag not in argv, f"unexpected flag {flag} in argv"


def test_cc_reasoning_and_model_compose(cc_brain):
    """``reasoning_level`` works alongside an explicit
    ``model_tier``. Both flags appear in the argv; --effort
    after --model is fine for claude-code (order doesn't matter)."""
    captured: list[list[str]] = []
    with patch(
        "subprocess.run",
        side_effect=lambda argv, **_kw: (
            captured.append(list(argv)) or _ok_bytes_completed_process()
        ),
    ):
        asyncio.run(cc_brain.spawn_aux(
            "test prompt",
            model_tier="claude-opus-4-7",  # raw model name passes through
            reasoning_level="max",
        ))
    argv = captured[0]
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "claude-opus-4-7"
    assert "--effort" in argv
    assert argv[argv.index("--effort") + 1] == "max"


# ──────────────────────────────────────────────────────────────────
# opencode: --variant flag
# ──────────────────────────────────────────────────────────────────


def test_oc_passes_reasoning_level_via_variant_flag(oc_brain):
    """``reasoning_level="high"`` translates to ``--variant high``
    in the spawned argv."""
    captured: list[list[str]] = []
    with patch(
        "subprocess.run",
        side_effect=lambda argv, **_kw: (
            captured.append(list(argv)) or _ok_bytes_completed_process()
        ),
    ):
        asyncio.run(oc_brain.spawn_aux(
            "test prompt", reasoning_level="high",
        ))
    argv = captured[0]
    assert "--variant" in argv
    assert argv[argv.index("--variant") + 1] == "high"


def test_oc_no_variant_flag_when_reasoning_level_none(oc_brain):
    """No reasoning_level → no --variant flag."""
    captured: list[list[str]] = []
    with patch(
        "subprocess.run",
        side_effect=lambda argv, **_kw: (
            captured.append(list(argv)) or _ok_bytes_completed_process()
        ),
    ):
        asyncio.run(oc_brain.spawn_aux("test prompt"))
    assert "--variant" not in captured[0]


def test_oc_context_window_is_inert(oc_brain):
    """opencode CLI also has no runtime context flag."""
    captured: list[list[str]] = []
    with patch(
        "subprocess.run",
        side_effect=lambda argv, **_kw: (
            captured.append(list(argv)) or _ok_bytes_completed_process()
        ),
    ):
        asyncio.run(oc_brain.spawn_aux(
            "test prompt", context_window=1000000,
        ))
    argv = captured[0]
    for flag in ("--context", "--context-window", "--max-context"):
        assert flag not in argv, f"unexpected flag {flag} in argv"


# ──────────────────────────────────────────────────────────────────
# BrainNull: kwargs accepted + recorded
# ──────────────────────────────────────────────────────────────────


def test_null_records_reasoning_and_context_kwargs():
    """BrainNull is the test fake; it must accept the new kwargs
    AND record them so cross-brain contract tests can assert
    plumbing without spinning up real subprocesses."""
    from vexis_agent.core.brain.null import BrainNull
    from vexis_agent.core.brain.base import AuxResult
    null = BrainNull(
        aux_results=[AuxResult(stdout="", stderr="", returncode=0)],
    )
    asyncio.run(null.spawn_aux(
        "test prompt",
        model_tier="small",
        reasoning_level="medium",
        context_window=200000,
        subsystem="curator",
    ))
    rec = null.aux_call_records()[0]
    assert rec["reasoning_level"] == "medium"
    assert rec["context_window"] == 200000


# ══════════════════════════════════════════════════════════════════
# Issue #50 — Workstream A: foreground (chat) reasoning effort
#
# ``models.brain`` grows the same dict shape the subsystems already
# accept (``{model: ..., reasoning: ...}``) so a headless deployment
# can pin the chat brain's effort level without an out-of-band
# ``~/.claude/settings.json`` edit. Two layers under test: the
# yaml_config resolvers that read the config, and the handler's
# foreground resolution that plumbs both into ``Brain.respond`` /
# ``Brain.astream`` while preserving the per-turn-override precedence.
# ══════════════════════════════════════════════════════════════════

from vexis_agent.core import yaml_config  # noqa: E402
from vexis_agent.core.brain.null import BrainNull  # noqa: E402
from vexis_agent.core.handler import MessageHandler  # noqa: E402

_A_USER = 99
_A_CHAT = 100


def _write_config(monkeypatch, tmp_path, body: str) -> None:
    """Point yaml_config at a tmp config.yaml carrying ``body``.

    Overrides the autouse ``_isolated_yaml_config`` patch (last
    ``monkeypatch.setattr`` wins) so a single test can pin an exact
    ``models.brain`` shape and exercise the disk-reading resolvers."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(body, encoding="utf-8")
    monkeypatch.setattr(yaml_config, "_config_path", lambda: cfg)


def _build_handler(brain: BrainNull) -> MessageHandler:
    """Minimal foreground handler over a null brain. ``sessions`` is a
    bare object — the compression hook swallows its ``AttributeError``
    — and ``notifier`` is None so ``_inject_context`` passes the user
    text through untouched on the first turn."""
    return MessageHandler(
        brain=brain,
        sessions=object(),
        allowed_user_id=_A_USER,
        notifier=None,
    )


# ── yaml_config resolvers: pure-function shape ────────────────────


def test_model_brain_from_config_string_backcompat():
    """A plain-string ``models.brain`` returns the string as the model
    and ``None`` as the effort — byte-identical to the pre-#50 helper."""
    assert yaml_config.model_brain_from_config({"brain": "opus"}) == "opus"
    assert (
        yaml_config.model_brain_reasoning_from_config({"brain": "opus"})
        is None
    )


def test_model_brain_from_config_dict_model_and_reasoning():
    """The dict shape yields the model id AND the effort level."""
    section = {"brain": {"model": "sonnet", "reasoning": "low"}}
    assert yaml_config.model_brain_from_config(section) == "sonnet"
    assert yaml_config.model_brain_reasoning_from_config(section) == "low"


def test_model_brain_from_config_reasoning_only_is_reporter_fix():
    """The issue reporter's exact case: ``{reasoning: low}`` with no
    model → account-default model (the ``default`` sentinel) at low
    effort. Model half falls to ``default``, effort half survives."""
    section = {"brain": {"reasoning": "low"}}
    assert yaml_config.model_brain_from_config(section) == "default"
    assert yaml_config.model_brain_reasoning_from_config(section) == "low"


def test_model_brain_from_config_default_model_with_reasoning():
    """An explicit ``model: default`` behaves like the model-less dict:
    default model, effort preserved."""
    section = {"brain": {"model": "default", "reasoning": "low"}}
    assert yaml_config.model_brain_from_config(section) == "default"
    assert yaml_config.model_brain_reasoning_from_config(section) == "low"


def test_model_brain_from_config_malformed_falls_through():
    """Non-string-non-dict values, non-string dict members, and a
    non-dict section all collapse to the safe (default, None) pair —
    a config typo never bricks the daemon."""
    for section in (
        "not a dict",
        {"brain": ["a", "b"]},
        {"brain": True},
        {"brain": {"model": ["a"], "reasoning": 123}},
        {},
    ):
        assert yaml_config.model_brain_from_config(section) == "default"
        assert yaml_config.model_brain_reasoning_from_config(section) is None


# ── yaml_config resolvers: disk-reading wrappers hot-reload ───────


def test_model_brain_disk_string_backcompat(monkeypatch, tmp_path):
    """Existing string configs read off disk unchanged — the exact
    behaviour test_yaml_config_models pins, re-asserted here alongside
    the new effort wrapper returning None."""
    _write_config(monkeypatch, tmp_path, "models:\n  brain: opus\n")
    assert yaml_config.model_brain() == "opus"
    assert yaml_config.model_brain_reasoning() is None


def test_model_brain_disk_default_when_unset(monkeypatch, tmp_path):
    """No ``models.brain`` → the ``default`` sentinel and no effort."""
    _write_config(monkeypatch, tmp_path, "other: {}\n")
    assert yaml_config.model_brain() == "default"
    assert yaml_config.model_brain_reasoning() is None


def test_model_brain_disk_dict_shape(monkeypatch, tmp_path):
    """The dict shape round-trips through YAML + the disk wrappers."""
    _write_config(
        monkeypatch,
        tmp_path,
        "models:\n  brain:\n    model: sonnet\n    reasoning: low\n",
    )
    assert yaml_config.model_brain() == "sonnet"
    assert yaml_config.model_brain_reasoning() == "low"


def test_model_brain_disk_reasoning_only(monkeypatch, tmp_path):
    """Reporter's fix, end-to-end off disk: effort with no model."""
    _write_config(
        monkeypatch, tmp_path, "models:\n  brain:\n    reasoning: low\n"
    )
    assert yaml_config.model_brain() == "default"
    assert yaml_config.model_brain_reasoning() == "low"


# ── handler foreground resolution: precedence contract ────────────


def test_resolver_string_config_no_reasoning(monkeypatch, tmp_path):
    """Plain-chat turn, string config: model resolves, effort None."""
    _write_config(monkeypatch, tmp_path, "models:\n  brain: sonnet\n")
    handler = _build_handler(BrainNull())
    assert handler._resolve_foreground_model(None, None) == ("sonnet", None)


def test_resolver_default_config_both_none(monkeypatch, tmp_path):
    """Unset ``models.brain`` → no model flag, no effort — historical
    account-default behaviour, untouched."""
    _write_config(monkeypatch, tmp_path, "models: {}\n")
    handler = _build_handler(BrainNull())
    assert handler._resolve_foreground_model(None, None) == (None, None)


def test_resolver_dict_model_and_reasoning(monkeypatch, tmp_path):
    """Plain-chat turn, dict config: model + effort both resolve."""
    _write_config(
        monkeypatch,
        tmp_path,
        "models:\n  brain:\n    model: sonnet\n    reasoning: low\n",
    )
    handler = _build_handler(BrainNull())
    assert handler._resolve_foreground_model(None, None) == ("sonnet", "low")


def test_resolver_reasoning_only_reporter_case(monkeypatch, tmp_path):
    """``{reasoning: low}`` → model None (account default) but effort
    still rides through. This alone fixes the reporter's deployment."""
    _write_config(
        monkeypatch, tmp_path, "models:\n  brain:\n    reasoning: low\n"
    )
    handler = _build_handler(BrainNull())
    assert handler._resolve_foreground_model(None, None) == (None, "low")


def test_resolver_override_suppresses_config_reasoning(monkeypatch, tmp_path):
    """Per-turn override precedence: a non-None ``model`` (voice call
    mode / computer-use substitution) passes BOTH values through
    untouched. Config effort must NOT leak onto an overridden model."""
    _write_config(
        monkeypatch,
        tmp_path,
        "models:\n  brain:\n    model: sonnet\n    reasoning: high\n",
    )
    handler = _build_handler(BrainNull())
    assert handler._resolve_foreground_model("opus", None) == ("opus", None)
    assert handler._resolve_foreground_model("opus", "max") == ("opus", "max")


def test_resolver_caller_reasoning_wins_over_config(monkeypatch, tmp_path):
    """If a caller somehow passes reasoning while leaving model None,
    the caller's effort beats config — the caller is the more specific
    intent."""
    _write_config(
        monkeypatch, tmp_path, "models:\n  brain:\n    reasoning: low\n"
    )
    handler = _build_handler(BrainNull())
    assert handler._resolve_foreground_model(None, "high") == (None, "high")


def test_resolver_malformed_dict_falls_through(monkeypatch, tmp_path):
    """Malformed dict members degrade to the safe (None, None) pair —
    no crash, account default."""
    _write_config(
        monkeypatch,
        tmp_path,
        "models:\n  brain:\n    model: [a, b]\n    reasoning: 123\n",
    )
    handler = _build_handler(BrainNull())
    assert handler._resolve_foreground_model(None, None) == (None, None)


# ── handler end-to-end: null brain records the resolved pair ──────


def test_handle_plumbs_config_reasoning_into_respond(monkeypatch, tmp_path):
    """The dict config reaches ``Brain.respond`` on a plain chat turn:
    the null brain records both the resolved model and effort."""
    _write_config(
        monkeypatch,
        tmp_path,
        "models:\n  brain:\n    model: sonnet\n    reasoning: low\n",
    )
    brain = BrainNull(responses=["ok"])
    handler = _build_handler(brain)
    asyncio.run(handler.handle(_A_USER, _A_CHAT, "hi"))
    message, chat_id, model, reasoning = brain._respond_calls[0]
    assert message == "hi"
    assert chat_id == _A_CHAT
    assert model == "sonnet"
    assert reasoning == "low"


def test_stream_plumbs_config_reasoning_into_astream(monkeypatch, tmp_path):
    """Same resolution on the streaming path — the SSE route must not
    diverge from Telegram/text-chat. BrainNull.astream (the base-class
    default) delegates to respond, so ``_respond_calls`` still captures
    the resolved pair."""
    _write_config(
        monkeypatch,
        tmp_path,
        "models:\n  brain:\n    model: sonnet\n    reasoning: low\n",
    )
    brain = BrainNull(responses=["ok"])
    handler = _build_handler(brain)

    async def _drain() -> None:
        async for _event in handler.stream(_A_USER, _A_CHAT, "hi"):
            pass

    asyncio.run(_drain())
    _message, _chat_id, model, reasoning = brain._respond_calls[0]
    assert model == "sonnet"
    assert reasoning == "low"


def test_handle_reporter_case_default_model_low_effort(monkeypatch, tmp_path):
    """Reporter's deployment, end-to-end: ``{reasoning: low}`` reaches
    the brain as (model=None, reasoning="low") — account default at low
    effort, no ``--model`` flag."""
    _write_config(
        monkeypatch, tmp_path, "models:\n  brain:\n    reasoning: low\n"
    )
    brain = BrainNull(responses=["ok"])
    handler = _build_handler(brain)
    asyncio.run(handler.handle(_A_USER, _A_CHAT, "hi"))
    _message, _chat_id, model, reasoning = brain._respond_calls[0]
    assert model is None
    assert reasoning == "low"


def test_handle_voice_override_suppresses_config_reasoning(monkeypatch, tmp_path):
    """A caller-supplied model (voice call mode) survives to the brain
    untouched, and config effort does NOT leak onto it."""
    _write_config(
        monkeypatch,
        tmp_path,
        "models:\n  brain:\n    model: sonnet\n    reasoning: high\n",
    )
    brain = BrainNull(responses=["ok"])
    handler = _build_handler(brain)
    asyncio.run(handler.handle(_A_USER, _A_CHAT, "hi", model="opus"))
    _message, _chat_id, model, reasoning = brain._respond_calls[0]
    assert model == "opus"
    assert reasoning is None


def test_handle_string_config_is_byte_identical(monkeypatch, tmp_path):
    """Back-compat pin: a string ``models.brain`` reaches the brain
    exactly as before — model resolved, effort None (defer to CLI
    default). No effort ever appears from a string config."""
    _write_config(monkeypatch, tmp_path, "models:\n  brain: sonnet\n")
    brain = BrainNull(responses=["ok"])
    handler = _build_handler(brain)
    asyncio.run(handler.handle(_A_USER, _A_CHAT, "hi"))
    _message, _chat_id, model, reasoning = brain._respond_calls[0]
    assert model == "sonnet"
    assert reasoning is None

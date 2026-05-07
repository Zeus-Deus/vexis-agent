"""Day 2 model UX — spawn-site BrainModelNotFoundError backstop.

Tests the per-brain detection helpers + the structured exception
they raise. The validator (Day 1) catches the same condition
pre-write at every UX surface; this test pins the safety net for
cases the validator missed (stale claude-code discovery list,
opencode discovery cache empty, validator rule edge cases in
flight).

Both brains' detection wording is pinned against the empirically
verified outputs. If the upstream CLIs change their error
messages, the smoke probe in
``tests/test_brain_*_smoke.py`` against ``--model
definitely-not-a-real-model`` will surface the drift; this file
keeps the test infrastructure honest by mocking the subprocess
output to the canonical patterns.

Design citation: ``.plans/model-management-ux-research.md`` §4
"Spawn-site error vocabulary" + §6 Day 2.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.brain.base import BrainError, BrainModelNotFoundError
from core.brain.claude_code import (
    _CC_MODEL_NOT_FOUND_STDOUT_MARKER,
    ClaudeCodeBrain,
)
from core.brain.opencode import OpenCodeBrain, _detect_model_not_found
from core.model_validator import (
    CLAUDE_CODE_MODEL_NOT_FOUND_FIX_TEMPLATE,
    OPENCODE_FORMAT_FIX_TEMPLATE,
)
from core.running_tasks import RunningTasks
from core.sessions import SessionStore


# ──────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolated_yaml_config(monkeypatch, tmp_path):
    """Tier resolution reads ``~/.vexis/config.yaml``. Insulate
    from the user's real config so the brain's tier resolution
    behaves like a fresh install."""
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
    return ws


@pytest.fixture
def cc_brain(workspace: Path, tmp_path: Path) -> ClaudeCodeBrain:
    return ClaudeCodeBrain(
        workspace=workspace,
        session=SessionStore(tmp_path / "cc-sessions.json"),
        running_tasks=RunningTasks(),
    )


@pytest.fixture
def oc_brain(workspace: Path, tmp_path: Path) -> OpenCodeBrain:
    return OpenCodeBrain(
        workspace=workspace,
        session=SessionStore(tmp_path / "oc-sessions.json"),
        running_tasks=RunningTasks(),
    )


# ──────────────────────────────────────────────────────────────────
# BrainModelNotFoundError exception shape
# ──────────────────────────────────────────────────────────────────


def test_exception_in_brain_error_hierarchy():
    """Subclass of BrainError so transport-layer
    ``except BrainError:`` paths still catch it. Distinct semantic
    is signalled by the class identity, not by escaping the
    hierarchy."""
    exc = BrainModelNotFoundError(
        subsystem="curator",
        model_id="bogus",
        brain_kind="claude-code",
        suggested_fix="fix this",
    )
    assert isinstance(exc, BrainError)


def test_exception_carries_all_fields():
    exc = BrainModelNotFoundError(
        subsystem="goal_judge",
        model_id="anthropic/totally-fake",
        brain_kind="opencode",
        suggested_fix="run /model set goal_judge large",
    )
    assert exc.subsystem == "goal_judge"
    assert exc.model_id == "anthropic/totally-fake"
    assert exc.brain_kind == "opencode"
    assert exc.suggested_fix == "run /model set goal_judge large"


def test_exception_str_includes_actionable_text():
    """str(exc) is what a generic ``except BrainError as e: log(e)``
    would print. Must include the suggested_fix so a user reading
    the curator log sees what to do without rummaging through
    attributes."""
    exc = BrainModelNotFoundError(
        subsystem="curator",
        model_id="x",
        brain_kind="claude-code",
        suggested_fix="run /model set curator small",
    )
    s = str(exc)
    assert "x" in s
    assert "claude-code" in s
    assert "curator" in s
    assert "run /model set curator small" in s


# ──────────────────────────────────────────────────────────────────
# claude-code detector — stdout substring + non-zero exit
# ──────────────────────────────────────────────────────────────────


def test_cc_marker_constant_is_what_we_observed_empirically():
    """Pin: the marker is the stable prefix of the actual claude-
    code error wording. If claude rewords the message, the smoke
    probe against ``--model definitely-not-a-real-model`` will
    surface; until then this is the canonical detection signal."""
    assert _CC_MODEL_NOT_FOUND_STDOUT_MARKER == (
        "There's an issue with the selected model"
    )


def test_cc_spawn_aux_raises_on_bad_model(cc_brain: ClaudeCodeBrain, tmp_path):
    """Mock subprocess.run to return the canonical bad-model output:
    exit=1, stderr empty, stdout starts with the marker."""
    fake_completed = MagicMock(spec=subprocess.CompletedProcess)
    fake_completed.returncode = 1
    fake_completed.stderr = b""
    fake_completed.stdout = (
        b"There's an issue with the selected model "
        b"(definitely-not-a-real-model). It may not exist or you "
        b"may not have access to it. Run --model to pick a different "
        b"model.\n"
    )
    with patch("subprocess.run", return_value=fake_completed):
        with pytest.raises(BrainModelNotFoundError) as ei:
            asyncio.run(cc_brain.spawn_aux(
                "irrelevant prompt",
                model_tier="definitely-not-a-real-model",
                subsystem="curator",
            ))
    exc = ei.value
    assert exc.brain_kind == "claude-code"
    assert exc.subsystem == "curator"
    assert exc.model_id == "definitely-not-a-real-model"


def test_cc_spawn_aux_does_not_raise_on_normal_failure(
    cc_brain: ClaudeCodeBrain,
):
    """A non-zero exit WITHOUT the marker (e.g. timeout, auth
    failure) returns the AuxResult — same pre-Day-2 behaviour. Only
    the model-not-found marker triggers the structured exception."""
    fake_completed = MagicMock(spec=subprocess.CompletedProcess)
    fake_completed.returncode = 1
    fake_completed.stderr = b"some other error\n"
    fake_completed.stdout = b""
    with patch("subprocess.run", return_value=fake_completed):
        result = asyncio.run(cc_brain.spawn_aux(
            "irrelevant",
            model_tier="haiku",
            subsystem="curator",
        ))
    assert result.returncode == 1
    assert "some other error" in result.stderr


def test_cc_spawn_aux_does_not_raise_when_no_model_arg(
    cc_brain: ClaudeCodeBrain,
):
    """If no ``--model`` was passed (model_tier is None or
    "default"), we can't attribute a model-not-found to a
    specific model id. Return AuxResult with the failure rather
    than raising — caller decides what to do."""
    fake_completed = MagicMock(spec=subprocess.CompletedProcess)
    fake_completed.returncode = 1
    fake_completed.stderr = b""
    fake_completed.stdout = (
        b"There's an issue with the selected model (xxx).\n"
    )
    with patch("subprocess.run", return_value=fake_completed):
        result = asyncio.run(cc_brain.spawn_aux(
            "irrelevant",
            model_tier=None,
            subsystem="curator",
        ))
    assert result.returncode == 1


def test_cc_subsystem_defaults_to_unknown_marker(cc_brain: ClaudeCodeBrain):
    """Subsystem is optional in the API; missing → '<unknown>' in the
    exception. Pin so callers know what they'll see if they forget
    to pass it."""
    fake_completed = MagicMock(spec=subprocess.CompletedProcess)
    fake_completed.returncode = 1
    fake_completed.stderr = b""
    fake_completed.stdout = (
        b"There's an issue with the selected model (xxx).\n"
    )
    with patch("subprocess.run", return_value=fake_completed):
        with pytest.raises(BrainModelNotFoundError) as ei:
            asyncio.run(cc_brain.spawn_aux(
                "irrelevant",
                model_tier="bogus",
                # subsystem not passed
            ))
    assert ei.value.subsystem == "<unknown>"


def test_cc_suggested_fix_uses_template_constant(cc_brain: ClaudeCodeBrain):
    """The suggested_fix copy comes from the imported template
    constant, NOT a duplicated string. Pin so a Day 4 wording
    update in core.model_validator propagates to the spawn site
    without a separate edit."""
    fake_completed = MagicMock(spec=subprocess.CompletedProcess)
    fake_completed.returncode = 1
    fake_completed.stderr = b""
    fake_completed.stdout = (
        b"There's an issue with the selected model (xxx).\n"
    )
    with patch("subprocess.run", return_value=fake_completed):
        with pytest.raises(BrainModelNotFoundError) as ei:
            asyncio.run(cc_brain.spawn_aux(
                "irrelevant",
                model_tier="bogus",
                subsystem="curator",
            ))
    expected = CLAUDE_CODE_MODEL_NOT_FOUND_FIX_TEMPLATE.format(
        model_id="bogus", subsystem="curator",
    )
    assert ei.value.suggested_fix == expected


# ──────────────────────────────────────────────────────────────────
# opencode detector — typed JSON event in stdout
# ──────────────────────────────────────────────────────────────────


def test_oc_detector_finds_canonical_event():
    """Pin the detection logic against the actual opencode JSON
    event shape verified empirically:

        {"type":"error","timestamp":...,"sessionID":"ses_...",
         "error":{"name":"UnknownError",
                  "data":{"message":"Model not found: <id>/."}}}
    """
    canonical_event = (
        '{"type":"error","timestamp":1778146803345,'
        '"sessionID":"ses_abc","error":{"name":"UnknownError",'
        '"data":{"message":"Model not found: not-a-real-model/."}}}'
    )
    assert _detect_model_not_found(canonical_event) is True


def test_oc_detector_silent_on_clean_stream():
    """Normal text events shouldn't trigger detection."""
    clean = json.dumps({
        "type": "text", "sessionID": "ses_x",
        "part": {"text": "hello"},
    })
    assert _detect_model_not_found(clean) is False


def test_oc_detector_silent_on_session_error_message():
    """A typed error event with a DIFFERENT message
    (session-not-found, etc.) shouldn't trip the model-not-found
    detector — different recovery path. SessionLost handles
    session errors; this only fires for model errors."""
    sess_err = json.dumps({
        "type": "error",
        "error": {
            "name": "NotFoundError",
            "data": {"message": "Session not found: ses_abc"},
        },
    })
    assert _detect_model_not_found(sess_err) is False


def test_oc_detector_handles_malformed_lines():
    """Mixed stream — malformed lines, blank lines, non-JSON
    interleaved with the marker event. Detector finds the marker
    without raising."""
    stream = "\n".join([
        "",
        "not json at all",
        '{"type":"text","part":{"text":"hi"}}',
        '{"type":"error","error":{"data":{"message":"Model not found: xxx/."}}}',
        "",
    ])
    assert _detect_model_not_found(stream) is True


def test_oc_detector_case_insensitive_message():
    """The message-substring check should be case-insensitive so
    a future opencode wording like "MODEL NOT FOUND" still trips."""
    stream = json.dumps({
        "type": "error",
        "error": {"data": {"message": "MODEL NOT FOUND: xxx/."}},
    })
    assert _detect_model_not_found(stream) is True


def test_oc_spawn_aux_raises_on_bad_model_event_in_stdout(
    oc_brain: OpenCodeBrain,
):
    """Full integration through ``spawn_aux``: mock subprocess to
    return the canonical bad-model event in stdout (with exit=0,
    matching opencode's actual behaviour in --format json mode)."""
    canonical_stdout = (
        '{"type":"error","timestamp":1778146803345,'
        '"sessionID":"ses_abc","error":{"name":"UnknownError",'
        '"data":{"message":"Model not found: bogus-model/."}}}\n'
    )
    fake_completed = MagicMock(spec=subprocess.CompletedProcess)
    fake_completed.returncode = 0  # opencode exits 0 even on bad model
    fake_completed.stdout = canonical_stdout.encode("utf-8")
    fake_completed.stderr = b""
    with patch("subprocess.run", return_value=fake_completed):
        with pytest.raises(BrainModelNotFoundError) as ei:
            asyncio.run(oc_brain.spawn_aux(
                "irrelevant",
                model_tier="bogus-model",
                subsystem="goal_judge",
            ))
    exc = ei.value
    assert exc.brain_kind == "opencode"
    assert exc.subsystem == "goal_judge"
    # The model_id field carries whatever opencode was told to use,
    # which after tier resolution is the raw-string passthrough.
    assert exc.model_id == "bogus-model"


def test_oc_spawn_aux_does_not_raise_when_no_model_arg(
    oc_brain: OpenCodeBrain, monkeypatch,
):
    """When tier resolution returns None (no model picked), opencode
    falls back to its account default. We can't attribute a
    model-not-found in this case — return AuxResult."""
    canonical_stdout = (
        '{"type":"error","error":{"data":'
        '{"message":"Model not found: xxx/."}}}\n'
    )
    fake_completed = MagicMock(spec=subprocess.CompletedProcess)
    fake_completed.returncode = 0
    fake_completed.stdout = canonical_stdout.encode("utf-8")
    fake_completed.stderr = b""
    with patch("subprocess.run", return_value=fake_completed):
        # tier=None means model_for_tier returns None → no --model arg.
        # Without a model arg, the detector still sees the event but
        # we don't attribute. Caller gets AuxResult.
        result = asyncio.run(oc_brain.spawn_aux(
            "irrelevant", model_tier=None, subsystem="curator",
        ))
    assert isinstance(result.returncode, int)


def test_oc_suggested_fix_uses_format_template_constant(
    oc_brain: OpenCodeBrain,
):
    """Same single-source-of-truth pin for opencode: the
    suggested_fix copy comes from
    ``OPENCODE_FORMAT_FIX_TEMPLATE`` imported from
    core.model_validator."""
    canonical_stdout = (
        '{"type":"error","error":{"data":'
        '{"message":"Model not found: xxx/."}}}\n'
    )
    fake_completed = MagicMock(spec=subprocess.CompletedProcess)
    fake_completed.returncode = 0
    fake_completed.stdout = canonical_stdout.encode("utf-8")
    fake_completed.stderr = b""
    with patch("subprocess.run", return_value=fake_completed):
        with pytest.raises(BrainModelNotFoundError) as ei:
            asyncio.run(oc_brain.spawn_aux(
                "irrelevant",
                model_tier="some-bare-alias",
                subsystem="curator",
            ))
    expected = OPENCODE_FORMAT_FIX_TEMPLATE.format(
        model_id="some-bare-alias", subsystem="curator",
    )
    assert ei.value.suggested_fix == expected


# ──────────────────────────────────────────────────────────────────
# Cross-brain — exception fields shape pin
# ──────────────────────────────────────────────────────────────────


def test_validator_and_backstop_share_suggested_fix_constants():
    """The shared-vocabulary contract: the validator's pre-write
    findings and the spawn-site backstop's exception MUST emit the
    same suggested_fix copy for the same condition. Pin by asserting
    both surfaces resolve to the same template-substituted string
    given the same inputs."""
    from core.model_validator import (
        OPENCODE_FORMAT_FIX_TEMPLATE,
        validate_models_config,
    )

    # Validator pre-write: opencode brain + bare alias.
    config = {
        "brain": {"kind": "opencode"},
        "models": {"learning_review": "sonnet"},
    }
    findings = validate_models_config(config, "opencode")
    rule4_match = next(
        f for f in findings
        if f.severity == "error"
        and f.subsystem == "learning_review"
        and "bare alias" in f.problem
    )
    validator_fix = rule4_match.suggested_fix

    # Backstop emits the same template with the same substitutions.
    # (We don't actually spawn opencode here — just assert the
    # template substitution matches.)
    backstop_fix = OPENCODE_FORMAT_FIX_TEMPLATE.format(
        model_id="sonnet", subsystem="learning_review",
    )
    assert validator_fix == backstop_fix

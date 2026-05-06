"""Phase B routing pins: every aux-spawn consumer site calls
``Brain.spawn_aux`` with the right tier name.

These tests pin the *contract* between each subsystem and the brain:
which abstract size tier (``tiny`` / ``small`` / ``medium`` /
``large``) the subsystem requests, and which env-override marker it
sets for recursion-guard inheritance. The brain implementation owns
tier-to-native translation; subsystems own tier *choice*.

If any of these break, a Phase B consumer migration regressed —
either the wrong tier was passed, the wrong env-override marker, or
the brain abstraction was bypassed entirely. Either way, fail loud.

Design citation: ``.plans/brain-abstraction-research.md`` §5 Day 2.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.brain.base import AuxResult
from core.brain.null import BrainNull


@pytest.fixture(autouse=True)
def _isolated_yaml_config(monkeypatch, tmp_path):
    """Tier resolution reads ``~/.vexis/config.yaml``. Tests in this
    file assert on the DEFAULT tier per subsystem, so the user's
    real config (which may override e.g. ``models.coherence_judge:
    sonnet``) must not leak in."""
    from core import yaml_config
    cfg_dir = tmp_path / "vexis-config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        yaml_config, "_config_path", lambda: cfg_dir / "config.yaml"
    )


# ──────────────────────────────────────────────────────────────────
# goal_judge → spawn_aux(model_tier="large")
# ──────────────────────────────────────────────────────────────────


def test_goal_judge_routes_with_large_tier(tmp_path: Path):
    """goal_judge uses ``large`` because false-positive 'done' would
    silently stall the loop — quality outweighs cost."""
    from core.goal_judge import GOAL_JUDGE_ENV_VAR, judge_goal

    brain = BrainNull(
        aux_results=[
            AuxResult(
                stdout='{"done": false, "reason": "test"}',
                stderr="",
                returncode=0,
            )
        ]
    )
    asyncio.run(judge_goal(tmp_path, "ship the thing", "made progress", brain))
    record = brain.aux_call_records()[0]
    assert record["model_tier"] == "large"
    assert record["env_overrides"] == {GOAL_JUDGE_ENV_VAR: "1"}
    assert record["allow_tools"] is False
    assert record["cwd"] == tmp_path


# ──────────────────────────────────────────────────────────────────
# coherence_judge → spawn_aux(model_tier="small")
# ──────────────────────────────────────────────────────────────────


def test_coherence_judge_routes_with_small_tier(tmp_path: Path):
    from core.coherence_judge import COHERENCE_JUDGE_ENV_VAR, run_coherence_judge
    from core.transcripts import TranscriptMessage
    from datetime import datetime, timezone

    msgs = [
        TranscriptMessage(
            role="user",
            text="evidence text here",
            timestamp=datetime.now(timezone.utc),
            uuid="m1",
            tool_calls=(),
            raw={},
        )
    ]
    lesson = {
        "class": "PROCEDURAL",
        "tier": "S3",
        "lesson": "L",
        "scope": "S",
        "evidence": "evidence text here",
    }
    brain = BrainNull(
        aux_results=[
            AuxResult(
                stdout='{"verdict": "COHERENT", "reason": null, "explanation": null}',
                stderr="",
                returncode=0,
            )
        ]
    )
    run_coherence_judge(tmp_path, lesson, msgs, brain)
    record = brain.aux_call_records()[0]
    assert record["model_tier"] == "small"
    assert record["env_overrides"] == {COHERENCE_JUDGE_ENV_VAR: "1"}
    assert record["allow_tools"] is False


# ──────────────────────────────────────────────────────────────────
# relationships_extractor → spawn_aux(model_tier="medium")
# ──────────────────────────────────────────────────────────────────


def test_relationships_extractor_routes_with_medium_tier(tmp_path: Path):
    from core.relationships.candidate_store import RelationshipsCandidateStore
    from core.relationships.extractor import (
        EXTRACTOR_ENV_VAR,
        extract_relationships,
    )
    from core.transcripts import TranscriptMessage
    from datetime import datetime, timezone

    cstore = RelationshipsCandidateStore(tmp_path / "candidates.json")
    msgs = [
        TranscriptMessage(
            role="user",
            text="had lunch with mom today",
            timestamp=datetime.now(timezone.utc),
            uuid="m1",
            tool_calls=(),
            raw={},
        )
    ]
    brain = BrainNull(
        aux_results=[
            AuxResult(stdout='{"extractions": []}', stderr="", returncode=0)
        ]
    )
    asyncio.run(
        extract_relationships(
            msgs, "sess-x", workspace=tmp_path,
            candidate_store=cstore, brain=brain,
        )
    )
    record = brain.aux_call_records()[0]
    assert record["model_tier"] == "medium"
    assert record["env_overrides"] == {EXTRACTOR_ENV_VAR: "1"}


# ──────────────────────────────────────────────────────────────────
# relationships_classifier → spawn_aux(model_tier="tiny")
# ──────────────────────────────────────────────────────────────────


def test_relationships_classifier_routes_with_tiny_tier(tmp_path: Path):
    from core.relationships.triggers import (
        RELATIONSHIPS_CLASSIFIER_ENV_VAR,
        _classifier_call,
    )

    brain = BrainNull(
        aux_results=[
            AuxResult(
                stdout='{"verdict": "NONE", "person": null, "facts": [], "confidence": 0.0}',
                stderr="",
                returncode=0,
            )
        ]
    )
    asyncio.run(
        _classifier_call(
            "remember that Sarah likes mystery novels",
            session_uuid="sess-x",
            turn_index=1,
            workspace=tmp_path,
            brain=brain,
        )
    )
    record = brain.aux_call_records()[0]
    assert record["model_tier"] == "tiny"
    assert record["env_overrides"] == {RELATIONSHIPS_CLASSIFIER_ENV_VAR: "1"}


# ──────────────────────────────────────────────────────────────────
# skill curator (run_phase2) → spawn_aux(model_tier="small", allow_tools=True)
# ──────────────────────────────────────────────────────────────────


def test_skill_curator_routes_with_small_tier_and_allow_tools(tmp_path: Path):
    """The skill-consolidation pass is the only consumer that passes
    ``allow_tools=True`` — it needs to invoke vexis-skill archive /
    rename / move via the brain's tool layer."""
    from core import curator as cur

    # Seed a skill so phase2 has something to consolidate (otherwise
    # it short-circuits with "No candidates").
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
        ]
    )
    cur.run_phase2(tmp_path, brain)
    record = brain.aux_call_records()[0]
    assert record["model_tier"] == "small"
    assert record["env_overrides"] == {"VEXIS_CURATOR": "1"}
    # The signature flag — ONLY consumer with allow_tools=True.
    assert record["allow_tools"] is True


# ──────────────────────────────────────────────────────────────────
# learning_review.run_review → spawn_aux(model_tier="small")
# learning_review._run_triage → spawn_aux(model_tier="tiny")
# ──────────────────────────────────────────────────────────────────


def test_learning_review_routes_triage_with_tiny_then_review_with_small(
    tmp_path: Path, monkeypatch
):
    """When triage is enabled, two spawns fire per session: triage
    (tiny) then full review (small). When triage returns NO, the
    full review never fires (separate path tested in
    test_learning_review.py)."""
    from core import learning_review as lr
    from core.learning_review import RECURSION_ENV_VAR, run_review
    from core.transcripts import SessionMeta, TranscriptMessage
    from datetime import datetime, timezone

    monkeypatch.setattr(lr, "learning_triage_enabled", lambda: True)

    msgs = [
        TranscriptMessage(
            role="user", text="hi",
            timestamp=datetime.now(timezone.utc),
            uuid="m1", tool_calls=(), raw={},
        )
    ]
    meta = SessionMeta(
        session_uuid="s1",
        jsonl_path=tmp_path / "s1.jsonl",
        last_message_timestamp=datetime.now(timezone.utc),
        message_count_estimate=1,
    )
    brain = BrainNull(
        aux_results=[
            AuxResult(stdout="YES\n", stderr="", returncode=0),
            AuxResult(stdout="Nothing to save.\n", stderr="", returncode=0),
        ]
    )
    run_review(tmp_path, meta, msgs, brain)
    records = brain.aux_call_records()
    assert len(records) == 2
    # Triage call: tiny tier, recursion env set.
    assert records[0]["model_tier"] == "tiny"
    assert records[0]["env_overrides"] == {RECURSION_ENV_VAR: "1"}
    # Review call: small tier, same env.
    assert records[1]["model_tier"] == "small"
    assert records[1]["env_overrides"] == {RECURSION_ENV_VAR: "1"}
    # Neither call allows tools — review is text-only verdicts/JSON.
    assert all(r["allow_tools"] is False for r in records)


# ──────────────────────────────────────────────────────────────────
# Cross-brain smoke — both implementations honour the same ABC for
# the methods that don't require subprocess spawning
# ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "brain_under_test",
    ["null", "claude_code", "opencode"],
    indirect=True,
)
def test_brain_under_test_implements_inspection_methods(brain_under_test):
    """Smoke test the cross-brain fixture from conftest.py. All
    three implementations (BrainNull, ClaudeCodeBrain, OpenCodeBrain)
    expose the inspection-only ABC methods correctly. The deeper
    contract is in test_brain_contract.py; this test exists to pin
    the parameterised fixture itself."""
    from core.brain.base import Brain

    assert isinstance(brain_under_test, Brain)
    assert isinstance(brain_under_test.instruction_file_name(), str)
    assert isinstance(brain_under_test.build_system_prompt(), str)
    # session_token may be str or None; both are valid.
    tok = brain_under_test.session_token()
    assert tok is None or isinstance(tok, str)
    # Day 3 transcript-readback stubs return empty (no SQL reader
    # yet). Verify they don't raise.
    assert list(brain_under_test.iter_session_metas()) == []
    assert list(brain_under_test.iter_messages("nonexistent-id")) == []
    assert brain_under_test.is_brain_owned_session("nonexistent-id") is False


# ──────────────────────────────────────────────────────────────────
# /cancel arriving during async judge — Phase B note (not a test)
# ──────────────────────────────────────────────────────────────────
#
# Pre-Phase-B, ``evaluate_after_turn`` was synchronous and
# ``subprocess.run`` blocked the event loop for the duration of the
# judge call. ``/cancel`` could only land BEFORE evaluate_after_turn
# fired (covered by the existing
# test_cancel_mid_kickoff_does_not_run_goal_hook in
# tests/test_goal_command.py).
#
# Phase B's async migration WIDENS the race window: ``await
# brain.spawn_aux(...)`` yields back to the event loop, so /cancel
# CAN now arrive while the judge is awaiting. The transport-layer
# reload-guard at transports/telegram.py:1310-1329 covers the
# "evaluate completed but state flipped during evaluate" case by
# re-reading state from disk after evaluate_after_turn returns and
# bailing if not active.
#
# A test for the new mid-judge race would need to coordinate three
# event-loop-aware actors (the goal hook, the cancel handler, and
# the brain's awaiting spawn_aux) which the existing test harness
# doesn't model cleanly. Punt: rely on the existing transport-layer
# tests + the dogfood checklist's "/cancel mid-turn" step (Phase A,
# §7 of the brain-abstraction research doc) to catch real-world
# regressions. Note here is the audit trail.

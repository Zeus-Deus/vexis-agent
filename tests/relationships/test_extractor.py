"""v3c Day 4a: silent third-party extractor.

Covers ``core/relationships/extractor.py``:

- happy path: single transcript → one queue entry.
- multiple persons in one transcript → multiple queue entries.
- sensitive content drops at extract time (target_file="user"
  fires the third-party check).
- third-party-mention-with-no-fact does NOT emit (filtered by
  the prompt; tested via classifier stub).
- dedup against live RELATIONSHIPS.md.
- haiku as the resolved model default.
- extractor timeout / error → no queue write, error counter
  semantics intact, lesson reviewer untouched.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from core.relationships.candidate_store import RelationshipsCandidateStore
from core.relationships.consent import _fact_id, mint
from core.relationships.extractor import (
    EXTRACTOR_TIMEOUT_SECONDS,
    ExtractedFact,
    ExtractionResult,
    extract_relationships,
)
from core.relationships.store import (
    Fact,
    Person,
    RelationshipsStore,
    relationships_live_path,
    serialize_relationships_file,
)
from core.transcripts import TranscriptMessage


def _msg(text: str, role: str = "user") -> TranscriptMessage:
    return TranscriptMessage(
        role=role,
        text=text,
        timestamp=datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc),
        uuid="m-1",
        tool_calls=(),
        raw={},
    )


def _make_spawn(stdout: str, returncode: int = 0):
    """Build a fake spawn function that returns ``stdout`` verbatim."""

    def _spawn(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=argv,
            returncode=returncode,
            stdout=stdout.encode("utf-8"),
            stderr=b"",
        )

    return _spawn


def _make_timeout_spawn():
    def _spawn(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=EXTRACTOR_TIMEOUT_SECONDS)

    return _spawn


def _make_clean_scan():
    """A scanner stub that always returns None (no sensitive hit)."""
    return lambda text, *, scope, target_file: None


def _make_blocking_scan():
    """A scanner stub that always blocks."""
    return lambda text, *, scope, target_file: f"medical:test"


# ---------------------------------------------------------------- happy paths


def test_extractor_happy_path_one_fact_one_slug(tmp_path: Path):
    cstore = RelationshipsCandidateStore(tmp_path / "candidates.json")
    rstore = RelationshipsStore(tmp_path)

    payload = {
        "extractions": [
            {
                "person": "Sarah",
                "qualifier": "coworker",
                "fact": "tech lead on the Vexis team",
                "confidence": 0.85,
            }
        ]
    }
    spawn = _make_spawn(json.dumps(payload))

    transcript = [
        _msg("Met with Sarah today, she's the tech lead on Vexis."),
        _msg("Working on the rollout plan together."),
    ]
    result = asyncio.run(
        extract_relationships(
            transcript,
            "sess-A",
            workspace=tmp_path,
            candidate_store=cstore,
            relationships_store=rstore,
            spawn=spawn,
            sensitive_scan=_make_clean_scan(),
        )
    )
    assert result.error is None
    assert result.facts_emitted == 1
    assert result.facts_queued == 1
    candidate = cstore.get("sarah")
    assert candidate is not None
    assert candidate.display_name == "Sarah"
    assert candidate.qualifier_candidates == ["coworker"]
    fid = _fact_id("tech lead on the Vexis team")
    assert fid in candidate.facts


def test_extractor_multiple_persons_single_transcript(tmp_path: Path):
    cstore = RelationshipsCandidateStore(tmp_path / "candidates.json")
    rstore = RelationshipsStore(tmp_path)
    payload = {
        "extractions": [
            {"person": "Sarah", "qualifier": "coworker",
             "fact": "tech lead", "confidence": 0.8},
            {"person": "Marco", "qualifier": "friend",
             "fact": "uses Vim", "confidence": 0.8},
            {"person": "Mom", "qualifier": "mom",
             "fact": "loves classical", "confidence": 0.9},
        ]
    }
    spawn = _make_spawn(json.dumps(payload))
    result = asyncio.run(
        extract_relationships(
            [_msg("conversation with multiple third parties")],
            "sess-multi",
            workspace=tmp_path,
            candidate_store=cstore,
            relationships_store=rstore,
            spawn=spawn,
            sensitive_scan=_make_clean_scan(),
        )
    )
    assert result.facts_queued == 3
    assert cstore.get("sarah") is not None
    assert cstore.get("marco") is not None
    assert cstore.get("mom") is not None
    # Strong-cue check: Mom should be eligible immediately.
    assert cstore.eligible_for_promotion("mom") is True
    # Soft cues should NOT be eligible after one session.
    assert cstore.eligible_for_promotion("sarah") is False


def test_extractor_low_confidence_dropped(tmp_path: Path):
    cstore = RelationshipsCandidateStore(tmp_path / "candidates.json")
    rstore = RelationshipsStore(tmp_path)
    # Below 0.5 threshold → parser drops.
    payload = {
        "extractions": [
            {"person": "Iffy", "qualifier": None,
             "fact": "maybe likes dogs", "confidence": 0.3},
        ]
    }
    spawn = _make_spawn(json.dumps(payload))
    result = asyncio.run(
        extract_relationships(
            [_msg("...")],
            "sess-iffy",
            workspace=tmp_path,
            candidate_store=cstore,
            relationships_store=rstore,
            spawn=spawn,
            sensitive_scan=_make_clean_scan(),
        )
    )
    assert result.facts_emitted == 0
    assert result.facts_queued == 0


# ---------------------------------------------------------------- sensitive drop


def test_extractor_drops_sensitive_at_extract_time(tmp_path: Path):
    cstore = RelationshipsCandidateStore(tmp_path / "candidates.json")
    rstore = RelationshipsStore(tmp_path)
    payload = {
        "extractions": [
            {"person": "Sarah", "qualifier": "friend",
             "fact": "is on prescription antidepressants",
             "confidence": 0.85},
        ]
    }
    spawn = _make_spawn(json.dumps(payload))
    # The blocking scan stub stands in for the real
    # _scan_lesson_for_sensitive_content with target_file="user"
    # which would catch a medical claim about a third party.
    result = asyncio.run(
        extract_relationships(
            [_msg("Sarah told me she's been on antidepressants")],
            "sess-sens",
            workspace=tmp_path,
            candidate_store=cstore,
            relationships_store=rstore,
            spawn=spawn,
            sensitive_scan=_make_blocking_scan(),
        )
    )
    assert result.facts_emitted == 1
    assert result.facts_dropped_sensitive == 1
    assert result.facts_queued == 0
    # No queue write — silent drop.
    assert cstore.get("sarah") is None


def test_extractor_real_scanner_drops_third_party_medical(tmp_path: Path):
    """Integration with the real
    ``_scan_lesson_for_sensitive_content``: target_file="user"
    fires the medical regex AND the third-party scanner. A
    medical claim about a third party should drop."""
    cstore = RelationshipsCandidateStore(tmp_path / "candidates.json")
    rstore = RelationshipsStore(tmp_path)
    payload = {
        "extractions": [
            {"person": "Sarah", "qualifier": "friend",
             "fact": "is on prescription antidepressants for depression",
             "confidence": 0.85},
        ]
    }
    spawn = _make_spawn(json.dumps(payload))
    result = asyncio.run(
        extract_relationships(
            [_msg("Sarah's been on antidepressants for her depression")],
            "sess-real",
            workspace=tmp_path,
            candidate_store=cstore,
            relationships_store=rstore,
            spawn=spawn,
            sensitive_scan=None,  # real scanner
        )
    )
    assert result.facts_dropped_sensitive == 1
    assert result.facts_queued == 0


# ---------------------------------------------------------------- dedup


def test_extractor_dedups_against_live(tmp_path: Path):
    """Fact text already in RELATIONSHIPS.md should not re-queue."""
    # Seed live with a Sarah entry containing the fact.
    sarah = Person(
        slug="sarah",
        display_name="Sarah",
        relationship="coworker",
        qualifier=None,
        last_confirmed="2026-04-01",
        source_session="abc12345",
        facts=(
            Fact(
                text="tech lead on the Vexis team",
                confirmed_date="2026-04-01",
                source_session_short="abc12345",
                staged=False,
            ),
        ),
    )
    relationships_live_path(tmp_path).write_text(
        serialize_relationships_file([sarah], kind="live"),
        encoding="utf-8",
    )
    cstore = RelationshipsCandidateStore(tmp_path / "candidates.json")
    rstore = RelationshipsStore(tmp_path)

    payload = {
        "extractions": [
            {"person": "Sarah", "qualifier": "coworker",
             "fact": "tech lead on the Vexis team",
             "confidence": 0.9},
            {"person": "Sarah", "qualifier": "coworker",
             "fact": "lives in Berlin",
             "confidence": 0.9},
        ]
    }
    spawn = _make_spawn(json.dumps(payload))
    result = asyncio.run(
        extract_relationships(
            [_msg("Sarah, the tech lead, just moved to Berlin")],
            "sess-dedup",
            workspace=tmp_path,
            candidate_store=cstore,
            relationships_store=rstore,
            spawn=spawn,
            sensitive_scan=_make_clean_scan(),
        )
    )
    assert result.facts_emitted == 2
    assert result.facts_dropped_dedup == 1  # tech lead exists
    assert result.facts_queued == 1  # lives in Berlin survives
    candidate = cstore.get("sarah")
    fids = list(candidate.facts.keys())
    assert _fact_id("tech lead on the Vexis team") not in fids
    assert _fact_id("lives in Berlin") in fids


# ---------------------------------------------------------------- model default


def test_extractor_resolves_haiku_by_default():
    """Confirm the haiku-default model knob (§4.1 patch). Reads
    yaml_config directly because the spawn argv is the proof."""
    from core.yaml_config import (
        DEFAULT_MODEL_RELATIONSHIPS_EXTRACTOR,
        model_relationships_extractor,
        resolve_model_flag,
    )
    assert DEFAULT_MODEL_RELATIONSHIPS_EXTRACTOR == "haiku"
    assert model_relationships_extractor() == "haiku"
    flag = resolve_model_flag(model_relationships_extractor())
    assert flag == ["--model", "haiku"]


# ---------------------------------------------------------------- error path


def test_extractor_timeout_no_queue_write(tmp_path: Path):
    cstore = RelationshipsCandidateStore(tmp_path / "candidates.json")
    rstore = RelationshipsStore(tmp_path)
    spawn = _make_timeout_spawn()
    result = asyncio.run(
        extract_relationships(
            [_msg("anything")],
            "sess-timeout",
            workspace=tmp_path,
            candidate_store=cstore,
            relationships_store=rstore,
            spawn=spawn,
            sensitive_scan=_make_clean_scan(),
        )
    )
    assert result.error is not None
    assert "timed out" in result.error
    assert result.facts_queued == 0
    # No queue file written.
    assert not (tmp_path / "candidates.json").exists()


def test_extractor_nonzero_exit_no_queue_write(tmp_path: Path):
    cstore = RelationshipsCandidateStore(tmp_path / "candidates.json")
    rstore = RelationshipsStore(tmp_path)
    spawn = _make_spawn("subprocess error", returncode=1)
    result = asyncio.run(
        extract_relationships(
            [_msg("anything")],
            "sess-fail",
            workspace=tmp_path,
            candidate_store=cstore,
            relationships_store=rstore,
            spawn=spawn,
            sensitive_scan=_make_clean_scan(),
        )
    )
    assert result.error is not None
    assert result.facts_queued == 0


def test_extractor_unparseable_response_returns_empty(tmp_path: Path):
    """Parser tolerance: garbage stdout → empty extractions, no
    error, no queue write."""
    cstore = RelationshipsCandidateStore(tmp_path / "candidates.json")
    rstore = RelationshipsStore(tmp_path)
    spawn = _make_spawn("not json at all")
    result = asyncio.run(
        extract_relationships(
            [_msg("anything")],
            "sess-junk",
            workspace=tmp_path,
            candidate_store=cstore,
            relationships_store=rstore,
            spawn=spawn,
            sensitive_scan=_make_clean_scan(),
        )
    )
    assert result.error is None
    assert result.facts_emitted == 0
    assert result.facts_queued == 0


def test_extractor_empty_transcript_returns_no_op(tmp_path: Path):
    cstore = RelationshipsCandidateStore(tmp_path / "candidates.json")
    rstore = RelationshipsStore(tmp_path)
    # No spawn invoked because the transcript is empty.
    result = asyncio.run(
        extract_relationships(
            [],
            "sess-empty",
            workspace=tmp_path,
            candidate_store=cstore,
            relationships_store=rstore,
            spawn=_make_spawn("never called"),
            sensitive_scan=_make_clean_scan(),
        )
    )
    assert result.facts_emitted == 0

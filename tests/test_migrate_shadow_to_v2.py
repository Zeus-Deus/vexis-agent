"""Tests for scripts/migrate_shadow_to_v2.py.

Coverage:
  - parse_shadow_file: round-trips real v1-shape entries; ignores
    entries lacking the [learned] tag; handles Day 2 v2-shape entries.
  - render_plan / parse_plan: round-trip preserves decisions.
  - parse_plan: invalid decision raises; missing decision defaults to SKIP.
  - apply_plan per decision: SKIP and DROP are no-ops; SITUATIONAL
    appends to MEMORY.md; PROCEDURAL_S2 stages a support file;
    PROCEDURAL_S3 stages a new skill; PROCEDURAL_S1 falls back to
    references-S2 (documented behavior); IDENTITY inserts the
    synthetic 2-occurrence prefill.
  - Idempotent re-apply: SITUATIONAL doesn't double-write; staged
    skill collisions are surfaced.
  - LLM classification mocking: spawn injection; malformed response
    defaults to SKIP; length mismatch defaults to SKIP.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Allow importing the migration script as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import migrate_shadow_to_v2 as mig  # noqa: E402

from core.memory import ENTRY_DELIMITER, MemoryStore  # noqa: E402
from core.paths import memories_dir, user_candidates_path  # noqa: E402
from core.skills import create_skill  # noqa: E402
from core.user_candidates import UserCandidateStore  # noqa: E402


# --------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Workspace + fake $HOME so vexis_dir() lands in tmp."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    workspace = tmp_path / "vexis-workspace"
    (workspace / "memories").mkdir(parents=True)
    return workspace


def _write_shadow(workspace: Path, entries: list[str]) -> Path:
    """Write a MEMORY-SHADOW.md with §-delimited entries."""
    path = memories_dir(workspace) / "MEMORY-SHADOW.md"
    path.write_text(ENTRY_DELIMITER.join(entries) + "\n", encoding="utf-8")
    return path


def _v1_entry(lesson: str, scope: str, evidence: str, *, date: str = "2026-05-02") -> str:
    return (
        f"[learned {date}] {lesson}\n"
        f"  Scope: {scope}\n"
        f"  Evidence: {evidence}"
    )


# --------------------------------------------------------------------
# parse_shadow_file
# --------------------------------------------------------------------


def test_parse_shadow_file_round_trip(env):
    src = _write_shadow(env, [
        _v1_entry("Lesson A.", "Scope A", "Evidence A"),
        _v1_entry("Lesson B.", "Scope B", "Evidence B", date="2026-05-03"),
    ])
    entries = mig.parse_shadow_file(src)
    assert len(entries) == 2
    assert entries[0].lesson == "Lesson A."
    assert entries[0].scope == "Scope A"
    assert entries[0].evidence == "Evidence A"
    assert entries[0].learned_date == "2026-05-02"
    assert entries[1].lesson == "Lesson B."
    assert entries[1].learned_date == "2026-05-03"


def test_parse_shadow_file_skips_untagged_entries(env):
    src = _write_shadow(env, [
        _v1_entry("Tagged.", "x", "y"),
        "Some hand-written note without the [learned] tag",
    ])
    entries = mig.parse_shadow_file(src)
    assert len(entries) == 1


def test_parse_shadow_file_handles_v2_day2_format(env):
    """Day 2+ entries also include Class: / Tier: / Staged: lines.
    The migration script reads lesson/scope/evidence and ignores
    the rest — supports re-running migration over partially-v2
    state."""
    v2_entry = (
        "[learned 2026-05-04] When listing time-bound options, filter ahead.\n"
        "  Class: PROCEDURAL\n"
        "  Tier: S3 (would create new skill: time-bound-listings)\n"
        "  Scope: time-bound listings\n"
        "  Evidence: filter to upcoming items only please\n"
        "  Staged: /tmp/skills/.shadow/time-bound-listings/SKILL.md"
    )
    src = _write_shadow(env, [v2_entry])
    entries = mig.parse_shadow_file(src)
    assert len(entries) == 1
    assert entries[0].lesson == "When listing time-bound options, filter ahead."
    assert entries[0].is_v1_shape() is False


def test_parse_shadow_file_missing_returns_empty(env):
    """A missing source file returns empty (no error)."""
    src = memories_dir(env) / "does-not-exist.md"
    assert mig.parse_shadow_file(src) == []


# --------------------------------------------------------------------
# render_plan / parse_plan round-trip
# --------------------------------------------------------------------


def test_render_and_parse_plan_round_trip(env, tmp_path):
    rows = [
        mig.PlanRow(
            index=1,
            entry=mig.ShadowEntry(
                raw="", lesson="Lesson 1.", scope="x", evidence="ev1",
            ),
            decision="PROCEDURAL_S3",
            arg="some-skill",
        ),
        mig.PlanRow(
            index=2,
            entry=mig.ShadowEntry(
                raw="", lesson="Lesson 2.", scope="y", evidence="ev2",
            ),
            decision="IDENTITY",
        ),
    ]
    plan_path = tmp_path / "plan.md"
    plan_path.write_text(
        mig.render_plan(plan_path, tmp_path / "src.md", rows),
        encoding="utf-8",
    )
    parsed = mig.parse_plan(plan_path)
    assert len(parsed) == 2
    assert parsed[0].decision == "PROCEDURAL_S3"
    assert parsed[0].arg == "some-skill"
    assert parsed[0].entry.lesson == "Lesson 1."
    assert parsed[1].decision == "IDENTITY"
    assert parsed[1].entry.lesson == "Lesson 2."


def test_parse_plan_invalid_decision_raises(env, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(
        "## Entry 1\n"
        "lesson:    L\n"
        "evidence:  E\n"
        "scope:     S\n"
        "decision:  TYPO_DECISION\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="invalid decision"):
        mig.parse_plan(plan)


def test_parse_plan_missing_decision_defaults_to_skip(env, tmp_path):
    plan = tmp_path / "plan.md"
    plan.write_text(
        "## Entry 1\n"
        "lesson:    L\n"
        "evidence:  E\n"
        "scope:     S\n",  # no decision: line
        encoding="utf-8",
    )
    rows = mig.parse_plan(plan)
    assert rows[0].decision == "SKIP"


# --------------------------------------------------------------------
# apply: SKIP / DROP no-ops
# --------------------------------------------------------------------


def test_apply_skip_is_noop(env):
    row = mig.PlanRow(
        index=1,
        entry=mig.ShadowEntry(raw="", lesson="L", scope="S", evidence="E"),
        decision="SKIP",
    )
    summary = mig.apply_plan(env, [row])
    assert summary.ok
    # No skill staging, no MEMORY.md, no queue entry:
    from core.learning_writes import shadow_skills_root
    assert not (shadow_skills_root(env)).iterdir().__next__() if (shadow_skills_root(env)).exists() and any((shadow_skills_root(env)).iterdir()) else True
    assert not (memories_dir(env) / "MEMORY.md").exists()
    assert UserCandidateStore(user_candidates_path()).list_all() == []


def test_apply_drop_is_noop(env):
    row = mig.PlanRow(
        index=1,
        entry=mig.ShadowEntry(raw="", lesson="L", scope="S", evidence="E"),
        decision="DROP",
    )
    summary = mig.apply_plan(env, [row])
    assert summary.ok
    assert summary.results[0].message == "dropped (no migration target)"


# --------------------------------------------------------------------
# apply: SITUATIONAL → MEMORY.md
# --------------------------------------------------------------------


def test_apply_situational_writes_memory_md(env):
    row = mig.PlanRow(
        index=1,
        entry=mig.ShadowEntry(
            raw="",
            lesson="Vexis runs on Hetzner VPS at 203.0.113.42.",
            scope="environment",
            evidence="yeah this is still on the Hetzner box",
        ),
        decision="SITUATIONAL",
    )
    summary = mig.apply_plan(env, [row])
    assert summary.ok
    body = (memories_dir(env) / "MEMORY.md").read_text(encoding="utf-8")
    assert "Hetzner VPS" in body
    assert "[migrated" in body  # carries the migration marker, not [learned]


# --------------------------------------------------------------------
# apply: PROCEDURAL_S2 stages support file
# --------------------------------------------------------------------


def test_apply_s2_stages_support_file(env):
    """S2 needs a live skill to attach under; create one first."""
    create_skill(
        env / "skills",
        "umbrella",
        "---\nname: umbrella\ndescription: D.\n---\n\nB\n",
    )
    row = mig.PlanRow(
        index=1,
        entry=mig.ShadowEntry(
            raw="",
            lesson="When using bge-m3 on Dutch corpora, query in Dutch.",
            scope="multilingual RAG",
            evidence="dutch corpus retrieval is broken with English queries",
        ),
        decision="PROCEDURAL_S2",
        arg="umbrella/references/dutch-bge-m3.md",
    )
    summary = mig.apply_plan(env, [row])
    assert summary.ok, summary.results[0].message
    from core.learning_writes import shadow_skills_root
    staged = shadow_skills_root(env) / "umbrella" / "references" / "dutch-bge-m3.md"
    assert staged.exists()
    body = staged.read_text(encoding="utf-8")
    assert "When using bge-m3" in body
    assert "Migrated lesson" in body  # provenance header


def test_apply_s2_invalid_arg_returns_failure(env):
    create_skill(
        env / "skills",
        "umbrella",
        "---\nname: umbrella\ndescription: D.\n---\n\nB\n",
    )
    row = mig.PlanRow(
        index=1,
        entry=mig.ShadowEntry(raw="", lesson="L", scope="S", evidence="E"),
        decision="PROCEDURAL_S2",
        arg="bare-name-no-slash",
    )
    summary = mig.apply_plan(env, [row])
    assert not summary.ok
    assert "skill-name" in summary.results[0].message.lower() or "rel-path" in summary.results[0].message.lower()


# --------------------------------------------------------------------
# apply: PROCEDURAL_S3 stages new skill
# --------------------------------------------------------------------


def test_apply_s3_stages_new_skill(env):
    row = mig.PlanRow(
        index=1,
        entry=mig.ShadowEntry(
            raw="",
            lesson="When listing time-bound options, filter ahead of now.",
            scope="time-bound listings",
            evidence="some past entries showed up in the list",
            learned_date="2026-05-02",
        ),
        decision="PROCEDURAL_S3",
        arg="time-bound-listings",
    )
    summary = mig.apply_plan(env, [row])
    assert summary.ok, summary.results[0].message
    from core.learning_writes import shadow_skills_root
    staged = shadow_skills_root(env) / "time-bound-listings" / "SKILL.md"
    assert staged.exists()
    body = staged.read_text(encoding="utf-8")
    assert "origin: learning-curator-migration" in body
    assert "When listing time-bound options" in body
    assert "Migrated from v1" in body


def test_apply_s3_collision_with_live_skill_fails(env):
    """If the proposed S3 name collides with an existing live skill,
    the staging refuses (per stage_new_skill's contract)."""
    create_skill(
        env / "skills",
        "occupied",
        "---\nname: occupied\ndescription: D.\n---\n\nB\n",
    )
    row = mig.PlanRow(
        index=1,
        entry=mig.ShadowEntry(raw="", lesson="L", scope="S", evidence="E"),
        decision="PROCEDURAL_S3",
        arg="occupied",
    )
    summary = mig.apply_plan(env, [row])
    assert not summary.ok
    assert "live skill" in summary.results[0].message.lower() or "already exists" in summary.results[0].message.lower()


# --------------------------------------------------------------------
# apply: PROCEDURAL_S1 falls back to S2 (documented behavior)
# --------------------------------------------------------------------


def test_apply_s1_falls_back_to_s2_references(env):
    """v1 entries don't carry SKILL.md context, so PROCEDURAL_S1 in
    the plan can't formulate a real patch — the script falls back
    to writing the lesson body as a references file under the named
    skill. Documented in the apply_plan docstring."""
    create_skill(
        env / "skills",
        "comm-style",
        "---\nname: comm-style\ndescription: D.\n---\n\nB\n",
    )
    row = mig.PlanRow(
        index=42,
        entry=mig.ShadowEntry(
            raw="", lesson="Lead with answer.", scope="x", evidence="be terse",
        ),
        decision="PROCEDURAL_S1",
        arg="comm-style",
    )
    summary = mig.apply_plan(env, [row])
    assert summary.ok, summary.results[0].message
    from core.learning_writes import shadow_skills_root
    expected = (
        shadow_skills_root(env) / "comm-style" / "references"
        / f"migrated-{row.index:03d}.md"
    )
    assert expected.exists()


def test_apply_s1_without_arg_fails_clearly(env):
    row = mig.PlanRow(
        index=1,
        entry=mig.ShadowEntry(raw="", lesson="L", scope="S", evidence="E"),
        decision="PROCEDURAL_S1",
        arg="",
    )
    summary = mig.apply_plan(env, [row])
    assert not summary.ok
    assert "skill name" in summary.results[0].message.lower()


# --------------------------------------------------------------------
# apply: IDENTITY inserts synthetic 2-occurrence prefill
# --------------------------------------------------------------------


def test_apply_identity_inserts_with_synthetic_prefill(env):
    row = mig.PlanRow(
        index=7,
        entry=mig.ShadowEntry(
            raw="",
            lesson="User prefers terse responses for direct questions.",
            scope="communication preferences",
            evidence="just give me the answer",
            learned_date="2026-05-02",
        ),
        decision="IDENTITY",
    )
    summary = mig.apply_plan(env, [row])
    assert summary.ok
    queue = UserCandidateStore(user_candidates_path()).list_all()
    assert len(queue) == 1
    candidate = queue[0]
    assert candidate.claim == "User prefers terse responses for direct questions."
    # Synthetic prefill: 2 distinct UUIDs so the next eligible
    # session triggers promotion via the existing dispatcher.
    assert len(candidate.distinct_session_uuids()) == 2
    assert "migration:v1-007:a" in candidate.distinct_session_uuids()
    assert "migration:v1-007:b" in candidate.distinct_session_uuids()


def test_apply_identity_threat_scanner_blocks_religion(env):
    """B2 fix: migration's _apply_identity must run the same
    USER.md-specific threat scanner the curator hot path runs in
    _validate_lesson. A migration plan that hand-classifies a
    religion claim as IDENTITY must NOT be allowed to ride into the
    queue and on into USER.md via the synthetic 2-occurrence
    prefill."""
    row = mig.PlanRow(
        index=1,
        entry=mig.ShadowEntry(
            raw="",
            lesson="User is a Christian and prays daily.",
            scope="religion",
            evidence="I go to church on Sundays",
            learned_date="2026-05-02",
        ),
        decision="IDENTITY",
    )
    summary = mig.apply_plan(env, [row])
    assert not summary.ok
    msg = summary.results[0].message.lower()
    assert "user.md threat scanner" in msg
    assert "religion" in msg
    # Queue is empty: the row was refused before any add_occurrence.
    queue = UserCandidateStore(user_candidates_path()).list_all()
    assert queue == []


def test_apply_identity_threat_scanner_blocks_named_third_party(env):
    """Migration plans that promote a third-party-named claim to
    IDENTITY get refused at _apply_identity — same defense as the
    curator hot path."""
    row = mig.PlanRow(
        index=2,
        entry=mig.ShadowEntry(
            raw="",
            lesson="User's wife Sarah prefers Italian food.",
            scope="dining preferences",
            evidence="my wife and I had Italian last night",
            learned_date="2026-05-02",
        ),
        decision="IDENTITY",
    )
    summary = mig.apply_plan(env, [row])
    assert not summary.ok
    assert "named-third-party" in summary.results[0].message
    assert UserCandidateStore(user_candidates_path()).list_all() == []


def test_apply_identity_threat_scanner_passes_benign(env):
    """Benign IDENTITY content still passes the scanner — the gate is
    a sieve, not a wall. Without this regression check, a too-broad
    pattern could silently block all migrations."""
    row = mig.PlanRow(
        index=3,
        entry=mig.ShadowEntry(
            raw="",
            lesson="User prefers concise answers without preamble.",
            scope="communication style",
            evidence="just give me the answer",
            learned_date="2026-05-02",
        ),
        decision="IDENTITY",
    )
    summary = mig.apply_plan(env, [row])
    assert summary.ok
    queue = UserCandidateStore(user_candidates_path()).list_all()
    assert len(queue) == 1


def test_apply_identity_already_promoted_is_no_op(env):
    """Re-running apply over a claim that's already promoted skips
    the insert — defends against double-prefill on idempotent retry."""
    store = UserCandidateStore(user_candidates_path())
    claim = "User prefers terse responses."
    store.add_occurrence(claim, "real-sess-1", "ev")
    store.add_occurrence(claim, "real-sess-2", "ev")
    store.mark_promoted(claim)
    # Now apply a migration row for the same claim.
    row = mig.PlanRow(
        index=1,
        entry=mig.ShadowEntry(
            raw="", lesson=claim, scope="x", evidence="ev",
        ),
        decision="IDENTITY",
    )
    summary = mig.apply_plan(env, [row])
    assert summary.ok
    assert "already promoted" in summary.results[0].message
    # Queue size unchanged (no synthetic insert):
    candidate = store.get(claim)
    assert candidate.distinct_session_uuids() == {"real-sess-1", "real-sess-2"}


# --------------------------------------------------------------------
# Idempotent re-apply (key resumability property)
# --------------------------------------------------------------------


def test_re_apply_s3_collides_on_second_run(env):
    """First apply stages the skill; second apply hits the staging-
    tree collision check in stage_new_skill (per the v2 design,
    staging collisions are explicit failures). The user re-runs
    --plan to get a fresh decision for failures."""
    row = mig.PlanRow(
        index=1,
        entry=mig.ShadowEntry(
            raw="", lesson="L", scope="x", evidence="E",
            learned_date="2026-05-02",
        ),
        decision="PROCEDURAL_S3",
        arg="fresh-skill",
    )
    first = mig.apply_plan(env, [row])
    assert first.ok
    second = mig.apply_plan(env, [row])
    assert not second.ok
    # The error message names the staging collision so the user
    # knows what to do next:
    assert "staged skill" in second.results[0].message.lower()


# --------------------------------------------------------------------
# Classification mocking
# --------------------------------------------------------------------


def test_classify_entries_happy_path():
    entries = [
        mig.ShadowEntry(raw="", lesson="L1", scope="S1", evidence="E1"),
        mig.ShadowEntry(raw="", lesson="L2", scope="S2", evidence="E2"),
    ]
    response = json.dumps([
        {"index": 1, "decision": "SITUATIONAL", "arg": ""},
        {"index": 2, "decision": "PROCEDURAL_S3", "arg": "some-skill"},
    ])

    def spawn(argv, env):
        return subprocess.CompletedProcess(
            args=argv, returncode=0,
            stdout=response.encode(), stderr=b"",
        )

    out = mig.classify_entries(entries, spawn=spawn)
    assert out == [("SITUATIONAL", ""), ("PROCEDURAL_S3", "some-skill")]


def test_classify_entries_malformed_response_defaults_to_skip():
    entries = [mig.ShadowEntry(raw="", lesson="L", scope="S", evidence="E")]

    def spawn(argv, env):
        return subprocess.CompletedProcess(
            args=argv, returncode=0,
            stdout=b"this is not JSON", stderr=b"",
        )

    out = mig.classify_entries(entries, spawn=spawn)
    assert out == [("SKIP", "")]


def test_classify_entries_length_mismatch_defaults_to_skip():
    entries = [
        mig.ShadowEntry(raw="", lesson="L1", scope="S", evidence="E"),
        mig.ShadowEntry(raw="", lesson="L2", scope="S", evidence="E"),
    ]
    # Returns one classification for two entries — mismatch.
    response = json.dumps([{"index": 1, "decision": "SITUATIONAL", "arg": ""}])

    def spawn(argv, env):
        return subprocess.CompletedProcess(
            args=argv, returncode=0,
            stdout=response.encode(), stderr=b"",
        )

    out = mig.classify_entries(entries, spawn=spawn)
    assert out == [("SKIP", ""), ("SKIP", "")]


def test_classify_entries_unknown_decision_normalized_to_skip():
    entries = [mig.ShadowEntry(raw="", lesson="L", scope="S", evidence="E")]
    response = json.dumps([{"index": 1, "decision": "MAYBE", "arg": ""}])

    def spawn(argv, env):
        return subprocess.CompletedProcess(
            args=argv, returncode=0,
            stdout=response.encode(), stderr=b"",
        )

    out = mig.classify_entries(entries, spawn=spawn)
    assert out == [("SKIP", "")]


# --------------------------------------------------------------------
# Full pipeline: parse → render → user-edit → parse → apply
# --------------------------------------------------------------------


def test_full_pipeline_with_skip_classify(env):
    """End-to-end: shadow file → plan with all-SKIP → user edits to
    real decisions → parse → apply → check side effects."""
    src = _write_shadow(env, [
        _v1_entry(
            "When listing time-bound options, filter ahead of now.",
            "scheduling",
            "you included past meetings, filter to upcoming",
        ),
        _v1_entry(
            "User prefers concise responses.",
            "communication",
            "stop wrapping yes/no in three paragraphs",
        ),
    ])
    plan_path = mig.cmd_plan(env, src, skip_classify=True)
    # User edits the plan: entry 1 → PROCEDURAL_S3, entry 2 → IDENTITY
    text = plan_path.read_text(encoding="utf-8")
    text = text.replace(
        "decision:  SKIP",
        "decision:  PROCEDURAL_S3 time-bound-listings",
        1,
    )
    text = text.replace(
        "decision:  SKIP",
        "decision:  IDENTITY",
        1,
    )
    plan_path.write_text(text, encoding="utf-8")
    summary = mig.cmd_apply(env, plan_path)
    assert summary.ok
    counts = summary.by_decision()
    assert counts.get("PROCEDURAL_S3") == 1
    assert counts.get("IDENTITY") == 1
    # Skill staged:
    from core.learning_writes import shadow_skills_root
    assert (shadow_skills_root(env) / "time-bound-listings" / "SKILL.md").exists()
    # Queue has the identity entry with synthetic prefill:
    queue = UserCandidateStore(user_candidates_path()).list_all()
    assert len(queue) == 1
    assert queue[0].claim == "User prefers concise responses."
    # MEMORY-SHADOW.md is UNTOUCHED (read-only contract):
    assert src.exists()
    assert "User prefers concise responses." in src.read_text(encoding="utf-8")

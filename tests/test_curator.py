"""Tests for the curator: phase 1 transitions, deferred-first-run,
pause/resume, phase 2 with mocked claude -p, report writing.

Phase B: phase2 now spawns via ``Brain.spawn_aux`` instead of an
inline subprocess.run. Tests inject a ``BrainNull`` pre-loaded
with one ``AuxResult`` (the simple cases) or a ``_SideEffectBrain``
subclass when the spawn must perform a real side-effect like
archiving a skill (the consolidation-pass test)."""

from __future__ import annotations

import os
import tarfile
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from core import curator as cur
from core.brain.base import AuxResult
from core.brain.null import BrainNull
from core.skills import (
    ARCHIVE_DIR_NAME,
    PinStore,
    STATE_ACTIVE,
    STATE_STALE,
    UsageStore,
)


def _brain_returning(stdout: str = "", stderr: str = "", returncode: int = 0) -> BrainNull:
    """BrainNull pre-loaded with one AuxResult — Phase B replacement
    for the old ``_FakeProc(...) + fake_spawn`` pair."""
    return BrainNull(
        aux_results=[
            AuxResult(stdout=stdout, stderr=stderr, returncode=returncode)
        ]
    )


class _SideEffectBrain(BrainNull):
    """BrainNull subclass that runs a callback before returning the
    canned AuxResult — for tests where the spawn must perform a
    real workspace mutation (e.g. archiving a skill so phase1's
    re-scan picks it up)."""

    def __init__(self, on_call, aux_result: AuxResult):
        super().__init__(aux_results=[aux_result])
        self._on_call = on_call

    async def spawn_aux(self, prompt, **kwargs):
        self._on_call()
        return await super().spawn_aux(prompt, **kwargs)


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated workspace + ~/.vexis/ overrides for one test."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    workspace = tmp_path / "vexis-workspace"
    workspace.mkdir()
    (workspace / "skills").mkdir()
    return workspace


def _seed_skill(
    workspace: Path,
    name: str,
    *,
    last_used_at: str | None = None,
    state: str = STATE_ACTIVE,
    pinned: bool = False,
) -> None:
    skills_root = workspace / "skills"
    sd = skills_root / name
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} description\n---\n\nbody\n",
        encoding="utf-8",
    )
    usage = UsageStore(skills_root)
    usage.initialize(name)
    if last_used_at:
        data = usage.load()
        rec = data.setdefault(name, {})
        rec["last_used_at"] = last_used_at
        rec["state"] = state
        usage.save(data)
    elif state != STATE_ACTIVE:
        usage.set_state(name, state)
    if pinned:
        PinStore(skills_root).pin(name)


# --------------------------------------------------------------------
# State persistence + should_run_now
# --------------------------------------------------------------------


def test_first_observation_seeds_and_returns_false(workspace: Path):
    assert cur.should_run_now() is False
    state = cur.load_state()
    # last_run_at was seeded so the next call sees an interval
    assert state.get("last_run_at") is not None
    # Immediately calling again should still be False (just seeded)
    assert cur.should_run_now() is False


def test_should_run_now_becomes_true_after_interval(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
):
    # Seed once
    cur.should_run_now()
    # Move last_run_at far enough in the past
    state = cur.load_state()
    state["last_run_at"] = "2020-01-01T00:00:00Z"
    cur.save_state(state)
    assert cur.should_run_now() is True


def test_pause_blocks_run(workspace: Path):
    cur.set_paused(True)
    state = cur.load_state()
    state["last_run_at"] = "2020-01-01T00:00:00Z"
    cur.save_state(state)
    assert cur.should_run_now() is False
    cur.set_paused(False)
    assert cur.should_run_now() is True


# --------------------------------------------------------------------
# Phase 1 transitions
# --------------------------------------------------------------------


def test_phase1_marks_stale_after_30_days(workspace: Path):
    now = cur._utc_now()
    old = (now - timedelta(days=35)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _seed_skill(workspace, "old-one", last_used_at=old)
    _seed_skill(workspace, "new-one")  # fresh

    out = cur.run_phase1(workspace, now=now)
    assert "old-one" in out.stale_names
    assert "new-one" not in out.stale_names

    rec = UsageStore(workspace / "skills").record("old-one")
    assert rec["state"] == STATE_STALE


def test_phase1_archives_after_90_days(workspace: Path):
    now = cur._utc_now()
    very_old = (now - timedelta(days=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _seed_skill(workspace, "very-old", last_used_at=very_old, state=STATE_STALE)

    out = cur.run_phase1(workspace, now=now)
    assert "very-old" in out.archived_names
    archive = workspace / "skills" / ARCHIVE_DIR_NAME / "very-old"
    assert archive.is_dir()


def test_phase1_reactivates_recent_stale(workspace: Path):
    now = cur._utc_now()
    recent = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _seed_skill(workspace, "comeback", last_used_at=recent, state=STATE_STALE)

    out = cur.run_phase1(workspace, now=now)
    assert "comeback" in out.reactivated_names
    rec = UsageStore(workspace / "skills").record("comeback")
    assert rec["state"] == STATE_ACTIVE


def test_phase1_skips_pinned(workspace: Path):
    now = cur._utc_now()
    very_old = (now - timedelta(days=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _seed_skill(workspace, "pinned-one", last_used_at=very_old, pinned=True)

    out = cur.run_phase1(workspace, now=now)
    assert "pinned-one" not in out.archived_names
    assert (workspace / "skills" / "pinned-one").is_dir()


def test_phase1_anchors_on_created_at_when_no_last_used(workspace: Path):
    now = cur._utc_now()
    _seed_skill(workspace, "fresh-one")
    # Backdate created_at directly
    usage_store = UsageStore(workspace / "skills")
    data = usage_store.load()
    data["fresh-one"]["created_at"] = (
        now - timedelta(days=120)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    usage_store.save(data)

    out = cur.run_phase1(workspace, now=now)
    assert "fresh-one" in out.archived_names


# --------------------------------------------------------------------
# Phase 2 — pre-run tarball + mocked LLM
# --------------------------------------------------------------------


def test_phase2_writes_backup_tarball(workspace: Path):
    _seed_skill(workspace, "alpha")
    _seed_skill(workspace, "beta")

    brain = _brain_returning(stdout="CURATOR-SUMMARY:\nNo changes needed.\n")
    out = cur.run_phase2(workspace, brain)
    assert out.ran
    assert out.backup_path is not None

    backup = Path(out.backup_path)
    assert backup.is_file()
    with tarfile.open(backup) as tf:
        members = [m.name for m in tf.getmembers()]
    assert any("alpha/SKILL.md" in m for m in members)
    assert any("beta/SKILL.md" in m for m in members)


def test_phase2_sets_curator_env(workspace: Path):
    _seed_skill(workspace, "alpha")

    brain = _brain_returning(stdout="CURATOR-SUMMARY:\ndone\n")
    cur.run_phase2(workspace, brain)
    # The curator passes VEXIS_CURATOR=1 via env_overrides; the brain
    # records the kwarg shape — assert the env-override surfaced
    # correctly to the spawn layer.
    record = brain.aux_call_records()[0]
    assert record["env_overrides"] == {"VEXIS_CURATOR": "1"}
    # Curator is the only consumer that needs allow_tools=True so it
    # can write SKILL.md trees during consolidation.
    assert record["allow_tools"] is True


def test_phase2_no_candidates_returns_no_op(workspace: Path):
    # No candidates → brain.spawn_aux is never called, so an empty
    # BrainNull is fine.
    out = cur.run_phase2(workspace, BrainNull())
    assert not out.ran
    assert "No candidates" in out.final_message


def test_phase2_failure_recorded(workspace: Path):
    _seed_skill(workspace, "alpha")

    brain = _brain_returning(stderr="something exploded", returncode=2)
    out = cur.run_phase2(workspace, brain)
    assert out.ran
    assert out.error is not None
    assert "exited 2" in out.error


# --------------------------------------------------------------------
# End-to-end run_curator + report
# --------------------------------------------------------------------


def test_run_curator_writes_report_md_and_run_json(workspace: Path):
    _seed_skill(workspace, "alpha")
    # skip_phase2=True means brain.spawn_aux is never called; pass
    # an empty BrainNull rather than mocking.
    summary = cur.run_curator(workspace, BrainNull(), skip_phase2=True)
    assert (summary.folder / "REPORT.md").is_file()
    assert (summary.folder / "run.json").is_file()
    state = cur.load_state()
    assert state.get("last_run_at") is not None
    assert "phase1" in (state.get("last_run_summary") or "")


def test_run_once_prunes_backups_before_running(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
):
    """The daemon's _run_once should call prune_backups(keep=11) before
    each pass, so steady state is 12 tarballs (11 old + 1 fresh)."""
    from core.curator import CuratorController
    from core.skills import CURATOR_BACKUPS_DIR_NAME

    backups = workspace / "skills" / CURATOR_BACKUPS_DIR_NAME
    backups.mkdir(parents=True)
    # Pre-populate 14 fake tarballs with monotonically increasing mtimes
    # so prune_backups can pick "oldest" deterministically.
    import time
    for i in range(14):
        p = backups / f"old-{i:02d}.tar.gz"
        p.write_bytes(b"x")
        # Stagger mtimes by 1s so newest = highest i
        os.utime(p, (time.time() + i, time.time() + i))

    ctrl = CuratorController(workspace=workspace)
    # Stub out run_curator so we don't actually spawn claude -p
    captured: dict[str, int] = {}

    def fake_run_curator(_ws, _brain, **_kw):
        # Phase B: signature gained ``brain`` as the second positional;
        # the stub ignores it. At this point, prune should already
        # have run, leaving 11 backups.
        captured["before_run"] = sum(1 for _ in backups.iterdir())
        return cur.RunSummary(
            folder=workspace, phase1=cur.Phase1Result(), phase2=cur.Phase2Result()
        )

    monkeypatch.setattr(cur, "run_curator", fake_run_curator)
    ctrl._run_once()
    assert captured["before_run"] == 11


def test_run_curator_phase2_records_archived_names(workspace: Path):
    _seed_skill(workspace, "alpha")
    _seed_skill(workspace, "beta")

    def archive_beta() -> None:
        # Simulate the LLM archiving 'beta' by actually invoking the
        # archive directly. This is what the real LLM would do via
        # vexis-skill archive beta.
        from core.skills import archive_skill
        archive_skill(workspace / "skills", "beta")

    brain = _SideEffectBrain(
        on_call=archive_beta,
        aux_result=AuxResult(
            stdout="CURATOR-SUMMARY:\nArchived beta as redundant with alpha.\n",
            stderr="",
            returncode=0,
        ),
    )
    summary = cur.run_curator(workspace, brain)
    assert "beta" in summary.phase2.archived_names
    assert (workspace / "skills" / ARCHIVE_DIR_NAME / "beta").exists()

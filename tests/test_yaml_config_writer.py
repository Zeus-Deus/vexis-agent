"""Day 2 model UX — atomic writer + comment-presence-gated backup.

Tests for ``core/yaml_config_writer.py``:
- ``has_comments(yaml_text)`` — shared detection helper
- ``backup_if_commented(path)`` — comment-gated backup with the
  daemon-restart-preserves-bak guarantee from §5 research doc
- ``atomic_write_yaml(path, data)`` — fcntl.flock + temp-rename

The daemon-restart-preserves-bak test is the centrepiece — it
pins the bug-fix story for the in-memory-flag pattern that would
have destroyed comments after restart.

Design citation: ``.plans/model-management-ux-research.md`` §5
+ §6 Day 2 backup-tests bullet.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from vexis_agent.core.yaml_config_writer import (
    atomic_write_yaml,
    backup_if_commented,
    has_comments,
)


# ──────────────────────────────────────────────────────────────────
# has_comments — shared detection helper
# ──────────────────────────────────────────────────────────────────


def test_has_comments_finds_full_line_comment():
    text = "# this is a comment\nbrain:\n  kind: claude-code\n"
    assert has_comments(text) is True


def test_has_comments_finds_indented_comment():
    text = "brain:\n  # indented comment\n  kind: claude-code\n"
    assert has_comments(text) is True


def test_has_comments_silent_on_pure_yaml():
    text = "brain:\n  kind: claude-code\nmodels:\n  brain: default\n"
    assert has_comments(text) is False


def test_has_comments_silent_on_empty_string():
    assert has_comments("") is False


def test_has_comments_silent_on_inline_comments():
    """Inline comments at end of value lines aren't detected by
    the simple line-prefix check. PyYAML wouldn't preserve them
    either, so this is honest — false-negative documented in the
    helper's docstring."""
    text = "brain:\n  kind: claude-code  # this is inline\n"
    assert has_comments(text) is False


def test_has_comments_handles_non_string():
    """Defensive — accepts non-string inputs without raising."""
    assert has_comments(None) is False  # type: ignore[arg-type]
    assert has_comments(123) is False  # type: ignore[arg-type]


# ──────────────────────────────────────────────────────────────────
# backup_if_commented — comment-gated backup
# ──────────────────────────────────────────────────────────────────


def test_backup_runs_when_comments_present(tmp_path: Path):
    """The default Day-2 case: user has commented config, slash
    runs, backup fires verbatim."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "# learning curator notes\n"
        "models:\n"
        "  learning_review: sonnet\n",
        encoding="utf-8",
    )
    bak_path = backup_if_commented(cfg)
    assert bak_path == tmp_path / "config.yaml.bak"
    assert bak_path.is_file()
    assert bak_path.read_text(encoding="utf-8") == cfg.read_text(encoding="utf-8")


def test_backup_skipped_when_no_comments_present(tmp_path: Path):
    """Comment-less config — no backup needed; helper returns None
    and creates no .bak file."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "models:\n  learning_review: sonnet\n",
        encoding="utf-8",
    )
    bak_path = backup_if_commented(cfg)
    assert bak_path is None
    assert not (tmp_path / "config.yaml.bak").exists()


def test_backup_skipped_when_file_missing(tmp_path: Path):
    """Fresh install — no config file yet; helper returns None
    without creating anything."""
    cfg = tmp_path / "config.yaml"  # doesn't exist
    bak_path = backup_if_commented(cfg)
    assert bak_path is None
    assert not (tmp_path / "config.yaml.bak").exists()


def test_post_edit_second_edit_skips_backup(tmp_path: Path):
    """Pin: after the first edit comments are gone (PyYAML stripped
    them), so the second edit's backup attempt sees no comments
    and skips — preserving the original .bak."""
    cfg = tmp_path / "config.yaml"
    original_commented = (
        "# original notes\nmodels:\n  learning_review: sonnet\n"
    )
    cfg.write_text(original_commented, encoding="utf-8")

    # First edit: backup fires.
    bak_path = backup_if_commented(cfg)
    assert bak_path is not None
    bak_after_first_edit = bak_path.read_text(encoding="utf-8")
    assert "# original notes" in bak_after_first_edit

    # Simulate the slash-command's atomic write stripping comments.
    atomic_write_yaml(cfg, {"models": {"learning_review": "haiku"}})
    assert not has_comments(cfg.read_text(encoding="utf-8"))

    # Second edit attempt: backup_if_commented sees no comments → skips.
    second_bak = backup_if_commented(cfg)
    assert second_bak is None

    # Original .bak preserved (still has the comment).
    assert "# original notes" in bak_path.read_text(encoding="utf-8")


def test_daemon_restart_preserves_bak(tmp_path: Path):
    """Centrepiece pin for the Day 2 audit's bug-fix story. The
    in-memory-flag pattern that the audit caught would have
    destroyed comments here. The comment-presence trigger pattern
    preserves them through arbitrary daemon-restart cycles."""
    cfg = tmp_path / "config.yaml"
    original = (
        "# carefully curated notes\n"
        "# explaining why each knob is set\n"
        "models:\n  learning_review: sonnet\n"
    )
    cfg.write_text(original, encoding="utf-8")

    # Daemon session 1: edit → backup happens.
    bak_session_1 = backup_if_commented(cfg)
    assert bak_session_1 is not None
    atomic_write_yaml(cfg, {"models": {"learning_review": "haiku"}})

    # SIMULATE DAEMON RESTART (in-memory flags reset; on-disk state
    # persists). Nothing to clear here for this implementation —
    # that's the point: the trigger condition is on-disk state, not
    # process memory.

    # Daemon session 2: edit again. Without the fix this would
    # OVERWRITE the .bak with the now-comment-stripped version.
    second_bak = backup_if_commented(cfg)
    assert second_bak is None, (
        "REGRESSION: backup_if_commented re-fired after restart. "
        "The post-first-edit config has no comments so this MUST "
        "skip — otherwise the original .bak is overwritten with "
        "a comment-stripped version, destroying user notes from "
        "BOTH files."
    )

    # Original .bak is BYTE-IDENTICAL to the original.
    bak_contents = bak_session_1.read_text(encoding="utf-8")
    assert bak_contents == original


def test_backup_re_fires_if_user_re_adds_comments(tmp_path: Path):
    """Edge case: user manually re-comments their config after a
    slash-command write. The next slash sees comments again and
    re-backs-up — overwriting the previous .bak with the newly
    curated state. That's correct behaviour — the user
    deliberately re-curated, the new state IS what they want
    preserved."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("# v1 notes\nmodels: {}\n", encoding="utf-8")

    backup_if_commented(cfg)
    atomic_write_yaml(cfg, {"models": {"learning_review": "haiku"}})

    # User manually re-comments.
    cfg.write_text(
        "# v2 notes — the user re-curated\n"
        "models:\n  learning_review: haiku\n",
        encoding="utf-8",
    )

    # Next slash: re-backs-up. .bak now has v2 content.
    bak = backup_if_commented(cfg)
    assert bak is not None
    bak_contents = bak.read_text(encoding="utf-8")
    assert "v2 notes" in bak_contents
    assert "v1 notes" not in bak_contents


# ──────────────────────────────────────────────────────────────────
# atomic_write_yaml — fcntl.flock + temp-rename
# ──────────────────────────────────────────────────────────────────


def test_atomic_write_creates_file(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    atomic_write_yaml(cfg, {"brain": {"kind": "claude-code"}})
    assert cfg.is_file()
    parsed = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert parsed == {"brain": {"kind": "claude-code"}}


def test_atomic_write_overwrites_existing(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("brain:\n  kind: opencode\n", encoding="utf-8")
    atomic_write_yaml(cfg, {"brain": {"kind": "claude-code"}})
    parsed = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert parsed == {"brain": {"kind": "claude-code"}}


def test_atomic_write_creates_parent_dir(tmp_path: Path):
    """Fresh install: parent doesn't exist; write creates it."""
    cfg = tmp_path / "nested" / "subdir" / "config.yaml"
    atomic_write_yaml(cfg, {"k": "v"})
    assert cfg.is_file()


def test_atomic_write_preserves_key_order(tmp_path: Path):
    """``sort_keys=False`` so user-meaningful ordering survives.
    Important for diff-friendliness — a write that reshuffles
    keys would noise up every dashboard save."""
    cfg = tmp_path / "config.yaml"
    data = {
        "brain": {"kind": "claude-code"},
        "models": {
            "subsystems": {
                "curator": "small",
                "goal_judge": "large",
            },
        },
        "goals": {"enabled": True},
    }
    atomic_write_yaml(cfg, data)
    text = cfg.read_text(encoding="utf-8")
    # Pin order: brain then models then goals.
    assert text.find("brain:") < text.find("models:") < text.find("goals:")


def test_atomic_write_strips_comments_on_round_trip(tmp_path: Path):
    """Documents the limitation: PyYAML's safe_dump can't preserve
    YAML comments. The backup_if_commented pre-step is the
    middle-ground; this test pins that the writer itself doesn't
    try to fight that fight."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "# this comment will be stripped\nmodels:\n  brain: default\n",
        encoding="utf-8",
    )
    parsed = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    atomic_write_yaml(cfg, parsed)
    final = cfg.read_text(encoding="utf-8")
    assert "this comment will be stripped" not in final


def test_atomic_write_releases_lock_on_exception(tmp_path: Path, monkeypatch):
    """Defensive: if yaml.safe_dump raises mid-write, the lock
    must be released so the next call doesn't deadlock."""
    cfg = tmp_path / "config.yaml"
    bad = object()  # not yaml-serialisable

    with pytest.raises(yaml.YAMLError):
        atomic_write_yaml(cfg, {"k": bad})

    # Second call must succeed (lock released).
    atomic_write_yaml(cfg, {"k": "v"})
    assert cfg.is_file()


def test_atomic_write_creates_lockfile_with_safe_perms(tmp_path: Path):
    """The .lock sidecar is created with mode 0o600 — owner-only.
    Important on shared systems."""
    cfg = tmp_path / "config.yaml"
    atomic_write_yaml(cfg, {"k": "v"})
    lock_path = tmp_path / "config.yaml.lock"
    if lock_path.exists():
        # On Linux the file persists; verify mode.
        mode = oct(os.stat(lock_path).st_mode & 0o777)
        assert mode == "0o600", f"lockfile perms surprise: {mode}"


# ──────────────────────────────────────────────────────────────────
# Round-trip: write → read → equal (sanity)
# ──────────────────────────────────────────────────────────────────


def test_round_trip_preserves_data(tmp_path: Path):
    cfg = tmp_path / "config.yaml"
    data = {
        "brain": {"kind": "opencode"},
        "models": {
            "subsystems": {
                "curator": "small",
                "goal_judge": "large",
            },
            "tiers": {
                "opencode": {
                    "large": "anthropic/claude-sonnet-4",
                },
            },
        },
        "model_ux": {"enabled": True},
    }
    atomic_write_yaml(cfg, data)
    parsed = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    assert parsed == data

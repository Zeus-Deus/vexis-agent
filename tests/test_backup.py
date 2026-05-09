"""Phase 5c — vexis-agent backup / backup-restore.

Round-trips a synthetic ~/.vexis + workspace through the zip writer
and restorer to lock the contract: every preserved file lands in the
same relative spot, secrets get re-tightened to 0600, exclusions
stay exclusions.
"""

from __future__ import annotations

import os
import stat
import zipfile
from pathlib import Path

import pytest

from vexis_agent.daemon import backup as bk


# ── exclusion contract ─────────────────────────────────────────────


def test_should_skip_excludes_pycache_and_compiled_artifacts() -> None:
    assert bk._should_skip(Path("__pycache__/foo.pyc"))
    assert bk._should_skip(Path("a/b/__pycache__/c"))
    assert bk._should_skip(Path("foo.pyc"))
    assert bk._should_skip(Path("foo.pyo"))


def test_should_skip_excludes_runtime_pid_files() -> None:
    assert bk._should_skip(Path("daemon.pid"))


def test_should_skip_excludes_sqlite_sidecars() -> None:
    """WAL/SHM/journal pair badly with a re-created .db file."""
    assert bk._should_skip(Path("state.db-wal"))
    assert bk._should_skip(Path("state.db-shm"))
    assert bk._should_skip(Path("state.db-journal"))


def test_should_skip_excludes_browser_profiles() -> None:
    """Cached chromium profiles are large + regenerable; never ship."""
    assert bk._should_skip(Path("browser-profiles/Default/Cookies"))


def test_should_skip_keeps_user_state() -> None:
    assert not bk._should_skip(Path("config.yaml"))
    assert not bk._should_skip(Path("memories/MEMORY.md"))
    assert not bk._should_skip(Path("skills/foo/SKILL.md"))
    assert not bk._should_skip(Path(".env"))


# ── round-trip ─────────────────────────────────────────────────────


def _build_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Build a faux ~/.vexis + workspace tree with both kept files
    and excluded ones. Returns (home, workspace)."""
    home = tmp_path / "home"
    workspace = tmp_path / "ws"
    home.mkdir()
    workspace.mkdir()

    # ~/.vexis content
    (home / "config.yaml").write_text("brain:\n  kind: claude-code\n")
    (home / ".env").write_text("TELEGRAM_BOT_TOKEN=abc\n")
    os.chmod(home / ".env", 0o600)
    (home / "daemon.pid").write_text("12345")  # excluded
    (home / "browser-profiles").mkdir()
    (home / "browser-profiles" / "x").write_text("excluded\n")
    (home / "logs").mkdir()
    (home / "logs" / "curator.log").write_text("kept\n")
    (home / "__pycache__").mkdir()
    (home / "__pycache__" / "foo.pyc").write_bytes(b"\x00")  # excluded

    # workspace content
    (workspace / "CLAUDE.md").write_text("workspace instructions\n")
    (workspace / "SOUL.md").write_text("personality\n")
    (workspace / "memories").mkdir()
    (workspace / "memories" / "MEMORY.md").write_text("- a memory\n")
    (workspace / "memories" / "USER.md").write_text("- a preference\n")
    (workspace / "RELATIONSHIPS.md").write_text("# people\n")
    (workspace / "skills").mkdir()
    (workspace / "skills" / "foo" / "SKILL.md").parent.mkdir(parents=True)
    (workspace / "skills" / "foo" / "SKILL.md").write_text("a skill\n")
    (workspace / ".git").mkdir()  # excluded
    (workspace / ".git" / "HEAD").write_text("excluded\n")

    return home, workspace


def test_backup_writes_zip_with_expected_entries(tmp_path) -> None:
    home, ws = _build_fixture(tmp_path)
    out = tmp_path / "out.zip"
    result = bk.run_backup(out=out, home=home, workspace=ws)

    assert result.archive == out
    assert out.is_file()
    assert result.file_count >= 6  # at least: config, .env, log, ws/CLAUDE.md + 3 memory files

    with zipfile.ZipFile(out, "r") as zf:
        names = set(zf.namelist())

    assert "vexis-home/config.yaml" in names
    assert "vexis-home/.env" in names
    assert "vexis-home/logs/curator.log" in names
    assert "vexis-workspace/CLAUDE.md" in names
    assert "vexis-workspace/memories/MEMORY.md" in names
    assert "vexis-workspace/memories/USER.md" in names
    assert "vexis-workspace/RELATIONSHIPS.md" in names
    assert "vexis-workspace/skills/foo/SKILL.md" in names

    # Exclusions
    assert not any("__pycache__" in n for n in names)
    assert not any(n.endswith("daemon.pid") for n in names)
    assert not any("browser-profiles" in n for n in names)
    assert not any(".git/" in n for n in names)
    assert not any(".pyc" in n for n in names)


def test_backup_handles_missing_workspace(tmp_path) -> None:
    home = tmp_path / "h"
    home.mkdir()
    (home / "config.yaml").write_text("x")
    out = tmp_path / "o.zip"
    result = bk.run_backup(
        out=out, home=home, workspace=tmp_path / "no-such-workspace"
    )
    assert result.workspace_root is None
    assert result.file_count == 1


def test_restore_round_trip_recreates_files(tmp_path) -> None:
    """Backup → wipe → restore yields identical content + 0600 secrets."""
    home, ws = _build_fixture(tmp_path)
    archive = tmp_path / "archive.zip"
    bk.run_backup(out=archive, home=home, workspace=ws)

    # Fresh destination
    new_home = tmp_path / "home2"
    new_ws = tmp_path / "ws2"
    new_home.mkdir()
    new_ws.mkdir()

    result = bk.run_restore(archive, home=new_home, workspace=new_ws)
    assert result.home_files_restored >= 1
    assert result.workspace_files_restored >= 1

    # Content survives
    assert (new_home / "config.yaml").read_text() == "brain:\n  kind: claude-code\n"
    assert (new_ws / "memories" / "MEMORY.md").read_text() == "- a memory\n"
    assert (new_ws / "skills" / "foo" / "SKILL.md").read_text() == "a skill\n"

    # Secrets restored at 0600
    env_mode = stat.S_IMODE(os.stat(new_home / ".env").st_mode)
    assert env_mode == 0o600


def test_restore_skips_existing_without_overwrite(tmp_path) -> None:
    home, ws = _build_fixture(tmp_path)
    archive = tmp_path / "archive.zip"
    bk.run_backup(out=archive, home=home, workspace=ws)

    new_home = tmp_path / "home2"
    new_ws = tmp_path / "ws2"
    new_home.mkdir()
    new_ws.mkdir()
    (new_home / "config.yaml").write_text("USER EDITED CONTENT\n")

    bk.run_restore(archive, home=new_home, workspace=new_ws, overwrite=False)
    assert (new_home / "config.yaml").read_text() == "USER EDITED CONTENT\n"


def test_restore_overwrites_when_asked(tmp_path) -> None:
    home, ws = _build_fixture(tmp_path)
    archive = tmp_path / "archive.zip"
    bk.run_backup(out=archive, home=home, workspace=ws)

    new_home = tmp_path / "home2"
    new_ws = tmp_path / "ws2"
    new_home.mkdir()
    new_ws.mkdir()
    (new_home / "config.yaml").write_text("OLD\n")

    bk.run_restore(archive, home=new_home, workspace=new_ws, overwrite=True)
    assert (new_home / "config.yaml").read_text() == "brain:\n  kind: claude-code\n"


def test_restore_raises_when_archive_missing(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        bk.run_restore(
            tmp_path / "nope.zip", home=tmp_path / "h", workspace=tmp_path / "w"
        )


def test_default_archive_path_lives_under_vexis_home() -> None:
    """The default archive lands under whatever ``vexis_dir()``
    resolves to (which is patched by tests/conftest.py's autouse
    fixture in this run, but the assertion stays valid: the parent
    dir is named ``backups``)."""
    p = bk._default_archive_path()
    assert p.parent.name == "backups"
    assert p.name.startswith("vexis-")
    assert p.suffix == ".zip"


# ── brain-session inclusion (Phase 5j) ────────────────────────────


def test_backup_excludes_brain_sessions_by_default(tmp_path, monkeypatch) -> None:
    """Default backup leaves brain conversation history alone — that
    can be huge and not every user wants to roundtrip it."""
    home, ws = _build_fixture(tmp_path)
    out = tmp_path / "out.zip"
    result = bk.run_backup(out=out, home=home, workspace=ws)
    assert result.brain_sessions_included is False
    assert result.brain_session_files == 0
    with zipfile.ZipFile(out, "r") as zf:
        names = zf.namelist()
    assert not any(n.startswith("brain-sessions/") for n in names)


def test_backup_includes_claude_code_sessions_when_opted_in(
    tmp_path, monkeypatch
) -> None:
    """With --include-brain-sessions, claude-code's projects/<encoded>
    dir lands under brain-sessions/claude-code/ in the archive."""
    home, ws = _build_fixture(tmp_path)
    # Lay a fake claude-code projects dir for ws.
    encoded = str(ws).replace("/", "-").replace(".", "-")
    cc_dir = tmp_path / "fakehome" / ".claude" / "projects" / encoded
    cc_dir.mkdir(parents=True)
    (cc_dir / "session-1.jsonl").write_text('{"type":"user","msg":"hi"}\n')
    (cc_dir / "session-2.jsonl").write_text('{"type":"user","msg":"yo"}\n')
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "fakehome")

    out = tmp_path / "out.zip"
    result = bk.run_backup(
        out=out, home=home, workspace=ws, include_brain_sessions=True
    )
    assert result.brain_sessions_included is True
    assert result.brain_session_files == 2
    with zipfile.ZipFile(out, "r") as zf:
        names = zf.namelist()
    # Encoded workspace path is preserved in the prefix so restore
    # can put the session jsonls back at the right ~/.claude path.
    assert f"brain-sessions/claude-code/{encoded}/session-1.jsonl" in names
    assert f"brain-sessions/claude-code/{encoded}/session-2.jsonl" in names


def test_backup_includes_opencode_db_when_opted_in(
    tmp_path, monkeypatch
) -> None:
    home, ws = _build_fixture(tmp_path)
    oc_db = tmp_path / "fakehome" / ".local" / "share" / "opencode" / "opencode.db"
    oc_db.parent.mkdir(parents=True)
    oc_db.write_bytes(b"SQLITEx00\xfake-db-bytes")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "fakehome")

    out = tmp_path / "out.zip"
    result = bk.run_backup(
        out=out, home=home, workspace=ws, include_brain_sessions=True
    )
    assert result.brain_sessions_included is True
    assert result.brain_session_files == 1
    with zipfile.ZipFile(out, "r") as zf:
        assert "brain-sessions/opencode/opencode.db" in zf.namelist()


def test_restore_replays_claude_code_sessions(tmp_path, monkeypatch) -> None:
    """Round-trip: pack with --include-brain-sessions, restore against
    a fresh ~/.claude/projects/ — sessions land back in place."""
    home, ws = _build_fixture(tmp_path)
    encoded = str(ws).replace("/", "-").replace(".", "-")
    src_cc = tmp_path / "src-home" / ".claude" / "projects" / encoded
    src_cc.mkdir(parents=True)
    (src_cc / "s1.jsonl").write_text("session1")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "src-home")

    archive = tmp_path / "a.zip"
    bk.run_backup(out=archive, home=home, workspace=ws, include_brain_sessions=True)

    # Switch HOME for restore — fresh machine.
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "dest-home")
    new_home = tmp_path / "dest-home" / ".vexis"
    new_ws = tmp_path / "dest-home" / "vexis-workspace"
    new_home.mkdir(parents=True)
    new_ws.mkdir(parents=True)

    result = bk.run_restore(archive, home=new_home, workspace=new_ws)
    assert result.brain_sessions_restored == 1
    restored = tmp_path / "dest-home" / ".claude" / "projects" / encoded / "s1.jsonl"
    assert restored.read_text() == "session1"

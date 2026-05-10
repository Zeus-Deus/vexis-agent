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


def test_should_skip_excludes_backups_directory() -> None:
    """``$VEXIS_HOME/backups/`` lives inside the directory we walk
    AND it's where the output zip itself gets written. Without this
    exclusion the walker crawls into prior backups (compounding the
    archive size linearly per release) and — worse — picks up the
    in-progress output file mid-write, producing a recursive
    self-include that fills the disk before getting killed.

    Surfaced on the first real migration backup of a populated
    install: zip grew to 10+ GB and was still climbing when the
    underlying state was only ~300 MB. Same exclusion covers the
    pre-update snapshots ``daemon.update._pre_update_snapshot``
    drops there for the same reason — both are regenerable artifacts."""
    assert bk._should_skip(Path("backups/vexis-20260510T172203Z.zip"))
    assert bk._should_skip(Path("backups/pre-update-20260510T155537Z.zip"))


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


# ── --migrate flag (v0.1.5) ────────────────────────────────────────


def test_migrate_overwrites_brain_state_but_preserves_machine_local(
    tmp_path,
) -> None:
    """The flag this whole feature exists for. Surfaced when the
    first real dev → home-server migration ran into the daemon-stub
    trap: the home server's daemon ran briefly between the wizard
    install and the restore, and the curator silently wrote a stub
    USER.md. The default overlay then refused to replace that stub
    with the dev box's accumulated USER.md, so the user thought
    their migration succeeded but their accumulated user-prefs
    learning was silently dropped.

    --migrate fixes the trap by overwriting brain state (USER.md,
    MEMORY.md, skills, ...) while preserving the destination's
    machine-local files (.env with the new bot token, config.yaml
    with the destination's brain.kind, dashboard_token).
    """
    home, ws = _build_fixture(tmp_path)
    # Add the brain-state files we care about preserving across migrate.
    (ws / "memories").mkdir(exist_ok=True)
    (ws / "memories" / "USER.md").write_text("DEV-USER-PREFS\n")
    (ws / "SOUL.md").write_text("DEV-PERSONALITY\n")

    archive = tmp_path / "archive.zip"
    bk.run_backup(out=archive, home=home, workspace=ws)

    # Destination simulates a freshly-wizard'd home server with a
    # daemon-generated USER.md stub plus a NEW bot token.
    new_home = tmp_path / "home2"
    new_ws = tmp_path / "ws2"
    new_home.mkdir()
    new_ws.mkdir()
    (new_ws / "memories").mkdir()
    (new_home / ".env").write_text("TELEGRAM_BOT_TOKEN=NEW-BOT-FOR-HOME-SERVER\n")
    (new_home / "config.yaml").write_text("brain:\n  kind: claude-code\n")
    (new_ws / "memories" / "USER.md").write_text("daemon-stub\n")
    (new_ws / "SOUL.md").write_text("daemon-stub\n")

    bk.run_restore(archive, home=new_home, workspace=new_ws, migrate=True)

    # Brain state must come from the backup.
    assert (new_ws / "memories" / "USER.md").read_text() == "DEV-USER-PREFS\n"
    assert (new_ws / "SOUL.md").read_text() == "DEV-PERSONALITY\n"
    # Machine-local files must stay as the destination wrote them.
    assert "NEW-BOT-FOR-HOME-SERVER" in (new_home / ".env").read_text()


def test_migrate_does_not_clobber_dotenv(tmp_path) -> None:
    """Critical invariant: even when the source's .env has different
    secrets, --migrate keeps the destination's .env. Pre-fix, users
    hitting this trap would unknowingly lose their new home-server
    bot token and end up serving from the dev token, which would
    silently get a 'bot already polling' conflict on Telegram or
    (worse) accept messages meant for the dev install."""
    home, ws = _build_fixture(tmp_path)
    (home / ".env").write_text("TELEGRAM_BOT_TOKEN=DEV-TOKEN-AAAA\n")
    archive = tmp_path / "archive.zip"
    bk.run_backup(out=archive, home=home, workspace=ws)

    new_home = tmp_path / "home2"
    new_ws = tmp_path / "ws2"
    new_home.mkdir()
    new_ws.mkdir()
    (new_home / ".env").write_text("TELEGRAM_BOT_TOKEN=HOME-TOKEN-BBBB\n")

    bk.run_restore(archive, home=new_home, workspace=new_ws, migrate=True)

    body = (new_home / ".env").read_text()
    assert "HOME-TOKEN-BBBB" in body, (
        ".env was overwritten despite --migrate — destination secrets "
        "must always survive a migrate restore."
    )
    assert "DEV-TOKEN-AAAA" not in body


def test_overwrite_does_replace_dotenv(tmp_path) -> None:
    """--overwrite is the explicit "I really mean nuke everything"
    option. Different from --migrate. This test pins the contrast."""
    home, ws = _build_fixture(tmp_path)
    (home / ".env").write_text("TELEGRAM_BOT_TOKEN=DEV-TOKEN-AAAA\n")
    archive = tmp_path / "archive.zip"
    bk.run_backup(out=archive, home=home, workspace=ws)

    new_home = tmp_path / "home2"
    new_ws = tmp_path / "ws2"
    new_home.mkdir()
    new_ws.mkdir()
    (new_home / ".env").write_text("TELEGRAM_BOT_TOKEN=HOME-TOKEN-BBBB\n")

    bk.run_restore(archive, home=new_home, workspace=new_ws, overwrite=True)
    assert "DEV-TOKEN-AAAA" in (new_home / ".env").read_text()


# ── brain-session path re-encoding (v0.1.5) ─────────────────────────


def test_brain_session_path_re_encoded_on_restore(tmp_path) -> None:
    """claude-code session storage is keyed by the encoded workspace
    path. dev's `/home/zeus/vexis-workspace` encodes to
    `-home-zeus-vexis-workspace`; pandora's `/home/deus/vexis-workspace`
    to `-home-deus-vexis-workspace`. Without re-encoding, the dev
    sessions land at the source-encoded path on the destination —
    orphaned because the destination's daemon reads from a different
    directory.

    Pin the fix: a session zipped from one workspace path must land
    at the destination workspace's encoded path, regardless of how
    the source encoded it.
    """
    # Source: simulate sessions captured under one workspace path.
    home, ws = _build_fixture(tmp_path)
    src_workspace_str = "/home/zeus/vexis-workspace"
    src_encoded = "-home-zeus-vexis-workspace"
    fake_cc_root = tmp_path / "fake-claude-projects" / src_encoded
    fake_cc_root.mkdir(parents=True)
    (fake_cc_root / "session-001.jsonl").write_text(
        '{"type":"user","text":"hi"}\n'
    )
    (fake_cc_root / "session-002.jsonl").write_text(
        '{"type":"assistant","text":"hello"}\n'
    )

    # Pack a minimal archive by hand with the source-encoded prefix.
    archive = tmp_path / "archive.zip"
    import zipfile
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(
            f"brain-sessions/claude-code/{src_encoded}/session-001.jsonl",
            '{"type":"user","text":"hi"}\n',
        )
        zf.writestr(
            f"brain-sessions/claude-code/{src_encoded}/session-002.jsonl",
            '{"type":"assistant","text":"hello"}\n',
        )

    # Destination: a workspace at a DIFFERENT absolute path. The
    # encoded form will differ from the source's.
    dest_workspace = tmp_path / "dest-ws"
    dest_workspace.mkdir()
    dest_encoded = bk._encode_workspace_for_claude_code(dest_workspace)
    assert dest_encoded != src_encoded, "test setup invalid: paths collide"

    # Point the destination's claude-projects dir at a tmp location
    # so the test doesn't write into the developer's real ~/.claude.
    fake_home = tmp_path / "fake-user-home"
    fake_home.mkdir()
    monkeypatch_home_target = tmp_path / "monkeypatch-target"
    monkeypatch_home_target.mkdir()

    # We need to redirect Path.home() inside run_restore. Easiest:
    # use a context manager that monkeypatches Path.home for the
    # call. pytest's monkeypatch isn't accessible here, so we
    # construct one inline using unittest.mock.
    from unittest.mock import patch
    with patch.object(
        bk.Path, "home", classmethod(lambda cls: fake_home)
    ):
        bk.run_restore(
            archive,
            home=tmp_path / "h",
            workspace=dest_workspace,
        )

    dest_dir = fake_home / ".claude" / "projects" / dest_encoded
    assert dest_dir.exists(), (
        f"sessions did not land at destination-encoded path "
        f"{dest_dir}; check the re-encoding logic in run_restore."
    )
    assert (dest_dir / "session-001.jsonl").exists()
    assert (dest_dir / "session-002.jsonl").exists()
    # And the source-encoded path must NOT exist — that would indicate
    # the re-encoding silently fell through.
    assert not (
        fake_home / ".claude" / "projects" / src_encoded
    ).exists(), "sessions landed at source-encoded path; re-encoding broke"


def test_encode_workspace_matches_transcripts_encoder() -> None:
    """The re-encoder must match vexis_agent.core.transcripts'
    canonical encoder — it's the same algorithm, must stay in sync.
    A drift would silently cause sessions to land at one path while
    the curator reads from another."""
    from vexis_agent.core.transcripts import claude_session_jsonl_dir

    workspace = Path("/home/somebody/vexis-workspace")
    canonical_dir = claude_session_jsonl_dir(workspace)
    canonical_encoded = canonical_dir.name

    backup_encoded = bk._encode_workspace_for_claude_code(workspace)
    assert backup_encoded == canonical_encoded, (
        f"backup encoder out of sync with transcripts encoder: "
        f"backup={backup_encoded!r}, canonical={canonical_encoded!r}"
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
    a fresh ~/.claude/projects/ — sessions land back at the
    destination's encoded workspace path.

    Updated v0.1.5: the assertion path is the DESTINATION's encoded
    workspace, not the source's. Pre-fix the restore wrote sessions
    to the source-encoded path, which orphaned them whenever the
    destination's workspace lived at a different absolute path
    (typical when usernames differ — dev=zeus → home=deus). See
    ``test_brain_session_path_re_encoded_on_restore`` for the
    explicit pin on the re-encoding contract."""
    home, ws = _build_fixture(tmp_path)
    src_encoded = str(ws).replace("/", "-").replace(".", "-")
    src_cc = tmp_path / "src-home" / ".claude" / "projects" / src_encoded
    src_cc.mkdir(parents=True)
    (src_cc / "s1.jsonl").write_text("session1")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "src-home")

    archive = tmp_path / "a.zip"
    bk.run_backup(out=archive, home=home, workspace=ws, include_brain_sessions=True)

    # Switch HOME for restore — fresh machine. Destination workspace
    # lives at a different absolute path, so its encoding differs.
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "dest-home")
    new_home = tmp_path / "dest-home" / ".vexis"
    new_ws = tmp_path / "dest-home" / "vexis-workspace"
    new_home.mkdir(parents=True)
    new_ws.mkdir(parents=True)

    result = bk.run_restore(archive, home=new_home, workspace=new_ws)
    assert result.brain_sessions_restored == 1
    dest_encoded = str(new_ws).replace("/", "-").replace(".", "-")
    restored = (
        tmp_path / "dest-home" / ".claude" / "projects" / dest_encoded
        / "s1.jsonl"
    )
    assert restored.read_text() == "session1"
    # And the source-encoded path must NOT have been written — that
    # would be the v0.1.4-and-earlier bug we just fixed.
    assert not (
        tmp_path / "dest-home" / ".claude" / "projects" / src_encoded
    ).exists(), "session landed at source-encoded path; re-encoding regressed"

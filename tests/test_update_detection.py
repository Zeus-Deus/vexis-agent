"""Phase 3 — install-type detection.

The dispatch logic in ``vexis-agent update`` picks a recipe based on
``InstallType``: pipx → ``pipx upgrade`` (with reinstall fallback);
editable → git pull + pip install -e .; unknown → manual instructions.
The detector reads ``sys.executable`` and ``vexis_agent.__file__``,
so these tests exercise the heuristic by feeding fabricated paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vexis_agent.daemon import update as upd


def test_detect_pipx_install(tmp_path, monkeypatch) -> None:
    """pipx puts venvs under $PIPX_HOME (default ~/.local/share/pipx).
    A python path under that prefix → InstallType.PIPX."""
    pipx_root = tmp_path / "pipx"
    venv = pipx_root / "venvs" / "vexis-agent"
    bin_dir = venv / "bin"
    bin_dir.mkdir(parents=True)
    py = bin_dir / "python"
    py.write_text("")

    monkeypatch.setenv("PIPX_HOME", str(pipx_root))
    info = upd.detect_install_type(python_path=py, package_file=tmp_path / "pkg" / "__init__.py")
    assert info.kind is upd.InstallType.PIPX
    assert info.pipx_venv == venv


def test_detect_pipx_install_with_symlinked_python(tmp_path, monkeypatch) -> None:
    """Pipx 1.12 (Arch / Debian) symlinks the venv's python to the
    system interpreter rather than copying it. Path.resolve() would
    follow that symlink out of pipx-land and the prefix-match
    heuristic alone would fail. The structural-walk fallback should
    still classify this as PIPX."""
    pipx_root = tmp_path / "pipx"
    venv = pipx_root / "venvs" / "vexis-agent"
    bin_dir = venv / "bin"
    bin_dir.mkdir(parents=True)
    real_python = tmp_path / "system" / "bin" / "python3.14"
    real_python.parent.mkdir(parents=True)
    real_python.write_text("")
    py = bin_dir / "python"
    py.symlink_to(real_python)  # this is the pipx 1.12 layout

    monkeypatch.setenv("PIPX_HOME", str(pipx_root))
    info = upd.detect_install_type(
        python_path=py,
        package_file=tmp_path / "pkg" / "__init__.py",
    )
    assert info.kind is upd.InstallType.PIPX
    assert info.pipx_venv == venv


def test_detect_editable_install(tmp_path) -> None:
    """An editable install (pip install -e .) leaves the package in
    a working tree with a sibling .git directory; the detector walks
    up from __init__.py to find it."""
    repo = tmp_path / "checkout"
    pkg = repo / "vexis_agent"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (repo / ".git").mkdir()  # marker

    info = upd.detect_install_type(
        python_path=Path("/usr/bin/python3"),
        package_file=pkg / "__init__.py",
    )
    assert info.kind is upd.InstallType.EDITABLE
    assert info.source_root == repo


def test_detect_unknown_install(tmp_path, monkeypatch) -> None:
    """A python in /usr/bin (not under PIPX_HOME) AND a package
    location with no .git ancestor → unknown."""
    monkeypatch.setenv("PIPX_HOME", str(tmp_path / "no-pipx-here"))
    pkg = tmp_path / "system-pkgs" / "vexis_agent"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")

    info = upd.detect_install_type(
        python_path=Path("/usr/lib/python3/site-packages/python3"),
        package_file=pkg / "__init__.py",
    )
    assert info.kind is upd.InstallType.UNKNOWN


def test_run_update_unknown_returns_nonzero(capsys) -> None:
    """Plan §6.4: 'system pip: print a warning and ask user to do it
    manually.' Manual = exit code 1 so a wrapper script can branch."""
    info = upd.InstallInfo(
        kind=upd.InstallType.UNKNOWN,
        python_path=Path("/usr/bin/python3"),
    )
    code = upd.run_update(info=info)
    assert code == 1
    out = capsys.readouterr().out
    assert "Couldn't detect" in out or "manually" in out


def test_run_update_editable_refuses_when_no_git(tmp_path, capsys) -> None:
    """If the editable source root no longer has .git (rare — user
    deleted it), refuse to operate. Plan: 'never read or write under
    ~/.vexis/'; same posture for the source checkout."""
    repo = tmp_path / "broken-checkout"
    repo.mkdir()
    info = upd.InstallInfo(
        kind=upd.InstallType.EDITABLE,
        python_path=Path("/usr/bin/python3"),
        source_root=repo,
    )
    code = upd.run_update(info=info, snapshot=False)
    assert code == 1
    assert "no longer has a .git" in capsys.readouterr().out


# ── Phase 5f: hangup protection + mirrored log + snapshot ─────────


def test_pre_update_snapshot_writes_archive(tmp_path, monkeypatch) -> None:
    """Snapshot lands at $VEXIS_HOME/backups/pre-update-<utc>.zip and
    contains the seed config we drop into VEXIS_HOME."""
    home = tmp_path / "v"
    home.mkdir()
    (home / "config.yaml").write_text("brain:\n  kind: claude-code\n")
    monkeypatch.setattr(
        "vexis_agent.core.paths.vexis_dir", lambda: home
    )

    archive = upd._pre_update_snapshot()
    assert archive is not None
    assert archive.exists()
    assert archive.parent == home / "backups"
    assert archive.name.startswith("pre-update-")


def test_run_update_unknown_writes_log_file(tmp_path, monkeypatch, capsys) -> None:
    """The mirrored-log context wraps the update; even when the
    install-type is UNKNOWN and run_update bails, the log file
    should exist with at least the timestamped header."""
    home = tmp_path / "v"
    home.mkdir()
    monkeypatch.setattr(
        "vexis_agent.core.paths.vexis_dir", lambda: home
    )
    info = upd.InstallInfo(
        kind=upd.InstallType.UNKNOWN,
        python_path=Path("/usr/bin/python3"),
    )
    upd.run_update(info=info, snapshot=False)
    log_path = home / "logs" / "update.log"
    assert log_path.exists()
    body = log_path.read_text(encoding="utf-8")
    assert "vexis-agent update" in body  # the header banner


def test_hangup_protection_restores_handler() -> None:
    """The context manager must restore the previous SIGHUP handler
    when it exits — leaving SIG_IGN around forever would surprise
    long-running parents."""
    import signal as _signal

    before = _signal.getsignal(_signal.SIGHUP)
    with upd._hangup_protection():
        # Inside the context: SIGHUP is ignored.
        assert _signal.getsignal(_signal.SIGHUP) == _signal.SIG_IGN
    # After: handler restored to whatever it was.
    assert _signal.getsignal(_signal.SIGHUP) == before

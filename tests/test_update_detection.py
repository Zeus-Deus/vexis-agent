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
    code = upd.run_update(info=info)
    assert code == 1
    assert "no longer has a .git" in capsys.readouterr().out

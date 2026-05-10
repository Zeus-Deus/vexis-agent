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


# ── latest-tag resolution + channel dispatch ────────────────────────


def test_resolve_latest_tag_picks_newest_semver(monkeypatch) -> None:
    """git ls-remote with --sort=-v:refname returns tags newest-first;
    we take the first semver-shaped line. Non-semver refs (a branch
    accidentally tagged 'staging', etc.) must be skipped."""
    sample = (
        "abc123\trefs/tags/v0.3.0\n"
        "def456\trefs/tags/v0.2.1\n"
        "ghi789\trefs/tags/v0.1.0\n"
    )

    class _Result:
        returncode = 0
        stdout = sample
        stderr = ""

    monkeypatch.setattr(upd.shutil, "which", lambda _: "/usr/bin/git")
    monkeypatch.setattr(
        upd.subprocess, "run", lambda *a, **kw: _Result()
    )
    assert upd._resolve_latest_tag() == "v0.3.0"


def test_resolve_latest_tag_skips_non_semver(monkeypatch) -> None:
    """Stray non-semver tags at the top (e.g. 'staging', 'release-foo')
    should be skipped — we only return semver-shaped releases so a
    misnamed tag can't poison auto-update."""
    sample = (
        "aaa\trefs/tags/staging\n"
        "bbb\trefs/tags/release-foo\n"
        "ccc\trefs/tags/v0.2.0\n"
    )

    class _Result:
        returncode = 0
        stdout = sample
        stderr = ""

    monkeypatch.setattr(upd.shutil, "which", lambda _: "/usr/bin/git")
    monkeypatch.setattr(
        upd.subprocess, "run", lambda *a, **kw: _Result()
    )
    assert upd._resolve_latest_tag() == "v0.2.0"


def test_resolve_latest_tag_returns_none_when_no_tags(monkeypatch) -> None:
    """Fresh repo before first release: ls-remote succeeds with empty
    output. Caller falls back to 'main'."""

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(upd.shutil, "which", lambda _: "/usr/bin/git")
    monkeypatch.setattr(
        upd.subprocess, "run", lambda *a, **kw: _Result()
    )
    assert upd._resolve_latest_tag() is None


def test_resolve_latest_tag_returns_none_when_git_missing(monkeypatch) -> None:
    """No git on PATH → return None so the caller falls back; never
    raise (the daemon mustn't crash on update if git is unavailable)."""
    monkeypatch.setattr(upd.shutil, "which", lambda _: None)
    assert upd._resolve_latest_tag() is None


def test_resolve_latest_tag_returns_none_on_ls_remote_error(monkeypatch) -> None:
    """ls-remote nonzero exit (offline, 404, rate-limit) → None.
    Caller falls back to 'main' rather than raising."""

    class _Result:
        returncode = 128
        stdout = ""
        stderr = "fatal: unable to access ..."

    monkeypatch.setattr(upd.shutil, "which", lambda _: "/usr/bin/git")
    monkeypatch.setattr(
        upd.subprocess, "run", lambda *a, **kw: _Result()
    )
    assert upd._resolve_latest_tag() is None


def test_update_pipx_stable_uses_resolved_tag(monkeypatch, capsys) -> None:
    """``--channel stable`` (the default) reinstalls from the resolved
    latest tag, not 'main'. This is the whole point of the latest-tag
    behaviour: a home-server running ``vexis-agent update`` only lands
    on tagged releases."""
    monkeypatch.setattr(upd.shutil, "which", lambda _: "/usr/bin/pipx")
    monkeypatch.setattr(upd, "_resolve_latest_tag", lambda: "v0.5.0")

    captured: dict[str, list[str]] = {}

    class _Reinstall:
        returncode = 0

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return _Reinstall()

    monkeypatch.setattr(upd.subprocess, "run", _fake_run)

    code = upd._update_pipx("stable")
    assert code == 0
    # The exact ref must end up in the install spec.
    assert any("@v0.5.0" in arg for arg in captured["cmd"]), captured["cmd"]
    out = capsys.readouterr().out
    assert "v0.5.0" in out


def test_update_pipx_stable_falls_back_to_main_when_no_tags(
    monkeypatch, capsys
) -> None:
    """Pre-first-release repo: resolver returns None, channel=stable
    must fall back to ``@main`` rather than emitting a broken
    ``@None`` install spec."""
    monkeypatch.setattr(upd.shutil, "which", lambda _: "/usr/bin/pipx")
    monkeypatch.setattr(upd, "_resolve_latest_tag", lambda: None)

    captured: dict[str, list[str]] = {}

    class _Reinstall:
        returncode = 0

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return _Reinstall()

    monkeypatch.setattr(upd.subprocess, "run", _fake_run)

    code = upd._update_pipx("stable")
    assert code == 0
    assert any("@main" in arg for arg in captured["cmd"]), captured["cmd"]
    assert "falling back to main" in capsys.readouterr().out


def test_update_pipx_dev_uses_main(monkeypatch) -> None:
    """``--channel dev`` is the dev-machine escape hatch: track main
    tip even when there are tagged releases."""
    monkeypatch.setattr(upd.shutil, "which", lambda _: "/usr/bin/pipx")
    # Resolver wouldn't be called for dev, but stub it anyway so a
    # regression that calls it accidentally fails loudly.
    monkeypatch.setattr(
        upd, "_resolve_latest_tag",
        lambda: pytest.fail("dev channel must not call tag resolver"),
    )

    captured: dict[str, list[str]] = {}

    class _Reinstall:
        returncode = 0

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return _Reinstall()

    monkeypatch.setattr(upd.subprocess, "run", _fake_run)

    code = upd._update_pipx("dev")
    assert code == 0
    assert any("@main" in arg for arg in captured["cmd"]), captured["cmd"]


def test_update_pipx_literal_ref_pins(monkeypatch) -> None:
    """Anything other than 'stable'/'dev' is treated as a literal ref,
    so power users can do ``vexis-agent update --channel v0.2.0``."""
    monkeypatch.setattr(upd.shutil, "which", lambda _: "/usr/bin/pipx")

    captured: dict[str, list[str]] = {}

    class _Reinstall:
        returncode = 0

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return _Reinstall()

    monkeypatch.setattr(upd.subprocess, "run", _fake_run)

    code = upd._update_pipx("v0.2.0")
    assert code == 0
    assert any("@v0.2.0" in arg for arg in captured["cmd"]), captured["cmd"]


# ── auto-render systemd unit after update (v0.1.3) ──────────────────


def test_render_unit_skipped_when_no_unit_on_disk(
    tmp_path, monkeypatch
) -> None:
    """No unit file on disk → user never ran service install. Don't
    surprise them by suddenly creating one. Silent no-op."""
    nonexistent = tmp_path / "no-such-unit.service"
    monkeypatch.setattr(
        "vexis_agent.daemon.systemd.user_unit_path", lambda: nonexistent
    )
    # If subprocess.run gets called, the test should fail loudly.
    monkeypatch.setattr(
        upd.subprocess, "run",
        lambda *a, **kw: pytest.fail(
            "Should not invoke subprocess when no unit is installed"
        ),
    )
    upd._render_unit_if_installed()  # no exception, no subprocess call


def test_render_unit_skipped_when_skip_flag_set(
    tmp_path, monkeypatch
) -> None:
    """skip=True (CLI flag) suppresses re-render even when a unit
    exists. Lets users with hand-customized units opt out."""
    unit = tmp_path / "vexis-agent.service"
    unit.write_text("# fake")
    monkeypatch.setattr(
        "vexis_agent.daemon.systemd.user_unit_path", lambda: unit
    )
    monkeypatch.setattr(
        upd.subprocess, "run",
        lambda *a, **kw: pytest.fail(
            "Should not invoke subprocess when skip=True"
        ),
    )
    upd._render_unit_if_installed(skip=True)


def test_render_unit_skipped_when_env_var_set(
    tmp_path, monkeypatch
) -> None:
    """VEXIS_NO_SERVICE_RENDER=1 suppresses re-render. Same
    rationale as the CLI flag, but accessible to callers that
    don't thread the flag through."""
    unit = tmp_path / "vexis-agent.service"
    unit.write_text("# fake")
    monkeypatch.setattr(
        "vexis_agent.daemon.systemd.user_unit_path", lambda: unit
    )
    monkeypatch.setenv("VEXIS_NO_SERVICE_RENDER", "1")
    monkeypatch.setattr(
        upd.subprocess, "run",
        lambda *a, **kw: pytest.fail(
            "Should not invoke subprocess when env var is set"
        ),
    )
    upd._render_unit_if_installed()


def test_render_unit_runs_subprocess_when_unit_installed(
    tmp_path, monkeypatch, capsys
) -> None:
    """Happy path: unit exists, no opt-out → subprocess fires
    `vexis-agent service install` against the freshly-installed
    venv python. Pin the exact argv so the contract with the CLI
    sub-command (which expects `service install`) is locked in."""
    unit = tmp_path / "vexis-agent.service"
    unit.write_text("# fake")
    monkeypatch.setattr(
        "vexis_agent.daemon.systemd.user_unit_path", lambda: unit
    )
    monkeypatch.delenv("VEXIS_NO_SERVICE_RENDER", raising=False)

    captured: dict[str, list[str]] = {}

    class _Result:
        returncode = 0

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        return _Result()

    monkeypatch.setattr(upd.subprocess, "run", _fake_run)

    upd._render_unit_if_installed()
    cmd = captured["cmd"]
    # Must be a python -m invocation so the subprocess starts a
    # fresh interpreter that sees the updated code, not the stale
    # imports the current process is running.
    assert cmd[1:] == ["-m", "vexis_agent.cli", "service", "install"], (
        f"Unexpected re-render command: {cmd}"
    )
    assert "Re-rendering systemd unit" in capsys.readouterr().out


@pytest.mark.parametrize("falsy_value", ["", "0", "false", "FALSE", "no", "NO"])
def test_service_render_falsy_env_values_dont_skip(
    falsy_value, tmp_path, monkeypatch
) -> None:
    """Canonical falsy strings must NOT count as "skip". A user
    who explicitly sets VEXIS_NO_SERVICE_RENDER=0 expects re-render
    to run, not be inhibited."""
    monkeypatch.setenv("VEXIS_NO_SERVICE_RENDER", falsy_value)
    assert upd._service_render_disabled_via_env() is False


@pytest.mark.parametrize("truthy_value", ["1", "true", "yes", "anything"])
def test_service_render_truthy_env_values_skip(
    truthy_value, monkeypatch
) -> None:
    """Anything other than the canonical falsy strings counts as
    truthy — matches the pattern install.sh's _envflag uses for
    VEXIS_DRY_RUN / VEXIS_SKIP_SETUP."""
    monkeypatch.setenv("VEXIS_NO_SERVICE_RENDER", truthy_value)
    assert upd._service_render_disabled_via_env() is True

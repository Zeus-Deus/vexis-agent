"""Phase 3 — systemd unit renderer.

The unit is generated at install time so each machine's actual venv
python and resolved VEXIS_HOME get baked in (decision D6 in
``.plans/packaging-implementation-plan.md`` §2). These tests pin the
shape of the rendered string so future edits don't quietly drop a
field that production unit files depend on.
"""

from __future__ import annotations

from pathlib import Path

from vexis_agent.daemon import systemd


def test_render_user_unit_contains_required_sections() -> None:
    unit = systemd.render_user_unit(
        python_path=Path("/opt/vexis/bin/python"),
        vexis_home=Path("/home/u/.vexis"),
    )
    # Three top-level sections in the right order.
    unit_idx = unit.index("[Unit]")
    service_idx = unit.index("[Service]")
    install_idx = unit.index("[Install]")
    assert unit_idx < service_idx < install_idx, (
        "[Unit] / [Service] / [Install] sections are out of order"
    )


def test_render_user_unit_bakes_python_and_vexis_home() -> None:
    """The whole point of runtime rendering: the actual interpreter
    path and the resolved VEXIS_HOME need to live in the unit body."""
    unit = systemd.render_user_unit(
        python_path=Path("/var/venv/vexis/bin/python"),
        vexis_home=Path("/srv/state/vexis"),
    )
    assert "ExecStart=/var/venv/vexis/bin/python -m vexis_agent.cli run" in unit
    assert "WorkingDirectory=/srv/state/vexis" in unit
    assert "Environment=VEXIS_HOME=/srv/state/vexis" in unit


def test_render_user_unit_includes_local_bin_on_path() -> None:
    """Daemon needs ``claude`` / ``opencode`` on PATH; systemd's
    default user PATH (``/usr/local/sbin:/usr/local/bin:/usr/sbin:
    /usr/bin``) does NOT include ``~/.local/bin`` where every common
    install path drops the brain CLI.

    Surfaced in v0.1.1 → v0.1.2: the dotenv fix unblocked secret
    loading, but the daemon then crashed with ``claude CLI not found
    on PATH``. The unit must explicitly set PATH so brain-CLI
    discovery works without the user editing systemd config by hand.

    ``%h`` is systemd's user-home specifier and resolves at unit-load
    time, so the directive is portable across users / homedirs.
    """
    unit = systemd.render_user_unit(
        python_path="/x/py", vexis_home="/y"
    )
    assert "Environment=PATH=" in unit, (
        "systemd unit is missing the PATH directive — daemon will "
        "fail to find claude / opencode on PATH."
    )
    assert "%h/.local/bin" in unit, (
        "PATH directive does not include %h/.local/bin where pipx, "
        "npm-global, and claude-installer drop the brain CLIs."
    )


def test_render_user_unit_includes_envfile_directive() -> None:
    """Defense-in-depth alongside core.config.load_dotenv:
    EnvironmentFile=-{home}/.env makes systemd itself populate the
    daemon's env from the dotenv, so even if the in-process load_dotenv
    path ever regresses (as it did in v0.1.0 — bare load_dotenv()
    walked up from inside the pipx venv and never reached
    ~/.vexis/.env), the daemon still inherits TELEGRAM_BOT_TOKEN etc.
    from systemd's environment.

    The leading ``-`` makes a missing file non-fatal: a fresh box
    where the wizard hasn't run yet shouldn't fail to start the
    unit; the daemon's own ``_require`` raises a clearer error.
    """
    unit = systemd.render_user_unit(
        python_path="/x/py", vexis_home="/srv/state/vexis"
    )
    assert "EnvironmentFile=-/srv/state/vexis/.env" in unit, (
        "systemd unit is missing the EnvironmentFile directive that "
        "guards against the v0.1.0 bare-load_dotenv regression."
    )


def test_render_user_unit_has_network_after_target() -> None:
    unit = systemd.render_user_unit(
        python_path="/usr/bin/python3", vexis_home="/tmp/v"
    )
    assert "After=network-online.target" in unit
    assert "Wants=network-online.target" in unit


def test_render_user_unit_default_install_target() -> None:
    """User units want default.target, not multi-user.target — the
    distinction matters: default.target is the systemd --user equivalent
    of multi-user, but using the system-mode value would silently
    refuse to enable in user mode on some distros."""
    unit = systemd.render_user_unit(
        python_path="/x/python", vexis_home="/y"
    )
    assert "WantedBy=default.target" in unit
    assert "WantedBy=multi-user.target" not in unit


def test_render_user_unit_streams_to_journal() -> None:
    """Logs need to go through journalctl so 'vexis-agent service
    logs' has something to show. systemd's default stdout is journal
    on most distros, but we set it explicitly so containers / WSL /
    minimal setups don't surprise us."""
    unit = systemd.render_user_unit(
        python_path="/x/py", vexis_home="/y"
    )
    assert "StandardOutput=journal" in unit
    assert "StandardError=journal" in unit


def test_render_user_unit_restart_policy() -> None:
    unit = systemd.render_user_unit(
        python_path="/x/py", vexis_home="/y"
    )
    assert "Restart=on-failure" in unit
    assert "RestartSec=" in unit


def test_user_unit_path_uses_xdg_or_home(tmp_path, monkeypatch) -> None:
    """user_unit_dir() honours $XDG_CONFIG_HOME if set, falls back to
    ~/.config otherwise — important for distros / containers that
    relocate the config dir."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "fakehome")
    expected = tmp_path / "fakehome" / ".config" / "systemd" / "user"
    assert systemd.user_unit_dir() == expected

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdgcfg"))
    assert systemd.user_unit_dir() == tmp_path / "xdgcfg" / "systemd" / "user"


def test_install_user_unit_writes_file_and_returns_path(
    tmp_path, monkeypatch
) -> None:
    """install_user_unit writes the rendered body to the right path,
    creating the parent directory if missing. We mock systemctl out
    via PATH so daemon-reload is a no-op when systemd isn't present."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    # Stub systemctl out so we don't accidentally daemon-reload the
    # host's systemd from inside the test.
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "systemctl").write_text("#!/bin/sh\nexit 0\n")
    (fake_bin / "systemctl").chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{__import__('os').environ['PATH']}")

    written = systemd.install_user_unit(
        python_path=Path("/foo/python"),
        vexis_home=tmp_path / "fakehome",
    )
    assert written == tmp_path / "cfg" / "systemd" / "user" / "vexis-agent.service"
    body = written.read_text(encoding="utf-8")
    assert "ExecStart=/foo/python -m vexis_agent.cli run" in body
    assert f"Environment=VEXIS_HOME={tmp_path / 'fakehome'}" in body

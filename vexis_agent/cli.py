"""vexis-agent CLI — Typer entry point.

Phase 2 wired ``run``; Phase 3 wires ``service`` (systemd lifecycle),
``update`` (pipx-aware self-upgrade), and ``doctor`` (diagnostics).
``setup`` remains a Phase-4 stub.
"""

from __future__ import annotations

import sys

import typer

from vexis_agent import __version__

app = typer.Typer(
    name="vexis-agent",
    help="Telegram bot + agent CLI bridge for Linux desktops.",
    no_args_is_help=True,
    add_completion=False,
)

service_app = typer.Typer(
    name="service",
    help="Manage the systemd user unit (install, start, stop, logs, …).",
    no_args_is_help=True,
)
app.add_typer(service_app, name="service")


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"vexis-agent {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Print version and exit.",
    ),
) -> None:
    """vexis-agent — Telegram bot + agent CLI bridge.

    ``vexis-agent run`` starts the daemon. See ``vexis-agent --help``
    for the full command list.
    """


@app.command()
def run() -> None:
    """Start the vexis-agent daemon (foreground).

    Reads ``$VEXIS_HOME/config.yaml`` (default ``~/.vexis/config.yaml``)
    for runtime config and ``$VEXIS_HOME/.env`` (or process env) for
    secrets. Same behaviour as the legacy ``python main.py``.
    """
    # Local import so `vexis-agent --help` doesn't pay the daemon's
    # import cost (heavy: brain CLIs, MCP, FastAPI, etc.).
    from vexis_agent.main import main as _daemon_main

    _daemon_main()


@app.command()
def setup(
    reset: bool = typer.Option(
        False,
        "--reset",
        help="Archive existing config.yaml + .env to *.bak.<utc> and re-run.",
    ),
) -> None:
    """Interactive first-run setup.

    Creates ``$VEXIS_HOME/config.yaml`` and ``$VEXIS_HOME/.env`` (mode
    0600) from shipped templates if absent, prompts for the Telegram
    bot token + allowed user ID, and offers to install the systemd
    user unit. Existing curator/learning/goal state is left untouched
    — the wizard never deletes data.
    """
    from vexis_agent.setup_wizard import (
        SetupAborted,
        format_summary,
        run_setup,
    )

    try:
        result = run_setup(reset=reset)
    except SetupAborted as exc:
        typer.echo(f"vexis-agent setup: {exc}", err=True)
        raise typer.Exit(1)
    typer.echo(format_summary(result))


@app.command()
def doctor() -> None:
    """Diagnose installation + config.

    Runs an ordered set of checks (Python version, config.yaml,
    Telegram secrets, brain CLI, systemctl, linger, service unit).
    Prints a one-line summary per check and exits non-zero if any
    required check failed; warnings (optional checks) don't fail the
    run.
    """
    from vexis_agent.daemon.doctor import (
        format_results,
        overall_exit_code,
        run_all,
    )

    results = run_all()
    typer.echo(format_results(results, color=sys.stdout.isatty()))
    raise typer.Exit(overall_exit_code(results))


@app.command()
def update(
    channel: str = typer.Option(
        "stable",
        "--channel",
        help="Update channel: 'stable' (main branch) or 'dev'.",
    ),
) -> None:
    """Pull and reinstall the latest vexis-agent.

    Detects pipx vs editable-source installs and dispatches accordingly.
    Never touches ``~/.vexis/`` or ``~/vexis-workspace/`` — state is
    sacrosanct (decision D7 in the packaging plan). Does NOT auto-restart
    the service; prints a hint instead.
    """
    from vexis_agent.daemon.update import run_update

    raise typer.Exit(run_update(channel=channel))


# ──────────────────────────────────────────────────────────────────────
# `service` sub-app: thin shells around vexis_agent.daemon.systemd.
# ──────────────────────────────────────────────────────────────────────


@service_app.command("install")
def service_install() -> None:
    """Render and install the systemd user unit, then daemon-reload.

    The interpreter path baked in is ``sys.executable`` of the running
    process (the pipx venv python when invoked via the console script).
    ``VEXIS_HOME`` is resolved from the env var (default ``~/.vexis``)
    and frozen into the unit so the service stays pinned even if the
    user later changes their shell environment.
    """
    from vexis_agent.daemon.systemd import install_user_unit

    target = install_user_unit()
    typer.echo(f"Installed {target}")
    typer.echo(
        "Enable + start with:\n"
        "  systemctl --user enable --now vexis-agent.service"
    )


@service_app.command("uninstall")
def service_uninstall() -> None:
    """Stop, disable, and remove the systemd user unit."""
    from vexis_agent.daemon.systemd import uninstall_user_unit

    removed = uninstall_user_unit()
    if removed:
        typer.echo("Uninstalled vexis-agent.service")
    else:
        typer.echo("vexis-agent.service was not installed; nothing to do.")


@service_app.command("start")
def service_start() -> None:
    """systemctl --user start vexis-agent.service"""
    from vexis_agent.daemon.systemd import start

    proc = start()
    if proc.stdout:
        typer.echo(proc.stdout.rstrip())


@service_app.command("stop")
def service_stop() -> None:
    """systemctl --user stop vexis-agent.service"""
    from vexis_agent.daemon.systemd import stop

    proc = stop()
    if proc.stdout:
        typer.echo(proc.stdout.rstrip())


@service_app.command("restart")
def service_restart() -> None:
    """systemctl --user restart vexis-agent.service"""
    from vexis_agent.daemon.systemd import restart

    proc = restart()
    if proc.stdout:
        typer.echo(proc.stdout.rstrip())


@service_app.command("status")
def service_status() -> None:
    """systemctl --user status vexis-agent.service"""
    from vexis_agent.daemon.systemd import status

    proc = status()
    if proc.stdout:
        typer.echo(proc.stdout.rstrip())
    raise typer.Exit(proc.returncode)


@service_app.command("logs")
def service_logs(
    follow: bool = typer.Option(
        False, "--follow", "-f", help="Stream new log lines (journalctl -f)."
    ),
    lines: int = typer.Option(
        200, "--lines", "-n", help="How many trailing lines to print before tailing."
    ),
) -> None:
    """journalctl --user-unit vexis-agent.service [-f]"""
    from vexis_agent.daemon.systemd import logs as _logs

    raise typer.Exit(_logs(follow=follow, lines=lines))


if __name__ == "__main__":
    app()

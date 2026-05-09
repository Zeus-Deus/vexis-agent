"""vexis-agent CLI — Typer entry point.

Wired up in Phase 2:
  vexis-agent run     → start the daemon (foreground)

Stubs reserving the surface for later phases:
  vexis-agent setup   → Phase 4: interactive first-run wizard.
  vexis-agent doctor  → Phase 3: diagnose installation + config.
  vexis-agent update  → Phase 3: pipx-aware update + reinstall.
  vexis-agent service → Phase 3: systemd user-unit lifecycle subapp.

Each stub raises ``NotImplementedError`` with a pointer to the phase
that fills it in. This keeps ``vexis-agent --help`` honest about which
commands are real today without committing to plumbing yet.
"""

from __future__ import annotations

import typer

from vexis_agent import __version__

app = typer.Typer(
    name="vexis-agent",
    help="Telegram bot + agent CLI bridge for Linux desktops.",
    no_args_is_help=True,
    add_completion=False,
)


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

    Run ``vexis-agent run`` to start the daemon, or
    ``vexis-agent --help`` for the full command list.
    """


@app.command()
def run() -> None:
    """Start the vexis-agent daemon (foreground).

    Reads ``~/.vexis/config.yaml`` (or ``$VEXIS_HOME/config.yaml``) for
    runtime config and ``~/.vexis/.env`` (or process env) for secrets.
    Same behaviour as the legacy ``python main.py``.
    """
    # Local import so `vexis-agent --help` doesn't pay the daemon's
    # import cost (heavy: brain CLIs, MCP, FastAPI, etc.).
    from vexis_agent.main import main as _daemon_main

    _daemon_main()


@app.command()
def setup() -> None:
    """Interactive first-run setup. (Phase 4 — not implemented yet.)

    Will write ``~/.vexis/config.yaml`` and ``~/.vexis/.env`` from
    shipped templates, prompt for Telegram token + allowed user ID,
    and optionally install the systemd user unit.
    """
    raise NotImplementedError(
        "vexis-agent setup is implemented in Phase 4 of the packaging plan. "
        "For now, copy ~/.vexis/config.yaml and ~/.vexis/.env by hand."
    )


@app.command()
def doctor() -> None:
    """Diagnose installation + config. (Phase 3 — not implemented yet.)"""
    raise NotImplementedError(
        "vexis-agent doctor is implemented in Phase 3 of the packaging plan."
    )


@app.command()
def update(
    channel: str = typer.Option(
        "stable",
        "--channel",
        help="Update channel: 'stable' (main branch) or 'dev'.",
    ),
) -> None:
    """Pull and reinstall the latest vexis-agent. (Phase 3 — not implemented yet.)

    Will detect pipx vs editable-source installs and dispatch
    accordingly. Never touches ``~/.vexis/`` or ``~/vexis-workspace/`` —
    state is sacrosanct (decision D7 in the packaging plan).
    """
    _ = channel  # silence unused-arg until Phase 3 wires it up
    raise NotImplementedError(
        "vexis-agent update is implemented in Phase 3 of the packaging plan."
    )


# `service` Typer sub-app is added in Phase 3 (systemd user-unit lifecycle).


if __name__ == "__main__":
    app()

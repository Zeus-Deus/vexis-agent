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

mcp_app = typer.Typer(
    name="mcp",
    help="Manage MCP servers in ~/.vexis/mcp-servers.yaml.",
    no_args_is_help=True,
)
app.add_typer(mcp_app, name="mcp")


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
        help=(
            "Update channel: 'stable' (latest tagged release), "
            "'dev' (main branch tip), or a literal git ref "
            "(e.g. 'v0.3.0', a branch name, a sha)."
        ),
    ),
) -> None:
    """Pull and reinstall the latest vexis-agent.

    Default ``--channel stable`` resolves the newest semver tag on the
    upstream remote so you only land on code that has been explicitly
    released — matching what the curl-bash one-liner does on a fresh
    install. ``--channel dev`` follows main branch tip for tracking
    pre-release work. Pass any other ref string to pin.

    Detects pipx vs editable-source installs and dispatches accordingly.
    Never touches ``~/.vexis/`` or ``~/vexis-workspace/`` — state is
    sacrosanct (decision D7 in the packaging plan). Does NOT auto-restart
    the service; prints a hint instead.
    """
    from vexis_agent.daemon.update import run_update

    raise typer.Exit(run_update(channel=channel))


@app.command()
def backup(
    out: str = typer.Option(
        "",
        "--out",
        "-o",
        help="Output zip path. Defaults to ~/.vexis/backups/vexis-<utc>.zip.",
    ),
    include_brain_sessions: bool = typer.Option(
        False,
        "--include-brain-sessions",
        help=(
            "Also pack the brain's conversation history "
            "(~/.claude/projects/<encoded-cwd>/ for claude-code or "
            "~/.local/share/opencode/opencode.db for opencode). "
            "Useful when migrating a long-lived install; can be large."
        ),
    ),
) -> None:
    """Pack the whole agent — config, secrets, memories, skills,
    SOUL, RELATIONSHIPS, curator/learning state, goals — into a zip.

    Default backup includes everything that makes \"your agent\" your
    agent on the vexis side: ~/.vexis/ + ~/vexis-workspace/.
    ``--include-brain-sessions`` adds the brain's stored conversation
    history (claude-code's projects dir or opencode's DB) so the
    restored install picks up exactly where the source left off.

    Excludes regenerable junk: caches, node_modules, browser
    profiles, SQLite WAL sidecars, daemon.pid, .git history.
    """
    from pathlib import Path

    from vexis_agent.daemon.backup import run_backup

    out_path = Path(out).expanduser() if out else None
    result = run_backup(
        out=out_path,
        include_brain_sessions=include_brain_sessions,
    )
    typer.echo(f"Wrote {result.file_count} files to {result.archive}")
    typer.echo(f"  vexis-home:      {result.home_root}")
    if result.workspace_root:
        typer.echo(f"  vexis-workspace: {result.workspace_root}")
    if result.brain_sessions_included:
        typer.echo(
            f"  brain-sessions:  {result.brain_session_files} files"
        )


@app.command()
def uninstall(
    purge_state: bool = typer.Option(
        False,
        "--purge-state",
        help=(
            "Also delete $VEXIS_HOME (config, curator/learning state, "
            "goals.json, dashboard token, .env). DESTRUCTIVE."
        ),
    ),
    purge_workspace: bool = typer.Option(
        False,
        "--purge-workspace",
        help=(
            "Also delete $VEXIS_WORKSPACE (CLAUDE.md, SOUL.md, MEMORY.md, "
            "USER.md, RELATIONSHIPS.md, memories/, skills/). DESTRUCTIVE."
        ),
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the confirmation prompts. Use with care.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the uninstall plan and exit without doing anything.",
    ),
) -> None:
    """Remove vexis-agent. Layered: service / package / state.

    Without ``--purge-state``, your ``~/.vexis/`` survives — re-installing
    later picks up where you left off. ``--purge-workspace`` is even more
    destructive (deletes memories + skills); only pass it when you're
    sure you want to start fresh.

    Always prompts before destructive steps unless ``--yes``.
    """
    from vexis_agent.daemon.uninstall import build_plan, run_uninstall

    plan = build_plan(purge_state=purge_state, purge_workspace=purge_workspace)
    typer.echo(plan.describe())
    if dry_run:
        return
    rc = run_uninstall(plan, confirm=not yes)
    raise typer.Exit(rc)


@app.command("backup-restore")
def backup_restore(
    archive: str = typer.Argument(..., help="Path to a vexis backup zip."),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Overwrite existing files (default: skip).",
    ),
) -> None:
    """Restore a backup zip into $VEXIS_HOME + $VEXIS_WORKSPACE.

    Run ``vexis-agent setup`` first on a fresh machine; then point
    this at the backup zip to bring memories, skills, config, and
    secrets across. Existing files are skipped unless ``--overwrite``
    is passed.
    """
    from pathlib import Path

    from vexis_agent.daemon.backup import run_restore

    result = run_restore(Path(archive).expanduser(), overwrite=overwrite)
    typer.echo(
        f"Restored {result.home_files_restored} home file(s) → {result.home_dest}"
    )
    typer.echo(
        f"Restored {result.workspace_files_restored} workspace file(s) "
        f"→ {result.workspace_dest}"
    )
    if not overwrite:
        typer.echo("(existing files skipped — pass --overwrite to replace them)")


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


# ──────────────────────────────────────────────────────────────────────
# `mcp` sub-app: thin shells around vexis_agent.daemon.mcp.
# ──────────────────────────────────────────────────────────────────────


@mcp_app.command("add")
def mcp_add(
    name: str = typer.Argument(..., help="MCP server name surfaced to the brain."),
    command: str = typer.Option(..., "--command", "-c", help="Binary to invoke."),
    arg: list[str] = typer.Option(
        [],
        "--arg",
        "-a",
        help="Args passed after the command (repeatable).",
    ),
    env: list[str] = typer.Option(
        [],
        "--env",
        "-e",
        help="KEY=VALUE per-server env (repeatable).",
    ),
    binary: str = typer.Option(
        "",
        "--binary",
        "-b",
        help="Override the PATH-presence check; defaults to --command.",
    ),
) -> None:
    """Add (or replace) an MCP server in ~/.vexis/mcp-servers.yaml.

    Auto-refreshes both per-brain native files (~/vexis-workspace/.mcp.json
    and opencode.json) so the brain sees the new entry on next session.
    """
    from vexis_agent.daemon.mcp import add_server, parse_env_assignments

    try:
        env_dict = parse_env_assignments(env)
    except ValueError as exc:
        typer.echo(f"vexis-agent mcp add: {exc}", err=True)
        raise typer.Exit(2)

    result = add_server(
        name=name,
        command=command,
        args=arg or None,
        env=env_dict or None,
        binary=binary or None,
    )
    verb = "Replaced" if result.replaced_existing else "Added"
    typer.echo(f"{verb} '{result.name}' in {result.yaml_path}")
    for p in result.refreshed_paths:
        typer.echo(f"  refreshed {p}")


@mcp_app.command("remove")
def mcp_remove(
    name: str = typer.Argument(..., help="MCP server name to remove."),
) -> None:
    """Remove an MCP server from ~/.vexis/mcp-servers.yaml.

    Built-in detectors (e.g. omarchy-kb) live in vexis source and
    can't be removed this way; ``vexis-agent mcp list`` shows which
    are user-declared vs built-in.
    """
    from vexis_agent.daemon.mcp import remove_server

    result = remove_server(name=name)
    if not result.found:
        typer.echo(
            f"'{name}' is not in {result.yaml_path}; nothing to do.",
            err=True,
        )
        raise typer.Exit(1)
    typer.echo(f"Removed '{result.name}' from {result.yaml_path}")
    for p in result.refreshed_paths:
        typer.echo(f"  refreshed {p}")


@mcp_app.command("list")
def mcp_list() -> None:
    """List MCP servers vexis knows about (user-declared + built-in).

    Each row shows: source (user/builtin), name, on-PATH check,
    command line.
    """
    from vexis_agent.daemon.mcp import list_servers

    rows = list_servers()
    if not rows:
        typer.echo("No MCP servers configured.")
        typer.echo("Add one with: vexis-agent mcp add <name> --command <bin>")
        return
    width_name = max(len(r.name) for r in rows)
    for r in rows:
        marker = "✓" if r.on_path else "✗"
        cmdline = r.command + (" " + " ".join(r.args) if r.args else "")
        typer.echo(
            f"  {marker} [{r.source:<7}] {r.name:<{width_name}}  {cmdline}"
        )


@mcp_app.command("status")
def mcp_status() -> None:
    """Detailed per-server status: PATH-resolvable, brain-wired in
    each native file, full command line + env. Use this to diagnose
    'why doesn't the brain see my MCP server' issues."""
    from vexis_agent.daemon.mcp import status_servers

    rows = status_servers()
    if not rows:
        typer.echo("No MCP servers configured.")
        typer.echo("Add one with: vexis-agent mcp add <name> --command <bin>")
        return

    for s in rows:
        e = s.entry
        path_glyph = "✓" if e.on_path else "✗"
        claude_glyph = "✓" if s.in_claude_native else "✗"
        opencode_glyph = "✓" if s.in_opencode_native else "✗"
        wired_label = "ready" if s.fully_wired else "incomplete"
        typer.echo(f"{e.name}  [{e.source}, {wired_label}]")
        typer.echo(f"  binary on PATH:        {path_glyph}  ({e.binary})")
        typer.echo(f"  in claude-code native: {claude_glyph}  ({e.name} in <workspace>/.mcp.json)")
        typer.echo(f"  in opencode native:    {opencode_glyph}  (vexis-{e.name} in <workspace>/opencode.json)")
        cmdline = e.command + (" " + " ".join(e.args) if e.args else "")
        typer.echo(f"  command:               {cmdline}")
        if e.env:
            for k, v in e.env.items():
                typer.echo(f"  env:                   {k}={v}")
        typer.echo()


@mcp_app.command("refresh")
def mcp_refresh() -> None:
    """Rewrite both per-brain native files from the current yaml +
    built-in detectors. Use after editing
    ~/.vexis/mcp-servers.yaml by hand."""
    from vexis_agent.daemon.mcp import refresh_workspace

    result = refresh_workspace()
    typer.echo(f"Refreshed {result.server_count} server(s) into:")
    for p in result.refreshed_paths:
        typer.echo(f"  {p}")


if __name__ == "__main__":
    app()

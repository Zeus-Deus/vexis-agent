"""systemd user-unit lifecycle for vexis-agent.

Renders the unit at install time so the actual venv python (sys.executable
of the running ``vexis-agent`` process) and the resolved ``VEXIS_HOME`` get
baked into the unit. A static ``.service`` file shipped in the repo
would point at the wrong python on every machine that doesn't match
the dev's pipx layout.

Decision D6 in ``.plans/packaging-implementation-plan.md`` §2.

Public API:

  render_user_unit(...)   → str  — pure renderer, used by tests too
  render_user_slice(...)  → str  — pure renderer for vexis-agent.slice
  install_user_unit(...)  → Path — writes unit + slice, daemon-reloads
  uninstall_user_unit()   → bool — stops, disables, removes, reloads

Functions that shell out (install / uninstall / start / stop / status /
logs) live here too so the Typer subcommands in ``vexis_agent.cli``
stay a thin presentation layer.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

SERVICE_NAME = "vexis-agent"
UNIT_FILENAME = f"{SERVICE_NAME}.service"
DESCRIPTION = "vexis-agent — Telegram bot + agent CLI bridge"

# Aggregate memory blast-radius cap for the bot + every subagent scope
# (2026-06-12 freeze fix). The bot service and each per-subagent
# ``systemd-run --scope`` both live under this slice, so its MemoryMax
# bounds their *combined* footprint for host protection while each scope
# still self-limits. Deliberately NO MemoryHigh anywhere — a hard cap
# OOM-kills the single biggest offender (a runaway tool) instead of
# throttle-freezing the whole cgroup, which is exactly the bug that froze
# the bot. See docs/memory-isolation.md.
#
# SLICE_NAME must equal ``core.brain._memory_scope.VEXIS_SLICE`` (the
# value the scope wrapper passes to ``--slice``); the equality is
# drift-guarded by tests/test_memory_scope.py. Defined independently here
# rather than imported to keep this module free of the heavy brain
# package import.
SLICE_NAME = "vexis-agent.slice"
SLICE_DESCRIPTION = "vexis-agent — bot + subagent scopes (aggregate memory cap)"
# 5G on the 7.5 GB home box leaves ~2.5 GB for the docker AI stack + OS.
# Tune here (or override per-machine by editing the installed slice file).
SLICE_MEMORY_MAX = "5G"
SLICE_MEMORY_SWAP_MAX = "1G"

# Legacy hand-rolled drop-in (added 2026-06-09, superseded 2026-06-12).
# Its ``MemoryHigh`` is what throttle-froze the bot; the installer removes
# it so the shipped slice policy is authoritative.
_LEGACY_MEMORY_DROPIN = "50-memory-limit.conf"


def user_unit_dir() -> Path:
    """``~/.config/systemd/user`` (or ``$XDG_CONFIG_HOME/systemd/user``)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "systemd" / "user"


def user_unit_path() -> Path:
    """Full path to the installed user unit file."""
    return user_unit_dir() / UNIT_FILENAME


def slice_unit_path() -> Path:
    """Full path to the installed ``vexis-agent.slice`` unit file."""
    return user_unit_dir() / SLICE_NAME


def render_user_unit(
    *,
    python_path: Path | str,
    vexis_home: Path | str,
    description: str = DESCRIPTION,
) -> str:
    """Render a systemd user unit body for ``vexis-agent run``.

    Pure function — no filesystem side-effects, no subprocess. The
    rendered string is what ``install_user_unit`` writes; tests can
    snapshot this directly.

    ``python_path`` should be the absolute path to the interpreter that
    has the ``vexis_agent`` package installed (usually the pipx venv
    python). ``vexis_home`` becomes both the WorkingDirectory and the
    ``VEXIS_HOME`` env var so the daemon resolves state under the same
    root the install knew about — even if the user later edits their
    shell to set a different value, the service stays pinned.
    """
    python = str(python_path)
    home = str(vexis_home)
    return (
        "[Unit]\n"
        f"Description={description}\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        # Run the bot under the shared slice so its aggregate MemoryMax
        # bounds the bot + all per-subagent scopes together. No memory
        # limit on the service itself: the slice owns host protection and
        # the bot's own RSS is tiny. Changing Slice= needs a service
        # *restart* (not just daemon-reload) to take effect.
        f"Slice={SLICE_NAME}\n"
        f"ExecStart={python} -m vexis_agent.cli run\n"
        f"WorkingDirectory={home}\n"
        f"Environment=VEXIS_HOME={home}\n"
        # PATH-with-~/.local/bin: brain CLIs (claude, opencode) get
        # installed under ``~/.local/bin/`` by every common path —
        # npm-global, pipx, pip --user, the official claude installer.
        # systemd's default user-unit PATH is just
        # ``/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin``, so
        # the daemon's ``shutil.which("claude")`` returns None and
        # the brain assertion in core.config aborts startup.
        # Surfaced in v0.1.1 right after the dotenv fix unblocked the
        # daemon — same ``crash-restart-loop`` failure mode, different
        # missing piece. ``%h`` is systemd's user-home specifier; in
        # ``systemctl --user`` mode it expands to the invoking user's
        # ``$HOME`` at unit-load time, so this works for any user
        # installing under any home dir.
        "Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin\n"
        # Defense-in-depth alongside core.config.load_dotenv: systemd
        # itself loads VEXIS_HOME/.env into the unit's environment so
        # even if the in-process dotenv path ever regresses, the
        # daemon still gets TELEGRAM_BOT_TOKEN, etc. The leading `-`
        # makes a missing file non-fatal — a fresh box where setup
        # hasn't run yet shouldn't fail to start the unit; the daemon
        # can surface its own clearer error.
        f"EnvironmentFile=-{home}/.env\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "StandardOutput=journal\n"
        "StandardError=journal\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def render_user_slice(
    *,
    description: str = SLICE_DESCRIPTION,
    memory_max: str = SLICE_MEMORY_MAX,
    memory_swap_max: str = SLICE_MEMORY_SWAP_MAX,
) -> str:
    """Render the ``vexis-agent.slice`` body (aggregate memory cap).

    Pure function — no side effects, used by tests too. Carries only a
    hard ``MemoryMax`` (+ swap cap), never ``MemoryHigh``: the 2026-06-12
    freeze was a ``memory.high`` throttle that parked every task in the
    shared cgroup in uninterruptible sleep. A hard cap instead OOM-kills
    the single biggest offender — a runaway subagent tool — and leaves
    the bot running. See docs/memory-isolation.md.
    """
    return (
        "[Unit]\n"
        f"Description={description}\n"
        "\n"
        "[Slice]\n"
        "# No MemoryHigh on purpose — see module docstring / the\n"
        "# 2026-06-12 freeze. Hard cap OOM-kills the offender instead\n"
        "# of throttle-freezing the whole cgroup.\n"
        f"MemoryMax={memory_max}\n"
        f"MemorySwapMax={memory_swap_max}\n"
    )


def _remove_legacy_memory_dropin() -> None:
    """Delete the superseded hand-rolled ``50-memory-limit.conf`` drop-in.

    Best-effort: its ``MemoryHigh`` would re-introduce the throttle-freeze
    on top of the shipped slice policy. Only removes the one file we know
    about (leaving any other user drop-ins untouched) and prunes the
    ``.service.d`` dir if it ends up empty.
    """
    dropin = user_unit_dir() / f"{UNIT_FILENAME}.d" / _LEGACY_MEMORY_DROPIN
    if not dropin.exists():
        return
    try:
        dropin.unlink()
        log.info(
            "Removed legacy memory drop-in %s (superseded by %s)",
            dropin, SLICE_NAME,
        )
        parent = dropin.parent
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError as exc:
        log.warning("Could not remove legacy drop-in %s: %s", dropin, exc)


def install_user_unit(
    *,
    python_path: Path | str | None = None,
    vexis_home: Path | str | None = None,
) -> Path:
    """Render and install the user unit, then ``daemon-reload``.

    Defaults: ``python_path = sys.executable`` (the interpreter running
    this process — the pipx venv python when invoked through the
    console script), ``vexis_home = vexis_dir()`` (resolved with
    VEXIS_HOME applied).

    Returns the path the unit was written to.
    """
    from vexis_agent.core.paths import vexis_dir

    python = Path(python_path) if python_path is not None else Path(sys.executable)
    home = Path(vexis_home) if vexis_home is not None else vexis_dir()

    body = render_user_unit(python_path=python, vexis_home=home)

    unit_dir = user_unit_dir()
    unit_dir.mkdir(parents=True, exist_ok=True)
    target = user_unit_path()
    target.write_text(body, encoding="utf-8")

    # Ship the aggregate-cap slice the unit's Slice= references and the
    # per-subagent scopes nest under. Written every install so policy
    # tweaks (cap sizes) ship with upgrades.
    slice_unit_path().write_text(render_user_slice(), encoding="utf-8")

    # Drop the superseded hand-rolled drop-in so its MemoryHigh can't
    # re-introduce the throttle-freeze on top of the new slice policy.
    _remove_legacy_memory_dropin()

    # daemon-reload tells systemd to re-scan unit files. Without it,
    # "systemctl --user start" can race against a stale view of the
    # filesystem on first install. Tolerate failure here — a missing
    # systemctl (containers, WSL without --user wired up) shouldn't
    # block the file write; ``vexis-agent doctor`` surfaces the issue.
    _systemctl(["daemon-reload"], check=False)

    return target


def uninstall_user_unit() -> bool:
    """Stop, disable, remove, and ``daemon-reload``.

    Returns ``True`` if the unit file was present (and was removed),
    ``False`` if it was already missing. Does NOT raise on a non-zero
    systemctl return — best-effort cleanup, the file removal is the
    authoritative bit.
    """
    target = user_unit_path()
    existed = target.exists()

    # Stop first so we don't leave a running daemon orphaned from its
    # unit file. Best-effort: if the unit isn't loaded, stop returns
    # non-zero and we move on.
    _systemctl(["stop", UNIT_FILENAME], check=False)
    _systemctl(["disable", UNIT_FILENAME], check=False)

    if existed:
        target.unlink()

    # Remove the slice unit too (best-effort) so uninstall leaves no
    # orphaned vexis-agent.slice behind.
    slice_target = slice_unit_path()
    if slice_target.exists():
        try:
            slice_target.unlink()
        except OSError as exc:
            log.warning("Could not remove slice unit %s: %s", slice_target, exc)

    _systemctl(["daemon-reload"], check=False)
    return existed


def _systemctl(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run ``systemctl --user <args>`` with stdout/stderr captured.

    Raises ``FileNotFoundError`` (the natural one from subprocess) if
    systemctl isn't on PATH — caller can decide whether to swallow it.
    """
    cmd = ["systemctl", "--user", *args]
    log.debug("running %s", " ".join(cmd))
    return subprocess.run(
        cmd,
        check=check,
        capture_output=True,
        text=True,
    )


def systemctl_available() -> bool:
    """Cheap probe used by ``vexis-agent doctor`` — is systemctl
    callable at all? Doesn't validate the user-bus is reachable; that
    only fires on the first real call."""
    return shutil.which("systemctl") is not None


def enable_and_start() -> subprocess.CompletedProcess[str]:
    """``systemctl --user enable --now`` — both enable at boot AND
    start the unit immediately. The wizard's "fully one-shot install"
    path calls this so a single ``curl … | bash`` ends with a
    daemon that's running now and survives reboots, with no follow-up
    systemctl commands needed.

    Raises CalledProcessError on failure (e.g. user-bus unavailable
    in containers / WSL); the wizard catches and downgrades to a
    "start it manually" hint."""
    return _systemctl(["enable", "--now", UNIT_FILENAME])


def start() -> subprocess.CompletedProcess[str]:
    """Start the user service. Raises CalledProcessError on failure."""
    return _systemctl(["start", UNIT_FILENAME])


def stop() -> subprocess.CompletedProcess[str]:
    """Stop the user service. Best-effort — returns even on non-zero."""
    return _systemctl(["stop", UNIT_FILENAME], check=False)


def restart() -> subprocess.CompletedProcess[str]:
    return _systemctl(["restart", UNIT_FILENAME])


def status() -> subprocess.CompletedProcess[str]:
    """Status output. Uses ``--no-pager`` so we don't accidentally invoke
    a pager when called from a non-tty Typer context."""
    return _systemctl(["status", UNIT_FILENAME, "--no-pager"], check=False)


def logs(*, follow: bool = False, lines: int = 200) -> int:
    """journalctl --user-unit ... — exec'd directly so output streams
    to the user's terminal in real time (capture would defeat -f).
    Returns the exit code."""
    cmd = [
        "journalctl",
        "--user-unit",
        UNIT_FILENAME,
        "-n",
        str(lines),
    ]
    if follow:
        cmd.append("-f")
    return subprocess.call(cmd)

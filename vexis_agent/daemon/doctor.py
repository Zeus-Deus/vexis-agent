"""``vexis-agent doctor`` — diagnose installation and config.

Sequence of independent checks. Each returns a CheckResult with status
+ remediation hint. The CLI prints a green ✓ / red ✗ line per check
and exits 0 if all required checks pass, 1 otherwise. Optional checks
(linger, service installed, telegram-token roundtrip, tailscale,
workspace state) emit warnings but don't fail the run.

Phase 5d adds checks for the soft dependencies the daemon now treats
as warn-and-continue: tailscale (dashboard reachability) and the
workspace tree (CLAUDE.md, memories/, skills/). The wrapper-on-PATH
check confirms pipx-installed users have the dispatch console scripts
the brain prompt assumes.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

import yaml

from vexis_agent.core.paths import vexis_dir
from vexis_agent.daemon.systemd import (
    UNIT_FILENAME,
    systemctl_available,
    user_unit_path,
)


class Status(Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str = ""
    remediation: str = ""


def check_python_version() -> CheckResult:
    if sys.version_info >= (3, 11):
        return CheckResult("Python ≥ 3.11", Status.OK, sys.version.split()[0])
    return CheckResult(
        "Python ≥ 3.11",
        Status.FAIL,
        sys.version.split()[0],
        "Upgrade Python to 3.11 or newer; pyproject.toml requires it.",
    )


def check_config_yaml() -> CheckResult:
    """``~/.vexis/config.yaml`` (or ``$VEXIS_HOME/config.yaml``) exists
    and parses as YAML. Missing-file is treated as a warning (the
    daemon falls back to defaults), not a hard fail."""
    path = vexis_dir() / "config.yaml"
    if not path.exists():
        return CheckResult(
            "config.yaml present",
            Status.WARN,
            f"{path} not found",
            "Run 'vexis-agent setup' (Phase 4) or copy config.example.yaml.",
        )
    try:
        yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return CheckResult(
            "config.yaml parses",
            Status.FAIL,
            f"{path}: {exc.__class__.__name__}",
            "Fix the YAML syntax error or restore from config.yaml.bak.",
        )
    return CheckResult("config.yaml parses", Status.OK, str(path))


def check_secrets() -> CheckResult:
    """``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_ALLOWED_USER_ID`` reachable
    via env or ``$VEXIS_HOME/.env``. We don't actually load .env here
    (avoid a side-effect on the parent shell); we just confirm that
    one of the two surfaces has a value."""
    env_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    env_user = os.environ.get("TELEGRAM_ALLOWED_USER_ID")

    dotenv = vexis_dir() / ".env"
    file_has_token = False
    file_has_user = False
    if dotenv.exists():
        try:
            text = dotenv.read_text(encoding="utf-8")
        except OSError:
            text = ""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, _ = stripped.partition("=")
            key = key.strip()
            if key == "TELEGRAM_BOT_TOKEN":
                file_has_token = True
            elif key == "TELEGRAM_ALLOWED_USER_ID":
                file_has_user = True

    has_token = bool(env_token) or file_has_token
    has_user = bool(env_user) or file_has_user

    if has_token and has_user:
        return CheckResult("Telegram secrets set", Status.OK)
    missing = []
    if not has_token:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not has_user:
        missing.append("TELEGRAM_ALLOWED_USER_ID")
    return CheckResult(
        "Telegram secrets set",
        Status.FAIL,
        f"missing: {', '.join(missing)}",
        f"Add the missing key(s) to {dotenv} or export them in your shell.",
    )


def check_brain_cli() -> CheckResult:
    """The configured brain's CLI is on PATH. Reads brain.kind from
    ``$VEXIS_HOME/config.yaml`` (default ``claude-code``)."""
    brain_kind = "claude-code"
    cfg = vexis_dir() / "config.yaml"
    if cfg.exists():
        try:
            data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            brain_kind = (data.get("brain") or {}).get("kind") or brain_kind
        except (yaml.YAMLError, OSError):
            pass

    binary = {"claude-code": "claude", "opencode": "opencode", "null": None}.get(
        brain_kind, "claude"
    )
    if binary is None:
        return CheckResult(
            f"Brain CLI ({brain_kind})",
            Status.OK,
            "null brain — no CLI required",
        )
    found = shutil.which(binary)
    if found:
        return CheckResult(f"Brain CLI ({brain_kind})", Status.OK, found)
    install_hint = {
        "claude-code": "Install: see https://docs.anthropic.com/claude/claude-code",
        "opencode": "Install: curl -fsSL https://opencode.ai/install | bash",
    }.get(brain_kind, "")
    return CheckResult(
        f"Brain CLI ({brain_kind})",
        Status.FAIL,
        f"{binary} not on PATH",
        install_hint,
    )


def check_systemctl() -> CheckResult:
    """systemctl available — needed for ``vexis-agent service`` commands."""
    if systemctl_available():
        return CheckResult("systemctl on PATH", Status.OK)
    return CheckResult(
        "systemctl on PATH",
        Status.WARN,
        "not found",
        "systemd is needed for 'vexis-agent service' commands. "
        "Container or non-systemd host? Run the daemon in foreground via 'vexis-agent run'.",
    )


def check_linger() -> CheckResult:
    """User session linger so the service runs without an active login.
    Optional — emitted as WARN when off because the user may run vexis
    foreground only."""
    if not shutil.which("loginctl"):
        return CheckResult(
            "User linger enabled",
            Status.WARN,
            "loginctl not found",
            "Install loginctl (systemd-logind) to enable persistent user services.",
        )
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if not user:
        return CheckResult(
            "User linger enabled",
            Status.WARN,
            "couldn't determine current user",
        )
    try:
        result = subprocess.run(
            ["loginctl", "show-user", user, "--property=Linger"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return CheckResult(
            "User linger enabled",
            Status.WARN,
            "loginctl call failed",
        )
    if "Linger=yes" in result.stdout:
        return CheckResult("User linger enabled", Status.OK)
    return CheckResult(
        "User linger enabled",
        Status.WARN,
        "Linger=no",
        f"Run 'sudo loginctl enable-linger {user}' so the service persists "
        "across logouts.",
    )


def check_service_installed() -> CheckResult:
    """Informational: is the systemd user unit installed? Doesn't fail
    if missing — the unit lands on first ``vexis-agent service install``."""
    path = user_unit_path()
    if path.exists():
        return CheckResult("Service unit installed", Status.OK, str(path))
    return CheckResult(
        "Service unit installed",
        Status.WARN,
        f"{path} not found",
        f"Run 'vexis-agent service install' to create {UNIT_FILENAME}.",
    )


def check_tailscale() -> CheckResult:
    """Tailscale is soft — dashboard works on localhost without it. We
    surface the state so users know whether their phone can reach the
    dashboard and whether the live-stream tools have a tunnel.

    Three failure modes:
      * Not installed       → WARN with install link.
      * Installed, logged out → WARN with login hint.
      * Installed + logged in, but operator delegation missing
        (the gotcha that cost the maintainer a debugging round-trip
         on the first public install) → WARN with the exact two
         commands needed to fix it. Tailscale Serve — which the
         daemon's /dashboard URL relies on for tailnet-reachable
         URLs — refuses to write its config without root unless
         the operator user has been delegated. ``tailscale debug
         prefs`` exposes ``OperatorUser`` so we can check
         non-invasively.
    """
    if not shutil.which("tailscale"):
        return CheckResult(
            "Tailscale",
            Status.WARN,
            "not installed",
            "Optional. Install from https://tailscale.com/download "
            "and run 'tailscale up' to make the dashboard reachable "
            "from your phone.",
        )
    try:
        out = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return CheckResult(
            "Tailscale",
            Status.WARN,
            f"status probe failed: {exc}",
        )
    if out.returncode != 0:
        return CheckResult(
            "Tailscale",
            Status.WARN,
            "not logged in",
            "Run 'tailscale up' to log in. Without this the dashboard "
            "is localhost-only.",
        )

    operator_warning = _check_tailscale_operator()
    if operator_warning is not None:
        return operator_warning

    return CheckResult("Tailscale", Status.OK, "up + logged in")


def _check_tailscale_operator() -> CheckResult | None:
    """Probe whether the Tailscale operator is set to the user
    running this process.

    Returns ``None`` if the check passes (caller continues with the
    happy-path OK), or a ``CheckResult`` with the WARN + fix advice
    if the operator delegation is missing or pointed at someone else.

    Tailscale Serve — the feature the daemon uses to publish the
    dashboard at ``https://<host>.<tailnet>.ts.net`` — refuses to
    write its config without root, UNLESS the user has been
    delegated as the tailscale operator via
    ``sudo tailscale set --operator=$USER``. Without that, the
    daemon's web_server logs a warning at boot and falls back to a
    localhost-only dashboard URL.

    This check is best-effort: if ``tailscale debug prefs`` is
    unavailable (older Tailscale, daemon not responding) or returns
    something we can't parse, we silently return None — better to
    say the check passed than to bother users with a false alarm
    when we can't actually tell.
    """
    import getpass
    import json

    try:
        prefs_run = subprocess.run(
            ["tailscale", "debug", "prefs"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if prefs_run.returncode != 0:
        return None

    try:
        prefs = json.loads(prefs_run.stdout)
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(prefs, dict):
        return None

    operator = (prefs.get("OperatorUser") or "").strip()
    try:
        current_user = getpass.getuser()
    except (KeyError, OSError):
        return None  # can't determine current user → can't compare

    if operator == current_user:
        return None

    fix = (
        "Tailscale Serve (which publishes the dashboard URL to your\n"
        "tailnet) refuses to write its config without root unless\n"
        "the operator user is delegated. One-time fix:\n"
        f"  sudo tailscale set --operator={current_user}\n"
        "  systemctl --user restart vexis-agent\n"
        "Until you run these two commands, /dashboard returns a\n"
        "localhost-only URL that won't resolve from your phone or\n"
        "any other device on the tailnet."
    )
    if not operator:
        return CheckResult(
            "Tailscale",
            Status.WARN,
            "up, but operator not set",
            fix,
        )
    return CheckResult(
        "Tailscale",
        Status.WARN,
        f"operator is '{operator}', expected '{current_user}'",
        fix,
    )


def check_workspace() -> CheckResult:
    """Workspace tree exists and has the expected layout. The setup
    wizard creates this; missing here means setup either wasn't run
    or the user blew the directory away."""
    raw = os.environ.get("VEXIS_WORKSPACE")
    workspace = Path(raw).expanduser() if raw else Path.home() / "vexis-workspace"
    if not workspace.exists():
        return CheckResult(
            "Workspace",
            Status.FAIL,
            f"{workspace} missing",
            f"Run 'vexis-agent setup' to create {workspace} with the "
            "expected memories/ + skills/ tree.",
        )
    missing = [
        sub for sub in ("memories", "skills") if not (workspace / sub).is_dir()
    ]
    claude_md = workspace / "CLAUDE.md"
    if missing or not claude_md.exists():
        details = []
        if missing:
            details.append("missing dirs: " + ", ".join(missing))
        if not claude_md.exists():
            details.append("CLAUDE.md absent")
        return CheckResult(
            "Workspace",
            Status.WARN,
            "; ".join(details),
            "Run 'vexis-agent setup' to refresh the workspace template.",
        )
    return CheckResult("Workspace", Status.OK, str(workspace))


def check_compositor() -> CheckResult:
    """Detect the active Wayland compositor (or none). Used to scope
    install hints in the doctor output: a Hyprland user gets
    Hyprland-specific install commands; a sway user gets sway hints;
    a non-Wayland user just gets "desktop control unavailable here."
    """
    session = os.environ.get("XDG_SESSION_TYPE", "")
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "")
    hypr_signature = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if session != "wayland":
        return CheckResult(
            "Compositor",
            Status.WARN,
            f"non-Wayland session ({session or 'unset'})",
            "vexis runs on Wayland only for desktop control. Telegram chat "
            "still works on any session; tools needing screenshots / "
            "typing / clicking will fail.",
        )
    if hypr_signature or "hyprland" in desktop.lower():
        return CheckResult("Compositor", Status.OK, "Hyprland (Wayland)")
    return CheckResult(
        "Compositor",
        Status.WARN,
        f"Wayland compositor: {desktop or 'unknown'} (not Hyprland)",
        "vexis is Hyprland-targeted. wtype/grim work on most Wayland "
        "compositors; hyprctl is Hyprland-only — workspace/window "
        "dispatches will be no-ops elsewhere.",
    )


def check_feature_tools() -> CheckResult:
    """Per-feature dependency rollup. Surfaces which feature groups
    are usable on this install and which need binaries the user
    hasn't installed. WARN — daemon runs regardless."""
    feature_groups: dict[str, tuple[str, ...]] = {
        "voice notes": ("voxtype", "ffmpeg"),
        "desktop control": ("hyprctl", "wtype", "ydotool", "grim"),
        "shell helpers": ("jq",),
    }
    available: list[str] = []
    degraded: list[str] = []
    for feature, tools in feature_groups.items():
        missing = [t for t in tools if shutil.which(t) is None]
        if missing:
            degraded.append(f"{feature} (missing: {', '.join(missing)})")
        else:
            available.append(feature)
    if not degraded:
        return CheckResult(
            "Feature deps",
            Status.OK,
            f"all groups available ({len(available)})",
        )
    detail = "; ".join(degraded)
    return CheckResult(
        "Feature deps",
        Status.WARN,
        detail,
        "Install the missing binaries to enable the feature, or ignore "
        "if you don't need it. Telegram chat + brain dispatch don't "
        "depend on any of these.",
    )


def check_dispatch_wrappers() -> CheckResult:
    """The vexis-* dispatch wrappers are exposed as console scripts
    after pipx install. If they're not on PATH, the brain prompt's
    examples won't resolve — almost always means the wheel wasn't
    installed (user is running from source without pip install -e .)
    or PATH is mis-set."""
    expected = (
        "vexis-bg",
        "vexis-browse",
        "vexis-desktop",
        "vexis-dispatch",
        "vexis-click",
        "vexis-key",
        "vexis-type",
        "vexis-stream",
        "vexis-mem",
        "vexis-skill",
        "vexis-focus-wait",
    )
    missing = [name for name in expected if shutil.which(name) is None]
    if not missing:
        return CheckResult(
            "Dispatch wrappers",
            Status.OK,
            f"{len(expected)} on PATH",
        )
    return CheckResult(
        "Dispatch wrappers",
        Status.WARN,
        f"missing: {', '.join(missing)}",
        "These ship as console scripts when vexis-agent is pipx-installed. "
        "Check 'pipx list' or reinstall with 'pipx install --force'.",
    )


# Ordered list — the CLI iterates this. Tests use it too so the
# wire-up doesn't drift.
DEFAULT_CHECKS: list[Callable[[], CheckResult]] = [
    check_python_version,
    check_config_yaml,
    check_secrets,
    check_brain_cli,
    check_workspace,
    check_dispatch_wrappers,
    check_compositor,
    check_feature_tools,
    check_tailscale,
    check_systemctl,
    check_linger,
    check_service_installed,
]


def run_all(checks: Optional[list[Callable[[], CheckResult]]] = None) -> list[CheckResult]:
    """Run every check. Pure (well — side effects in subprocess calls)
    so callers can capture the list and decide how to render."""
    return [c() for c in (checks or DEFAULT_CHECKS)]


def overall_exit_code(results: list[CheckResult]) -> int:
    """0 if no FAIL, 1 otherwise. WARN doesn't influence the exit code —
    matches plan §6.5 ("Exit code 0 if all pass")."""
    return 1 if any(r.status is Status.FAIL for r in results) else 0


def format_results(results: list[CheckResult], *, color: bool = True) -> str:
    """Human-readable rendering. Color is opt-in so tests can assert
    against a deterministic plain-text form."""
    lines = []
    glyphs = {
        Status.OK: ("✓", "\033[32m"),
        Status.WARN: ("!", "\033[33m"),
        Status.FAIL: ("✗", "\033[31m"),
    }
    reset = "\033[0m"
    for r in results:
        glyph, code = glyphs[r.status]
        prefix = f"{code}{glyph}{reset}" if color else glyph
        line = f"{prefix} {r.name}"
        if r.detail:
            line += f"  ({r.detail})"
        lines.append(line)
        if r.remediation and r.status is not Status.OK:
            lines.append(f"     → {r.remediation}")
    return "\n".join(lines)

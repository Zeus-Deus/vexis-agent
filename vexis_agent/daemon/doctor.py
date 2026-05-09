"""``vexis-agent doctor`` — diagnose installation and config.

Sequence of independent checks. Each returns a CheckResult with status
+ remediation hint. The CLI prints a green ✓ / red ✗ line per check
and exits 0 if all required checks pass, 1 otherwise. Optional checks
(linger, service installed, telegram-token roundtrip) emit warnings
but don't fail the run.

Plan §6.5 listing.
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


# Ordered list — the CLI iterates this. Tests use it too so the
# wire-up doesn't drift.
DEFAULT_CHECKS: list[Callable[[], CheckResult]] = [
    check_python_version,
    check_config_yaml,
    check_secrets,
    check_brain_cli,
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

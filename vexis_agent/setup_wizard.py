"""``vexis-agent setup`` — interactive first-run wizard.

Plan §7.3. Sequence:

  1. TTY check (refuse non-tty unless --reset overrides).
  2. Ensure $VEXIS_HOME exists.
  3. Copy shipped config.example.yaml → $VEXIS_HOME/config.yaml (skip if present).
  4. Copy shipped dotenv.example → $VEXIS_HOME/.env (skip if present), mode 0600.
  5. Prompt for TELEGRAM_BOT_TOKEN; write to .env.
  6. Prompt for TELEGRAM_ALLOWED_USER_ID; write to .env.
  7. Prompt: install systemd service?  → optionally call install_user_unit.
  8. Print final summary.

``--reset`` archives an existing config.yaml + .env to *.bak.<utc>
before re-running. Existing ~/.vexis state (curator, learning,
daemon.pid, goals.json, …) is left untouched — the wizard never
deletes user data.

The module is importable without prompting so tests can drive
individual steps with mocked inputs. ``run_setup()`` is the public
entry; the Typer command in cli.py just calls it.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Callable, Optional

from vexis_agent.core.paths import vexis_dir

log = logging.getLogger(__name__)


CONFIG_FILENAME = "config.yaml"
DOTENV_FILENAME = ".env"

# Resource paths inside the wheel (vexis_agent.data).
_CONFIG_TEMPLATE = "config.example.yaml"
_DOTENV_TEMPLATE = "dotenv.example"


class SetupAborted(RuntimeError):
    """Raised when the wizard refuses to run (non-TTY, etc.)."""


# ──────────────────────────────────────────────────────────────────────
# Step helpers — each is small + testable.
# ──────────────────────────────────────────────────────────────────────


def require_tty(*, stdin: Optional[object] = None) -> None:
    """Refuse non-TTY runs. Setup is interactive by design — env-only
    deployments can hand-edit ~/.vexis/config.yaml + .env using the
    shipped examples (or cat-pipe them into place)."""
    s = stdin if stdin is not None else sys.stdin
    is_tty = getattr(s, "isatty", lambda: False)()
    if not is_tty:
        raise SetupAborted(
            "vexis-agent setup is interactive — refusing to run on a non-TTY "
            "stdin. Hand-edit $VEXIS_HOME/config.yaml + $VEXIS_HOME/.env using "
            "the shipped examples instead."
        )


def read_template(name: str) -> str:
    """Read a shipped template via importlib.resources. Works the same
    way for editable installs and pipx wheels."""
    return resources.files("vexis_agent.data").joinpath(name).read_text(encoding="utf-8")


def ensure_config_yaml(home: Path, *, force: bool = False) -> Path:
    """Create $VEXIS_HOME/config.yaml from the shipped template if it
    doesn't exist (or if force=True). Returns the path."""
    target = home / CONFIG_FILENAME
    if target.exists() and not force:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(read_template(_CONFIG_TEMPLATE), encoding="utf-8")
    return target


def ensure_dotenv(home: Path, *, force: bool = False) -> Path:
    """Create $VEXIS_HOME/.env from the shipped template if missing.
    Sets mode 0600 — secrets aren't world-readable. Returns the path."""
    target = home / DOTENV_FILENAME
    if target.exists() and not force:
        # Tighten perms even on pre-existing files; a re-run shouldn't
        # leave a 0644 .env behind.
        try:
            os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(read_template(_DOTENV_TEMPLATE), encoding="utf-8")
    os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    return target


def archive_existing(path: Path) -> Optional[Path]:
    """Move ``path`` to ``<path>.bak.<utc>`` if it exists.

    Used by ``--reset`` so a re-run doesn't silently overwrite a
    working config. Returns the archive path (or None if nothing to do).
    """
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = path.with_name(f"{path.name}.bak.{stamp}")
    shutil.move(str(path), str(archive))
    return archive


def update_env_value(env_path: Path, key: str, value: str) -> None:
    """Set ``KEY=VALUE`` in a dotenv file, preserving comments + order
    of the surrounding lines.

    If the key already appears, its line is replaced. Otherwise the
    line is appended at the end. Whitespace around ``=`` is normalized.
    Values are written verbatim — no quoting; the daemon's dotenv
    loader handles quoting on read.
    """
    text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    lines = text.splitlines()
    replaced = False
    new_line = f"{key}={value}"
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        existing_key = stripped.split("=", 1)[0].strip()
        if existing_key == key:
            lines[i] = new_line
            replaced = True
            break
    if not replaced:
        lines.append(new_line)
    # Preserve trailing newline so dotenv parsers don't misread the
    # final line.
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────
# Orchestration
# ──────────────────────────────────────────────────────────────────────


@dataclass
class SetupResult:
    """Summary of a wizard run. Useful for tests + the post-run banner."""

    home: Path
    config_path: Path
    dotenv_path: Path
    archived_config: Optional[Path] = None
    archived_dotenv: Optional[Path] = None
    service_installed: bool = False


# Prompt provider: () -> str. Tests inject a callable that returns
# canned answers; the CLI uses ``input()`` (or click.prompt for masked).
PromptFn = Callable[[str, bool], str]


def _default_prompt(message: str, secret: bool) -> str:
    """Default prompt: prints ``message`` and reads from stdin.
    ``secret=True`` could mask input via getpass; we keep it visible
    here because Telegram tokens are pasted in full, and getpass
    surprises users who can't see typos.
    """
    sys.stdout.write(message)
    sys.stdout.flush()
    raw = sys.stdin.readline()
    return raw.rstrip("\n")


def run_setup(
    *,
    reset: bool = False,
    install_service: Optional[bool] = None,
    prompt: PromptFn = _default_prompt,
    confirm: Optional[Callable[[str], bool]] = None,
    require_interactive: bool = True,
) -> SetupResult:
    """Drive the wizard. Returns a SetupResult.

    Parameters mostly exist for test injection:
      ``prompt`` — function taking ``(message, secret)`` and returning a
        line. Replace in tests with a closure returning canned values.
      ``confirm`` — function taking a yes/no question, returning bool.
      ``install_service`` — pre-decided answer to "install service?"
        (None = ask interactively).
      ``require_interactive`` — when False, skip the TTY check (used by
        ``--reset`` runs and tests).
    """
    if require_interactive:
        require_tty()

    home = vexis_dir()
    home.mkdir(parents=True, exist_ok=True)

    archived_config: Optional[Path] = None
    archived_dotenv: Optional[Path] = None
    if reset:
        archived_config = archive_existing(home / CONFIG_FILENAME)
        archived_dotenv = archive_existing(home / DOTENV_FILENAME)

    config_path = ensure_config_yaml(home)
    dotenv_path = ensure_dotenv(home)

    token = prompt("Telegram bot token (from @BotFather): ", True).strip()
    if token:
        update_env_value(dotenv_path, "TELEGRAM_BOT_TOKEN", token)

    user_id = prompt(
        "Allowed Telegram user ID (numeric — yours): ", False
    ).strip()
    if user_id:
        update_env_value(dotenv_path, "TELEGRAM_ALLOWED_USER_ID", user_id)

    service_installed = False
    decision = install_service
    if decision is None:
        confirm_fn = confirm if confirm is not None else _default_confirm
        decision = confirm_fn("Install the systemd user service now? [y/N] ")
    if decision:
        from vexis_agent.daemon.systemd import install_user_unit

        install_user_unit()
        service_installed = True

    return SetupResult(
        home=home,
        config_path=config_path,
        dotenv_path=dotenv_path,
        archived_config=archived_config,
        archived_dotenv=archived_dotenv,
        service_installed=service_installed,
    )


def _default_confirm(message: str) -> bool:
    sys.stdout.write(message)
    sys.stdout.flush()
    raw = sys.stdin.readline().strip().lower()
    return raw in {"y", "yes"}


def format_summary(result: SetupResult) -> str:
    """Final banner. Print this; don't bury the next-step instructions."""
    lines = [
        "vexis-agent setup complete.",
        f"  config:   {result.config_path}",
        f"  secrets:  {result.dotenv_path}  (mode 0600)",
    ]
    if result.archived_config:
        lines.append(f"  archived: {result.archived_config}")
    if result.archived_dotenv:
        lines.append(f"  archived: {result.archived_dotenv}")
    if result.service_installed:
        lines.append("")
        lines.append("Service installed. Enable + start with:")
        lines.append("  systemctl --user enable --now vexis-agent.service")
    else:
        lines.append("")
        lines.append("Next steps:")
        lines.append("  vexis-agent run                 # foreground")
        lines.append("  vexis-agent service install     # systemd user unit")
    return "\n".join(lines)

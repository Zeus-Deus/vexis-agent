"""``vexis-agent setup`` — interactive first-run wizard.

Plan §7.3 + Phase 5b expansion. Sequence:

  1. TTY check (refuse non-tty unless --reset overrides).
  2. Ensure $VEXIS_HOME exists; copy shipped templates if absent.
  3. Prompt for TELEGRAM_BOT_TOKEN + TELEGRAM_ALLOWED_USER_ID.
  4. Brain CLI check — verify configured brain.kind binary is on
     PATH; print install hint + offer to skip if missing.
  5. Workspace setup — mkdir $VEXIS_WORKSPACE, copy
     workspace CLAUDE.md template, symlink AGENTS.md (opencode).
  6. Tailscale soft-check — warn if not on PATH or not logged in.
  7. Optional: install systemd user unit.
  8. Print final tool-availability summary + next-step hints.

``--reset`` archives existing config.yaml + .env to *.bak.<utc>
before re-running. User state under ``$VEXIS_HOME`` (curator,
learning, daemon.pid, goals.json, …) is left untouched — the
wizard never deletes data.

The module is importable without prompting so tests can drive
individual steps with mocked inputs. ``run_setup()`` is the public
entry; the Typer command in cli.py just calls it.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
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
_WORKSPACE_CLAUDE_TEMPLATE = "workspace_CLAUDE.md.template"


# ──────────────────────────────────────────────────────────────────────
# ANSI color helpers — mirror hermes' style without adding a dep.
# ──────────────────────────────────────────────────────────────────────


def _color_supported() -> bool:
    """Best-guess: emit ANSI only if stdout is a tty AND TERM looks
    capable. Honors NO_COLOR (https://no-color.org/)."""
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    return os.environ.get("TERM", "") not in ("", "dumb")


_USE_COLOR = _color_supported()


def _c(code: str, text: str) -> str:
    return f"{code}{text}\033[0m" if _USE_COLOR else text


def _bold(text: str) -> str:    return _c("\033[1m", text)
def _cyan(text: str) -> str:    return _c("\033[36m", text)
def _green(text: str) -> str:   return _c("\033[32m", text)
def _yellow(text: str) -> str:  return _c("\033[33m", text)
def _red(text: str) -> str:     return _c("\033[31m", text)
def _dim(text: str) -> str:     return _c("\033[2m", text)


def section(title: str) -> None:
    """Section header. Cyan ◆ prefix, blank line above."""
    print()
    print(_bold(_cyan(f"◆ {title}")))


def ok(msg: str) -> None:
    print(f"  {_green('✓')} {msg}")


def warn(msg: str) -> None:
    print(f"  {_yellow('!')} {msg}")


def err(msg: str) -> None:
    print(f"  {_red('✗')} {msg}")


def info(msg: str) -> None:
    print(f"  {_dim('→')} {msg}")


# ──────────────────────────────────────────────────────────────────────
# Errors
# ──────────────────────────────────────────────────────────────────────


class SetupAborted(RuntimeError):
    """Raised when the wizard refuses to run (non-TTY, etc.)."""


# ──────────────────────────────────────────────────────────────────────
# Step helpers — each is small + testable.
# ──────────────────────────────────────────────────────────────────────


def require_tty(*, stdin: Optional[object] = None) -> None:
    """Refuse non-TTY runs. Setup is interactive by design."""
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
    target = home / CONFIG_FILENAME
    if target.exists() and not force:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(read_template(_CONFIG_TEMPLATE), encoding="utf-8")
    return target


def ensure_dotenv(home: Path, *, force: bool = False) -> Path:
    target = home / DOTENV_FILENAME
    if target.exists() and not force:
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
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = path.with_name(f"{path.name}.bak.{stamp}")
    shutil.move(str(path), str(archive))
    return archive


def update_env_value(env_path: Path, key: str, value: str) -> None:
    """Set ``KEY=VALUE`` in a dotenv, preserving comments + line order.

    Replaces if the key already appears; appends otherwise. Commented
    lines (``# KEY=…``) are NOT treated as definitions, so editable
    examples don't get clobbered.
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
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── Brain detection ───────────────────────────────────────────────


@dataclass(frozen=True)
class BrainCheck:
    """Result of probing whether the configured brain CLI is reachable."""

    kind: str
    binary: str            # "" for null brain
    found: bool
    install_hint: str      # one-liner to surface in the wizard


_BRAIN_INSTALL_HINTS: dict[str, tuple[str, str]] = {
    "claude-code": (
        "claude",
        "Install: see https://docs.anthropic.com/claude/claude-code, "
        "then run 'claude /login'.",
    ),
    "opencode": (
        "opencode",
        "Install: curl -fsSL https://opencode.ai/install | bash",
    ),
    "null": ("", ""),
}


def check_brain_cli(kind: str) -> BrainCheck:
    binary, hint = _BRAIN_INSTALL_HINTS.get(kind, ("claude", ""))
    if not binary:  # null brain
        return BrainCheck(kind=kind, binary="", found=True, install_hint="")
    return BrainCheck(
        kind=kind,
        binary=binary,
        found=shutil.which(binary) is not None,
        install_hint=hint,
    )


# ── Workspace setup ────────────────────────────────────────────────


def workspace_path() -> Path:
    """Resolve the user's vexis-workspace. Honors VEXIS_WORKSPACE env
    var; defaults to ~/vexis-workspace."""
    raw = os.environ.get("VEXIS_WORKSPACE")
    return Path(raw).expanduser() if raw else Path.home() / "vexis-workspace"


def ensure_workspace(ws: Path) -> Path:
    """mkdir -p the workspace, plus the gittable subdirs the daemon
    expects (memories/, skills/). Returns the path."""
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "memories").mkdir(exist_ok=True)
    (ws / "skills").mkdir(exist_ok=True)
    return ws


def ensure_workspace_claude_md(ws: Path) -> tuple[Path, bool]:
    """Drop the workspace CLAUDE.md template if missing.

    Returns (path, written) — written=True if we actually wrote the
    file, False if a user-edited copy was preserved.
    """
    target = ws / "CLAUDE.md"
    if target.exists():
        return target, False
    target.write_text(read_template(_WORKSPACE_CLAUDE_TEMPLATE), encoding="utf-8")
    return target, True


def ensure_agents_md_symlink(ws: Path, brain_kind: str) -> tuple[Optional[Path], str]:
    """Symlink ``<ws>/AGENTS.md → CLAUDE.md`` so opencode (and any
    future AGENTS.md-reading brain) finds the same content
    claude-code reads from CLAUDE.md.

    Only acts when brain.kind=opencode — claude-code doesn't need
    AGENTS.md, and creating one for users on claude-code would just
    be dead-symlink noise. Refuses to overwrite a real (non-symlink)
    AGENTS.md so a hand-maintained file isn't clobbered.

    Returns (path, status) where status ∈ {"created", "already_correct",
    "skipped_not_opencode", "refused_real_file", "replaced_wrong_target"}.
    """
    if brain_kind != "opencode":
        return None, "skipped_not_opencode"
    link = ws / "AGENTS.md"
    target_name = "CLAUDE.md"
    if link.is_symlink():
        if os.readlink(link) == target_name:
            return link, "already_correct"
        link.unlink()
        link.symlink_to(target_name)
        return link, "replaced_wrong_target"
    if link.exists():
        return link, "refused_real_file"
    link.symlink_to(target_name)
    return link, "created"


# ── MCP server detection + write ────────────────────────────────────


def _omarchy_kb_spec() -> Optional[dict]:
    """Detect omarchy-kb on PATH; return a minimal spec dict the
    write helper consumes, or None if not found."""
    if shutil.which("omarchy-kb") is None:
        return None
    return {
        "name": "omarchy-kb",
        "command": "omarchy-kb",
        "args": [],
        "env": {},
    }


# Built-in detect-and-wire MCP servers. Each entry is a callable
# that returns a spec dict if the server is locally usable, else None.
# Users extend this list by dropping entries into
# $VEXIS_HOME/mcp-servers.yaml — see _user_mcp_specs() below.
_MCP_DETECTORS: list[Callable[[], Optional[dict]]] = [_omarchy_kb_spec]


def _user_mcp_specs() -> list[dict]:
    """Read user-declared MCP servers from $VEXIS_HOME/mcp-servers.yaml.

    Schema (every key is optional except ``name`` + ``command``):

      servers:
        - name: peekaboo               # MCP server name (required)
          binary: peekaboo             # presence check (optional);
                                       # default: same as command's argv[0]
          command: npx                 # binary to invoke (required)
          args: ["-y", "@steipete/peekaboo"]
          env:
            PEEKABOO_AI_PROVIDERS: anthropic/claude-opus-4

    The wizard skips entries whose ``binary`` isn't on PATH so users
    can declare aspirational servers without having them installed
    yet. This is the canonical "vexis plugin" mechanism; see
    .plans/plugin-architecture-research.md for the design.

    Missing file → empty list (default state). Malformed file →
    warning + empty list (don't fail the whole wizard over YAML
    drift).
    """
    path = vexis_dir() / "mcp-servers.yaml"
    if not path.is_file():
        return []
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("could not parse %s: %s", path, exc)
        return []
    raw_servers = data.get("servers") or []
    if not isinstance(raw_servers, list):
        log.warning("%s: 'servers' must be a list", path)
        return []
    out: list[dict] = []
    for entry in raw_servers:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        command = entry.get("command")
        if not name or not command:
            log.warning(
                "%s: skipping entry missing 'name' or 'command': %r",
                path, entry,
            )
            continue
        # Presence check: prefer explicit binary field, else use the
        # command's basename. shutil.which honours PATH; entries
        # whose binary isn't reachable are filtered out so the
        # workspace MCP config doesn't reference dead invocations.
        binary = entry.get("binary") or command
        if shutil.which(binary) is None:
            log.info(
                "user mcp '%s' declared but %s not on PATH; skipping",
                name, binary,
            )
            continue
        out.append({
            "name": str(name),
            "command": str(command),
            "args": list(entry.get("args") or []),
            "env": dict(entry.get("env") or {}),
        })
    return out


def detect_mcp_servers() -> list[dict]:
    """Return spec dicts for every MCP server whose binary is on
    PATH today. Combines built-in detectors with user-declared
    entries from $VEXIS_HOME/mcp-servers.yaml. Empty list is a
    valid result — the workspace MCP config stays empty unless the
    user adds entries by hand."""
    out: list[dict] = []
    seen: set[str] = set()
    for detector in _MCP_DETECTORS:
        spec = detector()
        if spec is not None and spec["name"] not in seen:
            out.append(spec)
            seen.add(spec["name"])
    # User entries are appended; if a user declares an entry with the
    # same name as a built-in, the user's wins (matches the hermes
    # convention of "later sources override earlier ones").
    for spec in _user_mcp_specs():
        if spec["name"] in seen:
            # Replace built-in with user-defined. Drop the built-in
            # entry from `out` and append the user's.
            out = [s for s in out if s["name"] != spec["name"]]
        out.append(spec)
        seen.add(spec["name"])
    return out


def write_mcp_config(workspace: Path, brain_kind: str, specs: list[dict]) -> Optional[Path]:
    """Translate spec dicts into McpServerSpec objects and call the
    matching brain's writer. Returns the path written (None for the
    null brain or if no writer applies)."""
    if not specs and brain_kind == "null":
        return None
    # Lazy-import — keeps the wizard's startup graph small for
    # `vexis-agent --help` and friends.
    from vexis_agent.core.brain.base import McpServerSpec

    typed: list = [
        McpServerSpec(
            name=s["name"],
            command=s["command"],
            args=list(s.get("args", [])),
            env=dict(s.get("env", {})),
        )
        for s in specs
    ]
    if brain_kind == "claude-code":
        return _write_claude_code_mcp(workspace, typed)
    if brain_kind == "opencode":
        return _write_opencode_mcp(workspace, typed)
    return None


def _write_claude_code_mcp(workspace: Path, servers: list) -> Path:
    """Mirrors ClaudeCodeBrain.write_mcp_config without instantiating
    the brain — the brain ctor wants a SessionStore + RunningTasks the
    wizard has no business spinning up. Same atomic-write semantics.
    """
    import json

    path = workspace / ".mcp.json"
    servers_dict: dict = {}
    for spec in servers:
        entry: dict = {"command": spec.command, "args": list(spec.args)}
        if spec.env:
            entry["env"] = dict(spec.env)
        servers_dict[spec.name] = entry
    body = {"mcpServers": servers_dict}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def _write_opencode_mcp(workspace: Path, servers: list) -> Path:
    """Namespace-merge into <workspace>/opencode.json with the
    ``vexis-`` prefix. Preserves user-owned non-prefixed entries
    (matches OpenCodeBrain.write_mcp_config's contract)."""
    import json

    prefix = "vexis-"
    path = workspace / "opencode.json"
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            current = {}
    else:
        current = {}
    mcp_block = current.get("mcp") or {}
    # Drop any prior vexis-prefixed entries so we replace cleanly.
    mcp_block = {k: v for k, v in mcp_block.items() if not k.startswith(prefix)}
    for spec in servers:
        argv = [spec.command, *list(spec.args)]
        entry = {"type": "local", "command": argv, "enabled": True}
        if spec.env:
            entry["environment"] = dict(spec.env)
        mcp_block[f"{prefix}{spec.name}"] = entry
    if mcp_block:
        current["mcp"] = mcp_block
    elif "mcp" in current and not mcp_block:
        del current["mcp"]
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


# ── Tailscale soft check ───────────────────────────────────────────


@dataclass(frozen=True)
class TailscaleCheck:
    installed: bool
    logged_in: bool
    detail: str = ""


def check_tailscale() -> TailscaleCheck:
    """Probe for Tailscale. Daemon runs fine without it (dashboard on
    localhost only, livestream disabled), so this is informational —
    we never abort setup over tailscale."""
    if shutil.which("tailscale") is None:
        return TailscaleCheck(
            installed=False,
            logged_in=False,
            detail="tailscale CLI not on PATH",
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
        return TailscaleCheck(
            installed=True, logged_in=False, detail=f"status probe failed: {exc}"
        )
    if out.returncode != 0:
        return TailscaleCheck(
            installed=True,
            logged_in=False,
            detail="tailscale not logged in (run 'tailscale up')",
        )
    return TailscaleCheck(installed=True, logged_in=True)


# ──────────────────────────────────────────────────────────────────────
# Orchestration
# ──────────────────────────────────────────────────────────────────────


@dataclass
class SetupResult:
    home: Path
    config_path: Path
    dotenv_path: Path
    workspace: Optional[Path] = None
    workspace_claude_md: Optional[Path] = None
    agents_md_status: str = "skipped_not_opencode"
    archived_config: Optional[Path] = None
    archived_dotenv: Optional[Path] = None
    service_installed: bool = False
    brain_check: Optional[BrainCheck] = None
    tailscale_check: Optional[TailscaleCheck] = None
    mcp_config_path: Optional[Path] = None
    mcp_servers_wired: list = field(default_factory=list)


PromptFn = Callable[[str, bool], str]


def _default_prompt(message: str, secret: bool) -> str:
    sys.stdout.write(message)
    sys.stdout.flush()
    raw = sys.stdin.readline()
    return raw.rstrip("\n")


def _default_confirm(message: str) -> bool:
    sys.stdout.write(message)
    sys.stdout.flush()
    raw = sys.stdin.readline().strip().lower()
    return raw in {"y", "yes"}


# Choice prompts: a callable that takes (message, options, default_idx)
# and returns the chosen index. Tests inject canned answers; the
# default reads stdin and accepts a 1-based numeric pick (or empty
# for the default).
ChoiceFn = Callable[[str, list[str], int], int]


def _default_choice(message: str, options: list[str], default_idx: int) -> int:
    """Numbered-list prompt. Empty input → default; out-of-range or
    non-numeric → re-prompt up to a few times then bail to default."""
    sys.stdout.write(f"{message}\n")
    for i, opt in enumerate(options):
        marker = "*" if i == default_idx else " "
        sys.stdout.write(f"   {marker} {i + 1}) {opt}\n")
    for _ in range(3):
        sys.stdout.write(f"    [{default_idx + 1}]: ")
        sys.stdout.flush()
        raw = sys.stdin.readline().strip()
        if not raw:
            return default_idx
        try:
            picked = int(raw)
        except ValueError:
            sys.stdout.write(f"      not a number — pick 1..{len(options)}\n")
            continue
        if 1 <= picked <= len(options):
            return picked - 1
        sys.stdout.write(f"      out of range — pick 1..{len(options)}\n")
    return default_idx


def _set_brain_kind(config_path: Path, kind: str) -> None:
    """Rewrite the ``brain.kind`` line in an existing config.yaml.

    The shipped template already has ``kind: claude-code`` — we just
    swap the value if the user picked something else. Avoids pulling
    in a full YAML rewriter for one line; the schema's stable enough
    that a regex is safe here.
    """
    import re

    text = config_path.read_text(encoding="utf-8")
    new_text = re.sub(
        r"^(\s*kind:\s*).*$",
        rf"\1{kind}",
        text,
        count=1,
        flags=re.MULTILINE,
    )
    if new_text != text:
        config_path.write_text(new_text, encoding="utf-8")


def _read_brain_kind(home: Path) -> str:
    """Best-effort read of brain.kind from the just-written
    config.yaml. Mirrors yaml_config.brain_kind() but doesn't import
    it — keeps the wizard surface small and lets the wizard run
    before the config_yaml import path is wired."""
    cfg = home / CONFIG_FILENAME
    if not cfg.exists():
        return "claude-code"
    try:
        import yaml

        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except Exception:  # pragma: no cover — malformed config = default
        return "claude-code"
    raw = (data.get("brain") or {}).get("kind", "claude-code")
    if isinstance(raw, str) and raw.strip() in {"claude-code", "opencode", "null"}:
        return raw.strip()
    return "claude-code"


def run_setup(
    *,
    reset: bool = False,
    install_service: Optional[bool] = None,
    prompt: PromptFn = _default_prompt,
    confirm: Optional[Callable[[str], bool]] = None,
    choice: Optional[ChoiceFn] = None,
    brain_kind_override: Optional[str] = None,
    require_interactive: bool = True,
    print_banner: bool = True,
) -> SetupResult:
    """Drive the wizard. Returns a SetupResult.

    ``brain_kind_override`` skips the interactive brain picker — used
    by tests + by automation that wants to pin the kind via env.
    """
    confirm_fn = confirm if confirm is not None else _default_confirm
    choice_fn = choice if choice is not None else _default_choice

    if require_interactive:
        require_tty()

    if print_banner:
        _print_banner()

    # ── 1. config + .env templates ────────────────────────────────
    section("Configuration")
    home = vexis_dir()
    home.mkdir(parents=True, exist_ok=True)

    archived_config = None
    archived_dotenv = None
    if reset:
        archived_config = archive_existing(home / CONFIG_FILENAME)
        archived_dotenv = archive_existing(home / DOTENV_FILENAME)
        if archived_config:
            warn(f"archived previous config to {archived_config}")
        if archived_dotenv:
            warn(f"archived previous .env to {archived_dotenv}")

    config_path = ensure_config_yaml(home)
    dotenv_path = ensure_dotenv(home)
    ok(f"config: {config_path}")
    ok(f"secrets: {dotenv_path} (mode 0600)")

    # ── 2. Telegram secrets ───────────────────────────────────────
    section("Telegram")
    info("From @BotFather: /newbot, then copy the token.")
    token = prompt("    Telegram bot token: ", True).strip()
    if token:
        update_env_value(dotenv_path, "TELEGRAM_BOT_TOKEN", token)
        ok("TELEGRAM_BOT_TOKEN written")
    else:
        warn("skipped — set TELEGRAM_BOT_TOKEN later in ~/.vexis/.env")

    info("Your numeric Telegram user ID (use @userinfobot to look up).")
    user_id = prompt("    Allowed Telegram user ID: ", False).strip()
    if user_id:
        update_env_value(dotenv_path, "TELEGRAM_ALLOWED_USER_ID", user_id)
        ok("TELEGRAM_ALLOWED_USER_ID written")
    else:
        warn(
            "skipped — set TELEGRAM_ALLOWED_USER_ID later in ~/.vexis/.env "
            "(daemon refuses to start without it)"
        )

    # ── 3. Brain CLI: pick + check ────────────────────────────────
    section("Brain CLI")
    current = _read_brain_kind(home)
    if brain_kind_override is not None:
        brain_kind = brain_kind_override
    else:
        brain_options = ["claude-code", "opencode", "null"]
        labels = [
            "claude-code  — official Anthropic CLI (default)",
            "opencode     — 30+ providers including OAuth, OpenAI, Copilot",
            "null         — test fake; no real brain (advanced)",
        ]
        try:
            default_idx = brain_options.index(current)
        except ValueError:
            default_idx = 0
        picked_idx = choice_fn(
            "  Which brain should vexis-agent use?", labels, default_idx
        )
        brain_kind = brain_options[picked_idx]
    if brain_kind != current:
        _set_brain_kind(config_path, brain_kind)
        ok(f"set brain.kind = {brain_kind} (was {current})")
    else:
        info(f"brain.kind: {brain_kind}")
    bc = check_brain_cli(brain_kind)
    if bc.found:
        if bc.binary:
            ok(f"{bc.binary} on PATH")
        else:
            ok("null brain — no CLI required (test-only)")
    else:
        err(f"{bc.binary} NOT on PATH")
        info(bc.install_hint)
        warn(
            "daemon will refuse to start until the brain CLI is reachable. "
            "Install it, then re-run 'vexis-agent setup' or 'vexis-agent doctor'."
        )

    # ── 4. Workspace setup ────────────────────────────────────────
    section("Workspace")
    workspace = workspace_path()
    ensure_workspace(workspace)
    ok(f"workspace: {workspace}")
    ws_claude, written = ensure_workspace_claude_md(workspace)
    if written:
        ok(f"wrote workspace CLAUDE.md template at {ws_claude}")
    else:
        info(f"workspace CLAUDE.md already exists at {ws_claude} (kept)")
    agents_link, agents_status = ensure_agents_md_symlink(workspace, brain_kind)
    if agents_status == "created":
        ok(f"AGENTS.md → CLAUDE.md (opencode discovery)")
    elif agents_status == "already_correct":
        info("AGENTS.md symlink already pointed at CLAUDE.md")
    elif agents_status == "replaced_wrong_target":
        ok("re-pointed existing AGENTS.md symlink at CLAUDE.md")
    elif agents_status == "refused_real_file":
        warn(
            f"{agents_link} is a real file — refusing to overwrite. "
            "Delete or rename it, then re-run 'vexis-agent setup'."
        )

    # ── 5. MCP servers ────────────────────────────────────────────
    section("MCP servers")
    detected = detect_mcp_servers()
    mcp_path: Optional[Path] = None
    if detected:
        names = [s["name"] for s in detected]
        info(f"detected: {', '.join(names)}")
        mcp_path = write_mcp_config(workspace, brain_kind, detected)
        if mcp_path:
            ok(f"wrote {mcp_path}")
        else:
            info("null brain — MCP config skipped")
    else:
        info(
            "no MCP servers auto-detected. The brain runs fine without "
            "any; add custom entries by editing the workspace MCP config "
            "(<workspace>/.mcp.json for claude-code, "
            "<workspace>/opencode.json for opencode)."
        )
        if shutil.which("omarchy-kb") is None:
            info(
                "Tip: install omarchy-kb if you want Omarchy/Hyprland "
                "system knowledge in the brain."
            )

    # ── 6. Tailscale soft-check ───────────────────────────────────
    section("Tailscale (optional)")
    ts = check_tailscale()
    if ts.installed and ts.logged_in:
        ok("tailscale up — dashboard reachable from your phone")
    elif ts.installed:
        warn(ts.detail or "tailscale not logged in")
        info("Dashboard works on http://127.0.0.1:8766 only without tailscale.")
    else:
        warn("tailscale not installed — dashboard will be localhost-only")
        info(
            "Install: https://tailscale.com/download. After installing run "
            "'tailscale up' to log in."
        )

    # ── 7. Optional: install systemd service ──────────────────────
    section("Service")
    decision = install_service
    if decision is None:
        decision = confirm_fn("    Install the systemd user service now? [y/N] ")
    service_installed = False
    if decision:
        from vexis_agent.daemon.systemd import install_user_unit

        install_user_unit()
        service_installed = True
        ok("systemd user unit installed at ~/.config/systemd/user/vexis-agent.service")
    else:
        info("skipped — run 'vexis-agent service install' later if you want it.")

    return SetupResult(
        home=home,
        config_path=config_path,
        dotenv_path=dotenv_path,
        workspace=workspace,
        workspace_claude_md=ws_claude,
        agents_md_status=agents_status,
        archived_config=archived_config,
        archived_dotenv=archived_dotenv,
        service_installed=service_installed,
        brain_check=bc,
        tailscale_check=ts,
        mcp_config_path=mcp_path,
        mcp_servers_wired=[s["name"] for s in detected],
    )


# ──────────────────────────────────────────────────────────────────────
# Banner + summary
# ──────────────────────────────────────────────────────────────────────


def _print_banner() -> None:
    """Hermes-style box. Skip when stdout isn't a terminal (test
    capture, log redirect)."""
    line1 = "       vexis-agent setup wizard"
    line2 = "  Telegram-bridged agent for Linux (Hyprland)."
    bar = "─" * 56
    print()
    print(_cyan(f"┌{bar}┐"))
    print(_cyan("│") + _bold(line1.ljust(56)) + _cyan("│"))
    print(_cyan(f"├{bar}┤"))
    print(_cyan("│") + line2.ljust(56) + _cyan("│"))
    print(_cyan(f"└{bar}┘"))


def format_summary(result: SetupResult) -> str:
    """Final post-wizard banner. Plain text — color decisions are made
    in the live print loop, not here."""
    lines = ["", "vexis-agent setup complete.", ""]
    lines.append(f"  config:    {result.config_path}")
    lines.append(f"  secrets:   {result.dotenv_path}  (mode 0600)")
    if result.workspace:
        lines.append(f"  workspace: {result.workspace}")
    if result.archived_config:
        lines.append(f"  archived:  {result.archived_config}")
    if result.archived_dotenv:
        lines.append(f"  archived:  {result.archived_dotenv}")
    lines.append("")

    if result.brain_check and not result.brain_check.found:
        lines.append("⚠ Brain CLI missing — install it before starting:")
        lines.append(f"    {result.brain_check.install_hint}")
        lines.append("")

    if result.service_installed:
        lines.append("Service installed. Start it with:")
        lines.append("  systemctl --user enable --now vexis-agent.service")
    else:
        lines.append("Next steps:")
        lines.append("  vexis-agent doctor               # readiness check")
        lines.append("  vexis-agent run                  # foreground")
        lines.append("  vexis-agent service install      # systemd user unit")

    return "\n".join(lines)

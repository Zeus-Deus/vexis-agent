"""``vexis-agent mcp`` — manage MCP servers from the command line.

Wraps ``$VEXIS_HOME/mcp-servers.yaml`` (the universal config — one
source of truth across every brain native) with add / remove / list /
show / refresh verbs so users don't have to hand-edit yaml for the
common cases.

An MCP server is **either** a local stdio process (``command`` +
``args`` + ``env``) **or** a remote HTTP endpoint (``url`` +
``transport`` + ``headers``). The two shapes are mutually exclusive;
``add_server`` and the yaml parser reject an entry that sets both or
neither. ``on_path`` (the PATH-resolvable binary check) is meaningful
only for stdio servers — remote servers have no binary, so ``list`` /
``status`` report ``on_path=True`` for them unconditionally.

Notes:

- Only user-declared servers (the yaml) are mutable. Built-in
  detectors (e.g. ``omarchy-kb``) are vexis-internal and stay
  read-only — ``list`` surfaces them with a marker.
- Comments in the user's yaml are NOT preserved across edits — pyyaml
  is a round-trip-lossy parser. Users who maintain heavily-commented
  yaml should hand-edit instead.
- Every mutating verb (add / remove / refresh) rewrites all the
  per-brain native files via setup_wizard.write_all_mcp_configs so
  the brain sees the new state on next session without re-running
  the full wizard.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Module-level import so this module sees the SAME ``vexis_dir``
# binding ``setup_wizard`` does. Both modules read the function's
# env-driven behaviour (VEXIS_HOME) consistently — keeps tests +
# production agreed on where state lives. Conftest's autouse patch
# of ``core.paths.vexis_dir`` only reaches modules that lazy-import,
# which is a known mismatch with this consistent-env-var model.
from vexis_agent.core.paths import vexis_dir

log = logging.getLogger(__name__)

YAML_FILENAME = "mcp-servers.yaml"


def _yaml_path() -> Path:
    """``$VEXIS_HOME/mcp-servers.yaml``."""
    return vexis_dir() / YAML_FILENAME


def _read_yaml(path: Optional[Path] = None) -> dict:
    """Load mcp-servers.yaml; return ``{}`` if missing/empty/malformed."""
    import yaml

    p = path if path is not None else _yaml_path()
    if not p.is_file():
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        log.warning("malformed %s: %s — treating as empty", p, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _write_yaml(data: dict, path: Optional[Path] = None) -> Path:
    """Write the yaml back. Atomic via temp + rename."""
    import yaml

    p = path if path is not None else _yaml_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(p)
    return p


def _normalize_servers(data: dict) -> list[dict]:
    """Return the ``servers`` list (always a list, even if absent)."""
    raw = data.get("servers")
    return list(raw) if isinstance(raw, list) else []


@dataclass(frozen=True)
class ServerEntry:
    """One row of ``vexis-agent mcp list`` output.

    Either a local stdio server (``command`` set, ``url`` None) or a
    remote HTTP server (``url`` set, ``command`` None). ``is_remote``
    discriminates. For remote servers ``on_path`` is always True —
    there is no binary to resolve."""

    name: str
    source: str            # 'user' (in yaml) or 'builtin' (registry detector)
    on_path: bool          # stdio: binary resolves via shutil.which;
                           # remote: always True (no binary)
    # ── local stdio fields ──
    command: Optional[str] = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    binary: Optional[str] = None   # PATH-check target; defaults to command
    # ── remote HTTP fields ──
    url: Optional[str] = None
    transport: str = "http"        # 'http' or 'sse'
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def is_remote(self) -> bool:
        """True for a remote HTTP server, False for a local stdio one."""
        return self.url is not None


def parse_env_assignments(pairs: list[str]) -> dict[str, str]:
    """Parse ``--env KEY=VALUE`` arguments. Empty list → empty dict.
    Raises ValueError on malformed entries."""
    out: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(
                f"--env arguments must be KEY=VALUE; got {pair!r}"
            )
        k, _, v = pair.partition("=")
        k = k.strip()
        if not k:
            raise ValueError(f"--env argument has empty key: {pair!r}")
        out[k] = v
    return out


def parse_header_assignments(pairs: list[str]) -> dict[str, str]:
    """Parse ``--header 'Key: Value'`` arguments for remote MCP
    servers. Empty list → empty dict. Splits on the first ``:`` so
    header values may themselves contain colons (e.g. a bearer token
    or a URL). Surrounding whitespace is trimmed. Raises ValueError
    on malformed entries."""
    out: dict[str, str] = {}
    for pair in pairs:
        if ":" not in pair:
            raise ValueError(
                f"--header arguments must be 'Key: Value'; got {pair!r}"
            )
        k, _, v = pair.partition(":")
        k = k.strip()
        if not k:
            raise ValueError(f"--header argument has empty key: {pair!r}")
        out[k] = v.strip()
    return out


# ──────────────────────────────────────────────────────────────────────
# Verbs
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AddResult:
    name: str
    yaml_path: Path
    refreshed_paths: list[Path]
    replaced_existing: bool


def add_server(
    *,
    name: str,
    command: Optional[str] = None,
    args: Optional[list[str]] = None,
    env: Optional[dict[str, str]] = None,
    binary: Optional[str] = None,
    url: Optional[str] = None,
    transport: Optional[str] = None,
    headers: Optional[dict[str, str]] = None,
) -> AddResult:
    """Add (or replace) an MCP server in the user yaml, then refresh
    all per-brain native files. Returns a summary describing what
    landed.

    The server is either local stdio (pass ``command`` / ``args`` /
    ``env`` / ``binary``) or remote HTTP (pass ``url`` / ``transport``
    / ``headers``). ``command`` and ``url`` are mutually exclusive —
    exactly one must be given."""
    if not name:
        raise ValueError("name is required")
    has_command = bool(command)
    has_url = bool(url)
    if has_command and has_url:
        raise ValueError(
            "an MCP server is either local stdio or remote HTTP — "
            "pass --command or --url, not both"
        )
    if not has_command and not has_url:
        raise ValueError(
            "an MCP server needs --command (local stdio) or "
            "--url (remote HTTP)"
        )

    data = _read_yaml()
    servers = _normalize_servers(data)

    new_entry: dict = {"name": name}
    if has_url:
        new_entry["url"] = url
        transport = transport or "http"
        if transport not in ("http", "sse"):
            raise ValueError(
                f"--transport must be 'http' or 'sse'; got {transport!r}"
            )
        # Only persist a non-default transport — keeps simple entries
        # clean in the yaml.
        if transport != "http":
            new_entry["transport"] = transport
        if headers:
            new_entry["headers"] = dict(headers)
    else:
        new_entry["command"] = command
        if binary is not None and binary != command:
            new_entry["binary"] = binary
        if args:
            new_entry["args"] = list(args)
        if env:
            new_entry["env"] = dict(env)

    replaced = False
    out_servers: list[dict] = []
    for entry in servers:
        if isinstance(entry, dict) and entry.get("name") == name:
            replaced = True
            continue  # drop the old; we'll append the new at the same spot
        out_servers.append(entry)
    if replaced:
        # Insert in place — find the index of the dropped entry.
        idx = next(
            (
                i for i, e in enumerate(servers)
                if isinstance(e, dict) and e.get("name") == name
            ),
            len(out_servers),
        )
        out_servers.insert(idx, new_entry)
    else:
        out_servers.append(new_entry)

    data["servers"] = out_servers
    yaml_path = _write_yaml(data)
    refreshed = refresh_workspace().refreshed_paths
    return AddResult(
        name=name,
        yaml_path=yaml_path,
        refreshed_paths=refreshed,
        replaced_existing=replaced,
    )


@dataclass(frozen=True)
class RemoveResult:
    name: str
    yaml_path: Path
    refreshed_paths: list[Path]
    found: bool


def remove_server(*, name: str) -> RemoveResult:
    """Remove an MCP server from the user yaml. Built-in detector
    entries can't be removed this way (they live in vexis source);
    use ``vexis-agent mcp list`` to see which is which."""
    data = _read_yaml()
    servers = _normalize_servers(data)
    out_servers = [
        e for e in servers
        if not (isinstance(e, dict) and e.get("name") == name)
    ]
    found = len(out_servers) != len(servers)
    if found:
        data["servers"] = out_servers
        yaml_path = _write_yaml(data)
        refreshed = refresh_workspace().refreshed_paths
    else:
        # No-op: yaml unchanged, no refresh needed.
        yaml_path = _yaml_path()
        refreshed = []
    return RemoveResult(
        name=name, yaml_path=yaml_path, refreshed_paths=refreshed, found=found
    )


def list_servers() -> list[ServerEntry]:
    """Return both user-declared (from yaml) and built-in (from
    detector registry) servers, with PATH-resolution status."""
    import shutil

    from vexis_agent.setup_wizard import _MCP_DETECTORS

    out: list[ServerEntry] = []

    # Built-in detectors first (fixed order).
    for detector in _MCP_DETECTORS:
        spec = detector()
        if spec is None:
            continue
        out.append(
            ServerEntry(
                name=spec["name"],
                command=spec["command"],
                args=list(spec.get("args", [])),
                env=dict(spec.get("env", {})),
                binary=spec.get("binary", spec["command"]),
                source="builtin",
                on_path=True,  # detectors only return when PATH-resolvable
            )
        )

    # User entries.
    for entry in _normalize_servers(_read_yaml()):
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        command = entry.get("command")
        url = entry.get("url")
        if not name:
            continue
        # Skip malformed entries (both shapes set, or neither) so
        # ``list`` never crashes on a hand-edited yaml — the wizard's
        # _user_mcp_specs() warns about these on refresh.
        if bool(command) == bool(url):
            continue
        # User-defined name overrides built-in name (matches the
        # same precedence rule the detector resolution uses).
        out = [e for e in out if e.name != name]
        if url:
            out.append(
                ServerEntry(
                    name=str(name),
                    source="user",
                    on_path=True,  # remote — no binary to resolve
                    url=str(url),
                    transport=str(entry.get("transport") or "http"),
                    headers=dict(entry.get("headers") or {}),
                )
            )
        else:
            binary = entry.get("binary") or command
            out.append(
                ServerEntry(
                    name=str(name),
                    source="user",
                    on_path=shutil.which(str(binary)) is not None,
                    command=str(command),
                    args=list(entry.get("args") or []),
                    env=dict(entry.get("env") or {}),
                    binary=str(binary),
                )
            )

    return out


@dataclass(frozen=True)
class RefreshResult:
    yaml_path: Path
    refreshed_paths: list[Path]
    server_count: int


@dataclass(frozen=True)
class ServerStatus:
    """Detailed status of one MCP server. Powers ``vexis-agent mcp status``."""

    entry: ServerEntry
    in_claude_native: bool   # appears in <workspace>/.mcp.json
    in_opencode_native: bool # appears in <workspace>/opencode.json's mcp block
    in_codex_native: bool    # appears in $CODEX_HOME/vexis.config.toml's mcp_servers

    @property
    def fully_wired(self) -> bool:
        """Brain-ready: present in every brain native AND (for stdio
        servers) PATH-resolvable. Remote servers have no binary so
        ``on_path`` is always True for them — full wiring then just
        means present in all native files. All natives are written on
        every add/refresh so switching ``brain.kind`` stays
        zero-friction."""
        return (
            self.entry.on_path
            and self.in_claude_native
            and self.in_opencode_native
            and self.in_codex_native
        )


def status_servers() -> list[ServerStatus]:
    """Per-server full status: configured + on-PATH + brain-wired in
    each native file. Powers ``vexis-agent mcp status``."""
    import json

    from vexis_agent.core.brain.codex import _VEXIS_PROFILE_FILE, codex_home
    from vexis_agent.setup_wizard import workspace_path

    workspace = workspace_path()
    claude_native = workspace / ".mcp.json"
    opencode_native = workspace / "opencode.json"
    codex_native = codex_home() / _VEXIS_PROFILE_FILE

    claude_names: set[str] = set()
    if claude_native.is_file():
        try:
            claude_names = set(
                (json.loads(claude_native.read_text(encoding="utf-8")) or {})
                .get("mcpServers", {})
                .keys()
            )
        except (json.JSONDecodeError, OSError):
            pass

    opencode_names: set[str] = set()
    if opencode_native.is_file():
        try:
            block = (
                (json.loads(opencode_native.read_text(encoding="utf-8")) or {})
                .get("mcp", {})
            )
            opencode_names = {
                # opencode entries are namespaced with the 'vexis-' prefix;
                # strip it to compare against the bare server name.
                k[len("vexis-"):] if k.startswith("vexis-") else k
                for k in block.keys()
            }
        except (json.JSONDecodeError, OSError):
            pass

    codex_names: set[str] = set()
    if codex_native.is_file():
        import tomllib

        try:
            codex_names = set(
                (tomllib.loads(codex_native.read_text(encoding="utf-8")) or {})
                .get("mcp_servers", {})
                .keys()
            )
        except (tomllib.TOMLDecodeError, OSError):
            pass

    out: list[ServerStatus] = []
    for entry in list_servers():
        out.append(
            ServerStatus(
                entry=entry,
                in_claude_native=entry.name in claude_names,
                in_opencode_native=entry.name in opencode_names,
                in_codex_native=entry.name in codex_names,
            )
        )
    return out


def refresh_workspace() -> RefreshResult:
    """Re-read the yaml + built-in detectors and rewrite all
    per-brain native files. Used by ``vexis-agent mcp refresh`` for
    the ``I just edited the yaml manually`` flow, and called
    automatically after add/remove."""
    from vexis_agent.setup_wizard import (
        detect_mcp_servers,
        workspace_path,
        write_all_mcp_configs,
    )

    detected = detect_mcp_servers()
    workspace = workspace_path()
    workspace.mkdir(parents=True, exist_ok=True)
    paths = write_all_mcp_configs(workspace, detected)
    return RefreshResult(
        yaml_path=_yaml_path(),
        refreshed_paths=paths,
        server_count=len(detected),
    )

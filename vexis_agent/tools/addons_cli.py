"""``vexis-addons`` — inspect, enable, disable, install add-ons.

The CLI is the operator's front-door to the add-on system. It does
NOT touch the running daemon — every command is a read or write
against ``~/.vexis/config.yaml`` and the on-disk add-on directories.
A restart picks up the changes; live daemons stay agnostic.

Subcommands:

    vexis-addons list                       enabled + disabled + discovered
    vexis-addons enable <name>              add to addons.enabled
    vexis-addons disable <name>             add to addons.disabled
    vexis-addons inspect <name>             parsed manifest + provides
    vexis-addons doctor                     check requires for every
                                            enabled add-on (env, mcp)
    vexis-addons install <path>             rsync a local addon dir
                                            into ~/.vexis/addons/<name>/

Exit codes mirror the rest of vexis: 0 success, 1 user error
(missing addon, bad config), 2 unexpected internal error. JSON
output is available behind ``--json`` for any subcommand that
emits structured data.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

from vexis_agent.core.addons import (
    DiscoveredAddon,
    Manifest,
    bundled_addons_root,
    discover_addons,
    find_manifest_file,
    parse_manifest,
    user_addons_root,
)
from vexis_agent.core import paths as _paths_module  # late-binding for tests
from vexis_agent.core.addons.errors import ManifestError
from vexis_agent.core.yaml_config import (
    addons_disabled,
    addons_enabled,
)


def _vexis_dir() -> Path:
    """Re-resolve every call so the conftest autouse fixture (which
    patches ``vexis_agent.core.paths.vexis_dir``) takes effect — a
    direct ``from paths import vexis_dir`` would bind at module load
    and bypass the patch. Same posture as yaml_config's
    ``_config_path`` re-reading disk per call.
    """
    return _paths_module.vexis_dir()

log = logging.getLogger(__name__)


# ---------- helpers ---------------------------------------------------------


def _config_path() -> Path:
    """``~/.vexis/config.yaml``. Same lookup yaml_config uses, but
    here for write-side too (the read-side helpers won't write)."""
    return _vexis_dir() / "config.yaml"


def _read_config() -> dict[str, Any]:
    """Read the full config file as a dict.

    Missing file → empty dict (a brand-new vexis install hasn't yet
    created the config; ``enable`` is the natural place to create
    it). Malformed YAML raises — better to fail loudly than to
    silently overwrite user data.
    """
    path = _config_path()
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if isinstance(data, dict) else {}


def _write_config(data: dict[str, Any]) -> None:
    """Atomic write via tmp-rename. Preserves YAML formatting badly
    (PyYAML re-emits in its default style), but ``enable``/``disable``
    are the only writers in v1 so the cost is just "comments may
    move." Phase B+ can adopt ruamel.yaml if comment-preservation
    becomes a real ask."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, default_flow_style=False)
    tmp.replace(path)


def _emit_json(payload: Any) -> None:
    json.dump(payload, sys.stdout, indent=2, default=str)
    print()


def _discover_all() -> list[DiscoveredAddon]:
    """Discovery for inspect/list — return ALL on-disk addons, ignoring
    the enabled/disabled filter.

    The CLI shows users what's installed so they can enable it; if we
    applied the user's enabled list here, ``vexis-addons list``
    would always show the same names the user already wrote.
    """
    # discover_addons() requires an enabled list; pass every name we
    # find on disk so nothing is filtered out.
    all_names = set()
    for root in (bundled_addons_root(), user_addons_root()):
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if child.is_dir() and find_manifest_file(child):
                try:
                    all_names.add(parse_manifest(find_manifest_file(child)).name)
                except ManifestError:
                    pass  # broken addons surface in `doctor`
    return discover_addons(
        enabled=sorted(all_names),
        disabled=[],  # don't apply user's disabled list here either
    )


# ---------- subcommands -----------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    discovered = _discover_all()
    enabled = set(addons_enabled())
    disabled = set(addons_disabled())

    rows: list[dict[str, Any]] = []
    for d in discovered:
        name = d.manifest.name
        status = (
            "disabled" if name in disabled else
            "enabled" if name in enabled else
            "discovered"
        )
        rows.append({
            "name": name,
            "version": d.manifest.version,
            "source": d.source,
            "status": status,
            "kind": d.manifest.kind,
            "description": d.manifest.description,
        })

    if args.json:
        _emit_json(rows)
        return 0

    if not rows:
        print("No add-ons found.")
        print(f"Searched: {bundled_addons_root()}, {user_addons_root()}")
        return 0

    name_w = max(len(r["name"]) for r in rows)
    ver_w = max(len(r["version"]) for r in rows)
    print(f"{'NAME'.ljust(name_w)}  {'VERSION'.ljust(ver_w)}  "
          f"{'STATUS'.ljust(10)}  SOURCE   DESCRIPTION")
    for r in rows:
        print(
            f"{r['name'].ljust(name_w)}  "
            f"{r['version'].ljust(ver_w)}  "
            f"{r['status'].ljust(10)}  "
            f"{r['source'].ljust(8)} {r['description'] or ''}"
        )
    return 0


def cmd_enable(args: argparse.Namespace) -> int:
    """Add ``args.name`` to ``addons.enabled`` in ~/.vexis/config.yaml.

    Idempotent — re-running on an already-enabled add-on is a no-op
    that prints a clarifying message rather than failing. The
    daemon picks up the change on next restart.
    """
    name = args.name
    cfg = _read_config()
    addons_section = cfg.setdefault("addons", {})
    enabled_list = addons_section.setdefault("enabled", [])
    if not isinstance(enabled_list, list):
        print(f"error: addons.enabled is not a list in {_config_path()}",
              file=sys.stderr)
        return 1
    if name in enabled_list:
        print(f"add-on {name!r} is already enabled.")
        return 0
    enabled_list.append(name)
    # Also remove from disabled if it was there — enable should win.
    disabled_list = addons_section.get("disabled", [])
    if isinstance(disabled_list, list) and name in disabled_list:
        disabled_list.remove(name)
    _write_config(cfg)
    print(f"enabled add-on {name!r}. restart vexis-agent to load it.")
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    """Add ``args.name`` to ``addons.disabled`` in ~/.vexis/config.yaml.

    Keeps the ``enabled`` entry — disable is "I want this off right
    now without forgetting I asked for it"; the user can re-enable
    by removing the disabled entry later.
    """
    name = args.name
    cfg = _read_config()
    addons_section = cfg.setdefault("addons", {})
    disabled_list = addons_section.setdefault("disabled", [])
    if not isinstance(disabled_list, list):
        print(f"error: addons.disabled is not a list in {_config_path()}",
              file=sys.stderr)
        return 1
    if name in disabled_list:
        print(f"add-on {name!r} is already disabled.")
        return 0
    disabled_list.append(name)
    _write_config(cfg)
    print(f"disabled add-on {name!r}. restart vexis-agent to unload it.")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    """Show the parsed manifest for one add-on plus where it lives."""
    name = args.name
    for d in _discover_all():
        if d.manifest.name == name:
            payload = _manifest_to_payload(d.manifest, d.addon_dir, d.source)
            if args.json:
                _emit_json(payload)
                return 0
            print(f"NAME:        {payload['name']}")
            print(f"VERSION:     {payload['version']}")
            print(f"SOURCE:      {payload['source']}  ({payload['addon_dir']})")
            print(f"KIND:        {payload['kind']}")
            print(f"DESCRIPTION: {payload['description']}")
            print(f"AUTHOR:      {payload['author']}")
            req = payload["requires"]
            print("REQUIRES:")
            print(f"  vexis_agent: {req['vexis_agent']}")
            print(f"  python:      {req['python']}")
            for mcp in req["mcp_servers"]:
                print(f"  mcp_server:  {mcp}")
            for env in req["env"]:
                print(f"  env:         {env}")
            print("PROVIDES:")
            for k, v in payload["provides"].items():
                if v:
                    print(f"  {k}: {', '.join(v)}")
            if payload["config_schema"]:
                print("CONFIG_SCHEMA:")
                for k, v in payload["config_schema"].items():
                    print(f"  {k}: {v}")
            return 0
    print(f"error: add-on {name!r} not found on disk.", file=sys.stderr)
    return 1


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check ``requires:`` for every enabled add-on.

    Surfaces missing MCP servers and missing env vars before the
    user's next daemon start. ``vexis-addons doctor`` is what we
    point users at when an add-on "doesn't seem to be working" —
    nine times out of ten the answer is a missing env var.
    """
    import os

    # Need to read mcp-servers.yaml to know which MCPs are configured.
    mcp_path = _vexis_dir() / "mcp-servers.yaml"
    configured_mcps: set[str] = set()
    if mcp_path.is_file():
        with mcp_path.open("r", encoding="utf-8") as fh:
            try:
                raw = yaml.safe_load(fh) or {}
            except yaml.YAMLError:
                raw = {}
        if isinstance(raw, dict):
            servers = raw.get("servers") or []
            if isinstance(servers, list):
                for srv in servers:
                    if isinstance(srv, dict):
                        nm = srv.get("name")
                        if isinstance(nm, str):
                            configured_mcps.add(nm)

    enabled = set(addons_enabled())
    disabled = set(addons_disabled())
    findings: list[dict[str, Any]] = []

    for d in _discover_all():
        name = d.manifest.name
        if name not in enabled or name in disabled:
            continue
        issues: list[str] = []
        for mcp_req in d.manifest.requires.mcp_servers:
            if mcp_req.name not in configured_mcps and not mcp_req.optional:
                issues.append(
                    f"required mcp_server {mcp_req.name!r} not in "
                    f"{mcp_path} — add it or set ``optional: true``"
                )
        for env_var in d.manifest.requires.env:
            if not os.environ.get(env_var):
                issues.append(f"required env var {env_var!r} is unset")
        findings.append({"name": name, "issues": issues, "ok": not issues})

    bad = [f for f in findings if not f["ok"]]
    rc = 1 if bad else 0

    if args.json:
        _emit_json(findings)
        return rc

    if not bad:
        print(f"all {len(findings)} enabled add-on(s) check out clean.")
        return 0

    print(f"{len(bad)} add-on(s) have unmet requirements:")
    for f in bad:
        print(f"  {f['name']}:")
        for issue in f["issues"]:
            print(f"    - {issue}")
    return rc


def cmd_install(args: argparse.Namespace) -> int:
    """Copy a local add-on directory into ``~/.vexis/addons/<name>/``.

    v1 supports local paths only — git URLs land in v2 once we agree
    on the trust model (do we clone over HTTPS, do we vendor a
    minimal git client, etc.). For now: clone the repo yourself,
    then ``vexis-addons install /path/to/clone``.

    The source path must contain a valid ``addon.yaml`` — we parse it
    to learn the destination name. Refuses to overwrite an existing
    install unless ``--force`` is passed; refuses to install over a
    BUNDLED add-on of the same name (forks should be enabled by
    putting them in ``~/.vexis/addons/`` and disabling the bundled
    name in config).
    """
    src = Path(args.source).expanduser().resolve()
    if not src.is_dir():
        print(f"error: source {src} is not a directory.", file=sys.stderr)
        return 1
    manifest_path = find_manifest_file(src)
    if manifest_path is None:
        print(f"error: no addon.yaml found in {src}.", file=sys.stderr)
        return 1
    try:
        manifest = parse_manifest(manifest_path)
    except ManifestError as e:
        print(f"error: invalid manifest in {src}: {e}", file=sys.stderr)
        return 1

    bundled_dir = bundled_addons_root() / manifest.name
    if bundled_dir.is_dir():
        print(
            f"error: {manifest.name!r} is a bundled add-on. To use a "
            f"fork, disable the bundled one (``vexis-addons disable "
            f"{manifest.name}``) and the discovery layer will pick up "
            f"your user-installed copy.",
            file=sys.stderr,
        )
        return 1

    dest_root = user_addons_root()
    dest_root.mkdir(parents=True, exist_ok=True)
    dest = dest_root / manifest.name
    if dest.is_dir() and not args.force:
        print(
            f"error: {dest} already exists. Pass --force to overwrite.",
            file=sys.stderr,
        )
        return 1
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    print(f"installed {manifest.name!r} v{manifest.version} to {dest}.")
    print(
        f"to load: ``vexis-addons enable {manifest.name}`` then "
        f"restart vexis-agent."
    )
    return 0


# ---------- inspect payload helpers ----------------------------------------


def _manifest_to_payload(
    m: Manifest, addon_dir: Path, source: str
) -> dict[str, Any]:
    """Convert a Manifest dataclass to a JSON-serialisable dict."""
    return {
        "name": m.name,
        "version": m.version,
        "description": m.description,
        "author": m.author,
        "kind": m.kind,
        "source": source,
        "addon_dir": str(addon_dir),
        "requires": {
            "vexis_agent": m.requires.vexis_agent,
            "python": m.requires.python,
            "mcp_servers": [
                f"{mcp.name} (optional)" if mcp.optional else mcp.name
                for mcp in m.requires.mcp_servers
            ],
            "env": list(m.requires.env),
        },
        "provides": {
            "telegram_commands": list(m.provides.telegram_commands),
            "watcher_sources": list(m.provides.watcher_sources),
            "background_tasks": list(m.provides.background_tasks),
            "dispatch_handlers": list(m.provides.dispatch_handlers),
            "skills": list(m.provides.skills),
            "header_blocks": list(m.provides.header_blocks),
            "dashboard_pages": list(m.provides.dashboard_pages),
            "mcp_server_defaults": list(m.provides.mcp_server_defaults),
        },
        "config_schema": {
            k: {
                "type": v.type,
                "default": v.default,
                "description": v.description,
            }
            for k, v in m.config_schema.items()
        },
    }


# ---------- argv parser -----------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vexis-addons",
        description="Inspect, enable, disable, and install vexis-agent add-ons.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit structured JSON instead of human-readable text.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List all add-ons found on disk.").set_defaults(
        func=cmd_list
    )

    p_enable = sub.add_parser("enable", help="Enable an add-on by name.")
    p_enable.add_argument("name", help="Add-on slug.")
    p_enable.set_defaults(func=cmd_enable)

    p_disable = sub.add_parser("disable", help="Disable an add-on by name.")
    p_disable.add_argument("name", help="Add-on slug.")
    p_disable.set_defaults(func=cmd_disable)

    p_inspect = sub.add_parser(
        "inspect", help="Show one add-on's parsed manifest."
    )
    p_inspect.add_argument("name", help="Add-on slug.")
    p_inspect.set_defaults(func=cmd_inspect)

    sub.add_parser(
        "doctor",
        help="Check requires (MCP servers, env vars) for enabled add-ons.",
    ).set_defaults(func=cmd_doctor)

    p_install = sub.add_parser(
        "install",
        help="Copy a local add-on directory into ~/.vexis/addons/.",
    )
    p_install.add_argument("source", help="Path to add-on directory.")
    p_install.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing user-installed add-on of the same name.",
    )
    p_install.set_defaults(func=cmd_install)

    return parser


def main(argv: "list[str] | None" = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except Exception:  # noqa: BLE001
        log.exception("vexis-addons internal error")
        return 2


if __name__ == "__main__":
    sys.exit(main())

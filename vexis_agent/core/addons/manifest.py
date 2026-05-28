"""``addon.yaml`` schema + parser.

An add-on manifest is the loader's only source of truth about an
add-on BEFORE its ``register()`` is called. Manifests are read at
discovery time, validated, and used to:

  * Filter by ``addons.enabled`` / ``addons.disabled`` in user config
    (so a known-disabled add-on never gets imported at all — its
    Python module bugs can't affect the daemon).
  * Check ``requires:`` (env vars, MCP servers, vexis-agent version).
  * Surface ``provides:`` to ``vexis addons inspect`` and to conflict
    detection (two add-ons claiming the same Telegram command name).
  * Bind ``config_schema:`` defaults into the per-addon config slice
    the :class:`PluginContext` exposes.

Schema is hand-validated (no pydantic dependency) because the surface
is small and the error messages need to point at the offending YAML
field by name — generic validator output is hostile when a user is
debugging their own add-on at 2am.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from .errors import ManifestError

#: Manifests live here inside an add-on directory. Both ``addon.yaml`` and
#: ``addon.yml`` are accepted (matching common YAML-extension preference
#: drift); ``addon.yaml`` is canonical and used in docs.
MANIFEST_FILENAMES = ("addon.yaml", "addon.yml")

#: Pattern enforced on the ``name:`` slug. Lowercase + digits + hyphens,
#: same as PEP 503 normalised distribution names. The slug appears in
#: log namespaces (``vexis_agent.addons.<name>``), config keys
#: (``addons.<name>.*``), and directory paths — keeping it ASCII-safe
#: avoids escaping surprises.
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")

#: Allowed values for the ``kind:`` field. ``standalone`` is the only
#: kind v1 supports; ``core-extension`` is reserved for a future
#: privileged class (no opt-in required, can register additional
#: hooks). Reserving the slot now keeps the manifest forward-compatible.
ALLOWED_KINDS = frozenset({"standalone", "core-extension"})


@dataclass(frozen=True)
class McpRequirement:
    """One entry under ``requires.mcp_servers:`` in the manifest.

    ``optional=True`` means the add-on still loads when the MCP is
    absent — it's expected to degrade gracefully (e.g. log a warning
    and skip registering its source plugin). Default ``False`` makes
    the loader skip the add-on entirely with an
    :class:`AddonRequirementError` when the MCP isn't present.
    """

    name: str
    optional: bool = False


@dataclass(frozen=True)
class ConfigField:
    """One entry under ``config_schema:`` in the manifest.

    The ``type`` string is validated against a small set of primitives
    (``str``, ``int``, ``float``, ``bool``, ``list``, ``dict``); we
    don't try to be a full type system. Coercion happens at
    config-read time, not here.
    """

    type: str
    default: Any = None
    description: Optional[str] = None


@dataclass(frozen=True)
class Provides:
    """The ``provides:`` block — purely informational metadata.

    The loader uses these lists for conflict detection (e.g. two
    add-ons both promising ``telegram_commands: ["codemux"]``) and
    ``vexis addons inspect`` surfaces them for users. The actual
    registrations happen in ``register(ctx)`` and the runtime is the
    source of truth for what got wired — ``provides:`` is the
    add-on's *promise*, not its receipt.
    """

    telegram_commands: tuple[str, ...] = ()
    watcher_sources: tuple[str, ...] = ()
    background_tasks: tuple[str, ...] = ()
    dispatch_handlers: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    header_blocks: tuple[str, ...] = ()
    dashboard_pages: tuple[str, ...] = ()
    mcp_server_defaults: tuple[str, ...] = ()


@dataclass(frozen=True)
class Requires:
    """The ``requires:`` block — checked by the loader before import.

    ``vexis_agent`` and ``python`` are version constraints as plain
    strings (``">=0.9.0"``, ``">=3.11"``). We don't parse them with
    ``packaging.specifiers`` here to keep the dependency footprint
    flat; the loader does the comparison and emits a clear error
    when it fails.
    """

    vexis_agent: Optional[str] = None
    python: Optional[str] = None
    mcp_servers: tuple[McpRequirement, ...] = ()
    env: tuple[str, ...] = ()


@dataclass(frozen=True)
class Manifest:
    """Parsed + validated ``addon.yaml``.

    Frozen so the loader can hash/compare manifests across discovery
    sources and detect duplicates. The original raw dict is dropped
    after validation — anything code needs goes on a field. If a
    future schema addition lands, add the field here and a parse
    step below; don't reach back into raw YAML.
    """

    name: str
    version: str
    description: str = ""
    author: str = ""
    kind: str = "standalone"
    requires: Requires = field(default_factory=Requires)
    provides: Provides = field(default_factory=Provides)
    config_schema: dict[str, ConfigField] = field(default_factory=dict)

    #: Where this manifest was loaded from. Used by error messages
    #: and the loader for "first wins on conflict" precedence. Not
    #: settable by the YAML — populated by :func:`parse_manifest`
    #: from the caller-provided path.
    source_path: Optional[Path] = None


def find_manifest_file(addon_dir: Path) -> Optional[Path]:
    """Return the manifest path under ``addon_dir``, or ``None``.

    Accepts both ``addon.yaml`` and ``addon.yml``. If both exist
    (rare, almost always a user mistake), ``addon.yaml`` wins and
    the loader logs a warning so the duplicate gets cleaned up.
    """
    for name in MANIFEST_FILENAMES:
        candidate = addon_dir / name
        if candidate.is_file():
            return candidate
    return None


def parse_manifest(manifest_path: Path) -> Manifest:
    """Read + validate one ``addon.yaml`` file.

    Raises :class:`ManifestError` with a field-specific message on
    any schema violation. Never raises a generic ``yaml.YAMLError``
    or ``KeyError`` — the loader catches :class:`ManifestError` and
    keeps going; other exceptions propagate as bugs.
    """
    if not manifest_path.is_file():
        raise ManifestError(
            f"manifest file not found: {manifest_path}",
            manifest_path=manifest_path,
        )

    try:
        text = manifest_path.read_text(encoding="utf-8")
    except OSError as e:
        raise ManifestError(
            f"could not read manifest: {e}",
            manifest_path=manifest_path,
        ) from e

    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        raise ManifestError(
            f"malformed YAML in manifest: {e}",
            manifest_path=manifest_path,
        ) from e

    if not isinstance(raw, dict):
        raise ManifestError(
            f"manifest root must be a mapping, got {type(raw).__name__}",
            manifest_path=manifest_path,
        )

    # --- required fields ---------------------------------------------------

    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise ManifestError(
            "required field 'name' missing or empty",
            manifest_path=manifest_path,
        )
    if not NAME_PATTERN.fullmatch(name):
        raise ManifestError(
            f"'name' must match {NAME_PATTERN.pattern!r}, got {name!r}",
            addon_name=name,
            manifest_path=manifest_path,
        )

    version = raw.get("version")
    if not isinstance(version, str) or not version:
        raise ManifestError(
            "required field 'version' missing or empty",
            addon_name=name,
            manifest_path=manifest_path,
        )

    # --- optional top-level fields -----------------------------------------

    description = _str_or_default(raw, "description", "", name, manifest_path)
    author = _str_or_default(raw, "author", "", name, manifest_path)

    kind = raw.get("kind", "standalone")
    if not isinstance(kind, str) or kind not in ALLOWED_KINDS:
        raise ManifestError(
            f"'kind' must be one of {sorted(ALLOWED_KINDS)}, got {kind!r}",
            addon_name=name,
            manifest_path=manifest_path,
        )

    requires = _parse_requires(raw.get("requires") or {}, name, manifest_path)
    provides = _parse_provides(raw.get("provides") or {}, name, manifest_path)
    config_schema = _parse_config_schema(
        raw.get("config_schema") or {}, name, manifest_path
    )

    return Manifest(
        name=name,
        version=version,
        description=description,
        author=author,
        kind=kind,
        requires=requires,
        provides=provides,
        config_schema=config_schema,
        source_path=manifest_path,
    )


# ---------- internal parsers -------------------------------------------------


def _str_or_default(
    raw: dict, key: str, default: str, addon_name: str, path: Path
) -> str:
    """Accept a string or ``None`` (→ default). Reject other types loudly."""
    value = raw.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ManifestError(
            f"'{key}' must be a string, got {type(value).__name__}",
            addon_name=addon_name,
            manifest_path=path,
        )
    return value


def _parse_requires(raw: Any, addon_name: str, path: Path) -> Requires:
    if not isinstance(raw, dict):
        raise ManifestError(
            f"'requires' must be a mapping, got {type(raw).__name__}",
            addon_name=addon_name,
            manifest_path=path,
        )

    vexis_agent_req = raw.get("vexis_agent")
    if vexis_agent_req is not None and not isinstance(vexis_agent_req, str):
        raise ManifestError(
            "'requires.vexis_agent' must be a version string",
            addon_name=addon_name,
            manifest_path=path,
        )

    python_req = raw.get("python")
    if python_req is not None and not isinstance(python_req, str):
        raise ManifestError(
            "'requires.python' must be a version string",
            addon_name=addon_name,
            manifest_path=path,
        )

    mcp_raw = raw.get("mcp_servers") or []
    if not isinstance(mcp_raw, list):
        raise ManifestError(
            "'requires.mcp_servers' must be a list",
            addon_name=addon_name,
            manifest_path=path,
        )
    mcp_servers: list[McpRequirement] = []
    for i, entry in enumerate(mcp_raw):
        if isinstance(entry, str):
            mcp_servers.append(McpRequirement(name=entry, optional=False))
        elif isinstance(entry, dict):
            mcp_name = entry.get("name")
            if not isinstance(mcp_name, str) or not mcp_name:
                raise ManifestError(
                    f"'requires.mcp_servers[{i}].name' missing or not a string",
                    addon_name=addon_name,
                    manifest_path=path,
                )
            optional = bool(entry.get("optional", False))
            mcp_servers.append(McpRequirement(name=mcp_name, optional=optional))
        else:
            raise ManifestError(
                f"'requires.mcp_servers[{i}]' must be string or mapping, "
                f"got {type(entry).__name__}",
                addon_name=addon_name,
                manifest_path=path,
            )

    env_raw = raw.get("env") or []
    if not isinstance(env_raw, list) or not all(isinstance(v, str) for v in env_raw):
        raise ManifestError(
            "'requires.env' must be a list of strings",
            addon_name=addon_name,
            manifest_path=path,
        )

    return Requires(
        vexis_agent=vexis_agent_req,
        python=python_req,
        mcp_servers=tuple(mcp_servers),
        env=tuple(env_raw),
    )


def _parse_provides(raw: Any, addon_name: str, path: Path) -> Provides:
    if not isinstance(raw, dict):
        raise ManifestError(
            f"'provides' must be a mapping, got {type(raw).__name__}",
            addon_name=addon_name,
            manifest_path=path,
        )

    def _str_tuple(key: str) -> tuple[str, ...]:
        value = raw.get(key) or []
        if not isinstance(value, list) or not all(
            isinstance(v, str) for v in value
        ):
            raise ManifestError(
                f"'provides.{key}' must be a list of strings",
                addon_name=addon_name,
                manifest_path=path,
            )
        return tuple(value)

    return Provides(
        telegram_commands=_str_tuple("telegram_commands"),
        watcher_sources=_str_tuple("watcher_sources"),
        background_tasks=_str_tuple("background_tasks"),
        dispatch_handlers=_str_tuple("dispatch_handlers"),
        skills=_str_tuple("skills"),
        header_blocks=_str_tuple("header_blocks"),
        dashboard_pages=_str_tuple("dashboard_pages"),
        mcp_server_defaults=_str_tuple("mcp_server_defaults"),
    )


_ALLOWED_CONFIG_TYPES = frozenset({"str", "int", "float", "bool", "list", "dict"})


def _parse_config_schema(
    raw: Any, addon_name: str, path: Path
) -> dict[str, ConfigField]:
    if not isinstance(raw, dict):
        raise ManifestError(
            f"'config_schema' must be a mapping, got {type(raw).__name__}",
            addon_name=addon_name,
            manifest_path=path,
        )

    out: dict[str, ConfigField] = {}
    for key, entry in raw.items():
        if not isinstance(key, str):
            raise ManifestError(
                f"'config_schema' keys must be strings, got {type(key).__name__}",
                addon_name=addon_name,
                manifest_path=path,
            )
        if not isinstance(entry, dict):
            raise ManifestError(
                f"'config_schema.{key}' must be a mapping",
                addon_name=addon_name,
                manifest_path=path,
            )
        field_type = entry.get("type")
        if field_type not in _ALLOWED_CONFIG_TYPES:
            raise ManifestError(
                f"'config_schema.{key}.type' must be one of "
                f"{sorted(_ALLOWED_CONFIG_TYPES)}, got {field_type!r}",
                addon_name=addon_name,
                manifest_path=path,
            )
        description = entry.get("description")
        if description is not None and not isinstance(description, str):
            raise ManifestError(
                f"'config_schema.{key}.description' must be a string",
                addon_name=addon_name,
                manifest_path=path,
            )
        out[key] = ConfigField(
            type=field_type,
            default=entry.get("default"),
            description=description,
        )
    return out

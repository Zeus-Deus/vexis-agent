"""Manifest parser tests for the add-on system.

Pinned shape: every required field rejection comes out as a
``ManifestError`` (never a raw ``KeyError`` / ``YAMLError``), and
every message names the offending field path. The loader catches
``ManifestError`` and logs-and-continues, so any other exception
escaping ``parse_manifest`` is a bug. These tests lock the
boundary.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vexis_agent.core.addons.errors import ManifestError
from vexis_agent.core.addons.manifest import (
    ALLOWED_KINDS,
    Manifest,
    McpRequirement,
    find_manifest_file,
    parse_manifest,
)

# ---------- helpers ----------------------------------------------------------


def _write(tmp_path: Path, content: str, name: str = "addon.yaml") -> Path:
    """Drop a manifest file in tmp_path and return its path."""
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


MINIMAL = """\
name: myaddon
version: 1.0.0
"""


# ---------- happy path -------------------------------------------------------


def test_minimal_manifest_parses(tmp_path: Path) -> None:
    """A manifest with only the required fields is valid; everything
    else gets sensible defaults."""
    path = _write(tmp_path, MINIMAL)
    m = parse_manifest(path)
    assert isinstance(m, Manifest)
    assert m.name == "myaddon"
    assert m.version == "1.0.0"
    assert m.description == ""
    assert m.author == ""
    assert m.kind == "standalone"
    assert m.requires.vexis_agent is None
    assert m.provides.telegram_commands == ()
    assert m.config_schema == {}
    assert m.source_path == path


def test_full_manifest_parses(tmp_path: Path) -> None:
    """Every documented field round-trips."""
    path = _write(
        tmp_path,
        """\
name: codemux
version: 1.2.3
description: "Codemux watcher"
author: "Zeus-Deus"
kind: standalone
requires:
  vexis_agent: ">=0.9.0"
  python: ">=3.11"
  mcp_servers:
    - name: codemux
      optional: false
    - bare-string-form
  env: ["FOO", "BAR"]
provides:
  telegram_commands: ["codemux"]
  watcher_sources: ["codemux"]
  background_tasks: ["poller"]
  dispatch_handlers: ["watch_register"]
  skills: ["codemux.md"]
  header_blocks: ["active-work"]
  dashboard_pages: ["codemux"]
  mcp_server_defaults: ["codemux"]
config_schema:
  poll_interval_seconds:
    type: float
    default: 5.0
    description: "how often to poll"
  some_str:
    type: str
""",
    )
    m = parse_manifest(path)
    assert m.description == "Codemux watcher"
    assert m.requires.vexis_agent == ">=0.9.0"
    assert m.requires.python == ">=3.11"
    assert m.requires.mcp_servers == (
        McpRequirement(name="codemux", optional=False),
        McpRequirement(name="bare-string-form", optional=False),
    )
    assert m.requires.env == ("FOO", "BAR")
    assert m.provides.telegram_commands == ("codemux",)
    assert m.provides.dispatch_handlers == ("watch_register",)
    assert m.config_schema["poll_interval_seconds"].default == 5.0
    assert m.config_schema["poll_interval_seconds"].type == "float"
    assert m.config_schema["some_str"].default is None


def test_addon_yml_also_accepted(tmp_path: Path) -> None:
    """``.yml`` extension is accepted for users who prefer it."""
    path = _write(tmp_path, MINIMAL, name="addon.yml")
    m = parse_manifest(path)
    assert m.name == "myaddon"


def test_find_manifest_prefers_yaml(tmp_path: Path) -> None:
    """When both addon.yaml and addon.yml exist, .yaml wins (canonical)."""
    _write(tmp_path, MINIMAL, name="addon.yml")
    yaml_path = _write(tmp_path, "name: yaml-wins\nversion: 0.0.1\n",
                       name="addon.yaml")
    found = find_manifest_file(tmp_path)
    assert found == yaml_path


# ---------- required-field rejections ---------------------------------------


def test_missing_name_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "version: 1.0.0\n")
    with pytest.raises(ManifestError, match="'name'"):
        parse_manifest(path)


def test_missing_version_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, "name: foo\n")
    with pytest.raises(ManifestError, match="'version'"):
        parse_manifest(path)


def test_name_pattern_enforced(tmp_path: Path) -> None:
    """Names must be lowercase-with-hyphens; CamelCase and underscores
    rejected so the slug is safe in log namespaces, config keys, and
    paths."""
    path = _write(tmp_path, "name: My_Addon\nversion: 1.0.0\n")
    with pytest.raises(ManifestError, match="'name' must match"):
        parse_manifest(path)


def test_unknown_kind_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path, "name: foo\nversion: 1.0.0\nkind: wizardly\n"
    )
    with pytest.raises(ManifestError, match="'kind' must be one of"):
        parse_manifest(path)


def test_all_kinds_accepted(tmp_path: Path) -> None:
    """Sanity check the ALLOWED_KINDS constant is wired."""
    for kind in ALLOWED_KINDS:
        path = _write(
            tmp_path, f"name: foo\nversion: 1.0.0\nkind: {kind}\n",
            name=f"addon-{kind}.yaml",
        )
        m = parse_manifest(path)
        assert m.kind == kind


# ---------- malformed-YAML / wrong-type rejections --------------------------


def test_yaml_parse_error_wrapped(tmp_path: Path) -> None:
    path = _write(tmp_path, "name: foo\nversion: : bad yaml [\n")
    with pytest.raises(ManifestError, match="malformed YAML"):
        parse_manifest(path)


def test_root_must_be_mapping(tmp_path: Path) -> None:
    path = _write(tmp_path, "- just\n- a list\n")
    with pytest.raises(ManifestError, match="root must be a mapping"):
        parse_manifest(path)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="not found"):
        parse_manifest(tmp_path / "nope.yaml")


def test_requires_mcp_servers_must_be_list(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
name: foo
version: 1.0.0
requires:
  mcp_servers: "not-a-list"
""",
    )
    with pytest.raises(ManifestError, match="mcp_servers"):
        parse_manifest(path)


def test_requires_env_must_be_list_of_strings(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
name: foo
version: 1.0.0
requires:
  env: [123, "BAR"]
""",
    )
    with pytest.raises(ManifestError, match="env"):
        parse_manifest(path)


def test_provides_field_must_be_list_of_strings(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
name: foo
version: 1.0.0
provides:
  telegram_commands: [valid, 99]
""",
    )
    with pytest.raises(ManifestError, match="telegram_commands"):
        parse_manifest(path)


def test_config_schema_unknown_type_rejected(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
name: foo
version: 1.0.0
config_schema:
  weird:
    type: matrix
""",
    )
    with pytest.raises(ManifestError, match="must be one of"):
        parse_manifest(path)


def test_config_schema_entry_must_be_mapping(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
name: foo
version: 1.0.0
config_schema:
  bad: "scalar"
""",
    )
    with pytest.raises(ManifestError, match="config_schema.bad"):
        parse_manifest(path)


def test_mcp_server_entry_missing_name(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """\
name: foo
version: 1.0.0
requires:
  mcp_servers:
    - optional: true
""",
    )
    with pytest.raises(ManifestError, match=r"mcp_servers\[0\]"):
        parse_manifest(path)


def test_addon_error_carries_addon_name(tmp_path: Path) -> None:
    """Errors raised after the name is parsed carry ``addon_name`` so
    logs can include it without re-parsing."""
    path = _write(
        tmp_path,
        """\
name: foo
version: 1.0.0
kind: invalid
""",
    )
    with pytest.raises(ManifestError) as excinfo:
        parse_manifest(path)
    assert excinfo.value.addon_name == "foo"
    assert excinfo.value.manifest_path == path

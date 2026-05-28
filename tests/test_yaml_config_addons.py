"""yaml_config helpers for the add-on system.

Pins the explicit-opt-in policy: with no config file (or an empty
``addons:`` block), the addons system loads NOTHING — even bundled
add-ons require a user to name them under ``addons.enabled``. This
test file is what makes that contract executable.

Also pins the malformed-input handling: a stray non-string entry
in ``enabled`` is silently dropped, not raised — bad YAML in this
file shouldn't crash the daemon's startup, just produce harmless
"unknown addon" log lines downstream.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from vexis_agent.core import yaml_config


def _patch_config(tmp_path: Path, body: str | None):
    if body is not None:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(body, encoding="utf-8")

    def fake_vexis_dir() -> Path:
        return tmp_path

    return mock.patch(
        "vexis_agent.core.yaml_config.vexis_dir",
        side_effect=fake_vexis_dir,
    )


# ---------- addons_enabled --------------------------------------------------


def test_addons_enabled_default_empty(tmp_path):
    """No config file → no addons. Matches the loader's strictest
    discovery default."""
    with _patch_config(tmp_path, body=None):
        assert yaml_config.addons_enabled() == []


def test_addons_enabled_missing_section(tmp_path):
    """Config exists but no ``addons:`` section → still no addons.
    Lets users who don't use addons leave the section out entirely."""
    body = "memory:\n  memory_char_limit: 2200\n"
    with _patch_config(tmp_path, body=body):
        assert yaml_config.addons_enabled() == []


def test_addons_enabled_returns_list(tmp_path):
    body = "addons:\n  enabled: [codemux, foo, bar]\n"
    with _patch_config(tmp_path, body=body):
        assert yaml_config.addons_enabled() == ["codemux", "foo", "bar"]


def test_addons_enabled_drops_non_strings(tmp_path):
    """Stray ``- 99`` or ``- true`` in YAML is silently dropped —
    bad config can't crash startup."""
    body = "addons:\n  enabled:\n    - codemux\n    - 99\n    - true\n    - foo\n"
    with _patch_config(tmp_path, body=body):
        assert yaml_config.addons_enabled() == ["codemux", "foo"]


def test_addons_enabled_drops_empty_strings(tmp_path):
    """An empty-string entry would discover-against an addon named
    ``""``; safer to drop it at the config layer."""
    body = 'addons:\n  enabled: ["codemux", ""]\n'
    with _patch_config(tmp_path, body=body):
        assert yaml_config.addons_enabled() == ["codemux"]


def test_addons_enabled_not_a_list(tmp_path):
    """``enabled: codemux`` (a scalar) is malformed — treat as empty."""
    body = "addons:\n  enabled: codemux\n"
    with _patch_config(tmp_path, body=body):
        assert yaml_config.addons_enabled() == []


# ---------- addons_disabled -------------------------------------------------


def test_addons_disabled_default_empty(tmp_path):
    with _patch_config(tmp_path, body=None):
        assert yaml_config.addons_disabled() == []


def test_addons_disabled_returns_list(tmp_path):
    body = "addons:\n  enabled: [a, b]\n  disabled: [b]\n"
    with _patch_config(tmp_path, body=body):
        assert yaml_config.addons_disabled() == ["b"]


# ---------- addon_config (per-addon slice) ----------------------------------


def test_addon_config_default_empty(tmp_path):
    with _patch_config(tmp_path, body=None):
        assert yaml_config.addon_config("codemux") == {}


def test_addon_config_returns_slice(tmp_path):
    body = (
        "addons:\n"
        "  enabled: [codemux]\n"
        "  codemux:\n"
        "    poll_interval_seconds: 2.5\n"
        "    idle_after_seconds: 45\n"
    )
    with _patch_config(tmp_path, body=body):
        cfg = yaml_config.addon_config("codemux")
        assert cfg == {"poll_interval_seconds": 2.5, "idle_after_seconds": 45}


def test_addon_config_missing_addon_returns_empty(tmp_path):
    """Asking for an addon that has no user-config slice gives back
    an empty dict (NOT a KeyError) — add-ons can then layer their
    manifest defaults on top without a None check."""
    body = "addons:\n  enabled: [codemux]\n"
    with _patch_config(tmp_path, body=body):
        assert yaml_config.addon_config("nonexistent") == {}


def test_addon_config_non_dict_returns_empty(tmp_path):
    """A scalar at ``addons.<name>`` is malformed; return empty."""
    body = "addons:\n  enabled: [foo]\n  foo: this_is_not_a_dict\n"
    with _patch_config(tmp_path, body=body):
        assert yaml_config.addon_config("foo") == {}

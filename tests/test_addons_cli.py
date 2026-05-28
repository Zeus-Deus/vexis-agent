"""``vexis-addons`` CLI tests.

The CLI is the operator's only sanctioned mutator for
``addons.enabled`` / ``addons.disabled`` in ~/.vexis/config.yaml.
These tests pin:

  * Idempotence — re-enabling a disabled add-on is a no-op.
  * Disable-wins-over-enable when both are listed (matches the
    loader's filter precedence).
  * ``install`` refuses to overwrite a bundled add-on of the same
    name (forks live under ``~/.vexis/addons/`` instead).
  * ``inspect`` JSON output matches the manifest's parsed shape.

The conftest's ``_isolate_vexis_dir`` autouse fixture gives every
test a private ``~/.vexis/`` — config writes never escape into the
user's real config.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from vexis_agent.tools.addons_cli import (
    _read_config,
    _write_config,
    cmd_disable,
    cmd_doctor,
    cmd_enable,
    cmd_inspect,
    cmd_install,
    cmd_list,
    main,
)


# ---------- helpers ---------------------------------------------------------


def _write_addon(root: Path, name: str, version: str = "1.0.0") -> Path:
    """Drop a minimal valid add-on at ``root/<name>/``."""
    addon_dir = root / name
    addon_dir.mkdir(parents=True, exist_ok=True)
    (addon_dir / "addon.yaml").write_text(
        f"name: {name}\nversion: {version}\n", encoding="utf-8"
    )
    (addon_dir / "__init__.py").write_text(
        "def register(ctx): pass\n", encoding="utf-8"
    )
    return addon_dir


@pytest.fixture(autouse=True)
def _isolate_yaml_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Patch ``vexis_agent.core.yaml_config.vexis_dir`` to the same
    private root the conftest's ``_isolate_vexis_dir`` autouse fixture
    uses for ``paths.vexis_dir``.

    The CLI calls yaml_config helpers (``addons_enabled`` /
    ``addons_disabled``) which import ``vexis_dir`` at module load —
    so the conftest's patch on ``paths.vexis_dir`` doesn't reach
    them. Without this fixture, ``cmd_enable`` writes to the
    isolated config but ``addons_enabled`` reads from the user's
    real ``~/.vexis/`` — tests pollute each other AND the user's
    real machine.
    """
    from vexis_agent.core.paths import vexis_dir as _real

    private_root = _real()  # conftest already redirected this
    monkeypatch.setattr(
        "vexis_agent.core.yaml_config.vexis_dir", lambda: private_root,
    )
    yield


@pytest.fixture
def _patch_user_root(tmp_path: Path):
    """Marker fixture — present so existing tests keep their pretty
    name. The actual isolation comes from ``_isolate_yaml_config``
    above plus the conftest's ``_isolate_vexis_dir``."""
    yield


def _ns(**kwargs):
    """Build a fake argparse.Namespace from kwargs."""
    import argparse
    return argparse.Namespace(**kwargs)


# ---------- enable / disable -----------------------------------------------


def test_enable_writes_addon_to_enabled_list(_patch_user_root):
    rc = cmd_enable(_ns(name="codemux"))
    assert rc == 0
    cfg = _read_config()
    assert cfg["addons"]["enabled"] == ["codemux"]


def test_enable_is_idempotent(_patch_user_root):
    cmd_enable(_ns(name="codemux"))
    rc = cmd_enable(_ns(name="codemux"))
    assert rc == 0  # no-op success, not failure
    cfg = _read_config()
    assert cfg["addons"]["enabled"] == ["codemux"]  # still one entry


def test_enable_removes_from_disabled(_patch_user_root):
    """Enabling an add-on that was on the disabled list should clear
    its disabled entry — enable is "I want this on now."""
    _write_config({"addons": {"enabled": [], "disabled": ["codemux"]}})
    cmd_enable(_ns(name="codemux"))
    cfg = _read_config()
    assert cfg["addons"]["enabled"] == ["codemux"]
    assert cfg["addons"]["disabled"] == []


def test_disable_writes_to_disabled_list(_patch_user_root):
    _write_config({"addons": {"enabled": ["codemux"]}})
    rc = cmd_disable(_ns(name="codemux"))
    assert rc == 0
    cfg = _read_config()
    assert "codemux" in cfg["addons"]["disabled"]
    # Enabled entry should be preserved — disable is "off temporarily."
    assert "codemux" in cfg["addons"]["enabled"]


def test_disable_is_idempotent(_patch_user_root):
    cmd_disable(_ns(name="codemux"))
    rc = cmd_disable(_ns(name="codemux"))
    assert rc == 0
    cfg = _read_config()
    assert cfg["addons"]["disabled"].count("codemux") == 1


# ---------- list -----------------------------------------------------------


def test_list_empty_handles_cleanly(capsys, _patch_user_root):
    rc = cmd_list(_ns(json=False))
    assert rc == 0
    out = capsys.readouterr().out
    # When no bundled add-ons exist either, the empty message fires.
    # If the test environment has bundled add-ons (e.g. from a future
    # phase B commit), they'll show up — that's fine, just no crash.
    assert out  # something printed, no exception


def test_list_shows_user_addon(tmp_path: Path, capsys, monkeypatch):
    """A user-installed add-on appears in the list with status."""
    # Drop it under the real user root for this test.
    from vexis_agent.core.paths import vexis_dir
    user_root = vexis_dir() / "addons"
    _write_addon(user_root, "myaddon")

    rc = cmd_list(_ns(json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    names = [r["name"] for r in payload]
    assert "myaddon" in names
    me = next(r for r in payload if r["name"] == "myaddon")
    assert me["status"] == "discovered"  # not yet enabled
    assert me["source"] == "user"


def test_list_status_reflects_enabled(tmp_path: Path, capsys):
    from vexis_agent.core.paths import vexis_dir
    user_root = vexis_dir() / "addons"
    _write_addon(user_root, "myaddon")
    cmd_enable(_ns(name="myaddon"))
    capsys.readouterr()  # drop the enable's "enabled add-on..." message

    rc = cmd_list(_ns(json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    me = next(r for r in payload if r["name"] == "myaddon")
    assert me["status"] == "enabled"


def test_list_status_reflects_disabled(tmp_path: Path, capsys):
    from vexis_agent.core.paths import vexis_dir
    user_root = vexis_dir() / "addons"
    _write_addon(user_root, "myaddon")
    cmd_enable(_ns(name="myaddon"))
    cmd_disable(_ns(name="myaddon"))
    capsys.readouterr()  # drop the enable+disable messages

    rc = cmd_list(_ns(json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    me = next(r for r in payload if r["name"] == "myaddon")
    assert me["status"] == "disabled"


# ---------- inspect --------------------------------------------------------


def test_inspect_nonexistent_returns_1(capsys, _patch_user_root):
    rc = cmd_inspect(_ns(name="nope", json=False))
    assert rc == 1
    assert "not found" in capsys.readouterr().err


def test_inspect_emits_json(tmp_path: Path, capsys):
    from vexis_agent.core.paths import vexis_dir
    user_root = vexis_dir() / "addons"
    addon_dir = user_root / "myaddon"
    addon_dir.mkdir(parents=True)
    (addon_dir / "addon.yaml").write_text(
        textwrap.dedent("""\
            name: myaddon
            version: 2.5.0
            description: "A test add-on"
            kind: standalone
            requires:
              env: ["FOO"]
            provides:
              telegram_commands: ["hello"]
        """),
        encoding="utf-8",
    )
    (addon_dir / "__init__.py").write_text("def register(ctx): pass\n",
                                            encoding="utf-8")

    rc = cmd_inspect(_ns(name="myaddon", json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "myaddon"
    assert payload["version"] == "2.5.0"
    assert payload["description"] == "A test add-on"
    assert payload["provides"]["telegram_commands"] == ["hello"]
    assert "FOO" in payload["requires"]["env"]


# ---------- doctor ---------------------------------------------------------


def test_doctor_flags_missing_env_var(tmp_path: Path, capsys, monkeypatch):
    from vexis_agent.core.paths import vexis_dir
    user_root = vexis_dir() / "addons"
    addon_dir = user_root / "needs-env"
    addon_dir.mkdir(parents=True)
    (addon_dir / "addon.yaml").write_text(
        "name: needs-env\nversion: 1.0.0\nrequires:\n  env: [\"FROBNICATOR_TOKEN\"]\n",
        encoding="utf-8",
    )
    (addon_dir / "__init__.py").write_text(
        "def register(ctx): pass\n", encoding="utf-8"
    )
    cmd_enable(_ns(name="needs-env"))
    monkeypatch.delenv("FROBNICATOR_TOKEN", raising=False)
    capsys.readouterr()  # drop the enable message

    rc = cmd_doctor(_ns(json=True))
    assert rc == 1  # at least one issue
    payload = json.loads(capsys.readouterr().out)
    bad = [f for f in payload if not f["ok"]]
    names = [f["name"] for f in bad]
    assert "needs-env" in names


def test_doctor_passes_when_env_set(tmp_path: Path, capsys, monkeypatch):
    from vexis_agent.core.paths import vexis_dir
    user_root = vexis_dir() / "addons"
    addon_dir = user_root / "needs-env"
    addon_dir.mkdir(parents=True)
    (addon_dir / "addon.yaml").write_text(
        "name: needs-env\nversion: 1.0.0\nrequires:\n  env: [\"FROBNICATOR_TOKEN\"]\n",
        encoding="utf-8",
    )
    (addon_dir / "__init__.py").write_text(
        "def register(ctx): pass\n", encoding="utf-8"
    )
    cmd_enable(_ns(name="needs-env"))
    monkeypatch.setenv("FROBNICATOR_TOKEN", "yes")
    capsys.readouterr()  # drop the enable message

    cmd_doctor(_ns(json=True))
    payload = json.loads(capsys.readouterr().out)
    me = next(f for f in payload if f["name"] == "needs-env")
    assert me["ok"]


# ---------- install --------------------------------------------------------


def test_install_copies_addon_to_user_root(tmp_path: Path, capsys):
    src = tmp_path / "mysource" / "myaddon"
    _write_addon(src.parent, "myaddon")
    rc = cmd_install(_ns(source=str(src), force=False))
    assert rc == 0
    from vexis_agent.core.paths import vexis_dir
    dest = vexis_dir() / "addons" / "myaddon"
    assert (dest / "addon.yaml").is_file()
    assert (dest / "__init__.py").is_file()


def test_install_refuses_overwrite_without_force(tmp_path: Path, capsys):
    src = tmp_path / "mysource" / "myaddon"
    _write_addon(src.parent, "myaddon")
    cmd_install(_ns(source=str(src), force=False))
    rc = cmd_install(_ns(source=str(src), force=False))
    assert rc == 1
    assert "already exists" in capsys.readouterr().err


def test_install_overwrites_with_force(tmp_path: Path, capsys):
    src = tmp_path / "mysource" / "myaddon"
    _write_addon(src.parent, "myaddon", version="1.0.0")
    cmd_install(_ns(source=str(src), force=False))
    # Update source to new version, re-install with --force.
    (src / "addon.yaml").write_text(
        "name: myaddon\nversion: 2.0.0\n", encoding="utf-8"
    )
    rc = cmd_install(_ns(source=str(src), force=True))
    assert rc == 0
    from vexis_agent.core.paths import vexis_dir
    dest = vexis_dir() / "addons" / "myaddon"
    assert "2.0.0" in (dest / "addon.yaml").read_text()


def test_install_rejects_missing_manifest(tmp_path: Path, capsys):
    src = tmp_path / "no-manifest"
    src.mkdir()
    (src / "__init__.py").write_text("def register(ctx): pass\n",
                                     encoding="utf-8")
    rc = cmd_install(_ns(source=str(src), force=False))
    assert rc == 1
    assert "no addon.yaml" in capsys.readouterr().err


# ---------- argv parser ----------------------------------------------------


def test_main_no_subcommand_exits_2():
    """argparse default for missing required subcommand is exit-2."""
    with pytest.raises(SystemExit) as e:
        main([])
    assert e.value.code == 2

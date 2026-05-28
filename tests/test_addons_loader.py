"""Loader tests — discovery + ``register()`` invocation.

Discovery is exercised across all three roots (bundled / user /
project) with the env-var gate for project discovery. The actual
``register()`` invocation drives a programmatically-built fixture
add-on so we control exactly what it does (no on-disk fixture
needed for the basic happy path).

The pinned contract:

* ``discover_addons`` filters by ``enabled`` / ``disabled``.
* First root wins on name conflict; later roots log + skip.
* Malformed manifests log + skip, never raise.
* ``load_addon`` returns ``True`` only when ``register()`` completes
  cleanly.
* A failed ``register()`` is recorded on the runtime AND ``False``
  is returned — the daemon survives.
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

import pytest

from vexis_agent.core.addons import (
    AddonRuntime,
    PROJECT_ADDONS_ENV,
    bundled_addons_root,
    build_addon_config,
    discover_addons,
    load_addon,
    parse_manifest,
    project_addons_root,
    user_addons_root,
)


# ---------- discovery helpers -----------------------------------------------


def _write_addon(
    root: Path,
    name: str,
    *,
    manifest_extra: str = "",
    register_body: str = "    pass",
) -> Path:
    """Build a complete add-on under ``root/<name>/`` and return its dir.

    ``register_body`` is plain Python that goes into the body of
    ``register(ctx)``. It must already include its own leading
    indentation (4 spaces per level). Keeping the helper dumb
    avoids the textwrap-mangling subtleties that bit the earlier
    version of this fixture.
    """
    addon_dir = root / name
    addon_dir.mkdir(parents=True, exist_ok=True)
    manifest_lines = [f"name: {name}", "version: 1.0.0"]
    if manifest_extra:
        manifest_lines.append(manifest_extra)
    (addon_dir / "addon.yaml").write_text(
        "\n".join(manifest_lines) + "\n", encoding="utf-8"
    )
    init_lines = [
        f"'''Test fixture add-on {name!r}.'''",
        "from vexis_agent.core.addons import PluginContext",
        "",
        "def register(ctx: PluginContext) -> None:",
        register_body,
    ]
    (addon_dir / "__init__.py").write_text(
        "\n".join(init_lines) + "\n", encoding="utf-8"
    )
    return addon_dir


# ---------- discovery -------------------------------------------------------


def test_discover_finds_enabled_addon(tmp_path: Path) -> None:
    user_root = tmp_path / "user_addons"
    _write_addon(user_root, "alpha")
    found = discover_addons(
        enabled=["alpha"],
        bundled_root=tmp_path / "no-bundled",
        user_root=user_root,
        project_root_enabled=False,
    )
    assert len(found) == 1
    assert found[0].manifest.name == "alpha"
    assert found[0].source == "user"


def test_discover_skips_when_not_enabled(tmp_path: Path) -> None:
    user_root = tmp_path / "user_addons"
    _write_addon(user_root, "alpha")
    # enabled=[] explicitly — opt-in only, even with the add-on on disk.
    found = discover_addons(
        enabled=[],
        bundled_root=tmp_path / "no-bundled",
        user_root=user_root,
        project_root_enabled=False,
    )
    assert found == []


def test_discover_skips_when_disabled(tmp_path: Path) -> None:
    user_root = tmp_path / "user_addons"
    _write_addon(user_root, "alpha")
    found = discover_addons(
        enabled=["alpha"],
        disabled=["alpha"],
        bundled_root=tmp_path / "no-bundled",
        user_root=user_root,
        project_root_enabled=False,
    )
    assert found == []


def test_discover_first_root_wins_on_conflict(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Bundled add-on shadows a user add-on with the same name."""
    bundled = tmp_path / "bundled"
    user = tmp_path / "user"
    _write_addon(bundled, "shared", manifest_extra='description: "from-bundled"')
    _write_addon(user, "shared", manifest_extra='description: "from-user"')

    with caplog.at_level(logging.WARNING):
        found = discover_addons(
            enabled=["shared"],
            bundled_root=bundled,
            user_root=user,
            project_root_enabled=False,
        )

    assert len(found) == 1
    assert found[0].manifest.description == "from-bundled"
    assert found[0].source == "bundled"
    assert any("shadows" in rec.message for rec in caplog.records)


def test_discover_skips_malformed_manifest(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    user = tmp_path / "user"
    user.mkdir()
    bad = user / "broken"
    bad.mkdir()
    (bad / "addon.yaml").write_text("name: : not valid\n", encoding="utf-8")
    (bad / "__init__.py").write_text("def register(ctx): pass\n", encoding="utf-8")

    good = _write_addon(user, "good")

    with caplog.at_level(logging.WARNING):
        found = discover_addons(
            enabled=["good", "broken"],
            bundled_root=tmp_path / "none",
            user_root=user,
            project_root_enabled=False,
        )

    # The broken one is dropped, the good one survives.
    assert [d.manifest.name for d in found] == ["good"]
    assert any("skipping addon" in rec.message for rec in caplog.records)
    assert good.is_dir()  # not deleted


def test_discover_ignores_non_addon_directories(tmp_path: Path) -> None:
    """A directory without an ``addon.yaml`` is silently ignored —
    users have all sorts of junk under ``~/.vexis/addons/``."""
    user = tmp_path / "user"
    user.mkdir()
    (user / "not-an-addon").mkdir()
    (user / "not-an-addon" / "README.md").write_text("ignore me",
                                                     encoding="utf-8")
    _write_addon(user, "real")

    found = discover_addons(
        enabled=["real"],
        bundled_root=tmp_path / "none",
        user_root=user,
        project_root_enabled=False,
    )
    assert [d.manifest.name for d in found] == ["real"]


def test_discover_project_root_gated_by_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Project add-ons (./.vexis/addons/) only load when the env var
    is set — they're disabled by default to avoid surprise behaviour
    when ``vexis-agent`` is run from random shells."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    monkeypatch.chdir(project_dir)
    project_addons = project_dir / ".vexis" / "addons"
    _write_addon(project_addons, "projecty")

    # Default: env var unset → project root ignored.
    monkeypatch.delenv(PROJECT_ADDONS_ENV, raising=False)
    found_off = discover_addons(
        enabled=["projecty"],
        bundled_root=tmp_path / "none-b",
        user_root=tmp_path / "none-u",
    )
    assert found_off == []

    # With env var: project root scanned.
    monkeypatch.setenv(PROJECT_ADDONS_ENV, "1")
    found_on = discover_addons(
        enabled=["projecty"],
        bundled_root=tmp_path / "none-b",
        user_root=tmp_path / "none-u",
    )
    assert len(found_on) == 1
    assert found_on[0].source == "project"


def test_discover_missing_root_is_silent(tmp_path: Path) -> None:
    """A nonexistent discovery root is fine — most users don't have
    a ``~/.vexis/addons/`` directory."""
    found = discover_addons(
        enabled=["x"],
        bundled_root=tmp_path / "no-such-dir",
        user_root=tmp_path / "also-missing",
        project_root_enabled=False,
    )
    assert found == []


# ---------- load_addon (register invocation) --------------------------------


def test_load_addon_success(tmp_path: Path) -> None:
    user = tmp_path / "user"
    _write_addon(
        user,
        "happy",
        register_body=(
            "    async def _handler(update, context):\n"
            "        return None\n"
            "    ctx.register_telegram_command(\n"
            '        "happy", _handler,\n'
            '        menu_description="happy command",\n'
            "    )"
        ),
    )
    [discovered] = discover_addons(
        enabled=["happy"],
        bundled_root=tmp_path / "none",
        user_root=user,
        project_root_enabled=False,
    )
    runtime = AddonRuntime()
    ok = load_addon(discovered, runtime)
    assert ok is True
    cmds = list(runtime.telegram_commands())
    assert len(cmds) == 1
    assert cmds[0].name == "happy"

    loaded = runtime.loaded_addons()
    assert len(loaded) == 1
    assert loaded[0].register_ok is True
    assert loaded[0].register_error is None


def test_load_addon_register_failure_recorded(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A failing ``register()`` doesn't kill the daemon — it's logged
    and recorded as a failed load."""
    user = tmp_path / "user"
    _write_addon(
        user,
        "sad",
        register_body="    raise RuntimeError('boom')",
    )
    [discovered] = discover_addons(
        enabled=["sad"],
        bundled_root=tmp_path / "none",
        user_root=user,
        project_root_enabled=False,
    )
    runtime = AddonRuntime()

    with caplog.at_level(logging.ERROR):
        ok = load_addon(discovered, runtime)

    assert ok is False
    loaded = runtime.loaded_addons()
    assert loaded[0].register_ok is False
    assert "boom" in (loaded[0].register_error or "")
    assert any("register() failed" in r.message for r in caplog.records)


def test_load_addon_missing_register_function(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An add-on whose ``__init__.py`` defines no ``register`` is
    rejected with a clear message."""
    user = tmp_path / "user"
    addon_dir = user / "no-register"
    addon_dir.mkdir(parents=True)
    (addon_dir / "addon.yaml").write_text(
        "name: no-register\nversion: 1.0.0\n", encoding="utf-8"
    )
    (addon_dir / "__init__.py").write_text(
        "# no register here\n", encoding="utf-8"
    )

    [discovered] = discover_addons(
        enabled=["no-register"],
        bundled_root=tmp_path / "none",
        user_root=user,
        project_root_enabled=False,
    )
    runtime = AddonRuntime()
    with caplog.at_level(logging.ERROR):
        ok = load_addon(discovered, runtime)
    assert ok is False
    assert any("no callable 'register'" in r.message for r in caplog.records)


def test_load_addon_import_error_recorded(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An add-on whose ``__init__.py`` raises at import time is
    rejected; the rest of the daemon proceeds."""
    user = tmp_path / "user"
    addon_dir = user / "broken-import"
    addon_dir.mkdir(parents=True)
    (addon_dir / "addon.yaml").write_text(
        "name: broken-import\nversion: 1.0.0\n", encoding="utf-8"
    )
    (addon_dir / "__init__.py").write_text(
        "raise ImportError('cannot import')\n", encoding="utf-8"
    )

    [discovered] = discover_addons(
        enabled=["broken-import"],
        bundled_root=tmp_path / "none",
        user_root=user,
        project_root_enabled=False,
    )
    runtime = AddonRuntime()
    with caplog.at_level(logging.ERROR):
        ok = load_addon(discovered, runtime)
    assert ok is False
    assert any("failed to import" in r.message for r in caplog.records)


# ---------- config merging --------------------------------------------------


def test_build_addon_config_uses_manifest_defaults(tmp_path: Path) -> None:
    manifest_path = tmp_path / "addon.yaml"
    manifest_path.write_text(
        textwrap.dedent(
            """\
            name: x
            version: 1.0.0
            config_schema:
              poll_interval_seconds:
                type: float
                default: 5.0
              greeting:
                type: str
                default: "hi"
            """
        ),
        encoding="utf-8",
    )
    manifest = parse_manifest(manifest_path)
    cfg = build_addon_config(manifest, user_values=None)
    assert cfg.get("poll_interval_seconds") == 5.0
    assert cfg.get("greeting") == "hi"


def test_build_addon_config_user_overrides_defaults(tmp_path: Path) -> None:
    manifest_path = tmp_path / "addon.yaml"
    manifest_path.write_text(
        textwrap.dedent(
            """\
            name: x
            version: 1.0.0
            config_schema:
              poll_interval_seconds:
                type: float
                default: 5.0
            """
        ),
        encoding="utf-8",
    )
    manifest = parse_manifest(manifest_path)
    cfg = build_addon_config(manifest, user_values={"poll_interval_seconds": 10.0})
    assert cfg.get("poll_interval_seconds") == 10.0


def test_build_addon_config_passes_through_unknown_keys(tmp_path: Path) -> None:
    """User-supplied keys not in the schema survive — gives add-ons a
    forward-compat escape hatch."""
    manifest_path = tmp_path / "addon.yaml"
    manifest_path.write_text("name: x\nversion: 1.0.0\n", encoding="utf-8")
    manifest = parse_manifest(manifest_path)
    cfg = build_addon_config(manifest, user_values={"experimental_flag": True})
    assert cfg.get("experimental_flag") is True


# ---------- root-path helpers -----------------------------------------------


def test_bundled_addons_root_points_at_package(tmp_path: Path) -> None:
    """The bundled root resolves under the installed package dir."""
    root = bundled_addons_root()
    assert root.name == "addons"
    # It should live under a directory named vexis_agent
    assert root.parent.name == "vexis_agent"


def test_user_addons_root_accepts_explicit_home(tmp_path: Path) -> None:
    """Passing ``vexis_home`` explicitly is the supported test-friendly
    path. (The no-arg form pulls from ``core.paths.vexis_dir``, which
    the conftest's ``_isolate_vexis_dir`` autouse fixture stubs to a
    pytest-managed temp dir — so the no-arg form has its own coverage
    via every other test that loads add-ons.)"""
    root = user_addons_root(vexis_home=tmp_path)
    assert root == tmp_path / "addons"


def test_project_addons_root_uses_cwd(tmp_path: Path) -> None:
    root = project_addons_root(cwd=tmp_path)
    assert root == tmp_path / ".vexis" / "addons"

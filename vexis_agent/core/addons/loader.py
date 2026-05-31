"""Discover add-ons on disk, parse their manifests, run their
``register(ctx)`` entry points.

Discovery scans three roots in precedence order; the first definition
of a given add-on name wins, with a warning logged for overrides:

    1. Bundled — ``vexis_agent/addons/<name>/`` shipped in the wheel.
    2. User    — ``~/.vexis/addons/<name>/`` (or ``$VEXIS_HOME/addons/``).
    3. Project — ``./.vexis/addons/<name>/``, opt-in via the
       ``VEXIS_ENABLE_PROJECT_ADDONS=1`` env var. Off by default
       because cwd-based discovery would surprise users running
       ``vexis-agent`` from random shells.

After discovery the loader applies the user config gate:
``addons.enabled`` is an explicit allow-list; add-ons not named there
are skipped (even bundled ones — matches CLAUDE.md's "core stays
simple" goal). ``addons.disabled`` wins over ``enabled`` so a user
who hits a bug can kill an add-on without unsetting its enabled
entry.

Import isolation: each add-on is imported under a unique module name
(``vexis_addons.<name>``) using ``importlib.util.spec_from_file_location``,
so two add-ons with the same internal layout don't collide in
``sys.modules``. A failed ``register()`` is logged with a full
traceback and the loader continues — one broken add-on never kills
the daemon.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .context import AddonConfig, make_context
from .errors import ManifestError
from .manifest import (
    ConfigField,
    Manifest,
    find_manifest_file,
    parse_manifest,
)
from .registry import AddonRuntime, LoadedAddon

#: Env var used by tests + project add-ons. Setting it to "1" enables
#: discovery of ``./.vexis/addons/`` relative to ``Path.cwd()``.
PROJECT_ADDONS_ENV = "VEXIS_ENABLE_PROJECT_ADDONS"

#: Module-name prefix every add-on is imported under. Keeps add-on
#: imports namespaced so ``import addons.foo`` from inside the addon
#: doesn't accidentally hit some other package on ``sys.path``.
MODULE_NAMESPACE = "vexis_addons"

#: Bundled add-ons that load by default — without an explicit
#: ``addons.enabled`` entry — UNLESS the user opts out via
#: ``addons.disabled``. This is the carve-out for capabilities that
#: used to be hardcoded into core and were extracted into add-ons:
#: an existing user upgrading from such a build has a config with no
#: ``addons.enabled`` line, and a pure explicit-allow-list would
#: silently strip the capability on first restart. ``browser`` was
#: previously always-on core (the nine ``if op == browser_*`` dispatch
#: branches in main.py), so it MUST survive the extraction with no
#: config edit. The gate is source-scoped to ``"bundled"`` so a
#: user/project add-on of the same name still needs an explicit opt-in,
#: and ``addons.disabled`` always wins so the off switch keeps working.
DEFAULT_ENABLED_BUNDLED = frozenset({"browser"})


@dataclass(frozen=True)
class DiscoveredAddon:
    """One add-on directory + its parsed manifest.

    Returned by :func:`discover_addons` BEFORE ``register()`` is
    invoked. The caller (typically ``main.py``) decides whether to
    load it based on requirements checks or test-only filtering.
    """

    addon_dir: Path
    manifest: Manifest
    source: str  # "bundled" | "user" | "project"


def bundled_addons_root() -> Path:
    """Return the path to ``vexis_agent/addons/`` shipped in the wheel.

    This is the only one of the three discovery roots that's always
    available — it's literally inside the installed package. Used by
    Phase B's codemux extraction to ship the add-on alongside the
    daemon binary.
    """
    # core/addons/loader.py → vexis_agent/core/addons/loader.py
    # parents[2] gives us vexis_agent/, then we go to addons/
    return Path(__file__).resolve().parents[2] / "addons"


def user_addons_root(vexis_home: Optional[Path] = None) -> Path:
    """``~/.vexis/addons/`` or ``$VEXIS_HOME/addons/``.

    Doesn't auto-create the directory — discovery handles missing
    roots silently (the user just doesn't have any user add-ons).
    """
    if vexis_home is None:
        # Local import keeps this module free of the broader vexis
        # init chain at import time — useful for tests that import
        # the loader in isolation.
        from vexis_agent.core.paths import vexis_dir

        vexis_home = vexis_dir()
    return vexis_home / "addons"


def project_addons_root(cwd: Optional[Path] = None) -> Path:
    """``./.vexis/addons/`` — opt-in via env var. See module docstring."""
    return (cwd or Path.cwd()) / ".vexis" / "addons"


def discover_addons(
    *,
    enabled: Optional[list[str]] = None,
    disabled: Optional[list[str]] = None,
    bundled_root: Optional[Path] = None,
    user_root: Optional[Path] = None,
    project_root_enabled: Optional[bool] = None,
    default_enabled_bundled: Optional[frozenset[str]] = None,
    log: Optional[logging.Logger] = None,
) -> list[DiscoveredAddon]:
    """Walk all discovery roots and return successfully-parsed add-ons.

    Filtering by ``enabled`` / ``disabled``:

      * If ``enabled`` is ``None``: nothing passes EXCEPT default-on
        bundled add-ons (see below) — the strictest default for
        non-bundled sources.
      * If ``enabled`` is an empty list ``[]``: same as ``None`` for
        non-bundled add-ons (explicit "no add-ons" mode for user /
        project sources). Default-on bundled add-ons still load.
      * If ``enabled`` is a list of names: those names pass, plus any
        default-on bundled add-on not in ``disabled``.
      * ``disabled`` wins: even an enabled (or default-on bundled)
        add-on is skipped if also listed in ``disabled``.

    Default-on bundled add-ons (``default_enabled_bundled``, defaulting
    to :data:`DEFAULT_ENABLED_BUNDLED`): a bundled-source add-on in this
    set loads even when its name is absent from ``enabled`` — but only
    when it's also absent from ``disabled``. This is the upgrade-safety
    carve-out: capabilities extracted from core into bundled add-ons
    (browser) must NOT silently vanish for users whose pre-extraction
    config has no ``addons.enabled`` line. The gate is source-scoped:
    a user / project add-on of the same name still needs an explicit
    opt-in (no shadowing-by-default). Pass an empty frozenset to get
    the old pure-allow-list behaviour (tests do this when they want to
    assert the explicit gate in isolation).

    Manifest parse errors are logged and the offending add-on is
    skipped; everything else continues. This way one broken add-on
    in ``~/.vexis/addons/`` doesn't take down the daemon.

    ``bundled_root`` / ``user_root`` / ``project_root_enabled`` are
    overrides for tests. Production calls pass nothing and gets the
    real paths + env-var-gated project discovery.
    """
    logger = log or logging.getLogger("vexis_agent.addons.loader")

    enabled_set = set(enabled or [])
    disabled_set = set(disabled or [])
    default_bundled = (
        DEFAULT_ENABLED_BUNDLED
        if default_enabled_bundled is None
        else default_enabled_bundled
    )

    if project_root_enabled is None:
        project_root_enabled = os.environ.get(PROJECT_ADDONS_ENV) == "1"

    roots: list[tuple[Path, str]] = [
        (bundled_root or bundled_addons_root(), "bundled"),
        (user_root or user_addons_root(), "user"),
    ]
    if project_root_enabled:
        roots.append((project_addons_root(), "project"))

    discovered: dict[str, DiscoveredAddon] = {}

    for root, source in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            manifest_path = find_manifest_file(child)
            if manifest_path is None:
                # Silent — the dir might be a partial install, a
                # scratch folder, or anything else. We only care
                # about dirs with a manifest.
                continue
            try:
                manifest = parse_manifest(manifest_path)
            except ManifestError as e:
                logger.warning(
                    "skipping addon at %s: %s", child, e
                )
                continue

            name = manifest.name

            # Apply enable/disable gate BEFORE recording so a disabled
            # add-on can't accidentally shadow an enabled one from a
            # later root. Note: this means a disabled bundled add-on
            # doesn't block a user add-on with the same name (which
            # is the desired behaviour — you can replace bundled
            # codemux with a fork by disabling the bundled one in
            # config + dropping your fork in ~/.vexis/addons/).
            if name in disabled_set:
                logger.info("addon %r is disabled in config; skipping", name)
                continue
            # Default-on bundled add-ons load without an explicit
            # ``addons.enabled`` entry (upgrade safety for capabilities
            # extracted from core — see DEFAULT_ENABLED_BUNDLED).
            # ``disabled`` was already checked above and still wins.
            default_on = source == "bundled" and name in default_bundled
            if name not in enabled_set and not default_on:
                logger.debug(
                    "addon %r found at %s but not enabled in config; skipping",
                    name,
                    child,
                )
                continue

            if name in discovered:
                existing = discovered[name]
                logger.warning(
                    "addon %r in %s shadows earlier %s at %s; first wins",
                    name,
                    child,
                    existing.source,
                    existing.addon_dir,
                )
                continue

            discovered[name] = DiscoveredAddon(
                addon_dir=child,
                manifest=manifest,
                source=source,
            )

    return list(discovered.values())


def build_addon_config(
    manifest: Manifest, user_values: Optional[dict[str, Any]] = None
) -> AddonConfig:
    """Merge manifest defaults with the user's ``addons.<name>.*``.

    User values win over manifest defaults; unknown keys (not in the
    manifest's ``config_schema``) are kept as-is so an add-on can
    accept ad-hoc keys for forward-compatibility. Type coercion is
    not done here — add-ons that care about types should validate
    in their own ``register()``.
    """
    merged: dict[str, Any] = {}
    for key, schema in manifest.config_schema.items():
        merged[key] = _default_for(schema)
    if user_values:
        merged.update(user_values)
    return AddonConfig(values=merged)


def load_addon(
    discovered: DiscoveredAddon,
    runtime: AddonRuntime,
    *,
    user_config: Optional[dict[str, Any]] = None,
    user_id: Optional[str] = None,
    log: Optional[logging.Logger] = None,
) -> bool:
    """Import the add-on's package and invoke ``register(ctx)``.

    Returns ``True`` on success, ``False`` on failure (always logged
    with a full traceback). Failures don't propagate — the loader's
    contract with the daemon is "best-effort, report afterwards", so
    every add-on gets its shot regardless of which one errors.

    The loaded record (whether success or failure) is recorded on
    ``runtime`` so the dashboard / CLI can show it.
    """
    logger = log or logging.getLogger("vexis_agent.addons.loader")

    loaded = LoadedAddon(
        manifest=discovered.manifest,
        addon_dir=discovered.addon_dir,
    )
    runtime.record_loaded(loaded)

    config = build_addon_config(discovered.manifest, user_config)
    ctx = make_context(
        runtime,
        addon_name=discovered.manifest.name,
        addon_dir=discovered.addon_dir,
        config=config,
        user_id=user_id,
    )

    try:
        module = _import_addon_module(discovered)
    except Exception as e:  # noqa: BLE001 — wrap & continue
        msg = f"failed to import add-on {discovered.manifest.name!r}: {e}"
        logger.error(msg, exc_info=True)
        runtime.mark_register_result(
            discovered.manifest.name, ok=False, error=msg
        )
        return False

    register = getattr(module, "register", None)
    if register is None or not callable(register):
        msg = (
            f"add-on {discovered.manifest.name!r} has no callable "
            f"'register' in {discovered.addon_dir / '__init__.py'}"
        )
        logger.error(msg)
        runtime.mark_register_result(
            discovered.manifest.name, ok=False, error=msg
        )
        return False

    try:
        register(ctx)
    except Exception as e:  # noqa: BLE001 — wrap & continue
        tb = traceback.format_exc()
        msg = f"register() failed for add-on {discovered.manifest.name!r}: {e}"
        logger.error("%s\n%s", msg, tb)
        runtime.mark_register_result(
            discovered.manifest.name, ok=False, error=msg
        )
        # Loader contract is "best-effort, report afterwards": never
        # propagate the failure — one broken add-on can't kill the
        # daemon. ``vexis addons doctor`` re-runs each add-on with
        # ``raise_on_error=True`` to surface the traceback to users
        # who want to debug.
        return False

    runtime.mark_register_result(discovered.manifest.name, ok=True)
    return True


# ---------- internals --------------------------------------------------------


def _default_for(schema: ConfigField) -> Any:
    """Return the default value for a config-schema field.

    Falls back to a zero-value (``""``, ``0``, ``0.0``, ``False``,
    ``[]``, ``{}``) when ``default`` isn't set, matching the
    declared type so callers always get back the right shape.
    """
    if schema.default is not None:
        return schema.default
    return {
        "str": "",
        "int": 0,
        "float": 0.0,
        "bool": False,
        "list": [],
        "dict": {},
    }.get(schema.type)


def _import_addon_module(discovered: DiscoveredAddon) -> Any:
    """Import the add-on's ``__init__.py`` under
    ``vexis_addons.<name>``.

    Uses ``importlib.util.spec_from_file_location`` so the import
    works whether the add-on lives inside the installed wheel
    (``vexis_agent/addons/<name>/``), under ``~/.vexis/addons/``, or
    in a project directory — no matter what's on ``sys.path``.
    """
    module_name = f"{MODULE_NAMESPACE}.{discovered.manifest.name.replace('-', '_')}"
    init_path = discovered.addon_dir / "__init__.py"

    if not init_path.is_file():
        raise FileNotFoundError(
            f"add-on {discovered.manifest.name!r} has no __init__.py "
            f"at {init_path}"
        )

    spec = importlib.util.spec_from_file_location(
        module_name,
        init_path,
        submodule_search_locations=[str(discovered.addon_dir)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(
            f"could not build import spec for add-on "
            f"{discovered.manifest.name!r} at {init_path}"
        )

    module = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec so the add-on's own intra-
    # package imports (``from .submodule import X``) resolve correctly.
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        # Roll back the sys.modules entry on import failure so a
        # subsequent retry sees a clean slate (matters for tests and
        # for `vexis addons reload` if we ever add it).
        sys.modules.pop(module_name, None)
        raise
    return module

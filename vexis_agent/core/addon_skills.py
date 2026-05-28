"""Copy add-on-registered skill files into the workspace.

Add-ons declare skills via ``ctx.register_skill(skill_file)``;
the daemon walks those registrations at startup and copies each
file into ``<workspace>/skills/addons/<addon_name>/<filename>``.
Idempotent — re-running on the same content is a no-op; updated
content triggers an atomic overwrite.

The "addons/" subdir keeps add-on-supplied skills in their own
namespace under the workspace's skills/ root, so they don't
collide with user-authored or curator-authored skills and so the
dashboard can render them with an [addon] badge similar to how
bundled and installed skills are tagged today.

A ``.provenance.json`` sidecar marks each addon skill as
auto-managed (the same convention the existing ``skill_install``
layer uses), so the learning curator leaves them alone.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def install_addon_skills(workspace: Path, runtime: Any) -> int:
    """Copy every add-on-registered skill into the workspace.

    Returns the count of skills installed (NOT skipped).
    Idempotent on identical content — re-running just verifies
    hashes. Failures on individual skills are logged but don't
    abort the rest of the installation; a misbehaving add-on
    can't poison the workspace's skill set.
    """
    skills_root = workspace / "skills" / "addons"
    skills_root.mkdir(parents=True, exist_ok=True)
    installed = 0

    for reg in runtime.skills():
        try:
            n = _install_one(skills_root, reg)
            installed += n
        except Exception:
            log.exception(
                "failed to install skill %s from add-on %r",
                reg.skill_file, reg.addon_name,
            )

    if installed:
        log.info(
            "addon_skills: installed %d skill file(s) under %s",
            installed, skills_root,
        )
    return installed


def _install_one(skills_root: Path, reg: Any) -> int:
    """Copy one ``SkillRegistration`` into the workspace.

    Layout: ``<skills_root>/<addon_name>/<target_subdir>/<filename>``.
    ``target_subdir == "."`` means "directly under <addon_name>/".
    Returns 1 if the file was written (new or content-changed), 0
    if skipped (content hash matched).
    """
    addon_dir = skills_root / reg.addon_name
    if reg.target_subdir and reg.target_subdir != ".":
        target_dir = addon_dir / reg.target_subdir
    else:
        target_dir = addon_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / reg.skill_file.name

    src_bytes = reg.skill_file.read_bytes()
    src_hash = hashlib.sha256(src_bytes).hexdigest()

    if target.is_file():
        existing_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        if existing_hash == src_hash:
            return 0  # no-op: identical content

    # Atomic overwrite via tmp-rename.
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_bytes(src_bytes)
    tmp.replace(target)

    # Provenance sidecar — marks the file as add-on-owned so the
    # learning curator skips it and the dashboard can render an
    # [addon] badge.
    sidecar = target.with_suffix(target.suffix + ".provenance.json")
    sidecar_data: dict[str, Any] = {
        "kind": "addon",
        "addon_name": reg.addon_name,
        "source_path": str(reg.skill_file),
        "sha256": src_hash,
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    sidecar_tmp = sidecar.with_suffix(sidecar.suffix + ".tmp")
    sidecar_tmp.write_text(
        json.dumps(sidecar_data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    sidecar_tmp.replace(sidecar)

    return 1


def uninstall_addon_skills(workspace: Path, addon_name: str) -> int:
    """Remove every skill installed by ``addon_name`` from the workspace.

    Called when an add-on is disabled — keeps the workspace tidy
    instead of leaving orphan skills the brain still thinks it has.
    Returns count of files removed. Safe on missing dirs (returns 0).
    """
    addon_dir = workspace / "skills" / "addons" / addon_name
    if not addon_dir.is_dir():
        return 0
    removed = sum(1 for _ in addon_dir.rglob("*") if _.is_file())
    shutil.rmtree(addon_dir)
    log.info(
        "addon_skills: removed %d file(s) from %s", removed, addon_dir,
    )
    return removed

"""``vexis-agent uninstall`` — remove the install cleanly.

Three layers, each independently opt-out:

  * **service**  — stops and uninstalls the systemd user unit.
                   Always safe; the unit is regenerable from
                   ``vexis-agent service install``.
  * **package**  — removes the pipx venv (or warns about editable
                   installs since pip uninstall doesn't drop the
                   source tree). Only acts on pipx-managed installs
                   by default.
  * **state**    — deletes ``$VEXIS_HOME`` and optionally
                   ``$VEXIS_WORKSPACE``. **Destructive — opt-in
                   via --purge-state.** Without this flag, your
                   memories / skills / config survive uninstall so
                   re-installing later picks up where you left off.

The CLI prompts before each destructive step unless ``--yes`` is
passed. ``--dry-run`` enumerates what would happen without doing
anything.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class UninstallPlan:
    """What ``run_uninstall`` will do. Tests + the CLI's --dry-run
    render this directly."""

    service_unit: Optional[Path] = None
    pipx_venv: Optional[Path] = None
    editable_warning: Optional[str] = None
    state_dirs_to_purge: list[Path] = field(default_factory=list)
    workspace_to_purge: Optional[Path] = None

    def describe(self) -> str:
        lines = ["vexis-agent uninstall plan:"]
        if self.service_unit:
            lines.append(f"  ✓ stop + remove systemd unit: {self.service_unit}")
        if self.pipx_venv:
            lines.append(f"  ✓ remove pipx venv:           {self.pipx_venv}")
        if self.editable_warning:
            lines.append(f"  ! {self.editable_warning}")
        if self.state_dirs_to_purge:
            for p in self.state_dirs_to_purge:
                lines.append(f"  ✗ DESTROY:                    {p}")
        if self.workspace_to_purge:
            lines.append(f"  ✗ DESTROY workspace:          {self.workspace_to_purge}")
        if len(lines) == 1:
            lines.append("  (nothing to do)")
        return "\n".join(lines)


def build_plan(*, purge_state: bool, purge_workspace: bool) -> UninstallPlan:
    """Inspect the current install + state and produce an UninstallPlan.
    Pure inspection — no side effects."""
    from vexis_agent.core.paths import vexis_dir
    from vexis_agent.daemon.systemd import user_unit_path
    from vexis_agent.daemon.update import detect_install_type, InstallType
    from vexis_agent.setup_wizard import workspace_path

    plan = UninstallPlan()

    unit = user_unit_path()
    if unit.exists():
        plan.service_unit = unit

    info = detect_install_type()
    if info.kind is InstallType.PIPX and info.pipx_venv is not None:
        plan.pipx_venv = info.pipx_venv
    elif info.kind is InstallType.EDITABLE:
        plan.editable_warning = (
            f"Editable install at {info.source_root} — uninstall the package "
            f"with 'pip uninstall vexis-agent' from your venv, then remove "
            f"the checkout by hand if desired."
        )
    elif info.kind is InstallType.UNKNOWN:
        plan.editable_warning = (
            "Couldn't detect install type. Remove the package manually with "
            "'pipx uninstall vexis-agent' or 'pip uninstall vexis-agent'."
        )

    if purge_state:
        home = vexis_dir()
        if home.exists():
            plan.state_dirs_to_purge.append(home)
        # systemd unit dir we don't blast — that's user config the
        # user might have customized for unrelated services.

    if purge_workspace:
        ws = workspace_path()
        if ws.exists():
            plan.workspace_to_purge = ws

    return plan


def run_uninstall(
    plan: UninstallPlan,
    *,
    confirm: bool = True,
) -> int:
    """Execute the plan. Returns 0 on success, 1 on partial failure.

    With ``confirm=True``, prompts before each destructive step.
    With ``confirm=False`` (e.g. --yes), proceeds without prompting.
    """
    failures: list[str] = []

    if plan.service_unit is not None:
        if confirm and not _yn(f"Stop + remove {plan.service_unit}? [y/N] "):
            print("  skipped service unit removal.")
        else:
            try:
                from vexis_agent.daemon.systemd import uninstall_user_unit

                uninstall_user_unit()
                print(f"  removed {plan.service_unit}")
            except Exception as exc:  # pragma: no cover — defensive
                failures.append(f"service uninstall: {exc}")

    if plan.pipx_venv is not None:
        if confirm and not _yn(f"Remove pipx venv {plan.pipx_venv}? [y/N] "):
            print("  skipped pipx venv removal.")
        else:
            if shutil.which("pipx"):
                rc = subprocess.run(
                    ["pipx", "uninstall", "vexis-agent"],
                    capture_output=False,
                ).returncode
                if rc != 0:
                    failures.append(f"pipx uninstall returned {rc}")
            else:
                failures.append("pipx not on PATH — remove the venv by hand")

    if plan.editable_warning is not None:
        print(f"  ! {plan.editable_warning}")

    for path in plan.state_dirs_to_purge:
        if confirm and not _yn(
            f"DESTROY {path}? This deletes config, curator state, "
            f"learning state, goals, dashboard token, .env. [y/N] "
        ):
            print(f"  skipped {path}.")
            continue
        try:
            shutil.rmtree(path)
            print(f"  removed {path}")
        except OSError as exc:
            failures.append(f"rmtree {path}: {exc}")

    if plan.workspace_to_purge is not None:
        if confirm and not _yn(
            f"DESTROY workspace {plan.workspace_to_purge}? This deletes "
            f"CLAUDE.md, SOUL.md, MEMORY.md, USER.md, RELATIONSHIPS.md, "
            f"memories/, skills/. [y/N] "
        ):
            print(f"  skipped workspace removal.")
        else:
            try:
                shutil.rmtree(plan.workspace_to_purge)
                print(f"  removed {plan.workspace_to_purge}")
            except OSError as exc:
                failures.append(f"rmtree {plan.workspace_to_purge}: {exc}")

    if failures:
        for f in failures:
            print(f"  ! {f}")
        return 1
    return 0


def _yn(prompt: str) -> bool:
    """Default-no y/n. Anything other than 'y'/'yes' (case-insensitive)
    is treated as no — destructive ops shouldn't take a typo as
    permission."""
    try:
        raw = input(prompt).strip().lower()
    except EOFError:
        return False
    return raw in {"y", "yes"}

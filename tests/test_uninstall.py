"""Phase 5: vexis-agent uninstall.

Layered removal: service unit / package / state. Default is
non-destructive (preserves $VEXIS_HOME). State teardown is
opt-in via --purge-state; workspace teardown via --purge-workspace.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from vexis_agent.daemon import uninstall as un
from vexis_agent.daemon.update import InstallType, InstallInfo


# ── plan describe / build ─────────────────────────────────────────


def test_describe_empty_plan() -> None:
    plan = un.UninstallPlan()
    out = plan.describe()
    assert "nothing to do" in out


def test_describe_shows_service_and_pipx() -> None:
    plan = un.UninstallPlan(
        service_unit=Path("/x/vexis-agent.service"),
        pipx_venv=Path("/p/vexis-agent"),
    )
    out = plan.describe()
    assert "/x/vexis-agent.service" in out
    assert "/p/vexis-agent" in out


def test_describe_state_purge_marked_destroy() -> None:
    plan = un.UninstallPlan(state_dirs_to_purge=[Path("/h/.vexis")])
    out = plan.describe()
    assert "DESTROY" in out
    assert "/h/.vexis" in out


def test_describe_editable_warning_surfaces() -> None:
    plan = un.UninstallPlan(editable_warning="run pip uninstall by hand")
    out = plan.describe()
    assert "pip uninstall by hand" in out


# ── build_plan integration with detect_install_type ───────────────


def test_build_plan_skips_state_unless_opted_in(tmp_path, monkeypatch) -> None:
    """Default: state survives uninstall. Re-installing later picks
    up where the user left off."""
    home = tmp_path / "v"
    home.mkdir()
    monkeypatch.setattr("vexis_agent.core.paths.vexis_dir", lambda: home)
    monkeypatch.setattr(
        un, "build_plan", un.build_plan,
    )  # ensure name shadowing doesn't bite

    plan = un.build_plan(purge_state=False, purge_workspace=False)
    assert plan.state_dirs_to_purge == []
    assert plan.workspace_to_purge is None


def test_build_plan_includes_state_when_opted_in(tmp_path, monkeypatch) -> None:
    home = tmp_path / "v"
    home.mkdir()
    monkeypatch.setattr("vexis_agent.core.paths.vexis_dir", lambda: home)
    monkeypatch.setenv("VEXIS_WORKSPACE", str(tmp_path / "ws-doesnt-exist"))
    plan = un.build_plan(purge_state=True, purge_workspace=False)
    assert home in plan.state_dirs_to_purge
    # Workspace not yet created → not in plan even with --purge-workspace
    assert plan.workspace_to_purge is None


def test_build_plan_includes_workspace_when_present_and_opted_in(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "v"
    ws = tmp_path / "ws"
    home.mkdir()
    ws.mkdir()
    monkeypatch.setattr("vexis_agent.core.paths.vexis_dir", lambda: home)
    monkeypatch.setenv("VEXIS_WORKSPACE", str(ws))
    plan = un.build_plan(purge_state=True, purge_workspace=True)
    assert plan.workspace_to_purge == ws


def test_build_plan_pipx_install_records_venv(tmp_path, monkeypatch) -> None:
    """build_plan looks up detect_install_type via lazy-import inside
    the function, so monkey-patching the source module is what matters."""
    venv = tmp_path / "pipx-venv"
    venv.mkdir()
    monkeypatch.setattr(
        "vexis_agent.daemon.update.detect_install_type",
        lambda: InstallInfo(
            kind=InstallType.PIPX,
            python_path=venv / "bin" / "python",
            pipx_venv=venv,
        ),
    )
    monkeypatch.setattr("vexis_agent.core.paths.vexis_dir", lambda: tmp_path / "v")
    plan = un.build_plan(purge_state=False, purge_workspace=False)
    assert plan.pipx_venv == venv


def test_build_plan_editable_warns(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    monkeypatch.setattr(
        "vexis_agent.daemon.update.detect_install_type",
        lambda: InstallInfo(
            kind=InstallType.EDITABLE,
            python_path=Path("/usr/bin/python3"),
            source_root=repo,
        ),
    )
    monkeypatch.setattr("vexis_agent.core.paths.vexis_dir", lambda: tmp_path / "v")
    plan = un.build_plan(purge_state=False, purge_workspace=False)
    assert plan.editable_warning is not None
    assert "pip uninstall" in plan.editable_warning
    assert plan.pipx_venv is None


# ── run_uninstall side effects ────────────────────────────────────


def test_run_uninstall_no_confirm_purges_state(tmp_path, monkeypatch) -> None:
    home = tmp_path / "v"
    home.mkdir()
    (home / "config.yaml").write_text("x")
    plan = un.UninstallPlan(state_dirs_to_purge=[home])
    rc = un.run_uninstall(plan, confirm=False)
    assert rc == 0
    assert not home.exists()


def test_run_uninstall_skips_when_user_says_no(tmp_path, monkeypatch) -> None:
    home = tmp_path / "v"
    home.mkdir()
    (home / "config.yaml").write_text("x")
    plan = un.UninstallPlan(state_dirs_to_purge=[home])
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
    rc = un.run_uninstall(plan, confirm=True)
    assert rc == 0
    assert home.exists()  # untouched


def test_run_uninstall_handles_empty_plan() -> None:
    plan = un.UninstallPlan()
    rc = un.run_uninstall(plan, confirm=False)
    assert rc == 0

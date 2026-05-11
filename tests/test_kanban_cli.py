"""``vexis-kanban`` CLI smoke tests.

Drives the Typer app via the in-process runner so we don't have to
spawn a real subprocess (faster, no pipx install required). Each
test points the DB path at a tmp path via the standard vexis_dir
fixture so it doesn't touch the user's real ``~/.vexis/kanban.db``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vexis_agent.tools.kanban.cli import app


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """Redirect both yaml_config.vexis_dir AND paths.vexis_dir (the
    action layer uses paths.vexis_dir via api.default_db_path)."""
    private_root = tmp_path / "_vexis_isolated"
    private_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "vexis_agent.core.yaml_config.vexis_dir", lambda: private_root,
    )
    monkeypatch.setattr(
        "vexis_agent.core.paths.vexis_dir", lambda: private_root,
    )
    # api.default_db_path captured paths.vexis_dir at import — patch
    # the module-level binding too.
    monkeypatch.setattr(
        "vexis_agent.tools.kanban.api.vexis_dir", lambda: private_root,
    )
    yield


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ──────────────────────────────────────────────────────────────────
# create / list / show
# ──────────────────────────────────────────────────────────────────


def test_create_emits_text(runner):
    r = runner.invoke(app, ["create", "hello world", "--json"])
    assert r.exit_code == 0
    body = json.loads(r.stdout)
    assert body["ok"] is True
    assert body["data"]["title"] == "hello world"


def test_create_with_lane_and_priority(runner):
    r = runner.invoke(app, [
        "create", "build it", "--lane", "implementation",
        "--priority", "5", "--json",
    ])
    assert r.exit_code == 0
    body = json.loads(r.stdout)
    assert body["data"]["lane"] == "implementation"
    assert body["data"]["priority"] == 5


def test_create_unknown_lane_exits_nonzero(runner):
    r = runner.invoke(app, ["create", "x", "--lane", "nope", "--json"])
    assert r.exit_code == 1
    body = json.loads(r.stdout)
    assert body["ok"] is False
    assert body["kind"] == "LaneNotFoundError"


def test_list_shows_summary_and_tasks(runner):
    runner.invoke(app, ["create", "t1", "--json"])
    runner.invoke(app, ["create", "t2", "--json"])
    r = runner.invoke(app, ["list", "--json"])
    assert r.exit_code == 0
    body = json.loads(r.stdout)
    assert body["ok"] is True
    assert len(body["data"]["tasks"]) == 2


def test_show_returns_full_detail(runner):
    r = runner.invoke(app, ["create", "t1", "--body", "body text", "--json"])
    tid = json.loads(r.stdout)["data"]["id"]
    r = runner.invoke(app, ["show", tid, "--json"])
    body = json.loads(r.stdout)
    assert body["ok"] is True
    assert body["data"]["task"]["title"] == "t1"
    assert body["data"]["task"]["body"] == "body text"


def test_show_missing_exits_nonzero(runner):
    r = runner.invoke(app, ["show", "ghost", "--json"])
    assert r.exit_code == 1


# ──────────────────────────────────────────────────────────────────
# complete / block / unblock / comment
# ──────────────────────────────────────────────────────────────────


def test_complete_task(runner):
    r = runner.invoke(app, ["create", "x", "--json"])
    tid = json.loads(r.stdout)["data"]["id"]
    r = runner.invoke(app, ["complete", tid, "--summary", "done", "--json"])
    assert r.exit_code == 0
    body = json.loads(r.stdout)
    assert body["data"]["status"] == "done"


def test_block_then_unblock(runner):
    r = runner.invoke(app, ["create", "x", "--json"])
    tid = json.loads(r.stdout)["data"]["id"]
    r = runner.invoke(app, ["block", tid, "waiting on X", "--json"])
    assert r.exit_code == 0
    assert json.loads(r.stdout)["data"]["status"] == "blocked"
    r = runner.invoke(app, ["unblock", tid, "--json"])
    assert r.exit_code == 0
    assert json.loads(r.stdout)["data"]["status"] == "ready"


def test_comment(runner):
    r = runner.invoke(app, ["create", "x", "--json"])
    tid = json.loads(r.stdout)["data"]["id"]
    r = runner.invoke(app, ["comment", tid, "looking good", "--json"])
    assert r.exit_code == 0


# ──────────────────────────────────────────────────────────────────
# archive / assign / link / unlink
# ──────────────────────────────────────────────────────────────────


def test_archive(runner):
    r = runner.invoke(app, ["create", "x", "--json"])
    tid = json.loads(r.stdout)["data"]["id"]
    r = runner.invoke(app, ["archive", tid, "--json"])
    assert r.exit_code == 0


def test_assign(runner):
    r = runner.invoke(app, ["create", "x", "--json"])
    tid = json.loads(r.stdout)["data"]["id"]
    r = runner.invoke(app, ["assign", tid, "research", "--json"])
    assert r.exit_code == 0
    assert json.loads(r.stdout)["data"]["lane"] == "research"


def test_link_unlink(runner):
    r1 = runner.invoke(app, ["create", "p", "--json"])
    r2 = runner.invoke(app, ["create", "c", "--json"])
    pid = json.loads(r1.stdout)["data"]["id"]
    cid = json.loads(r2.stdout)["data"]["id"]
    r = runner.invoke(app, ["link", pid, cid, "--json"])
    assert r.exit_code == 0
    r = runner.invoke(app, ["unlink", pid, cid, "--json"])
    assert r.exit_code == 0


def test_lanes_lists_defaults(runner):
    r = runner.invoke(app, ["lanes", "--json"])
    assert r.exit_code == 0
    body = json.loads(r.stdout)
    names = {lane["name"] for lane in body["data"]["lanes"]}
    for d in ("research", "implementation", "review", "ops", "triage", "default"):
        assert d in names


# ──────────────────────────────────────────────────────────────────
# heartbeat (requires a claim)
# ──────────────────────────────────────────────────────────────────


def test_heartbeat_with_lock(runner):
    """End-to-end: create → manually claim via the store → heartbeat
    succeeds with the right lock, fails with the wrong one."""
    from vexis_agent.tools.kanban import api
    from vexis_agent.core.kanban.constants import STATUS_READY
    r = runner.invoke(app, [
        "create", "x", "--status", "ready", "--json",
    ])
    tid = json.loads(r.stdout)["data"]["id"]
    # Claim manually so we know the lock.
    store = api.open_default_store()
    try:
        store.claim_task(tid, claim_lock="L1", ttl_seconds=60)
    finally:
        store.close()
    r = runner.invoke(app, [
        "heartbeat", tid, "--claim-lock", "L1", "--json",
    ])
    assert r.exit_code == 0
    r = runner.invoke(app, [
        "heartbeat", tid, "--claim-lock", "WRONG", "--json",
    ])
    assert r.exit_code == 1


# ──────────────────────────────────────────────────────────────────
# Human-readable output (non-JSON paths)
# ──────────────────────────────────────────────────────────────────


def test_human_output_show(runner):
    r = runner.invoke(app, ["create", "x", "--body", "b"])
    assert r.exit_code == 0
    # Output mentions the title in some form.
    assert "x" in r.stdout

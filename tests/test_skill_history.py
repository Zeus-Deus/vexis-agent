"""Per-skill version history — core/skill_history.py + dashboard routes.

Pinned behaviours:

  * create_skill records NO history (nothing to supersede); the first
    edit/patch is what produces version 1.
  * edit_skill / patch_skill snapshot the pre-edit SKILL.md, tagged
    with the ``actor`` they were called with.
  * The learning-curator flip (flip_shadow_to_live) snapshots the
    superseded live SKILL.md with actor="curator".
  * list_versions returns newest-first; read_version round-trips
    content; both reject path-traversal in name / version_id.
  * Retention prunes to MAX_VERSIONS_PER_SKILL.
  * GET  /api/v1/skills/{name}/history            — timeline
    GET  /api/v1/skills/{name}/history/{vid}      — content + diff
    POST /api/v1/skills/{name}/history/{vid}/restore — revert
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vexis_agent.core.skill_history import (
    MAX_VERSIONS_PER_SKILL,
    list_versions,
    read_version,
    record_version,
)
from vexis_agent.core.skills import create_skill, edit_skill, patch_skill
from vexis_agent.core.web_server import DashboardConfig, WebDashboard


def _md(name: str, body: str, desc: str = "a skill for history tests") -> str:
    return f"---\nname: {name}\ndescription: {desc}\n---\n{body}\n"


# ──────────────────────────────────────────────────────────────────
# core/skill_history.py — record / list / read
# ──────────────────────────────────────────────────────────────────


def test_create_records_no_history(tmp_path: Path):
    root = tmp_path / "skills"
    root.mkdir()
    create_skill(root, "alpha", _md("alpha", "# v1"))
    assert list_versions(root, "alpha") == []


def test_edit_snapshots_pre_edit_content(tmp_path: Path):
    root = tmp_path / "skills"
    root.mkdir()
    create_skill(root, "alpha", _md("alpha", "# v1 original"))
    edit_skill(root, "alpha", _md("alpha", "# v2 new"), actor="agent")

    versions = list_versions(root, "alpha")
    assert len(versions) == 1
    # The snapshot is the version that was *replaced* — v1, not v2.
    content = read_version(root, "alpha", versions[0].version_id)
    assert "# v1 original" in content
    assert "# v2 new" not in content


def test_patch_snapshots_and_tags_actor(tmp_path: Path):
    root = tmp_path / "skills"
    root.mkdir()
    create_skill(root, "alpha", _md("alpha", "# body one"))
    patch_skill(root, "alpha", "body one", "body two", actor="curator")

    versions = list_versions(root, "alpha")
    assert len(versions) == 1
    assert versions[0].actor == "curator"
    assert "# body one" in read_version(root, "alpha", versions[0].version_id)


def test_versions_are_newest_first_with_distinct_actors(tmp_path: Path):
    root = tmp_path / "skills"
    root.mkdir()
    create_skill(root, "alpha", _md("alpha", "# v1"))
    edit_skill(root, "alpha", _md("alpha", "# v2"), actor="agent")
    patch_skill(root, "alpha", "v2", "v3", actor="curator")
    edit_skill(root, "alpha", _md("alpha", "# v4"), actor="dashboard")

    versions = list_versions(root, "alpha")
    assert [v.actor for v in versions] == ["dashboard", "curator", "agent"]
    ids = [v.version_id for v in versions]
    assert ids == sorted(ids, reverse=True)


def test_unknown_actor_degrades_to_agent(tmp_path: Path):
    root = tmp_path / "skills"
    root.mkdir()
    record_version(root, "alpha", "snapshot", actor="hacker")
    assert list_versions(root, "alpha")[0].actor == "agent"


def test_retention_prunes_to_cap(tmp_path: Path):
    root = tmp_path / "skills"
    root.mkdir()
    for i in range(MAX_VERSIONS_PER_SKILL + 8):
        record_version(root, "alpha", f"version {i}", actor="agent")
    assert len(list_versions(root, "alpha")) == MAX_VERSIONS_PER_SKILL


def test_read_version_rejects_path_traversal(tmp_path: Path):
    root = tmp_path / "skills"
    root.mkdir()
    record_version(root, "alpha", "content", actor="agent")
    vid = list_versions(root, "alpha")[0].version_id
    assert read_version(root, "alpha", "../../etc/passwd") is None
    assert read_version(root, "../../etc", vid) is None
    assert read_version(root, "alpha", "not-a-version-id") is None


def test_empty_content_is_not_recorded(tmp_path: Path):
    root = tmp_path / "skills"
    root.mkdir()
    record_version(root, "alpha", "", actor="agent")
    assert list_versions(root, "alpha") == []


def test_list_versions_missing_skill_is_empty(tmp_path: Path):
    root = tmp_path / "skills"
    root.mkdir()
    assert list_versions(root, "never-existed") == []


def test_curator_flip_snapshots_superseded_version(tmp_path: Path):
    """A learning-curator S1 flip overwrites a live skill — the version
    it replaces must land in history tagged actor="curator"."""
    from vexis_agent.core.learning_writes import (
        flip_shadow_to_live,
        stage_skill_patch,
    )

    workspace = tmp_path / "ws"
    root = workspace / "skills"
    root.mkdir(parents=True)
    create_skill(root, "alpha", _md("alpha", "# live body"))

    staged = stage_skill_patch(workspace, "alpha", "live body", "patched body")
    assert staged.ok, staged.message
    results = flip_shadow_to_live(workspace, only_skill="alpha")
    assert all(r.ok for r in results), results

    versions = list_versions(root, "alpha")
    assert len(versions) == 1
    assert versions[0].actor == "curator"
    assert "# live body" in read_version(root, "alpha", versions[0].version_id)


# ──────────────────────────────────────────────────────────────────
# Dashboard routes
# ──────────────────────────────────────────────────────────────────


_TOKEN = "test-token-skill-history-cafebabe"


def _build_dashboard(workspace: Path) -> WebDashboard:
    dashboard = WebDashboard.__new__(WebDashboard)
    dashboard._workspace = workspace
    dashboard._token = _TOKEN
    dashboard._learning = None
    dashboard._relationships_mutation_window_seconds = 600
    dashboard._relationships_mutation_limit = 100
    dashboard._relationships_mutation_log = defaultdict(deque)
    dashboard._config = DashboardConfig(
        host="127.0.0.1", port=0, web_dist=workspace / "no-frontend",
    )
    dashboard._tailscale_url = None
    dashboard._tailscale_dns = None
    dashboard._server = None
    dashboard._serve_task = None
    dashboard._started_at = datetime.now(timezone.utc)
    dashboard._sessions = None
    dashboard._running_tasks = None
    dashboard._background_tasks = None
    dashboard._curator = None
    dashboard._browser = None
    dashboard._chat = None
    dashboard._running_brain_kind = None
    dashboard._profile_size_cache = None
    dashboard._schedule_store = None
    dashboard._kanban_store = None
    dashboard._app = dashboard._build_app()
    return dashboard


@pytest.fixture(autouse=True)
def _bundled_off(monkeypatch, tmp_path):
    monkeypatch.setenv("VEXIS_BUNDLED_SKILLS", str(tmp_path / "no-bundled"))
    yield


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return TestClient(_build_dashboard(workspace)._app)


def _auth() -> dict:
    return {"Authorization": f"Bearer {_TOKEN}"}


def _seed(client: TestClient, name: str = "demo") -> None:
    """Create a skill, then edit it twice so it has 2 history entries."""
    client.post(
        "/api/v1/skills", headers=_auth(),
        json={"name": name, "content": _md(name, "# original")},
    )
    client.put(
        f"/api/v1/skills/{name}", headers=_auth(),
        json={"content": _md(name, "# second")},
    )
    client.put(
        f"/api/v1/skills/{name}", headers=_auth(),
        json={"content": _md(name, "# third")},
    )


def test_history_route_requires_auth(client):
    assert client.get("/api/v1/skills/demo/history").status_code == 401


def test_history_lists_versions_newest_first(client):
    _seed(client)
    r = client.get("/api/v1/skills/demo/history", headers=_auth())
    assert r.status_code == 200, r.text
    versions = r.json()["versions"]
    assert len(versions) == 2
    # Dashboard edits are tagged "dashboard".
    assert all(v["actor"] == "dashboard" for v in versions)
    ids = [v["version_id"] for v in versions]
    assert ids == sorted(ids, reverse=True)


def test_history_empty_for_unedited_skill(client):
    client.post(
        "/api/v1/skills", headers=_auth(),
        json={"name": "fresh", "content": _md("fresh", "# body")},
    )
    r = client.get("/api/v1/skills/fresh/history", headers=_auth())
    assert r.status_code == 200
    assert r.json()["versions"] == []


def test_version_detail_returns_content_and_diff(client):
    _seed(client)
    versions = client.get(
        "/api/v1/skills/demo/history", headers=_auth(),
    ).json()["versions"]
    # Oldest entry holds the very first ("# original") body.
    oldest = versions[-1]["version_id"]
    r = client.get(
        f"/api/v1/skills/demo/history/{oldest}", headers=_auth(),
    )
    assert r.status_code == 200, r.text
    detail = r.json()
    assert "# original" in detail["content"]
    # Diff is computed against the current live SKILL.md ("# third").
    assert "# original" in detail["diff"]
    assert "# third" in detail["diff"]


def test_version_detail_404_for_unknown_version(client):
    _seed(client)
    r = client.get(
        "/api/v1/skills/demo/history/20200101T000000000000Z",
        headers=_auth(),
    )
    assert r.status_code == 404


def test_restore_reverts_to_chosen_version(client):
    _seed(client)
    versions = client.get(
        "/api/v1/skills/demo/history", headers=_auth(),
    ).json()["versions"]
    oldest = versions[-1]["version_id"]

    r = client.post(
        f"/api/v1/skills/demo/history/{oldest}/restore", headers=_auth(),
    )
    assert r.status_code == 200, r.text

    body = client.get("/api/v1/skills/demo", headers=_auth()).json()
    assert "# original" in body["body"]
    # Restore is itself an edit — the pre-restore ("# third") state is
    # now the newest history entry, so the revert is reversible.
    after = client.get(
        "/api/v1/skills/demo/history", headers=_auth(),
    ).json()["versions"]
    assert len(after) == 3
    newest_content = client.get(
        f"/api/v1/skills/demo/history/{after[0]['version_id']}",
        headers=_auth(),
    ).json()["content"]
    assert "# third" in newest_content


def test_restore_pinned_skill_needs_force_unpin(client):
    _seed(client)
    client.post("/api/v1/skills/demo/pin", headers=_auth())
    versions = client.get(
        "/api/v1/skills/demo/history", headers=_auth(),
    ).json()["versions"]
    oldest = versions[-1]["version_id"]

    # Without force_unpin the pinned guard rejects the restore.
    blocked = client.post(
        f"/api/v1/skills/demo/history/{oldest}/restore", headers=_auth(),
    )
    assert blocked.status_code == 400

    # With force_unpin it goes through and re-pins afterward.
    ok = client.post(
        f"/api/v1/skills/demo/history/{oldest}/restore",
        headers=_auth(), json={"force_unpin": True},
    )
    assert ok.status_code == 200, ok.text
    skills = client.get("/api/v1/skills", headers=_auth()).json()
    demo = next(s for s in skills["active"] if s["name"] == "demo")
    assert demo["pinned"] is True

"""Dashboard skill CRUD endpoints — POST/PUT/DELETE under /api/v1/skills.

Companion to ``test_dashboard_skills_endpoint.py`` (read path).
Pinned behaviours:

  * POST /api/v1/skills creates a workspace skill, optional auto-pin
    via ``protect: true``.
  * Validation 400s name (must be kebab-case) and content (must be
    valid SKILL.md frontmatter + body).
  * Refuses creating a skill name that collides with bundled or
    archived workspace skills.
  * PUT /api/v1/skills/{name} edits, refuses on pinned without
    ``force_unpin: true``, re-pins after a forced edit so the
    user's protection intent isn't reset by the save.
  * DELETE /api/v1/skills/{name} deletes, refuses on pinned with 409
    (delete is destructive — no auto-bypass).
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vexis_agent.core.skills import PinStore, create_skill
from vexis_agent.core.web_server import DashboardConfig, WebDashboard


_TOKEN = "test-token-skill-crud-deadbeef"


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
    """Point the bundled root at an empty dir so the always-shipped
    skills don't leak into create-name-collision assertions."""
    monkeypatch.setenv("VEXIS_BUNDLED_SKILLS", str(tmp_path / "no-bundled"))
    yield


@pytest.fixture
def dashboard(tmp_path: Path) -> WebDashboard:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return _build_dashboard(workspace)


@pytest.fixture
def client(dashboard: WebDashboard) -> TestClient:
    return TestClient(dashboard._app)


def _auth() -> dict:
    return {"Authorization": f"Bearer {_TOKEN}"}


def _skill_md(name: str = "my-skill", desc: str = "test skill desc") -> str:
    return f"---\nname: {name}\ndescription: {desc}\n---\nbody content\n"


# ──────────────────────────────────────────────────────────────────
# Auth
# ──────────────────────────────────────────────────────────────────


def test_create_unauthorized_returns_401(client):
    r = client.post("/api/v1/skills", json={"name": "x", "content": ""})
    assert r.status_code == 401


def test_edit_unauthorized_returns_401(client):
    r = client.put("/api/v1/skills/x", json={"content": ""})
    assert r.status_code == 401


def test_delete_unauthorized_returns_401(client):
    r = client.delete("/api/v1/skills/x")
    assert r.status_code == 401


# ──────────────────────────────────────────────────────────────────
# Create
# ──────────────────────────────────────────────────────────────────


def test_create_basic(client, dashboard):
    r = client.post(
        "/api/v1/skills",
        headers=_auth(),
        json={"name": "my-skill", "content": _skill_md()},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["name"] == "my-skill"
    assert body["pinned"] is False  # protect not requested
    # Verify it landed on disk.
    skill_md = dashboard._workspace / "skills" / "my-skill" / "SKILL.md"
    assert skill_md.is_file()


def test_create_with_category(client, dashboard):
    r = client.post(
        "/api/v1/skills",
        headers=_auth(),
        json={
            "name": "my-skill",
            "content": _skill_md(),
            "category": "devops",
        },
    )
    assert r.status_code == 200
    skill_md = dashboard._workspace / "skills" / "devops" / "my-skill" / "SKILL.md"
    assert skill_md.is_file()


def test_create_with_protect_auto_pins(client, dashboard):
    r = client.post(
        "/api/v1/skills",
        headers=_auth(),
        json={
            "name": "my-skill",
            "content": _skill_md(),
            "protect": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["pinned"] is True
    assert PinStore(dashboard._workspace / "skills").is_pinned("my-skill")


def test_create_400_on_invalid_name(client):
    r = client.post(
        "/api/v1/skills",
        headers=_auth(),
        json={"name": "BAD CASE", "content": _skill_md()},
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["field"] == "name"


def test_create_400_on_invalid_content(client):
    r = client.post(
        "/api/v1/skills",
        headers=_auth(),
        json={"name": "my-skill", "content": "no frontmatter"},
    )
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail["field"] == "content"


def test_create_400_on_name_mismatch(client):
    """Frontmatter name must match the URL/body name field."""
    r = client.post(
        "/api/v1/skills",
        headers=_auth(),
        json={"name": "url-name", "content": _skill_md(name="frontmatter-name")},
    )
    assert r.status_code == 400


def test_create_400_on_duplicate(client, dashboard):
    create_skill(dashboard._workspace / "skills", "my-skill", _skill_md())
    r = client.post(
        "/api/v1/skills",
        headers=_auth(),
        json={"name": "my-skill", "content": _skill_md()},
    )
    assert r.status_code == 400
    assert "already exists" in r.json()["detail"]["error"]


# ──────────────────────────────────────────────────────────────────
# Edit
# ──────────────────────────────────────────────────────────────────


def test_edit_basic(client, dashboard):
    create_skill(dashboard._workspace / "skills", "my-skill", _skill_md())
    new_content = _skill_md(desc="updated description")
    r = client.put(
        "/api/v1/skills/my-skill",
        headers=_auth(),
        json={"content": new_content},
    )
    assert r.status_code == 200, r.text
    skill_md = dashboard._workspace / "skills" / "my-skill" / "SKILL.md"
    assert "updated description" in skill_md.read_text()


def test_edit_400_on_invalid_content(client, dashboard):
    create_skill(dashboard._workspace / "skills", "my-skill", _skill_md())
    r = client.put(
        "/api/v1/skills/my-skill",
        headers=_auth(),
        json={"content": "broken"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["field"] == "content"


def test_edit_pinned_refused_without_force(client, dashboard):
    create_skill(dashboard._workspace / "skills", "my-skill", _skill_md())
    PinStore(dashboard._workspace / "skills").pin("my-skill")
    r = client.put(
        "/api/v1/skills/my-skill",
        headers=_auth(),
        json={"content": _skill_md(desc="modified")},
    )
    # Edit refused — pin is honoured by default
    assert r.status_code == 400
    skill_md = dashboard._workspace / "skills" / "my-skill" / "SKILL.md"
    assert "modified" not in skill_md.read_text()


def test_edit_pinned_force_unpin_succeeds_and_repins(client, dashboard):
    create_skill(dashboard._workspace / "skills", "my-skill", _skill_md())
    PinStore(dashboard._workspace / "skills").pin("my-skill")
    r = client.put(
        "/api/v1/skills/my-skill",
        headers=_auth(),
        json={"content": _skill_md(desc="modified"), "force_unpin": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["pinned"] is True  # was_pinned reported back
    skill_md = dashboard._workspace / "skills" / "my-skill" / "SKILL.md"
    assert "modified" in skill_md.read_text()
    # And the pin is re-applied after the edit.
    assert PinStore(dashboard._workspace / "skills").is_pinned("my-skill")


def test_edit_missing_skill(client):
    r = client.put(
        "/api/v1/skills/ghost",
        headers=_auth(),
        json={"content": _skill_md(name="ghost")},
    )
    assert r.status_code == 400


# ──────────────────────────────────────────────────────────────────
# Delete
# ──────────────────────────────────────────────────────────────────


def test_delete_basic(client, dashboard):
    create_skill(dashboard._workspace / "skills", "my-skill", _skill_md())
    r = client.delete("/api/v1/skills/my-skill", headers=_auth())
    assert r.status_code == 200
    assert not (dashboard._workspace / "skills" / "my-skill").exists()


def test_delete_pinned_409(client, dashboard):
    create_skill(dashboard._workspace / "skills", "my-skill", _skill_md())
    PinStore(dashboard._workspace / "skills").pin("my-skill")
    r = client.delete("/api/v1/skills/my-skill", headers=_auth())
    # 409 Conflict — destructive op refuses pinned without explicit unpin first
    assert r.status_code == 409
    assert (dashboard._workspace / "skills" / "my-skill").exists()


def test_delete_missing_skill(client):
    r = client.delete("/api/v1/skills/ghost", headers=_auth())
    assert r.status_code == 400


# ──────────────────────────────────────────────────────────────────
# Round-trip: create → list → edit → list → delete → list
# ──────────────────────────────────────────────────────────────────


def test_full_lifecycle(client, dashboard):
    # Create
    r = client.post(
        "/api/v1/skills",
        headers=_auth(),
        json={"name": "lifecycle", "content": _skill_md(name="lifecycle")},
    )
    assert r.status_code == 200
    # List shows it
    r = client.get("/api/v1/skills", headers=_auth())
    names = {s["name"] for s in r.json()["active"]}
    assert "lifecycle" in names
    # Edit
    r = client.put(
        "/api/v1/skills/lifecycle",
        headers=_auth(),
        json={"content": _skill_md(name="lifecycle", desc="edited")},
    )
    assert r.status_code == 200
    # Get body shows edited description
    r = client.get("/api/v1/skills/lifecycle", headers=_auth())
    assert "edited" in r.json()["description"]
    # Delete
    r = client.delete("/api/v1/skills/lifecycle", headers=_auth())
    assert r.status_code == 200
    # List no longer shows it
    r = client.get("/api/v1/skills", headers=_auth())
    names = {s["name"] for s in r.json()["active"]}
    assert "lifecycle" not in names

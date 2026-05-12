"""Dashboard ``/api/v1/skills`` endpoint smoke test.

Locks the read path the SKILLS tab depends on. Companion to
``tests/test_skill_authoring_guidance.py``: that one pins the
brain-side guidance ("when to create a skill"); this one pins the
user-side surface ("can the user see what got created").

Mirrors the ``_build_dashboard`` pattern in
``tests/test_dashboard_schedules_endpoints.py``. No daemon, no
uvicorn — just a FastAPI ``TestClient`` against the assembled app.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vexis_agent.core.skills import create_skill
from vexis_agent.core.web_server import DashboardConfig, WebDashboard


_TOKEN = "test-token-skills-cafebabe1234"


def _build_dashboard(workspace: Path) -> WebDashboard:
    dashboard = WebDashboard.__new__(WebDashboard)
    dashboard._workspace = workspace  # type: ignore[attr-defined]
    dashboard._token = _TOKEN  # type: ignore[attr-defined]
    dashboard._learning = None  # type: ignore[attr-defined]
    dashboard._relationships_mutation_window_seconds = 600  # type: ignore[attr-defined]
    dashboard._relationships_mutation_limit = 100  # type: ignore[attr-defined]
    dashboard._relationships_mutation_log = defaultdict(deque)  # type: ignore[attr-defined]
    dashboard._config = DashboardConfig(  # type: ignore[attr-defined]
        host="127.0.0.1",
        port=0,
        web_dist=workspace / "no-frontend",
    )
    dashboard._tailscale_url = None  # type: ignore[attr-defined]
    dashboard._tailscale_dns = None  # type: ignore[attr-defined]
    dashboard._server = None  # type: ignore[attr-defined]
    dashboard._serve_task = None  # type: ignore[attr-defined]
    dashboard._started_at = datetime.now(timezone.utc)  # type: ignore[attr-defined]
    dashboard._sessions = None  # type: ignore[attr-defined]
    dashboard._running_tasks = None  # type: ignore[attr-defined]
    dashboard._background_tasks = None  # type: ignore[attr-defined]
    dashboard._curator = None  # type: ignore[attr-defined]
    dashboard._browser = None  # type: ignore[attr-defined]
    dashboard._chat = None  # type: ignore[attr-defined]
    dashboard._running_brain_kind = None  # type: ignore[attr-defined]
    dashboard._profile_size_cache = None  # type: ignore[attr-defined]
    dashboard._schedule_store = None  # type: ignore[attr-defined]
    dashboard._app = dashboard._build_app()
    return dashboard


@pytest.fixture
def dashboard(tmp_path: Path) -> WebDashboard:
    return _build_dashboard(tmp_path)


@pytest.fixture
def client(dashboard: WebDashboard) -> TestClient:
    return TestClient(dashboard._app)  # type: ignore[attr-defined]


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {_TOKEN}"}


# ──────────────────────────────────────────────────────────────────
# Empty workspace
# ──────────────────────────────────────────────────────────────────


def test_skills_endpoint_empty_workspace(client: TestClient, monkeypatch, tmp_path):
    """Fresh install with zero workspace skills AND no bundled root.
    Endpoint must 200 with empty arrays, not 500 or 404 — the
    dashboard must render the "no skills yet" empty state, which
    means the API has to succeed.

    Point ``$VEXIS_BUNDLED_SKILLS`` at an empty dir so the always-
    present in-package bundled skills (kanban-orchestrator etc) don't
    leak into this fresh-install assertion."""
    monkeypatch.setenv("VEXIS_BUNDLED_SKILLS", str(tmp_path / "no-bundled"))
    r = client.get("/api/v1/skills", headers=_auth_headers())
    assert r.status_code == 200
    body = r.json()
    assert body == {"active": [], "archived": []}


# ──────────────────────────────────────────────────────────────────
# Brain-style write → dashboard read (end-to-end)
# ──────────────────────────────────────────────────────────────────


def test_skills_endpoint_surfaces_a_brain_created_skill(
    dashboard: WebDashboard,
    client: TestClient,
    tmp_path: Path,
):
    """The whole point of the in-session self-authoring feature:
    the brain calls ``vexis-skill create`` (which routes through
    ``create_skill``), and the new skill appears on the dashboard's
    Skills tab the user is staring at.

    This is the contract the upstream design describes: agent saves
    a workflow → workflow is reusable next time. We don't run a
    full session here, just simulate the brain's CLI call and
    confirm the API picks it up.
    """
    skills_root = tmp_path / "skills"
    skills_root.mkdir()

    content = (
        "---\n"
        "name: hn-scrape-shortcut\n"
        "description: Use when scraping HackerNews — JS eval shortcut "
        "beats step-by-step clicking.\n"
        "---\n"
        "# HN scrape shortcut\n\n"
        "Use vexis-browse evaluate with a one-liner querySelectorAll\n"
        "instead of clicking each row.\n"
    )
    op = create_skill(skills_root, "hn-scrape-shortcut", content)
    assert op.ok, op.message

    r = client.get("/api/v1/skills", headers=_auth_headers())
    assert r.status_code == 200
    body = r.json()

    names = [s["name"] for s in body["active"]]
    assert "hn-scrape-shortcut" in names
    # Description carries through (the dashboard renders it as
    # the card subtitle — drift here would silently show wrong
    # text to the user).
    descriptions = {s["name"]: s["description"] for s in body["active"]}
    assert "JS eval shortcut" in descriptions["hn-scrape-shortcut"]


# ──────────────────────────────────────────────────────────────────
# Auth gate
# ──────────────────────────────────────────────────────────────────


def test_skills_endpoint_requires_auth(client: TestClient):
    """Bare GET with no Authorization header must fail closed.
    Otherwise a tailnet snoop could enumerate skill names without
    the rotating bearer token — which leaks workspace shape
    (project names, internal vocabulary) even if it can't mutate."""
    r = client.get("/api/v1/skills")
    assert r.status_code in (401, 403)

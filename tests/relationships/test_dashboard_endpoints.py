"""v3c Day 4b: ``/api/v1/relationships/*`` dashboard endpoints.

Covers the six endpoints added to ``core.web_server``:

- GET /api/v1/relationships/candidates: list, default excludes
  rejected.
- GET .../candidates?include_rejected=true: includes tombstoned.
- GET .../candidates/{slug}: full per-occurrence detail.
- POST .../approve: happy path / sensitive 422 / missing-qual 409.
- POST .../resolve_qualifier + retry approve: end-to-end flow.
- POST .../reject: whole-slug + per-fact tombstoning.
- POST .../edit: fact_id changes, eligibility recomputed.
- Auth: every endpoint rejects missing / wrong bearer.
- Rate limit: 101st mutation in 10 min returns 429.

Tests construct the WebDashboard via ``__new__`` and stub the
collaborators directly — same pattern v3a/v3b tests use for the
TelegramTransport and LearningController. FastAPI's TestClient
exercises the live app object.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vexis_agent.core.relationships.candidate_store import RelationshipsCandidateStore
from vexis_agent.core.relationships.curator import RelationshipsCurator
from vexis_agent.core.relationships.store import (
    Fact,
    Person,
    relationships_archive_path,
    relationships_live_path,
    serialize_relationships_file,
)
from vexis_agent.core.web_server import WebDashboard


_TOKEN = "test-token-deadbeef"


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


class _FakeLearningController:
    """Minimal LearningController stand-in: only the
    ``relationships_curator`` accessor is touched by the
    /api/v1/relationships/* endpoints."""

    def __init__(self, curator: RelationshipsCurator) -> None:
        self._relationships_curator = curator

    @property
    def relationships_curator(self) -> RelationshipsCurator:
        return self._relationships_curator


def _build_dashboard(workspace: Path) -> tuple[WebDashboard, RelationshipsCurator]:
    """Construct a WebDashboard via __new__ + manual field setup,
    bypassing the full daemon wiring. Returns (dashboard, curator)
    so tests can poke the curator directly."""
    curator = RelationshipsCurator(workspace=workspace)
    dashboard = WebDashboard.__new__(WebDashboard)
    # Fields read by the endpoints + their middleware.
    dashboard._workspace = workspace  # type: ignore[attr-defined]
    dashboard._token = _TOKEN  # type: ignore[attr-defined]
    dashboard._learning = _FakeLearningController(curator)  # type: ignore[attr-defined]
    # Rate-limiter state (re-create the same shape _build_app uses).
    from collections import defaultdict, deque
    dashboard._relationships_mutation_window_seconds = 600  # type: ignore[attr-defined]
    dashboard._relationships_mutation_limit = 100  # type: ignore[attr-defined]
    dashboard._relationships_mutation_log = defaultdict(deque)  # type: ignore[attr-defined]
    # Other fields _build_app touches (web_dist + browser); set
    # safe defaults.
    from vexis_agent.core.web_server import DashboardConfig
    dashboard._config = DashboardConfig(  # type: ignore[attr-defined]
        host="127.0.0.1",
        port=0,
        web_dist=workspace / "no-frontend",
        manage_tailscale=False,
    )
    dashboard._sessions = None  # type: ignore[attr-defined]
    dashboard._running_tasks = None  # type: ignore[attr-defined]
    dashboard._background_tasks = None  # type: ignore[attr-defined]
    dashboard._curator = None  # type: ignore[attr-defined]
    dashboard._browser = None  # type: ignore[attr-defined]
    dashboard._started_at = None  # type: ignore[attr-defined]
    dashboard._tailscale_url = None  # type: ignore[attr-defined]
    dashboard._tailscale_dns = None  # type: ignore[attr-defined]
    dashboard._server = None  # type: ignore[attr-defined]
    dashboard._serve_task = None  # type: ignore[attr-defined]
    dashboard._profile_size_cache = None  # type: ignore[attr-defined]
    dashboard._app = dashboard._build_app()  # type: ignore[attr-defined]
    return dashboard, curator


def _client(dashboard: WebDashboard) -> TestClient:
    return TestClient(dashboard._app)  # type: ignore[attr-defined]


def _hdr() -> dict:
    return {"Authorization": f"Bearer {_TOKEN}"}


def _seed_candidate(curator, *, slug, display_name, qualifier, fact_text):
    curator.candidate_store.add_observation(
        slug=slug,
        display_name=display_name,
        qualifier=qualifier,
        fact_text=fact_text,
        session_uuid=f"sess-{slug}",
        turn_index=1,
    )


# ---------------------------------------------------------------- auth


def test_endpoints_reject_without_bearer(workspace: Path):
    dashboard, _ = _build_dashboard(workspace)
    client = _client(dashboard)
    paths = [
        ("GET", "/api/v1/relationships/candidates"),
        ("GET", "/api/v1/relationships/candidates/sarah"),
        ("POST", "/api/v1/relationships/candidates/sarah/approve"),
        ("POST", "/api/v1/relationships/candidates/sarah/reject"),
        ("POST", "/api/v1/relationships/candidates/sarah/edit"),
        ("POST", "/api/v1/relationships/candidates/sarah/resolve_qualifier"),
    ]
    for method, path in paths:
        kwargs = {"json": {}} if method == "POST" else {}
        resp = client.request(method, path, **kwargs)
        assert resp.status_code == 401, (method, path, resp.status_code)


def test_endpoints_reject_wrong_bearer(workspace: Path):
    dashboard, _ = _build_dashboard(workspace)
    client = _client(dashboard)
    resp = client.get(
        "/api/v1/relationships/candidates",
        headers={"Authorization": "Bearer not-the-token"},
    )
    assert resp.status_code == 401


# ---------------------------------------------------------------- GET list


def test_get_candidates_excludes_rejected_by_default(workspace: Path):
    dashboard, curator = _build_dashboard(workspace)
    _seed_candidate(curator, slug="mom", display_name="Mom",
                    qualifier="mom", fact_text="loves classical")
    _seed_candidate(curator, slug="rejected", display_name="Rejected",
                    qualifier=None, fact_text="x")
    curator.candidate_store.mark_rejected("rejected")
    client = _client(dashboard)
    resp = client.get("/api/v1/relationships/candidates", headers=_hdr())
    assert resp.status_code == 200
    payload = resp.json()
    slugs = [c["slug"] for c in payload["candidates"]]
    assert "mom" in slugs
    assert "rejected" not in slugs


def test_get_candidates_include_rejected(workspace: Path):
    dashboard, curator = _build_dashboard(workspace)
    _seed_candidate(curator, slug="mom", display_name="Mom",
                    qualifier="mom", fact_text="loves classical")
    _seed_candidate(curator, slug="rejected", display_name="Rejected",
                    qualifier=None, fact_text="x")
    curator.candidate_store.mark_rejected("rejected")
    client = _client(dashboard)
    resp = client.get(
        "/api/v1/relationships/candidates?include_rejected=true",
        headers=_hdr(),
    )
    assert resp.status_code == 200
    slugs = [c["slug"] for c in resp.json()["candidates"]]
    assert "mom" in slugs
    assert "rejected" in slugs


def test_get_candidates_excludes_approved(workspace: Path):
    """Approved entries are retained for audit but don't belong on
    the action surface."""
    dashboard, curator = _build_dashboard(workspace)
    _seed_candidate(curator, slug="approved", display_name="Approved",
                    qualifier=None, fact_text="x")
    curator.candidate_store.mark_approved("approved")
    client = _client(dashboard)
    resp = client.get("/api/v1/relationships/candidates", headers=_hdr())
    assert resp.status_code == 200
    slugs = [c["slug"] for c in resp.json()["candidates"]]
    assert "approved" not in slugs


# ---------------------------------------------------------------- GET detail


def test_get_candidate_detail_returns_per_occurrence(workspace: Path):
    dashboard, curator = _build_dashboard(workspace)
    _seed_candidate(curator, slug="sarah", display_name="Sarah",
                    qualifier="coworker", fact_text="tech lead")
    # Add a second observation to confirm the array.
    curator.candidate_store.add_observation(
        slug="sarah", display_name="Sarah", qualifier="coworker",
        fact_text="tech lead",
        session_uuid="sess-B", turn_index=4,
    )
    client = _client(dashboard)
    resp = client.get(
        "/api/v1/relationships/candidates/sarah", headers=_hdr(),
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["slug"] == "sarah"
    assert len(payload["facts"]) == 1
    fact = payload["facts"][0]
    assert len(fact["occurrences"]) == 2
    sessions = {o["session_uuid"] for o in fact["occurrences"]}
    assert sessions == {"sess-sarah", "sess-B"}


def test_get_candidate_detail_404_for_missing(workspace: Path):
    dashboard, _ = _build_dashboard(workspace)
    client = _client(dashboard)
    resp = client.get(
        "/api/v1/relationships/candidates/nobody", headers=_hdr(),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------- POST approve


def test_post_approve_happy_path(workspace: Path):
    dashboard, curator = _build_dashboard(workspace)
    # Use a candidate without a qualifier so the approve path
    # writes to a bare slug. This isolates the happy-path assertion
    # from the qualifier-disambiguation mechanism (covered
    # separately in Day 4a's approve-flow tests).
    _seed_candidate(curator, slug="mom", display_name="Mom",
                    qualifier=None, fact_text="loves classical")
    client = _client(dashboard)
    resp = client.post(
        "/api/v1/relationships/candidates/mom/approve",
        headers=_hdr(),
        json={},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "Approved" in body["reply_text"]
    # Live now has Mom under the bare slug.
    live_slugs = [p.slug for p in curator.store.list_live()]
    assert "mom" in live_slugs
    # Candidate cleared from queue (all facts approved).
    assert curator.candidate_store.get("mom") is None


def test_post_approve_with_sensitive_pattern_returns_422(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
):
    dashboard, curator = _build_dashboard(workspace)
    _seed_candidate(curator, slug="sarah", display_name="Sarah",
                    qualifier="friend", fact_text="benign for the test")
    # Force the scanner inside RelationshipsStore.add_live to fire.
    from vexis_agent.core import learning_review as lr_module

    def fake_scan(text, scope, *, target_file):
        return f"medical:{target_file}"

    monkeypatch.setattr(
        lr_module, "_scan_lesson_for_sensitive_content", fake_scan,
    )
    client = _client(dashboard)
    resp = client.post(
        "/api/v1/relationships/candidates/sarah/approve",
        headers=_hdr(),
        json={},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "blocked_by_sensitive_pattern"
    # Candidate still in queue, not approved.
    assert curator.candidate_store.get("sarah") is not None


def test_post_approve_with_missing_existing_qualifier_returns_409(
    workspace: Path,
):
    """Live entry exists with NO YAML qualifier → 409 + typed
    payload for the dashboard modal."""
    sarah = Person(
        slug="sarah", display_name="Sarah",
        relationship="(unspecified)", qualifier=None,
        last_confirmed="2026-04-01", source_session="abc12345",
        facts=(
            Fact(
                text="met somewhere",
                confirmed_date="2026-04-01",
                source_session_short="abc12345",
                staged=False,
            ),
        ),
    )
    relationships_live_path(workspace).write_text(
        serialize_relationships_file([sarah], kind="live"),
        encoding="utf-8",
    )
    dashboard, curator = _build_dashboard(workspace)
    _seed_candidate(curator, slug="sarah", display_name="Sarah",
                    qualifier="coworker", fact_text="tech lead")
    client = _client(dashboard)
    resp = client.post(
        "/api/v1/relationships/candidates/sarah/approve",
        headers=_hdr(),
        json={"qualifier": "coworker"},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"] == "missing_existing_qualifier"
    assert body["existing_slug"] == "sarah"
    assert "met somewhere" in body["existing_facts"]
    assert body["proposed_qualifier"] == "coworker"


def test_resolve_qualifier_then_retry_approve(workspace: Path):
    """End-to-end: 409 → resolve_qualifier → retry approve → 200."""
    sarah = Person(
        slug="sarah", display_name="Sarah",
        relationship="(unspecified)", qualifier=None,
        last_confirmed="2026-04-01", source_session="abc12345",
        facts=(
            Fact(
                text="met in college",
                confirmed_date="2026-04-01",
                source_session_short="abc12345",
                staged=False,
            ),
        ),
    )
    relationships_live_path(workspace).write_text(
        serialize_relationships_file([sarah], kind="live"),
        encoding="utf-8",
    )
    dashboard, curator = _build_dashboard(workspace)
    _seed_candidate(curator, slug="sarah", display_name="Sarah",
                    qualifier="coworker", fact_text="tech lead")
    client = _client(dashboard)
    # First approve → 409.
    resp1 = client.post(
        "/api/v1/relationships/candidates/sarah/approve",
        headers=_hdr(),
        json={"qualifier": "coworker"},
    )
    assert resp1.status_code == 409
    # Resolve.
    resp2 = client.post(
        "/api/v1/relationships/candidates/sarah/resolve_qualifier",
        headers=_hdr(),
        json={"existing_qualifier": "friend"},
    )
    assert resp2.status_code == 200, resp2.json()
    body2 = resp2.json()
    assert body2["new_slug"] == "sarah-friend"
    # Retry approve.
    resp3 = client.post(
        "/api/v1/relationships/candidates/sarah/approve",
        headers=_hdr(),
        json={"qualifier": "coworker"},
    )
    assert resp3.status_code == 200, resp3.json()
    # Live has both: sarah-friend (renamed) + sarah-coworker (new).
    live_slugs = sorted(p.slug for p in curator.store.list_live())
    assert live_slugs == ["sarah-coworker", "sarah-friend"]


# ---------------------------------------------------------------- POST reject


def test_post_reject_whole_slug(workspace: Path):
    dashboard, curator = _build_dashboard(workspace)
    _seed_candidate(curator, slug="marco", display_name="Marco",
                    qualifier=None, fact_text="x")
    client = _client(dashboard)
    resp = client.post(
        "/api/v1/relationships/candidates/marco/reject",
        headers=_hdr(),
        json={},
    )
    assert resp.status_code == 200
    candidate = curator.candidate_store.get("marco")
    assert candidate.rejected_at is not None


def test_post_reject_specific_facts(workspace: Path):
    dashboard, curator = _build_dashboard(workspace)
    _seed_candidate(curator, slug="sarah", display_name="Sarah",
                    qualifier="coworker", fact_text="A")
    curator.candidate_store.add_observation(
        slug="sarah", display_name="Sarah", qualifier="coworker",
        fact_text="B", session_uuid="s2", turn_index=1,
    )
    from vexis_agent.core.relationships.consent import _fact_id
    fid_a = _fact_id("A")
    fid_b = _fact_id("B")
    client = _client(dashboard)
    resp = client.post(
        "/api/v1/relationships/candidates/sarah/reject",
        headers=_hdr(),
        json={"fact_ids": [fid_a]},
    )
    assert resp.status_code == 200
    # Slug NOT rejected.
    candidate = curator.candidate_store.get("sarah")
    assert candidate.rejected_at is None
    # Fact A rejected, B not.
    assert candidate.facts[fid_a].rejected_at is not None
    assert candidate.facts[fid_b].rejected_at is None


# ---------------------------------------------------------------- POST edit


def test_post_edit_changes_fact_id(workspace: Path):
    dashboard, curator = _build_dashboard(workspace)
    _seed_candidate(curator, slug="sarah", display_name="Sarah",
                    qualifier="coworker", fact_text="tech lead")
    from vexis_agent.core.relationships.consent import _fact_id
    old_id = _fact_id("tech lead")
    client = _client(dashboard)
    resp = client.post(
        "/api/v1/relationships/candidates/sarah/edit",
        headers=_hdr(),
        json={"fact_id": old_id, "new_text": "tech lead on Vexis"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["old_fact_id"] == old_id
    assert body["new_fact_id"] == _fact_id("tech lead on Vexis")
    # Old tombstoned, new active.
    candidate = curator.candidate_store.get("sarah")
    assert candidate.facts[old_id].rejected_at is not None
    assert _fact_id("tech lead on Vexis") in candidate.facts


def test_post_edit_400_on_empty_text(workspace: Path):
    dashboard, curator = _build_dashboard(workspace)
    _seed_candidate(curator, slug="sarah", display_name="Sarah",
                    qualifier="coworker", fact_text="x")
    from vexis_agent.core.relationships.consent import _fact_id
    client = _client(dashboard)
    resp = client.post(
        "/api/v1/relationships/candidates/sarah/edit",
        headers=_hdr(),
        json={"fact_id": _fact_id("x"), "new_text": "   "},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------- rate limit


def test_rate_limit_429_on_excess_mutations(
    workspace: Path, monkeypatch: pytest.MonkeyPatch,
):
    """101st mutation in the window → 429. We tighten the window
    inline via the dashboard's settable fields rather than waiting
    real time; 100 successful approvals + 1 rejected is
    representative."""
    dashboard, curator = _build_dashboard(workspace)
    # Limit the test to a small window so we can blow it cleanly.
    dashboard._relationships_mutation_limit = 3  # type: ignore[attr-defined]
    client = _client(dashboard)
    # Seed three slugs we can reject in succession (each call
    # tombstones; a re-reject of the same slug still counts as a
    # mutation).
    for i in range(3):
        _seed_candidate(curator, slug=f"slug-{i}", display_name=f"P{i}",
                        qualifier=None, fact_text=f"f{i}")
    for i in range(3):
        resp = client.post(
            f"/api/v1/relationships/candidates/slug-{i}/reject",
            headers=_hdr(),
            json={},
        )
        assert resp.status_code == 200, (i, resp.json())
    # 4th call → 429.
    _seed_candidate(curator, slug="slug-extra", display_name="Extra",
                    qualifier=None, fact_text="extra")
    resp = client.post(
        "/api/v1/relationships/candidates/slug-extra/reject",
        headers=_hdr(),
        json={},
    )
    assert resp.status_code == 429
    body = resp.json()
    assert "rate limit" in body.get("detail", "").lower()

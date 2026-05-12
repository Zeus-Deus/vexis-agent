"""Standalone E2E harness for the kanban dashboard.

Spins up the WebDashboard with a real KanbanStore + seeds demo tasks
across every column, then runs uvicorn on a high port. Designed for
the Phase 9 manual browser E2E walk — open the printed URL in the
codemux browser and exercise the page.

NOT a test runner. The real test suite (tests/test_dashboard_kanban_endpoints.py)
covers the REST + WS contract programmatically. This harness gives
the human (or codemux browser) a populated board to interact with.

Usage:
    cd /home/zeus/.codemux/worktrees/vexis-agent/research-upstream-kanban
    /home/zeus/miniconda3/envs/vexis-agent_env/bin/python scripts/e2e_kanban_dashboard.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

# Use a tmp VEXIS_HOME so we don't touch the user's real ~/.vexis/.
import tempfile
TMP_VEXIS = Path(tempfile.mkdtemp(prefix="vexis-e2e-"))
TMP_VEXIS.mkdir(parents=True, exist_ok=True)
os.environ["VEXIS_HOME"] = str(TMP_VEXIS)

# Patch paths.vexis_dir so all subsystems land in the tmp dir.
import vexis_agent.core.paths as _paths
_paths.vexis_dir = lambda: TMP_VEXIS  # type: ignore[assignment]
import vexis_agent.core.yaml_config as _yaml_config
_yaml_config.vexis_dir = lambda: TMP_VEXIS  # type: ignore[assignment]
import vexis_agent.tools.kanban.api as _kanban_api
_kanban_api.vexis_dir = lambda: TMP_VEXIS  # type: ignore[assignment]

from vexis_agent.core.kanban.db import KanbanStore  # noqa: E402
from vexis_agent.core.web_server import (  # noqa: E402
    DashboardConfig, WebDashboard,
)


def _build_dashboard(workspace: Path, web_dist: Path) -> WebDashboard:
    """Bypass the daemon constructor; build only what the dashboard
    routes touch. Same shape as tests/test_dashboard_*_endpoints.py."""
    d = WebDashboard.__new__(WebDashboard)
    d._workspace = workspace
    d._token = "e2e-token-kanban-walkthrough"
    d._learning = None
    d._relationships_mutation_window_seconds = 600
    d._relationships_mutation_limit = 100
    d._relationships_mutation_log = defaultdict(deque)
    d._config = DashboardConfig(
        host="127.0.0.1", port=8088, web_dist=web_dist,
    )
    d._tailscale_url = None
    d._tailscale_dns = None
    d._server = None
    d._serve_task = None
    d._started_at = datetime.now(timezone.utc)
    d._sessions = None
    d._running_tasks = None
    d._background_tasks = None
    d._curator = None
    d._browser = None
    d._chat = None
    d._running_brain_kind = None
    d._profile_size_cache = None
    d._schedule_store = None
    d._kanban_store = None
    d._app = d._build_app()
    return d


def seed_demo_data(store: KanbanStore) -> None:
    """Populate every column with at least one task so the board
    isn't empty when the user opens the page."""
    # Triage — newly filed, undecided.
    store.create_task(
        title="Investigate the spike in 4xx errors",
        body="See the dashboard around 14:00 UTC. Could be related to "
             "the auth changes from yesterday. Need someone to dig.",
        lane=None,
        status="triage",
        priority=0,
        created_by="user",
    )

    # Todo — accepted, waiting (no parents in this case).
    store.create_task(
        title="Update CHANGELOG for v0.4.0",
        body="Pull merged PRs since v0.3.0 and group by category.",
        lane="implementation",
        status="todo",
        priority=2,
        created_by="user",
    )
    store.create_task(
        title="Research lightweight task DAG visualisations",
        body="Survey vis-network, react-flow, and dagre. We want "
             "minimal dependencies and decent dark-mode support.",
        lane="research",
        status="todo",
        priority=1,
        created_by="user",
    )

    # Parent + child to demonstrate dependency blocking.
    parent = store.create_task(
        title="Schema for v2 multi-tenant tables",
        body="Define tenant_id columns, FKs, and index strategy.",
        lane="implementation",
        status="ready",
        priority=5,
        created_by="user",
    )
    store.create_task(
        title="Migration to add tenant_id columns (depends on schema)",
        body="Run the migration off-peak. Backfill tenant_id from the "
             "existing user_id mapping.",
        lane="ops",
        priority=3,
        created_by="user",
        parents=[parent.id],
    )

    # Ready — eligible for the dispatcher to claim.
    store.create_task(
        title="Draft the v0.4.0 announcement post",
        body="One screenshot of the kanban board, ~3 paragraphs on the "
             "design, tasteful list of new endpoints.",
        lane="implementation",
        status="ready",
        priority=4,
        created_by="user",
    )

    # In progress — claim it manually so the UI shows the in_progress
    # column populated.
    in_prog = store.create_task(
        title="Port the kanban-orchestrator skill to vexis lanes",
        body="Stub out a triage lane that fans out to specialist lanes "
             "via kanban_create. Should be an aux call only — no shell.",
        lane="implementation",
        status="ready",
        priority=2,
        created_by="user",
    )
    store.claim_task(in_prog.id, claim_lock="demo-claim", ttl_seconds=600)
    store.start_run(
        in_prog.id, lane="implementation", claim_lock="demo-claim",
        ttl_seconds=600, max_runtime_seconds=900, worker_pid=os.getpid(),
    )

    # Blocked — with a clear reason via comment + status flip.
    blocked = store.create_task(
        title="Wire the relationships extractor under kanban triage",
        body="The triage lane should be able to extract third-party "
             "facts and route them through the candidate queue.",
        lane="research",
        priority=3,
        created_by="user",
    )
    store.update_task(blocked.id, status="blocked")
    store.add_comment(
        blocked.id, author="agent",
        body="[blocked] Need design clarification: should kanban-spawned "
             "extractor sessions write to the same candidate queue, or "
             "to a separate kanban-tagged one?",
    )

    # Done — recently completed.
    done = store.create_task(
        title="Add KANBAN_WORKER_PREFIX to recursion guard",
        body="Both transcripts.py and brain/opencode.py.",
        lane="implementation",
        priority=1,
        created_by="user",
    )
    store.update_task(done.id, status="done")
    store.add_comment(
        done.id, author="agent",
        body="Done. Updated _is_curator_owned in transcripts.py and "
             "is_brain_owned_session in brain/opencode.py to check for "
             "the prefix. CLAUDE.md Invariant updated.",
    )

    done2 = store.create_task(
        title="Build initial KanbanPage.tsx",
        body="Six-column board, drag-drop between columns, live WS "
             "updates, goal-pad sidebar projecting active /goal.",
        lane="implementation",
        priority=2,
        created_by="user",
    )
    store.update_task(done2.id, status="done")

    # ── Stress test for column scrolling ─────────────────────────
    # Stuff TODO with 30 tasks to verify the per-column overflow-y
    # works on both desktop and mobile. Real users will hit this
    # exact pattern with morning task dumps and goal-driven fan-outs.
    for i in range(30):
        lane = ["research", "implementation", "review", "ops"][i % 4]
        store.create_task(
            title=f"Backlog item #{i+1:02d} — {_filler_title(i)}",
            body=f"Bulk-seeded task to stress per-column scroll. Index {i}.",
            lane=lane,
            status="todo",
            priority=(i % 5),
            created_by="user",
        )


def _filler_title(i: int) -> str:
    """Cycle through plausible-looking titles so the column doesn't
    visually look like the same row repeated. Real boards have
    different shapes per row — copy length, lane mix, priority."""
    samples = [
        "audit the brain abstraction backstop coverage",
        "wire opencode call_mode model override",
        "schema migration: drop tenant column",
        "unblock relationships extractor on weekend reset",
        "draft Q3 roadmap one-liner for the README",
        "investigate the 60s dispatcher tick latency",
        "spike: per-task workspace worktree integration",
        "tighten failure_limit copy in CLAUDE.md",
        "add /kanban watch <id> for per-task notify",
        "polish the goal-pad transition animation",
    ]
    return samples[i % len(samples)]


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    web_dist = repo_root / "vexis_agent" / "web_dist"
    if not (web_dist / "index.html").exists():
        web_dist = repo_root / "web" / "dist"
    if not (web_dist / "index.html").exists():
        print("error: web bundle not found; run `cd web && npm run build`",
              file=sys.stderr)
        sys.exit(1)

    workspace = TMP_VEXIS / "ws"
    workspace.mkdir(parents=True, exist_ok=True)

    store = KanbanStore(TMP_VEXIS / "kanban.db")
    seed_demo_data(store)

    dashboard = _build_dashboard(workspace, web_dist)
    dashboard.attach_kanban_store(store)

    print()
    print("=" * 64)
    print("  Vexis Kanban — E2E dashboard harness")
    print("=" * 64)
    print(f"  URL:    http://127.0.0.1:8088/?token={dashboard._token}#kanban")
    print(f"  Token:  {dashboard._token}")
    print(f"  DB:     {store.path}")
    print(f"  Tasks:  {len(store.list_tasks())}")
    print("=" * 64)
    print()

    # Run uvicorn directly without the dashboard.start() wrapper so
    # we can stay in the foreground and Ctrl-C cleanly.
    import uvicorn
    config = uvicorn.Config(
        dashboard._app, host="127.0.0.1", port=8088, log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    asyncio.run(server.serve())


if __name__ == "__main__":
    main()

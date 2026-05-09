"""FastAPI dashboard mounted alongside the daemon.

Read-only window into the agent's brain state plus three interactive
actions (pin/unpin, restore, force-curator). Bound to 127.0.0.1 and
fronted by Tailscale Serve so the only people who can reach it are on
the user's tailnet — same security model as Step 11's livestream.

Auth is a single bearer token, generated fresh on daemon start (see
``core.dashboard_token``). The bearer is checked on every ``/api/v1/*``
request; static assets at ``/`` and ``/assets/*`` are unauthenticated
so the React shell can load and read the token from ``?token=...``
on first paint.

Reuses existing primitives directly:
  * ``MemoryStore``     for memory entries + budget rendering
  * ``PinStore``        for skill pin state
  * ``discover_skills`` / ``list_active_reports`` / ``archived_skill_names``
  * ``UsageStore``      for per-skill telemetry
  * ``CuratorController.run_now()`` for force-run (shares the busy lock)
  * ``BackgroundTasks.status_summary()`` for the status page
  * ``RunningTasks.snapshot()`` for the foreground status page
  * ``SessionStore.list()`` for current session count

No duplicated logic, no parallel access paths.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from core.background_tasks import BackgroundTasks
from core.curator import (
    CuratorController,
    load_state as load_curator_state,
)
from core.dashboard_token import clear_token, issue_token
from core.learning_curator import LearningController
from core.memory import MemoryStore
from core.paths import (
    curator_logs_dir,
    memories_dir as memories_dir_fn,
    skills_dir,
    state_dir,
)
from core.running_tasks import RunningTasks
from core.sessions import SessionStore
from core.skills import (
    PinStore,
    UsageStore,
    archived_skill_names,
    discover_skills,
    iter_skill_dirs,
    parse_skill_md,
    restore_skill,
)
from core.subprocess import run as run_subprocess
from core import tailscale as tailscale_mod
from core import yaml_config
from core.yaml_config import (
    curator_archive_after_days,
    curator_enabled,
    curator_interval_hours,
    curator_stale_after_days,
)
from core.voice import (
    STTUnavailable,
    TTSUnavailable,
    VoiceError,
    stt_provider,
    tts_provider,
    voice_enabled,
)
from tools.browser import BrowserTools
from transports.web import WebChatTransport
from tools.browser.profile import (
    default_profile_name as browser_default_profile_name,
    profile_dir as browser_profile_dir,
    profiles_dir as browser_profiles_dir,
    screenshots_dir as browser_screenshots_dir,
)

log = logging.getLogger(__name__)


def _token_fingerprint(token: str) -> str:
    """Short fingerprint of the bearer token for audit logs.

    Logging the full token would defeat the auth model. SHA-256
    truncated to 12 hex chars is enough to disambiguate one
    daemon's session from another in the daemon log; not enough
    to reconstruct the token."""
    import hashlib
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]

# Default port. Can be overridden by VEXIS_DASHBOARD_PORT in the env.
DEFAULT_DASHBOARD_PORT = 8766


def _format_attachments_hint(raw: list) -> str:
    """Format an attachment list for the brain prompt.

    Each attachment is a ``{path, name, mime}`` dict the UI got from
    POST /chat/attach. We render a small block the brain can recognise
    and act on — claude-code already knows how to read files via its
    Read tool, so paths alone are sufficient.

    Filters out malformed entries silently (no raise) so a
    misbehaving client can't break sends — they'll just see the
    attachment hint missing if their payload was wrong.
    """
    lines: list[str] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        name = entry.get("name") or entry.get("path")
        if not isinstance(path, str) or not path.strip():
            continue
        mime = entry.get("mime")
        suffix = f" ({mime})" if isinstance(mime, str) and mime.strip() else ""
        lines.append(f"- {name}{suffix} → {path}")
    if not lines:
        return ""
    return "[ATTACHMENTS — files at these paths]\n" + "\n".join(lines)


# Source-vs-bundle freshness check (added 2026-05-08). Frontend
# changes need ``npm run build`` to compile ``web/src/**`` →
# ``web/dist/assets/index-*.js``; the daemon doesn't run the build,
# it just serves whatever's already in ``web/dist/``. Without this
# check, a forgotten build means the user sees stale dashboard
# behavior despite restarting the daemon — exactly the trap that
# bit on 2026-05-08 with the tier-fallbacks polish pass. We log a
# banner WARNING at dashboard startup so it surfaces immediately
# in the boot log; complementary to the pre-commit hook that
# auto-rebuilds on commit.
_DASHBOARD_SRC_EXTENSIONS = (".tsx", ".ts", ".css", ".html")
_DASHBOARD_STALE_BANNER = (
    "\n"
    "  ╭───────────────────────────────────────────────────────────╮\n"
    "  │  STALE DASHBOARD BUNDLE                                   │\n"
    "  │                                                           │\n"
    "  │  web/src/{src_rel} is newer than the compiled bundle.\n"
    "  │  Source mtime:  {src_mtime}\n"
    "  │  Bundle mtime:  {bundle_mtime}\n"
    "  │                                                           │\n"
    "  │  Run: cd web && npm run build                             │\n"
    "  │  then hard-refresh your browser (Ctrl+Shift+R).           │\n"
    "  ╰───────────────────────────────────────────────────────────╯"
)


def _warn_if_dashboard_bundle_stale(web_dist: Path) -> None:
    """Compare source mtimes under ``web/src/`` against the
    compiled bundle in ``web_dist``. Log a banner WARNING if any
    source file is newer than the newest bundle file.

    Silent fail-fast cases:
      - ``web_dist`` doesn't exist (fresh checkout, no build yet —
        a separate code path already 404s the dashboard route)
      - ``web/src/`` doesn't exist (test fixtures pointing at
        synthetic paths)
      - any I/O error during the walk (defensive — must never
        block daemon startup)

    Walks the source tree once; cheap (typically <100 files) and
    fires once per daemon boot. No caching needed."""
    try:
        bundle_dir = web_dist / "assets"
        src_dir = web_dist.parent / "src"
        if not bundle_dir.exists() or not src_dir.exists():
            return

        bundle_files = [
            p for p in bundle_dir.iterdir()
            if p.is_file() and p.suffix in (".js", ".css")
        ]
        if not bundle_files:
            return  # no bundle yet, no comparison possible

        newest_bundle_mtime = max(p.stat().st_mtime for p in bundle_files)

        # Find the newest source file across all watched extensions.
        # ``rglob('*')`` is sufficient — small tree, no recursion concerns.
        newest_src_path: Path | None = None
        newest_src_mtime = 0.0
        for src_file in src_dir.rglob("*"):
            if not src_file.is_file():
                continue
            if src_file.suffix not in _DASHBOARD_SRC_EXTENSIONS:
                continue
            mtime = src_file.stat().st_mtime
            if mtime > newest_src_mtime:
                newest_src_mtime = mtime
                newest_src_path = src_file

        if newest_src_path is None:
            return

        if newest_src_mtime > newest_bundle_mtime:
            from datetime import datetime, timezone
            src_rel = newest_src_path.relative_to(src_dir)

            def _fmt(ts: float) -> str:
                return (
                    datetime.fromtimestamp(ts, tz=timezone.utc)
                    .strftime("%Y-%m-%d %H:%M:%S UTC")
                )

            log.warning(
                _DASHBOARD_STALE_BANNER.format(
                    src_rel=str(src_rel),
                    src_mtime=_fmt(newest_src_mtime),
                    bundle_mtime=_fmt(newest_bundle_mtime),
                )
            )
    except OSError:
        # Defensive — never block daemon startup on a freshness
        # check failure. The trade-off here is silent skip vs
        # crash; silent is the safe call.
        pass

# Tailscale serve path. The dashboard owns the root of the tailnet
# host; the livestream side-process uses /vexis. Tailscale's longest-
# prefix matching means both can coexist — /vexis/* routes to livestream,
# everything else to the dashboard.
TAILSCALE_PATH = "/"
TAILSCALE_TIMEOUT_SECONDS = 10

# How many lines of vexis.log to surface on the status page.
STATUS_LOG_LINES = 100

# How many curator runs to surface on the curator page (most recent first).
CURATOR_RUN_HISTORY_LIMIT = 50

# How many recent screenshots to surface on the browser page.
BROWSER_SCREENSHOT_LIMIT = 5

# Profile-size cache TTL. Walking 60+ MB of Chromium profile every poll
# tick is wasteful; once every 30 s is plenty given the size doesn't
# change at second resolution. The UI surfaces "as of <ts>" so the user
# knows it isn't perfectly live.
BROWSER_PROFILE_SIZE_TTL_SECONDS = 30.0

# Allowed shape of screenshot filenames. ``BrowserTools.screenshot``
# writes ``<ts>.png`` where ``<ts>`` is ``YYYYMMDDTHHMMSSZ`` — uppercase
# letters and digits only. The pattern below rejects any path
# separator, dot-dot, or otherwise unexpected character so the file
# response can never reach outside the screenshots dir.
_BROWSER_SCREENSHOT_NAME_RE = re.compile(r"^[A-Z0-9]+\.png$")


class DashboardConfig:
    """Mutable knobs the daemon may flip before start-up.

    Kept as an instance rather than module globals so tests can spin
    up isolated dashboards without leaking state.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = DEFAULT_DASHBOARD_PORT,
        web_dist: Path,
        tailscale_path: str = TAILSCALE_PATH,
        manage_tailscale: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.web_dist = web_dist
        self.tailscale_path = tailscale_path
        self.manage_tailscale = manage_tailscale


class WebDashboard:
    """Owns the FastAPI app, the uvicorn server task, and Tailscale Serve.

    Lifecycle: ``await start()`` → daemon runs → ``await stop()``. The
    server is bound to a single asyncio task so cancelling it cleanly
    propagates a uvicorn graceful-shutdown signal.
    """

    def __init__(
        self,
        *,
        workspace: Path,
        sessions: SessionStore,
        running_tasks: RunningTasks,
        background_tasks: BackgroundTasks,
        curator: CuratorController | None,
        browser: BrowserTools,
        learning: LearningController | None,
        config: DashboardConfig,
        running_brain_kind: str | None = None,
        chat: WebChatTransport | None = None,
    ) -> None:
        self._workspace = workspace
        self._sessions = sessions
        self._running_tasks = running_tasks
        self._background_tasks = background_tasks
        self._curator = curator
        self._browser = browser
        self._learning = learning
        self._config = config
        # Optional — when None the /api/v1/chat/* routes return 503.
        # Tests that don't exercise chat omit it. main.py wires it
        # alongside the Telegram transport so both share one
        # MessageHandler (and therefore one SessionStore + Notifier).
        self._chat = chat
        # Day 5 of model UX: the dashboard payload's
        # check_brain_kind_consistency canary needs to know what
        # brain class the daemon actually instantiated. main.py
        # passes the kind string at construction; tests omit it
        # (default None → no canary check fires).
        self._running_brain_kind = running_brain_kind

        # Issue a fresh token immediately so the Telegram /dashboard
        # handler can read it as soon as the daemon's PTB layer comes up.
        # The token file is mode 0600 and rotates every daemon start.
        self._token: str = issue_token()
        self._started_at: datetime = datetime.now(timezone.utc)
        self._tailscale_url: str | None = None
        self._tailscale_dns: str | None = None
        self._server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task[None] | None = None

        # Profile-size cache. The walk is the only piece of the browser
        # payload that costs real I/O; everything else is in-memory.
        self._profile_size_cache: tuple[float, int, str] | None = None

        # v3c Day 4b: in-memory sliding-window rate limiter for the
        # relationships approval/reject/edit/resolve endpoints.
        # Keyed by the bearer token (only one in this single-user
        # deployment, but the structure carries forward if the auth
        # surface ever grows). Per-token deque of timestamps; we
        # evict anything older than the window on every check.
        # The limit defends the v3c "approval is now security-relevant"
        # risk row: 100 mutations / 10 min is well above human pace
        # and well below a malicious bulk-approve scenario.
        self._relationships_mutation_window_seconds: int = 600
        self._relationships_mutation_limit: int = 100
        self._relationships_mutation_log: dict[str, deque[float]] = defaultdict(deque)

        self._app = self._build_app()

    # ------------------------------------------------------------ public

    @property
    def url(self) -> str | None:
        """The Tailscale Serve URL once the dashboard is reachable, or
        ``None`` while we're still running on localhost only."""
        return self._tailscale_url

    @property
    def token(self) -> str:
        return self._token

    @property
    def local_url(self) -> str:
        return f"http://{self._config.host}:{self._config.port}"

    async def start(self) -> None:
        """Bind uvicorn, configure Tailscale Serve. Idempotent on re-call."""
        if self._serve_task is not None:
            return

        # Stale-bundle detector (added 2026-05-08): the daemon
        # serves whatever's in ``web/dist/`` — if the user edits
        # frontend source but forgets to ``npm run build``, they'll
        # see old behavior in their browser despite a fresh daemon
        # restart. Log a banner WARNING at boot so the next
        # ``journalctl -u vexis-agent`` (or whatever supervisor
        # they're on) surfaces the issue immediately.
        _warn_if_dashboard_bundle_stale(self._config.web_dist)

        # log_config=None silences uvicorn's default logger setup so it
        # inherits our root logger (rotating file + stderr) instead of
        # opening its own colorized stderr handler.
        uvicorn_config = uvicorn.Config(
            self._app,
            host=self._config.host,
            port=self._config.port,
            log_config=None,
            access_log=False,
            lifespan="on",
        )
        self._server = uvicorn.Server(uvicorn_config)
        self._serve_task = asyncio.create_task(
            self._server.serve(), name="vexis-dashboard"
        )

        # Wait for the server to actually bind before configuring
        # Tailscale Serve — otherwise the upstream proxy will get
        # connection refused on the first dashboard request.
        await self._await_listening()

        if self._config.manage_tailscale:
            try:
                await self._configure_tailscale()
            except _TailscaleError as exc:
                # The dashboard still works on localhost; without the
                # tailnet mapping the user just can't reach it from
                # their phone. Log and move on so a tailscale outage
                # doesn't take down the bot.
                log.warning(
                    "Could not configure Tailscale Serve for dashboard: %s "
                    "(dashboard still available at %s)",
                    exc,
                    self.local_url,
                )
        log.info(
            "Dashboard ready at %s (local: %s)",
            self._tailscale_url or "(localhost only)",
            self.local_url,
        )

    async def stop(self) -> None:
        """Tear down Tailscale Serve, shut uvicorn, drop the token file."""
        if self._serve_task is None:
            clear_token()
            return
        if self._config.manage_tailscale and self._tailscale_url is not None:
            try:
                await self._teardown_tailscale()
            except _TailscaleError as exc:
                log.warning("Tailscale Serve teardown failed: %s", exc)

        if self._server is not None:
            self._server.should_exit = True
        try:
            await asyncio.wait_for(self._serve_task, timeout=5)
        except asyncio.TimeoutError:
            log.warning("uvicorn graceful shutdown timed out; cancelling")
            self._serve_task.cancel()
            try:
                await self._serve_task
            except (asyncio.CancelledError, Exception):
                pass
        self._serve_task = None
        self._server = None

        clear_token()
        log.info("Dashboard stopped")

    # ------------------------------------------------------------ internals

    def _build_app(self) -> FastAPI:
        @asynccontextmanager
        async def _lifespan(_app: FastAPI):
            yield

        app = FastAPI(
            title="vexis-agent dashboard",
            version="1.0",
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
            lifespan=_lifespan,
        )
        # CORS lock-down: only same-origin or localhost dev. The token is
        # the real auth, but defense-in-depth is cheap.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[
                "http://localhost:5173",
                "http://127.0.0.1:5173",
            ],
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Authorization", "Content-Type"],
        )

        bearer = HTTPBearer(auto_error=False)

        async def _require_auth(
            request: Request,
            creds: HTTPAuthorizationCredentials | None = Depends(bearer),
        ) -> None:
            # Bearer header is the canonical path. Query-param ?token=
            # is supported on initial load only, so the React shell can
            # pull a token off the URL Telegram handed the user before
            # localStorage has a value to source from.
            presented: str | None = None
            if creds and creds.scheme.lower() == "bearer" and creds.credentials:
                presented = creds.credentials
            if presented is None:
                presented = request.query_params.get("token")
            if presented is None or not secrets.compare_digest(
                presented, self._token
            ):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="invalid or missing dashboard token",
                )

        @app.get("/api/v1/memory", dependencies=[Depends(_require_auth)])
        async def get_memory() -> dict:
            return self._memory_payload()

        @app.get("/api/v1/skills", dependencies=[Depends(_require_auth)])
        async def get_skills() -> dict:
            return self._skills_payload()

        @app.get(
            "/api/v1/skills/{name}", dependencies=[Depends(_require_auth)]
        )
        async def get_skill(name: str) -> dict:
            payload = self._skill_body(name)
            if payload is None:
                raise HTTPException(404, f"no skill named '{name}'")
            return payload

        @app.post(
            "/api/v1/skills/{name}/pin", dependencies=[Depends(_require_auth)]
        )
        async def pin_skill(name: str) -> dict:
            store = PinStore(skills_dir(self._workspace))
            if store.is_pinned(name):
                return {"ok": True, "name": name, "pinned": True, "changed": False}
            store.pin(name)
            return {"ok": True, "name": name, "pinned": True, "changed": True}

        @app.post(
            "/api/v1/skills/{name}/unpin",
            dependencies=[Depends(_require_auth)],
        )
        async def unpin_skill(name: str) -> dict:
            store = PinStore(skills_dir(self._workspace))
            if not store.is_pinned(name):
                return {
                    "ok": True,
                    "name": name,
                    "pinned": False,
                    "changed": False,
                }
            store.unpin(name)
            return {"ok": True, "name": name, "pinned": False, "changed": True}

        @app.post(
            "/api/v1/skills/{name}/restore",
            dependencies=[Depends(_require_auth)],
        )
        async def restore(name: str) -> dict:
            op = restore_skill(skills_dir(self._workspace), name)
            if not op.ok:
                raise HTTPException(400, op.message)
            return {"ok": True, "message": op.message, "extra": op.extra}

        @app.get("/api/v1/curator", dependencies=[Depends(_require_auth)])
        async def get_curator() -> dict:
            return self._curator_payload()

        @app.get(
            "/api/v1/curator/runs/{folder}",
            dependencies=[Depends(_require_auth)],
        )
        async def get_curator_run(folder: str) -> dict:
            payload = self._curator_run_payload(folder)
            if payload is None:
                raise HTTPException(404, f"no curator run '{folder}'")
            return payload

        @app.post(
            "/api/v1/curator/run", dependencies=[Depends(_require_auth)]
        )
        async def post_curator_run() -> dict:
            if self._curator is None:
                raise HTTPException(503, "curator not initialised")
            if self._curator.is_running():
                raise HTTPException(409, "curator pass already in flight")
            summary = await self._curator.run_now()
            if summary is None:
                # Race: another caller grabbed the busy lock between the
                # 409 check and run_now. Treat as 'in flight' rather
                # than a server error.
                raise HTTPException(409, "curator pass already in flight")
            return {
                "ok": True,
                "folder": str(summary.folder),
                "phase1": {
                    "archived": summary.phase1.archived,
                    "marked_stale": summary.phase1.marked_stale,
                    "reactivated": summary.phase1.reactivated,
                    "checked": summary.phase1.checked,
                },
                "phase2": {
                    "ran": summary.phase2.ran,
                    "archived_names": list(summary.phase2.archived_names),
                    "created_names": list(summary.phase2.created_names),
                    "error": summary.phase2.error,
                },
            }

        @app.get("/api/v1/status", dependencies=[Depends(_require_auth)])
        async def get_status() -> dict:
            return await self._status_payload()

        @app.get("/api/v1/browser", dependencies=[Depends(_require_auth)])
        async def get_browser() -> dict:
            return self._browser_payload()

        @app.get(
            "/api/v1/browser/screenshot/{name}",
            dependencies=[Depends(_require_auth)],
        )
        async def get_browser_screenshot(name: str) -> FileResponse:
            return self._browser_screenshot_response(name)

        @app.post(
            "/api/v1/browser/open-blank",
            dependencies=[Depends(_require_auth)],
        )
        async def post_browser_open_blank() -> dict:
            # Reuses the exact codepath the control socket dispatches
            # for ``browser_navigate about:blank`` — same lazy-launch
            # behavior, same session reuse if one is already running.
            return await self._browser.navigate("about:blank")

        @app.post(
            "/api/v1/browser/recycle",
            dependencies=[Depends(_require_auth)],
        )
        async def post_browser_recycle() -> dict:
            was_running = self._browser.manager.is_running()
            await self._browser.manager.stop()
            return {"ok": True, "was_running": was_running}

        # ----- Step 15: learning tab -----

        @app.get("/api/v1/learning", dependencies=[Depends(_require_auth)])
        async def get_learning() -> dict:
            return self._learning_payload()

        # ----- v3c Day 4b: relationships dashboard endpoints -----
        #
        # All gated by ``_require_auth`` (the existing bearer-token
        # check above). Mutations also hit the sliding-window rate
        # limiter. Approve / reject / edit / resolve_qualifier emit
        # structured INFO log lines for audit.

        def _rate_limit_or_raise() -> None:
            """Sliding-window check. Tokens are single-user in this
            deployment, but keying by token + path works if the
            auth surface ever grows."""
            now = time.time()
            window = self._relationships_mutation_window_seconds
            limit = self._relationships_mutation_limit
            log_for_token = self._relationships_mutation_log[self._token]
            cutoff = now - window
            while log_for_token and log_for_token[0] < cutoff:
                log_for_token.popleft()
            if len(log_for_token) >= limit:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(
                        f"rate limit exceeded: {limit} mutations per "
                        f"{window}s window"
                    ),
                )
            log_for_token.append(now)

        def _audit_log(
            action: str,
            slug: str,
            *,
            fact_ids: list[str] | None = None,
            extra: dict | None = None,
        ) -> None:
            """Structured audit line for the relationships mutation
            surface. Same INFO logger the rest of the daemon uses;
            JSON-shaped fields so downstream log shippers can parse."""
            payload = {
                "action": action,
                "slug": slug,
                "fact_ids": list(fact_ids) if fact_ids is not None else None,
                "token_subject": _token_fingerprint(self._token),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            if extra:
                payload.update(extra)
            log.info("relationships.%s %s", action, json.dumps(payload, sort_keys=True))

        def _candidate_view_to_dict(view) -> dict:
            return {
                "slug": view.slug,
                "display_name": view.display_name,
                "qualifier": view.qualifier,
                "qualifier_candidates": list(view.qualifier_candidates),
                "strongest_cue_seen": view.strongest_cue_seen,
                "session_count": view.session_count,
                "fact_count": view.fact_count,
                "eligible": view.eligible,
                "first_seen": view.first_seen.isoformat(),
                "last_seen": view.last_seen.isoformat(),
                "approved_at": (
                    view.approved_at.isoformat() if view.approved_at else None
                ),
                "rejected_at": (
                    view.rejected_at.isoformat() if view.rejected_at else None
                ),
                "facts": [
                    {
                        "fact_id": f.fact_id,
                        "text": f.text,
                        "occurrence_count": f.occurrence_count,
                        "first_seen": f.first_seen.isoformat(),
                        "last_seen": f.last_seen.isoformat(),
                        "rejected_at": (
                            f.rejected_at.isoformat() if f.rejected_at else None
                        ),
                    }
                    for f in view.facts
                ],
            }

        @app.get(
            "/api/v1/relationships/live",
            dependencies=[Depends(_require_auth)],
        )
        async def get_relationships_live() -> dict:
            """Read-only RELATIONSHIPS.md view for the dashboard
            top-half pane. Parses the live file via the existing
            ``RelationshipsStore.list_live`` and returns a JSON
            shape suitable for person-cards rendering."""
            if self._learning is None:
                raise HTTPException(503, "learning curator not initialised")
            curator = self._learning.relationships_curator
            people = curator.store.list_live()
            return {
                "people": [
                    {
                        "slug": p.slug,
                        "display_name": p.display_name,
                        "relationship": p.relationship,
                        "qualifier": p.qualifier,
                        "last_confirmed": p.last_confirmed,
                        "source_session": p.source_session,
                        "facts": [
                            {
                                "text": f.text,
                                "confirmed_date": f.confirmed_date,
                                "source_session_short": f.source_session_short,
                                "superseded_by_date": f.superseded_by_date,
                                "superseded_by_session": (
                                    f.superseded_by_session
                                ),
                            }
                            for f in p.facts
                        ],
                    }
                    for p in people
                ],
            }

        @app.get(
            "/api/v1/relationships/candidates",
            dependencies=[Depends(_require_auth)],
        )
        async def get_relationships_candidates(
            request: Request,
        ) -> dict:
            if self._learning is None:
                raise HTTPException(503, "learning curator not initialised")
            include_rejected = (
                request.query_params.get("include_rejected", "")
                .strip()
                .lower()
                in ("1", "true", "yes", "on")
            )
            curator = self._learning.relationships_curator
            views = curator.candidate_store.list_all(
                include_rejected=include_rejected,
            )
            # Filter out approved entries from the default "what
            # needs my attention" view (they're audit-retained but
            # don't belong on the action surface).
            return {
                "candidates": [
                    _candidate_view_to_dict(v) for v in views
                    if v.approved_at is None
                ],
            }

        @app.get(
            "/api/v1/relationships/candidates/{slug}",
            dependencies=[Depends(_require_auth)],
        )
        async def get_relationships_candidate_detail(slug: str) -> dict:
            if self._learning is None:
                raise HTTPException(503, "learning curator not initialised")
            curator = self._learning.relationships_curator
            candidate = curator.candidate_store.get(slug)
            if candidate is None:
                raise HTTPException(404, f"no candidate for slug={slug!r}")
            return {
                "slug": candidate.slug,
                "display_name": candidate.display_name,
                "qualifier_candidates": list(candidate.qualifier_candidates),
                "strongest_cue_seen": candidate.strongest_cue_seen,
                "first_seen": candidate.first_seen.isoformat(),
                "last_seen": candidate.last_seen.isoformat(),
                "approved_at": (
                    candidate.approved_at.isoformat()
                    if candidate.approved_at
                    else None
                ),
                "rejected_at": (
                    candidate.rejected_at.isoformat()
                    if candidate.rejected_at
                    else None
                ),
                "facts": [
                    {
                        "fact_id": fid,
                        "text": fact.text,
                        "first_seen": fact.first_seen.isoformat(),
                        "last_seen": fact.last_seen.isoformat(),
                        "approved_at": (
                            fact.approved_at.isoformat()
                            if fact.approved_at
                            else None
                        ),
                        "rejected_at": (
                            fact.rejected_at.isoformat()
                            if fact.rejected_at
                            else None
                        ),
                        "occurrences": [
                            {
                                "session_uuid": o.session_uuid,
                                "turn_index": o.turn_index,
                                "seen_at": o.seen_at.isoformat(),
                            }
                            for o in fact.occurrences
                        ],
                    }
                    for fid, fact in candidate.facts.items()
                ],
            }

        @app.post(
            "/api/v1/relationships/candidates/{slug}/approve",
            dependencies=[Depends(_require_auth)],
        )
        async def post_relationships_approve(slug: str, body: dict) -> JSONResponse:
            if self._learning is None:
                raise HTTPException(503, "learning curator not initialised")
            _rate_limit_or_raise()
            fact_ids_raw = body.get("fact_ids")
            fact_ids: list[str] | None
            if fact_ids_raw is None:
                fact_ids = None
            elif isinstance(fact_ids_raw, list) and all(
                isinstance(x, str) for x in fact_ids_raw
            ):
                fact_ids = list(fact_ids_raw) or None
            else:
                raise HTTPException(400, "fact_ids must be list[str] or absent")
            qualifier = body.get("qualifier")
            if qualifier is not None and not isinstance(qualifier, str):
                raise HTTPException(400, "qualifier must be str or null")
            curator = self._learning.relationships_curator
            try:
                result = curator.approve_candidate(
                    slug,
                    fact_ids=fact_ids,
                    qualifier=qualifier or None,
                )
            except Exception:
                log.exception("relationships approve raised")
                raise HTTPException(500, "approve failed")
            _audit_log(
                "approve",
                slug,
                fact_ids=fact_ids,
                extra={
                    "ok": result.ok,
                    "blocked_by": result.blocked_by,
                },
            )
            if result.ok:
                # v3c Day 4c: surface the brain-cache invalidation
                # hint as a separate field so the React panel can
                # render it as an inline toast. Flag-gated.
                hint: str | None = None
                if yaml_config.relationships_approval_hint_enabled():
                    hint = (
                        "Saved. Active in Vexis's next session — "
                        "run `/clear` in Telegram to start fresh."
                    )
                return JSONResponse(
                    {
                        "ok": True,
                        "slug": result.slug,
                        "reply_text": result.reply_text,
                        "approval_hint": hint,
                    }
                )
            if result.blocked_by == "missing_existing_qualifier":
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": "missing_existing_qualifier",
                        "slug": result.slug,
                        "existing_slug": result.existing_slug,
                        "existing_facts": list(result.existing_facts),
                        "existing_qualifier_candidates": list(
                            result.existing_qualifier_candidates
                        ),
                        "proposed_qualifier": result.proposed_qualifier,
                        "reply_text": result.reply_text,
                    },
                )
            if result.blocked_by == "sensitive-pattern":
                return JSONResponse(
                    status_code=422,
                    content={
                        "error": "blocked_by_sensitive_pattern",
                        "slug": result.slug,
                        "reply_text": result.reply_text,
                        "detail": result.detail,
                    },
                )
            # Other refusals (not-in-queue, slug-rejected,
            # no-active-facts, store-error) → 400 with the
            # blocked_by code.
            return JSONResponse(
                status_code=400,
                content={
                    "error": result.blocked_by or "unknown",
                    "slug": result.slug,
                    "reply_text": result.reply_text,
                    "detail": result.detail,
                },
            )

        @app.post(
            "/api/v1/relationships/candidates/{slug}/reject",
            dependencies=[Depends(_require_auth)],
        )
        async def post_relationships_reject(slug: str, body: dict) -> dict:
            if self._learning is None:
                raise HTTPException(503, "learning curator not initialised")
            _rate_limit_or_raise()
            fact_ids_raw = body.get("fact_ids")
            fact_ids: list[str] | None
            if fact_ids_raw is None:
                fact_ids = None
            elif isinstance(fact_ids_raw, list) and all(
                isinstance(x, str) for x in fact_ids_raw
            ):
                fact_ids = list(fact_ids_raw) or None
            else:
                raise HTTPException(400, "fact_ids must be list[str] or absent")
            curator = self._learning.relationships_curator
            try:
                result = curator.reject_candidate(slug, fact_ids=fact_ids)
            except Exception:
                log.exception("relationships reject raised")
                raise HTTPException(500, "reject failed")
            _audit_log(
                "reject",
                slug,
                fact_ids=fact_ids,
                extra={"ok": result.ok},
            )
            if not result.ok:
                raise HTTPException(404, result.reply_text)
            return {
                "ok": True,
                "slug": result.slug,
                "reply_text": result.reply_text,
            }

        @app.post(
            "/api/v1/relationships/candidates/{slug}/edit",
            dependencies=[Depends(_require_auth)],
        )
        async def post_relationships_edit(slug: str, body: dict) -> dict:
            if self._learning is None:
                raise HTTPException(503, "learning curator not initialised")
            _rate_limit_or_raise()
            fact_id = body.get("fact_id")
            new_text = body.get("new_text")
            if not isinstance(fact_id, str) or not fact_id.strip():
                raise HTTPException(400, "fact_id must be a non-empty string")
            if not isinstance(new_text, str) or not new_text.strip():
                raise HTTPException(400, "new_text must be a non-empty string")
            curator = self._learning.relationships_curator
            store = curator.candidate_store
            candidate = store.get(slug)
            if candidate is None:
                raise HTTPException(404, f"no candidate for slug={slug!r}")
            old_fact = candidate.facts.get(fact_id)
            if old_fact is None:
                raise HTTPException(404, f"no fact_id={fact_id!r} under {slug!r}")
            # Audit-edit semantics: tombstone the old fact_id
            # under the slug, queue a new observation with the
            # edited text inheriting the old fact's first
            # occurrence's session UUID + turn index. The new
            # fact_id is computed deterministically from the new
            # text; eligibility recomputes on next call.
            store.mark_rejected(slug, fact_ids=[fact_id])
            anchor = old_fact.occurrences[0] if old_fact.occurrences else None
            anchor_session = anchor.session_uuid if anchor else "edit"
            anchor_turn = anchor.turn_index if anchor else 1
            new_candidate = store.add_observation(
                slug=slug,
                display_name=candidate.display_name,
                qualifier=(
                    candidate.qualifier_candidates[-1]
                    if candidate.qualifier_candidates
                    else None
                ),
                fact_text=new_text.strip(),
                session_uuid=anchor_session,
                turn_index=anchor_turn,
            )
            from core.relationships.consent import _fact_id as compute_fact_id
            new_id = compute_fact_id(new_text.strip())
            _audit_log(
                "edit",
                slug,
                fact_ids=[fact_id],
                extra={
                    "new_fact_id": new_id,
                    "ok": new_candidate is not None,
                },
            )
            return {
                "ok": True,
                "slug": slug,
                "old_fact_id": fact_id,
                "new_fact_id": new_id,
            }

        @app.post(
            "/api/v1/relationships/candidates/{slug}/resolve_qualifier",
            dependencies=[Depends(_require_auth)],
        )
        async def post_relationships_resolve_qualifier(
            slug: str, body: dict,
        ) -> dict:
            """Used by the dashboard's missing-qualifier modal flow.
            Renames an existing live entry from ``slug`` to
            ``slug-<existing_qualifier>`` and stamps the YAML
            qualifier so the next approve attempt no longer hits
            the 409. Wraps ``RelationshipsStore.rename_live_slug``
            from v3b."""
            if self._learning is None:
                raise HTTPException(503, "learning curator not initialised")
            _rate_limit_or_raise()
            existing_qualifier = body.get("existing_qualifier")
            if (
                not isinstance(existing_qualifier, str)
                or not existing_qualifier.strip()
            ):
                raise HTTPException(
                    400,
                    "existing_qualifier must be a non-empty string",
                )
            existing_qualifier = existing_qualifier.strip()
            curator = self._learning.relationships_curator
            from core.relationships.triggers import (
                derive_slug_with_disambiguation,
            )
            existing = curator.store.get_live(slug)
            if existing is None:
                raise HTTPException(
                    404, f"no live entry for slug={slug!r}",
                )
            new_slug = derive_slug_with_disambiguation(
                existing.display_name, existing_qualifier,
            )
            today = datetime.now(timezone.utc).date().isoformat()
            try:
                rename_res = curator.store.rename_live_slug(
                    old_slug=slug,
                    new_slug=new_slug,
                    new_qualifier=existing_qualifier,
                    disambiguated_date=today,
                )
            except Exception:
                log.exception("relationships rename_live_slug raised")
                raise HTTPException(500, "rename failed")
            _audit_log(
                "resolve_qualifier",
                slug,
                extra={
                    "ok": rename_res.ok,
                    "new_slug": new_slug,
                    "existing_qualifier": existing_qualifier,
                },
            )
            if not rename_res.ok:
                raise HTTPException(409, rename_res.message)
            return {
                "ok": True,
                "old_slug": slug,
                "new_slug": new_slug,
                "qualifier": existing_qualifier,
            }

        @app.post(
            "/api/v1/learning/coherence-audit",
            dependencies=[Depends(_require_auth)],
        )
        async def post_learning_coherence_audit(body: dict) -> dict:
            if self._learning is None:
                raise HTTPException(503, "learning curator not initialised")
            # The judge needs at minimum lesson + scope + evidence.
            # Reject early with a clear error rather than returning a
            # garbage NEAR_MISS verdict from the degraded path.
            for required in ("lesson", "scope", "evidence"):
                value = body.get(required)
                if not isinstance(value, str) or not value.strip():
                    raise HTTPException(
                        400, f"missing or empty field: {required}"
                    )
            entry = {
                "lesson": body["lesson"],
                "scope": body["scope"],
                "evidence": body["evidence"],
                "class": body.get("class"),
                "tier": body.get("tier"),
                "source": body.get("source"),
                "entry_id": body.get("entry_id"),
            }
            return await asyncio.to_thread(self._learning.judge_entry, entry)

        # ----- Tailscale visibility -----

        @app.get(
            "/api/v1/tailscale/status",
            dependencies=[Depends(_require_auth)],
        )
        async def get_tailscale_status() -> dict:
            return await asyncio.to_thread(self._tailscale_payload)

        # ----- /goal observability + control -----
        #
        # Dashboard surface for the v3d /goal feature. The feature's
        # only entry point remains the Telegram /goal <text> command
        # — the dashboard is observability + control (pause / resume /
        # clear), never creation. Mutations route through the same
        # ``GoalManager`` that Telegram handlers use, with a sentinel
        # ``paused_reason="dashboard-paused"`` etc. so the audit trail
        # records where the action came from.

        @app.get(
            "/api/v1/goals",
            dependencies=[Depends(_require_auth)],
        )
        async def get_goals() -> dict:
            return await asyncio.to_thread(self._goals_payload)

        @app.post(
            "/api/v1/goals/pause",
            dependencies=[Depends(_require_auth)],
        )
        async def post_goals_pause() -> dict:
            return await self._goals_pause()

        @app.post(
            "/api/v1/goals/resume",
            dependencies=[Depends(_require_auth)],
        )
        async def post_goals_resume() -> dict:
            return await self._goals_resume()

        @app.post(
            "/api/v1/goals/clear",
            dependencies=[Depends(_require_auth)],
        )
        async def post_goals_clear() -> dict:
            return await self._goals_clear()

        # ----- /api/v1/models — Day 3 of model UX -----
        # Read-only resolution table backing the dashboard's
        # Models tab. Day 4 adds POST endpoints for edits +
        # discovery-refresh, all gated behind model_ux_enabled()
        # to match the slash command's flag posture.
        @app.get(
            "/api/v1/models",
            dependencies=[Depends(_require_auth)],
        )
        async def get_models() -> dict:
            return await asyncio.to_thread(self._models_payload)

        # ----- Day 4 mutation + discovery endpoints -----
        # All flag-gated behind model_ux_enabled() so the production
        # claude-code path is byte-equivalent for any user who
        # hasn't flipped the flag.
        @app.post(
            "/api/v1/models/set",
            dependencies=[Depends(_require_auth)],
        )
        async def post_models_set(payload: dict) -> dict:
            return await asyncio.to_thread(
                self._models_set, payload,
            )

        @app.post(
            "/api/v1/models/reset",
            dependencies=[Depends(_require_auth)],
        )
        async def post_models_reset(payload: dict | None = None) -> dict:
            return await asyncio.to_thread(
                self._models_reset, payload or {},
            )

        @app.post(
            "/api/v1/models/brain",
            dependencies=[Depends(_require_auth)],
        )
        async def post_models_brain(payload: dict) -> dict:
            return await asyncio.to_thread(
                self._models_set_brain, payload,
            )

        @app.post(
            "/api/v1/models/discovery/refresh",
            dependencies=[Depends(_require_auth)],
        )
        async def post_models_discovery_refresh() -> dict:
            return await asyncio.to_thread(
                self._models_discovery_refresh,
            )

        # ----- chat (web transport for the dashboard chat UI) -----
        #
        # All routes share three contracts:
        #   - 503 when ``self._chat`` was constructed without a
        #     transport (test fixtures, dashboard-only smoke).
        #   - User-text size cap at 32 KiB so a runaway paste can't
        #     starve the brain or fill the JSONL with a single message.
        #     Far above any reasonable typed/transcribed message; well
        #     below the kind of payload that suggests a misuse.
        #   - Handler return values are forwarded verbatim as ``reply``;
        #     a ``None`` (handler suppressed the message — only happens
        #     when the user_id check fails, which can't happen behind
        #     the auth gate today, but we 401 it to keep the contract
        #     honest in case a future code path opens that hole).

        _CHAT_TEXT_MAX_BYTES = 32 * 1024

        def _chat_or_503() -> WebChatTransport:
            if self._chat is None:
                raise HTTPException(503, "chat transport not initialised")
            return self._chat

        def _validated_text(body: dict, *, key: str = "text") -> str:
            value = body.get(key)
            if not isinstance(value, str) or not value.strip():
                raise HTTPException(400, f"'{key}' must be a non-empty string")
            if len(value.encode("utf-8")) > _CHAT_TEXT_MAX_BYTES:
                raise HTTPException(
                    413, f"'{key}' exceeds {_CHAT_TEXT_MAX_BYTES} bytes",
                )
            return value

        def _validated_name(body: dict, *, key: str, optional: bool = False) -> str | None:
            value = body.get(key)
            if value is None or value == "":
                if optional:
                    return None
                raise HTTPException(400, f"'{key}' is required")
            if not isinstance(value, str):
                raise HTTPException(400, f"'{key}' must be a string")
            return value

        @app.post(
            "/api/v1/chat/send",
            dependencies=[Depends(_require_auth)],
        )
        async def post_chat_send(body: dict) -> JSONResponse:
            chat = _chat_or_503()
            text = _validated_text(body)
            # Attachments are optional. Each entry is a {path, name, mime}
            # dict the UI got back from POST /chat/attach. We format
            # them into a hint block prepended to the user's message
            # so the brain (which can read files via its existing
            # tool surface) knows which paths to look at.
            attachments_raw = body.get("attachments")
            if attachments_raw is not None:
                if not isinstance(attachments_raw, list):
                    raise HTTPException(400, "'attachments' must be a list")
                hint = _format_attachments_hint(attachments_raw)
                if hint:
                    text = f"{hint}\n\n{text}"
            reply = await chat.send(text)
            if reply is None:
                raise HTTPException(401, "message rejected")
            return JSONResponse({"reply": reply})

        @app.get(
            "/api/v1/chat/sessions",
            dependencies=[Depends(_require_auth)],
        )
        async def get_chat_sessions() -> JSONResponse:
            chat = _chat_or_503()
            infos = chat.list_sessions()
            if infos is None:
                raise HTTPException(401, "session list rejected")
            return JSONResponse(
                {
                    "sessions": [
                        {
                            "name": s.name,
                            "is_active": s.is_active,
                            "created_at": s.created_at,
                        }
                        for s in infos
                    ],
                }
            )

        @app.post(
            "/api/v1/chat/sessions/new",
            dependencies=[Depends(_require_auth)],
        )
        async def post_chat_sessions_new(body: dict) -> JSONResponse:
            chat = _chat_or_503()
            # Empty body is fine — handle_new auto-generates a name.
            name = _validated_name(body, key="name", optional=True)
            reply = await chat.new_session(name)
            if reply is None:
                raise HTTPException(401, "new session rejected")
            return JSONResponse({"reply": reply})

        @app.post(
            "/api/v1/chat/sessions/switch",
            dependencies=[Depends(_require_auth)],
        )
        async def post_chat_sessions_switch(body: dict) -> JSONResponse:
            chat = _chat_or_503()
            name = _validated_name(body, key="name")
            assert name is not None  # narrowing for mypy; required above
            reply = await chat.switch_session(name)
            if reply is None:
                raise HTTPException(401, "switch rejected")
            return JSONResponse({"reply": reply})

        @app.post(
            "/api/v1/chat/sessions/rename",
            dependencies=[Depends(_require_auth)],
        )
        async def post_chat_sessions_rename(body: dict) -> JSONResponse:
            chat = _chat_or_503()
            old = _validated_name(body, key="old")
            new = _validated_name(body, key="new")
            assert old is not None and new is not None
            reply = await chat.rename_session(old, new)
            if reply is None:
                raise HTTPException(401, "rename rejected")
            return JSONResponse({"reply": reply})

        @app.post(
            "/api/v1/chat/sessions/delete",
            dependencies=[Depends(_require_auth)],
        )
        async def post_chat_sessions_delete(body: dict) -> JSONResponse:
            chat = _chat_or_503()
            name = _validated_name(body, key="name")
            assert name is not None
            reply = await chat.delete_session(name)
            if reply is None:
                raise HTTPException(401, "delete rejected")
            return JSONResponse({"reply": reply})

        @app.post(
            "/api/v1/chat/clear",
            dependencies=[Depends(_require_auth)],
        )
        async def post_chat_clear() -> JSONResponse:
            chat = _chat_or_503()
            reply = await chat.clear()
            if reply is None:
                raise HTTPException(401, "clear rejected")
            return JSONResponse({"reply": reply})

        # ----- chat attachments — phase 2 -----
        #
        # Two-step flow: UI uploads each file to /chat/attach,
        # gets back a server-side path, then includes those paths
        # in the next /chat/send body. Two-step rather than one
        # multipart per send because:
        #   1. Lets the user queue multiple files before sending,
        #   2. Composer can show inline previews from the returned
        #      paths without re-uploading,
        #   3. Keeps /chat/send a JSON endpoint (cleaner SSE
        #      upgrade path later).
        #
        # Files land in <workspace>/uploads/<active-session>/<ts>-<safe-name>.
        # Per-session subdir gives the user a quick `rm -rf` path
        # to wipe one conversation's attachments. Timestamp prefix
        # avoids collisions when uploading the same filename twice.

        _ATTACH_FILENAME_SAFE_RE = re.compile(r"[^a-zA-Z0-9._-]")

        def _safe_filename(name: str | None) -> str:
            """Reduce an uploaded filename to a safe basename. Strips
            path components, dot-dot, and any character outside the
            alnum + dot/underscore/dash set. Empty after sanitizing
            falls back to ``upload`` so we always have a usable name."""
            if not name:
                return "upload"
            base = name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            base = base.strip().lstrip(".")
            cleaned = _ATTACH_FILENAME_SAFE_RE.sub("_", base)
            return cleaned or "upload"

        @app.post(
            "/api/v1/chat/attach",
            dependencies=[Depends(_require_auth)],
        )
        async def post_chat_attach(
            file: UploadFile = File(...),
        ) -> JSONResponse:
            if not yaml_config.chat_attachments_enabled():
                raise HTTPException(503, "chat attachments disabled")

            allowed = yaml_config.chat_attachments_allowed_mimes()
            mime = (file.content_type or "").lower().strip()
            if mime not in allowed:
                raise HTTPException(
                    415,
                    f"mime {mime!r} not in allowlist (allowed: "
                    f"{', '.join(sorted(allowed))})",
                )

            # Per-session subdir so deleting a session can also
            # delete its uploads (phase 2.5 cleanup hook).
            chat_obj = _chat_or_503()
            sessions = chat_obj.list_sessions()
            active_name = "_default"
            if sessions:
                active = next((s for s in sessions if s.is_active), None)
                if active:
                    active_name = _safe_filename(active.name)

            uploads_root = self._workspace / "uploads" / active_name
            uploads_root.mkdir(parents=True, exist_ok=True)

            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            safe = _safe_filename(file.filename)
            target = uploads_root / f"{ts}-{safe}"

            # Stream-write with size cap. Same pattern as voice route
            # so a malicious client can't exhaust memory by claiming
            # a huge content-length.
            max_bytes = yaml_config.chat_attachments_max_bytes()
            written = 0
            try:
                with open(target, "wb") as out:
                    while True:
                        chunk = await file.read(64 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > max_bytes:
                            target.unlink(missing_ok=True)
                            raise HTTPException(
                                413, f"file exceeds {max_bytes} bytes",
                            )
                        out.write(chunk)
            except HTTPException:
                raise
            except OSError as exc:
                target.unlink(missing_ok=True)
                log.exception("attachment write failed")
                raise HTTPException(500, f"could not save upload: {exc}")

            if written == 0:
                target.unlink(missing_ok=True)
                raise HTTPException(400, "empty file upload")

            return JSONResponse({
                "path": str(target),
                "name": safe,
                "size": written,
                "mime": mime,
            })

        # ----- voice (STT + TTS) — phase 2 addon -----
        #
        # All three voice routes are auth-gated and report
        # availability through ``/voice/info``. The UI calls
        # ``/info`` once on load to decide whether to render the
        # mic button and hook up TTS playback; both individual
        # endpoints also 503 when voice is off so a stale UI
        # doesn't get a misleading 200.
        #
        # Audio cap: 25 MiB. Roughly 30 minutes of 192kbps Opus or
        # 4 minutes of 16-bit 16kHz WAV. Far above any reasonable
        # voice memo, well below the kind of payload that suggests
        # misuse.

        _VOICE_AUDIO_MAX_BYTES = 25 * 1024 * 1024
        _TTS_TEXT_MAX_BYTES = 16 * 1024

        @app.get(
            "/api/v1/chat/voice/info",
            dependencies=[Depends(_require_auth)],
        )
        async def get_voice_info() -> dict:
            """One-shot capability probe. Lets the UI render the
            mic button only when STT is actually wired and decide
            whether to autoplay TTS on assistant replies. Also
            ships the per-feature call-mode model override so the
            voice-call modal can apply it to /chat/voice without
            a second round-trip."""
            stt = stt_provider()
            tts = tts_provider()
            return {
                "enabled": voice_enabled(),
                "stt": {"provider": stt.name, "available": stt.name != "null"},
                "tts": {
                    "provider": tts.name,
                    "available": tts.name != "null",
                    "mime_type": tts.mime_type,
                },
                "call_mode": {
                    # Empty string = "use brain default" (matches the
                    # Voice tab's wire format for symmetry).
                    "model": yaml_config.voice_call_mode_model() or "",
                    "reasoning_level": (
                        yaml_config.voice_call_mode_reasoning_level() or ""
                    ),
                },
            }

        @app.post(
            "/api/v1/chat/voice",
            dependencies=[Depends(_require_auth)],
        )
        async def post_chat_voice(
            audio: UploadFile = File(...),
            # Optional per-turn model override — voice call mode
            # passes this when the user has set
            # ``voice.call_mode.model`` in config (or picked one in
            # the Voice tab). Empty string / missing both fall through
            # to "use brain default".
            model: str | None = Form(default=None),
            # Optional reasoning effort. Same source — voice call mode.
            # Empty/missing fall through to "no --effort flag".
            reasoning_level: str | None = Form(default=None),
        ) -> JSONResponse:
            """STT round-trip: receive audio → transcribe → send the
            transcription to the brain → return both the transcript
            and the brain's reply. Single round-trip so the UI doesn't
            have to chain two calls."""
            chat = _chat_or_503()
            stt = stt_provider()

            # Persist the upload to a temp file because the STT provider
            # accepts a Path (voxtype's pipeline shells out to ffmpeg
            # which wants a real filesystem path). NamedTemporaryFile
            # with delete=False so we control unlink order — the file
            # has to outlive the ``await`` chain inside transcribe().
            import os as _os
            import tempfile as _tempfile
            # Preserve the upload's extension hint so ffmpeg's auto-
            # detection picks the right demuxer. ``.bin`` is a safe
            # fallback when the browser didn't supply one.
            suffix = ".bin"
            if audio.filename and "." in audio.filename:
                ext = audio.filename.rsplit(".", 1)[1].lower()
                if ext.isalnum() and len(ext) <= 6:
                    suffix = f".{ext}"

            fd, tmp_path = _tempfile.mkstemp(suffix=suffix, prefix="vexis-voice-")
            written = 0
            try:
                # Stream the upload to disk in chunks so we don't buffer
                # the whole thing in memory before checking the size cap.
                while True:
                    chunk = await audio.read(64 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > _VOICE_AUDIO_MAX_BYTES:
                        raise HTTPException(
                            413,
                            f"audio exceeds {_VOICE_AUDIO_MAX_BYTES} bytes",
                        )
                    _os.write(fd, chunk)
                _os.close(fd)
                fd = -1

                if written == 0:
                    raise HTTPException(400, "empty audio upload")

                try:
                    transcript = await stt.transcribe(Path(tmp_path))
                except STTUnavailable as exc:
                    raise HTTPException(503, str(exc))
                except VoiceError as exc:
                    # Empty transcription (silence, noise) → 422 so the
                    # UI can surface "I didn't catch that, try again".
                    raise HTTPException(422, str(exc))
            finally:
                if fd != -1:
                    try:
                        _os.close(fd)
                    except OSError:
                        pass
                Path(tmp_path).unlink(missing_ok=True)

            if not transcript.strip():
                raise HTTPException(422, "empty transcription")

            # Empty-string model = unset; treat as None so the brain
            # uses its account default. Saves the frontend from having
            # to omit the form field when the user hasn't picked an
            # override. Same idea for reasoning_level.
            override = model.strip() if isinstance(model, str) and model.strip() else None
            effort = (
                reasoning_level.strip()
                if isinstance(reasoning_level, str) and reasoning_level.strip()
                else None
            )
            reply = await chat.send(
                transcript, model=override, reasoning_level=effort,
            )
            if reply is None:
                raise HTTPException(401, "message rejected")
            return JSONResponse({"transcript": transcript, "reply": reply})

        # ----- voice settings (dashboard surface) -----
        # Surfaces as the "Voice" tab. GET returns current config +
        # discovered Piper voice models so the dashboard doesn't have
        # to do filesystem walking client-side. POST accepts a partial
        # update (any subset of {enabled, stt.provider, tts.provider,
        # tts.voice_model_path, tts.binary}) and writes through the
        # same atomic+comment-preserving path the Models tab uses.

        @app.get(
            "/api/v1/voice",
            dependencies=[Depends(_require_auth)],
        )
        async def get_voice() -> dict:
            return await asyncio.to_thread(self._voice_payload)

        @app.post(
            "/api/v1/voice",
            dependencies=[Depends(_require_auth)],
        )
        async def post_voice(body: dict) -> dict:
            return await asyncio.to_thread(self._voice_set, body)

        @app.post(
            "/api/v1/chat/tts",
            dependencies=[Depends(_require_auth)],
        )
        async def post_chat_tts(body: dict) -> Response:
            """Synthesize ``text`` to audio bytes. The UI plays the
            response through an HTMLAudioElement after each assistant
            message when voice.enabled."""
            text = _validated_text(body, key="text")
            if len(text.encode("utf-8")) > _TTS_TEXT_MAX_BYTES:
                raise HTTPException(
                    413, f"text exceeds {_TTS_TEXT_MAX_BYTES} bytes",
                )
            tts = tts_provider()
            try:
                audio_bytes = await tts.synthesize(text)
            except TTSUnavailable as exc:
                raise HTTPException(503, str(exc))
            except VoiceError as exc:
                log.exception("TTS synthesis failed")
                raise HTTPException(500, str(exc))
            if not audio_bytes:
                # Whitespace-only after trim — nothing to play. Use
                # 204 so the UI doesn't try to attach an empty Blob
                # to <audio>.
                return Response(status_code=204)
            return Response(content=audio_bytes, media_type=tts.mime_type)

        # ----- frontend bootstrap -----

        index_html = self._config.web_dist / "index.html"
        assets_dir = self._config.web_dist / "assets"

        @app.get("/", response_class=FileResponse)
        async def get_index() -> FileResponse:
            if not index_html.is_file():
                return JSONResponse(
                    {
                        "error": "frontend not built",
                        "hint": (
                            "Run `cd web && npm install && npm run build` "
                            "or use `scripts/vexis-dashboard` which builds "
                            "automatically."
                        ),
                    },
                    status_code=503,
                )
            return FileResponse(index_html)

        if assets_dir.is_dir():
            app.mount(
                "/assets",
                StaticFiles(directory=str(assets_dir)),
                name="assets",
            )

        # Static asset directories that ship inside web/public/ — Vite
        # copies these verbatim into web/dist/ at build time. Each gets
        # its own mount so the SPA catchall below doesn't intercept
        # them and serve index.html.
        #   - /vad: Silero VAD onnx + audio worklet (voice call mode)
        #   - /ort: onnxruntime-web wasm runtime (loaded by VAD)
        # Add new directories here when they're added to web/public/.
        for static_subdir in ("vad", "ort"):
            d = self._config.web_dist / static_subdir
            if d.is_dir():
                app.mount(
                    f"/{static_subdir}",
                    StaticFiles(directory=str(d)),
                    name=static_subdir,
                )

        # Catch-all so client-side routing keeps working: any unmatched
        # GET (that isn't an API call) returns index.html. Necessary for
        # deep links once the frontend grows tabs.
        @app.get("/{rest:path}", response_class=FileResponse)
        async def spa_catchall(rest: str) -> FileResponse:
            if rest.startswith("api/") or rest.startswith("assets/"):
                raise HTTPException(404, "not found")
            if not index_html.is_file():
                raise HTTPException(503, "frontend not built")
            return FileResponse(index_html)

        return app

    async def _await_listening(self) -> None:
        """Block until uvicorn has finished its bind+listen sequence."""
        if self._server is None:
            return
        deadline = asyncio.get_running_loop().time() + 5.0
        while asyncio.get_running_loop().time() < deadline:
            if self._server.started:
                return
            await asyncio.sleep(0.05)
        raise RuntimeError("dashboard uvicorn server failed to start within 5s")

    # ----- payload builders ---------------------------------------------

    def _memory_payload(self) -> dict:
        store = MemoryStore(memories_dir_fn(self._workspace))
        memory_path = memories_dir_fn(self._workspace) / "MEMORY.md"
        user_path = memories_dir_fn(self._workspace) / "USER.md"

        def _block(target: str, path: Path) -> dict:
            entries = store.list_entries(target)  # type: ignore[arg-type]
            joined = "\n§\n".join(entries) if entries else ""
            current = len(joined)
            limit = store._limit(target)  # type: ignore[arg-type]
            percent = (
                max(1, min(100, int((current / limit) * 100)))
                if entries and limit > 0
                else 0
            )
            mtime = None
            try:
                mtime = datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc
                ).isoformat()
            except FileNotFoundError:
                pass
            return {
                "entries": entries,
                "current": current,
                "limit": limit,
                "percent": percent,
                "mtime": mtime,
                "path": str(path),
            }

        return {
            "memory": _block("memory", memory_path),
            "user": _block("user", user_path),
        }

    def _skills_payload(self) -> dict:
        root = skills_dir(self._workspace)
        pinned = set(PinStore(root).list())
        usage = UsageStore(root).load()

        # Build a name→relative-category lookup so the dashboard can show
        # which folder a skill lives in. discover_skills() throws away the
        # category since the brain index doesn't need it; we want it.
        category_for: dict[str, str] = {}
        path_for: dict[str, str] = {}
        for skill_dir in iter_skill_dirs(root):
            try:
                content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            except OSError:
                continue
            meta = parse_skill_md(content)
            if meta is None:
                continue
            try:
                rel = skill_dir.relative_to(root).parts
            except ValueError:
                rel = ()
            category_for[meta.name] = "/".join(rel[:-1]) if len(rel) > 1 else ""
            path_for[meta.name] = str(skill_dir)

        active: list[dict] = []
        for meta in discover_skills(root):
            rec = usage.get(meta.name) or {}
            active.append(
                {
                    "name": meta.name,
                    "description": meta.description,
                    "category": category_for.get(meta.name, ""),
                    "state": rec.get("state") or "active",
                    "view_count": int(rec.get("view_count") or 0),
                    "use_count": int(rec.get("use_count") or 0),
                    "patch_count": int(rec.get("patch_count") or 0),
                    "last_used_at": rec.get("last_used_at"),
                    "created_at": rec.get("created_at"),
                    "pinned": meta.name in pinned,
                    "path": path_for.get(meta.name, ""),
                }
            )

        archived: list[dict] = []
        for name in archived_skill_names(root):
            rec = usage.get(name) or {}
            archived.append(
                {
                    "name": name,
                    "archived_at": rec.get("archived_at"),
                    "description": _archived_description(root, name),
                }
            )

        return {"active": active, "archived": archived}

    def _skill_body(self, name: str) -> dict | None:
        root = skills_dir(self._workspace)
        for skill_dir in iter_skill_dirs(root):
            try:
                content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            except OSError:
                continue
            meta = parse_skill_md(content)
            if meta is None or meta.name != name:
                continue
            try:
                rel = skill_dir.relative_to(root).parts
            except ValueError:
                rel = ()
            return {
                "name": meta.name,
                "description": meta.description,
                "category": "/".join(rel[:-1]) if len(rel) > 1 else "",
                "body": meta.body,
                "path": str(skill_dir / "SKILL.md"),
                "frontmatter": meta.raw_frontmatter,
            }
        return None

    def _curator_payload(self) -> dict:
        state = load_curator_state()
        last = state.get("last_run_at")
        last_dt = _parse_iso(last)
        next_eligible: str | None = None
        if last_dt is not None:
            next_eligible = (
                last_dt
                + _hours(curator_interval_hours())
            ).isoformat()

        archived = archived_skill_names(skills_dir(self._workspace))
        runs = list(_iter_curator_runs(curator_logs_dir()))[
            :CURATOR_RUN_HISTORY_LIMIT
        ]
        return {
            "enabled": curator_enabled(),
            "paused": bool(state.get("paused")),
            "running": self._curator.is_running() if self._curator else False,
            "last_run_at": last,
            "last_run_summary": state.get("last_run_summary"),
            "next_eligible_at": next_eligible,
            "interval_hours": curator_interval_hours(),
            "stale_after_days": curator_stale_after_days(),
            "archive_after_days": curator_archive_after_days(),
            "archived_count": len(archived),
            "runs": runs,
        }

    def _curator_run_payload(self, folder: str) -> dict | None:
        # Folder names look like 2026-05-01T180000Z. Reject anything with
        # path separators so a malicious caller can't escape the logs dir.
        if "/" in folder or ".." in folder:
            return None
        run_dir = curator_logs_dir() / folder
        if not run_dir.is_dir():
            return None
        report_md = ""
        run_json: dict | None = None
        try:
            report_md = (run_dir / "REPORT.md").read_text(encoding="utf-8")
        except FileNotFoundError:
            report_md = ""
        try:
            run_json = json.loads(
                (run_dir / "run.json").read_text(encoding="utf-8")
            )
        except (FileNotFoundError, json.JSONDecodeError):
            run_json = None
        return {
            "folder": folder,
            "report_md": report_md,
            "run_json": run_json,
        }

    async def _status_payload(self) -> dict:
        log_lines = _tail_log(state_dir() / "vexis.log", STATUS_LOG_LINES)
        bg = await self._background_tasks.status_summary()
        fg = await self._running_tasks.snapshot()
        sessions = self._sessions.list()
        active_session = self._sessions.active_name()
        return {
            "started_at": self._started_at.isoformat(),
            "uptime_seconds": (
                datetime.now(timezone.utc) - self._started_at
            ).total_seconds(),
            "session_count": len(sessions),
            "active_session": active_session,
            "sessions": [
                {
                    "name": s.name,
                    "uuid": s.uuid,
                    "initialized": s.initialized,
                    "created_at": s.created_at.isoformat(),
                    "is_active": s.is_active,
                }
                for s in sessions
            ],
            "foreground_chats": fg,
            "background_tasks": bg,
            "log_lines": log_lines,
        }

    def _learning_payload(self) -> dict:
        """Combined Learning-tab payload.

        Pulls the lion's share from ``LearningController.dashboard_payload()``
        (which knows the on-disk shape), then prepends the archive-curator
        row sourced from the same internals ``_curator_payload`` uses so
        the user sees all three curator rows in one place.

        When the learning controller is None (curator disabled at boot),
        return a stub payload with empty arrays and an "off" archive
        row — the frontend renders a graceful message rather than 500.
        """
        if self._learning is None:
            return {
                "curators": [self._archive_curator_row()],
                "recent_activity": [],
                "shadow_entries": [],
                "distribution": {"window_ticks": 0, "by_class": {}, "by_tier": {}, "a2_watch": False},
                "rates": {
                    "window_ticks_scanned": 0,
                    "dedup_skipped": 0,
                    "coherence_flagged": 0,
                    "coherence_near_miss": 0,
                    "coherence_by_reason": {},
                },
                "user_candidates": {"pending": [], "promoted_count": 0},
                "coherence_pending_review": [],
                "curator_skills": {"live": [], "staged": []},
                "models": {},
                "learning_disabled": True,
            }
        payload = self._learning.dashboard_payload()
        # Prepend the archive-curator row — the learning controller
        # doesn't know about the archive curator, and shouldn't.
        archive_row = self._archive_curator_row()
        payload["curators"] = [archive_row, *payload["curators"]]
        payload["learning_disabled"] = False
        return payload

    def _archive_curator_row(self) -> dict:
        """Build the archive-curator row for the Learning-tab Curators
        panel. Sources the same data ``_curator_payload`` exposes but
        narrows it to the row shape (the Learning tab's row contract).
        """
        state = load_curator_state()
        last = state.get("last_run_at")
        last_dt = _parse_iso(last)
        next_eligible: str | None = None
        if last_dt is not None:
            next_eligible = (last_dt + _hours(curator_interval_hours())).isoformat()
        return {
            "name": "archive",
            "nested_under": None,
            "enabled": curator_enabled(),
            "paused": bool(state.get("paused")),
            "running": (
                self._curator.is_running() if self._curator is not None else False
            ),
            "last_run_at": last,
            "next_eligible_at": next_eligible,
            "summary": state.get("last_run_summary") or "no runs yet",
            "interval_label": f"{curator_interval_hours()}h",
        }

    def _tailscale_payload(self) -> dict:
        """Best-effort merge of the four ``core.tailscale`` calls.

        Each sub-call returns its own typed error string; we surface
        the first one we hit on the top-level ``error`` field so the
        frontend can render a single banner. The remaining sections
        still populate from whatever succeeded — a tailscaled hiccup
        on ``serve status`` shouldn't blank out the peer table when
        ``status --json`` is fine.
        """
        serve = tailscale_mod.get_serve_status()
        funnel = tailscale_mod.get_funnel_status()
        node = tailscale_mod.get_node_info()
        peers = tailscale_mod.get_peers()
        first_error = (
            node.error or serve.error or funnel.error or peers.error
        )
        return {
            "node": (
                {
                    "hostname": node.node.hostname,
                    "ip": node.node.ip,
                    "online": node.node.online,
                }
                if node.node is not None
                else None
            ),
            "serves": [
                {
                    "port": s.port,
                    "mount": s.mount,
                    "target": s.target,
                    "tls": s.tls,
                    "funnel": s.funnel,
                }
                for s in serve.serves
            ],
            "funnels": [
                {
                    "port": f.port,
                    "mount": f.mount,
                    "target": f.target,
                    "tls": f.tls,
                }
                for f in funnel.funnels
            ],
            "peers": [
                {
                    "hostname": p.hostname,
                    "ip": p.ip,
                    "online": p.online,
                    "last_seen": p.last_seen,
                    "os": p.os,
                }
                for p in peers.peers
            ],
            "error": first_error,
        }

    # ----- /goal payload + mutation helpers ---------------------------

    def _goal_record_dict(
        self, session_uuid: str, state: "GoalState"
    ) -> dict:
        """Serialize a (session_uuid, GoalState) pair to the JSON
        shape the dashboard frontend expects. Timestamps render as
        ISO-8601 strings (or ``null``)."""

        def _iso(dt):
            return dt.astimezone(timezone.utc).isoformat() if dt else None

        return {
            "session_uuid": session_uuid,
            "goal": state.goal,
            "status": state.status,
            "turns_used": state.turns_used,
            "max_turns": state.max_turns,
            "created_at": _iso(state.created_at),
            "last_turn_at": _iso(state.last_turn_at),
            "last_verdict": state.last_verdict,
            "last_reason": state.last_reason,
            "paused_reason": state.paused_reason,
        }

    def _goals_store(self) -> "GoalStateStore":
        from core.goal_state import GoalStateStore
        from core.paths import goals_path
        return GoalStateStore(goals_path())

    def _active_session_uuid(self) -> str | None:
        """Read the active session UUID via the shared SessionStore.

        ``self._sessions`` may be None on bare test fixtures that
        construct the dashboard without the session wiring; guard so
        the dashboard endpoint stays robust in that case (returns
        ``no active goal`` rather than crashing).
        """
        if self._sessions is None:
            return None
        try:
            return self._sessions.get()
        except Exception:
            log.debug("goals: SessionStore.get() raised", exc_info=True)
            return None

    def _goals_payload(self) -> dict:
        """``{active, history}`` for the dashboard.

        ``active`` is the goal record for the current session UUID
        (only when its status is ``"active"`` or ``"paused"`` — done
        and cleared records appear in history instead). ``history``
        is the most recent 20 non-active records sorted by
        ``last_turn_at`` desc.
        """
        store = self._goals_store()
        active_record: dict | None = None
        sid = self._active_session_uuid()
        if sid:
            try:
                state = store.load(sid)
            except Exception:
                log.debug("goals: load failed for %s", sid, exc_info=True)
                state = None
            if state is not None and state.status in ("active", "paused"):
                active_record = self._goal_record_dict(sid, state)
        try:
            history_pairs = store.list_recent_inactive(limit=20)
        except Exception:
            log.debug("goals: list_recent_inactive failed", exc_info=True)
            history_pairs = []
        return {
            "active": active_record,
            "history": [
                self._goal_record_dict(sid, state)
                for sid, state in history_pairs
            ],
        }

    def _models_payload(self) -> dict:
        """Day 3 of model UX — read-only resolution table for the
        dashboard's Models tab.

        Delegates to ``core.model_validator.build_resolution_table``
        which is the single source of truth shared with the slash
        command's ``/model status`` text rendering. The contract
        test in ``tests/test_models_api.py`` pins that both surfaces
        return byte-identical per-subsystem resolution data.

        Day 4 additions:
          - ``available_models`` per brain (cached 5 minutes via
            ``core.model_discovery``) so the dashboard's dropdown
            doesn't need a separate request per row. Validator's
            rule 6 also runs against the same set.
          - ``has_comments`` flag so the dashboard can pre-fetch
            the comment-preservation modal trigger without a
            separate round-trip.
          - ``model_ux_enabled`` flag so the dashboard can render
            the disabled-flag banner if appropriate.

        Graceful degradation: if config parsing fails (corrupt
        ``~/.vexis/config.yaml``), the validator still runs against
        the empty fallback dict ``yaml_config._read_raw`` returns
        and the dashboard surfaces a clean default-state table
        rather than 500-ing. Validator findings (which include the
        config-parse failure context if any) carry the diagnostic.
        """
        from core.model_discovery import (
            discovery_for_validator,
            discovery_grouped_for_validator,
        )
        from core.model_validator import build_resolution_table
        from core.yaml_config import (
            VALID_BRAIN_KINDS,
            _read_raw,
            brain_kind,
            model_ux_enabled,
        )
        from core.yaml_config_writer import has_comments
        try:
            cfg = _read_raw()
            kind = brain_kind()
            available = discovery_for_validator(VALID_BRAIN_KINDS)
            # Defensive getattr — Day 5 added _running_brain_kind
            # via the new constructor parameter. Existing test
            # fixtures that bypass __init__ via __new__ don't set
            # it; default to None so the canary stays silent for
            # them rather than raising AttributeError.
            running_kind = getattr(self, "_running_brain_kind", None)
            table = build_resolution_table(
                cfg, kind,
                available_models_per_brain=available,
                running_brain_kind=running_kind,
            )
            # Day 4 additions on top of the Day 3 shape.
            table["available_models"] = {
                k: sorted(v) for k, v in available.items()
            }
            # Day 1 of model picker UX — provider-grouped sibling of
            # ``available_models``. Pre-grouped per brain so the
            # Day 2 dashboard ``<optgroup>`` dropdown can render
            # without parsing on the client. Sourced from the same
            # 5-min cache as ``available_models`` (no additional
            # subprocess calls). Existing flat ``available_models``
            # field is retained for backwards compatibility — the
            # current dashboard dropdown still consumes it.
            table["available_models_by_provider"] = (
                discovery_grouped_for_validator(VALID_BRAIN_KINDS)
            )
            table["has_comments"] = self._config_has_comments(has_comments)
            table["model_ux_enabled"] = model_ux_enabled()
            return table
        except Exception:
            log.exception("models payload build failed; returning empty fallback")
            # Defensive: return an empty-but-shaped table so the
            # dashboard renders a clean error state instead of 500.
            return {
                "brain_kind": "claude-code",
                "subsystems": [],
                "tier_overrides": {},
                "brain_inventory": [],
                "global_findings": [{
                    "severity": "error",
                    "subsystem": None,
                    "problem": "models payload build failed; see daemon log",
                    "suggested_fix": "check daemon log for stack trace",
                }],
                "available_models": {},
                "available_models_by_provider": {},
                "has_comments": False,
                "model_ux_enabled": False,
            }

    @staticmethod
    def _config_has_comments(has_comments_fn) -> bool:
        """Read ~/.vexis/config.yaml and report whether it has
        any YAML comments. Used by the dashboard's comment-
        preservation modal pre-trigger. Self-managing: after the
        first slash/dashboard mutation comments are gone, so
        subsequent calls return False and the modal stays out of
        the way."""
        from core.yaml_config import _config_path
        path = _config_path()
        try:
            return has_comments_fn(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return False

    # ─── Day 4 mutation + discovery helpers ─────────────────────────

    def _models_set(self, payload: dict) -> dict:
        """POST /api/v1/models/set — set a per-subsystem
        assignment. Body: ``{"subsystem": str, "value": str}``.

        Same gates as the slash command:
          - flag-gated behind ``model_ux_enabled()``
          - validator runs pre-write; refuses on error-severity
          - comment-presence-gated backup before the atomic
            rewrite
          - returns the new resolution row + (optional) backup
            confirmation in the response body so the dashboard
            can render the toast inline

        Failure modes:
          - 403 if model_ux disabled
          - 400 unknown subsystem
          - 400 validator-error (response.detail carries the
            suggested_fix copy verbatim)
          - 500 only on unexpected internals (atomic-write IO
            failure, etc.)
        """
        from core.model_discovery import discovery_for_validator
        from core.model_validator import validate_models_config
        from core.yaml_config import (
            DEFAULT_SUBSYSTEM_TIERS,
            VALID_BRAIN_KINDS,
            _read_raw,
            brain_kind,
            model_for_tier_from_config,
            model_ux_enabled,
            subsystem_tier_from_config,
        )
        from core.yaml_config_writer import (
            atomic_write_yaml,
            backup_if_commented,
        )
        from core.paths import vexis_dir
        from fastapi import HTTPException

        if not model_ux_enabled():
            raise HTTPException(
                status_code=403,
                detail="model UX is disabled (set model_ux.enabled: true)",
            )
        subsystem = payload.get("subsystem")
        value = payload.get("value")
        if not isinstance(subsystem, str) or not isinstance(value, str):
            raise HTTPException(
                status_code=400,
                detail="payload requires {subsystem: str, value: str}",
            )
        if subsystem not in DEFAULT_SUBSYSTEM_TIERS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"unknown subsystem '{subsystem}'. Known: "
                    f"{', '.join(sorted(DEFAULT_SUBSYSTEM_TIERS))}"
                ),
            )

        cfg_path = vexis_dir() / "config.yaml"
        current = _read_raw()
        proposed = self._proposed_set_subsystem(current, subsystem, value)

        available = discovery_for_validator(VALID_BRAIN_KINDS)
        findings = validate_models_config(
            proposed, brain_kind(),
            available_models_per_brain=available,
        )
        errors = [f for f in findings if f.severity == "error"]
        if errors:
            problems = "\n".join(
                f"[{f.subsystem or '<global>'}] {f.problem} "
                f"-- fix: {f.suggested_fix}"
                for f in errors
            )
            raise HTTPException(status_code=400, detail=problems)

        backup_path = (
            backup_if_commented(cfg_path) if cfg_path.exists() else None
        )
        atomic_write_yaml(cfg_path, proposed)

        resolved_tier = subsystem_tier_from_config(
            proposed.get("models"), subsystem,
        )
        resolved_id = model_for_tier_from_config(
            proposed.get("models"), brain_kind(), resolved_tier,
        )
        return {
            "ok": True,
            "subsystem": subsystem,
            "value": value,
            "resolved_tier": resolved_tier,
            "resolved_model_id": resolved_id,
            "backup_path": str(backup_path) if backup_path else None,
        }

    def _models_reset(self, payload: dict) -> dict:
        """POST /api/v1/models/reset — reset all subsystem
        assignments (legacy + new schema), or one subsystem if
        ``payload["subsystem"]`` is provided. Preserves
        models.tiers and models.brain.

        Same gates as the slash command's reset path."""
        from core.yaml_config import (
            DEFAULT_SUBSYSTEM_TIERS,
            _read_raw,
            model_ux_enabled,
        )
        from core.yaml_config_writer import (
            atomic_write_yaml,
            backup_if_commented,
        )
        from core.paths import vexis_dir
        from fastapi import HTTPException

        if not model_ux_enabled():
            raise HTTPException(
                status_code=403,
                detail="model UX is disabled (set model_ux.enabled: true)",
            )

        target = payload.get("subsystem")
        if target is not None and not isinstance(target, str):
            raise HTTPException(
                status_code=400,
                detail="subsystem must be a string or omitted",
            )
        if target is not None and target not in DEFAULT_SUBSYSTEM_TIERS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"unknown subsystem '{target}'. Known: "
                    f"{', '.join(sorted(DEFAULT_SUBSYSTEM_TIERS))}"
                ),
            )

        cfg_path = vexis_dir() / "config.yaml"
        current = _read_raw()
        models = dict(current.get("models") or {})

        if target is None:
            # Drop every subsystem assignment (legacy + new schema)
            # but leave models.tiers and models.brain alone.
            models.pop("subsystems", None)
            for sub_name in list(models):
                if sub_name in DEFAULT_SUBSYSTEM_TIERS:
                    models.pop(sub_name)
            scope = "all subsystems"
        else:
            models.pop(target, None)
            subs_block = models.get("subsystems")
            if isinstance(subs_block, dict):
                subs_block.pop(target, None)
                if not subs_block:
                    models.pop("subsystems", None)
            scope = target

        new_cfg = {**current, "models": models}
        if not models:
            new_cfg.pop("models", None)

        backup_path = (
            backup_if_commented(cfg_path) if cfg_path.exists() else None
        )
        atomic_write_yaml(cfg_path, new_cfg)
        return {
            "ok": True,
            "scope": scope,
            "backup_path": str(backup_path) if backup_path else None,
        }

    def _models_set_brain(self, payload: dict) -> dict:
        """POST /api/v1/models/brain — set ``brain.kind``. Body:
        ``{"kind": str}``.

        Refuses unknown kind as policy (typo guard) — matches
        slash behaviour. Validator's rule 1 only warns; refusal
        here is policy-driven, not severity-driven.

        Returns ``{ok: true, kind, restart_required: true,
        warnings: [...preview-mode validator findings...]}``
        so the dashboard's modal can show what'll happen at
        next-start AND surface the "switch brain → restart
        required" reminder."""
        from core.model_discovery import discovery_for_validator
        from core.model_validator import validate_models_config
        from core.yaml_config import (
            VALID_BRAIN_KINDS,
            _read_raw,
            model_ux_enabled,
        )
        from core.yaml_config_writer import (
            atomic_write_yaml,
            backup_if_commented,
        )
        from core.paths import vexis_dir
        from fastapi import HTTPException

        if not model_ux_enabled():
            raise HTTPException(
                status_code=403,
                detail="model UX is disabled (set model_ux.enabled: true)",
            )
        kind = payload.get("kind")
        if not isinstance(kind, str) or kind not in VALID_BRAIN_KINDS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"invalid brain.kind '{kind}'. Valid: "
                    f"{', '.join(sorted(VALID_BRAIN_KINDS))}"
                ),
            )

        cfg_path = vexis_dir() / "config.yaml"
        current = _read_raw()
        brain_block = dict(current.get("brain") or {})
        brain_block["kind"] = kind
        new_cfg = {**current, "brain": brain_block}

        # Preview-mode validation against the proposed brain. If
        # rule 4 errors fire, surface them as warnings in the
        # response body — the dashboard's modal renders these
        # before the switch lands so the user knows the next
        # restart will need tier-key migration. Don't refuse;
        # the user opted into the brain change knowing they'll
        # fix the rest.
        available = discovery_for_validator(VALID_BRAIN_KINDS)
        preview = validate_models_config(
            new_cfg, kind, available_models_per_brain=available,
        )
        warnings = [
            {
                "severity": f.severity,
                "subsystem": f.subsystem,
                "problem": f.problem,
                "suggested_fix": f.suggested_fix,
            }
            for f in preview
            if f.severity in ("error", "warning")
        ]

        backup_path = (
            backup_if_commented(cfg_path) if cfg_path.exists() else None
        )
        atomic_write_yaml(cfg_path, new_cfg)
        return {
            "ok": True,
            "kind": kind,
            "restart_required": True,
            "warnings": warnings,
            "backup_path": str(backup_path) if backup_path else None,
        }

    def _models_discovery_refresh(self) -> dict:
        """POST /api/v1/models/discovery/refresh — bust the
        in-process discovery cache for both brains and re-fetch
        live. opencode runs ``opencode models --refresh`` so
        models.dev's own cache also refreshes; claude-code hits
        the Anthropic /v1/models endpoint with the user's OAuth
        bearer / ANTHROPIC_API_KEY (falls back to the hardcoded
        list on any failure). Returns the fresh per-brain model
        lists so the dashboard can repopulate the dropdowns inline.

        Not flag-gated — discovery is read-only and useful even
        for the ``model_ux.enabled: false`` case (e.g. if a user
        wants to inspect what's available before flipping the
        flag)."""
        from core.model_discovery import (
            refresh_claude_code_models,
            refresh_opencode_models,
        )
        # Both brains have meaningful refresh paths post-2026-05-07.
        # claude-code's was a no-op when its discovery was a
        # hardcoded constant; live /v1/models discovery made the
        # refresh meaningful (picks up newly-released Anthropic
        # models without a vexis PR).
        claude_code_models = refresh_claude_code_models()
        opencode_models = refresh_opencode_models()
        return {
            "ok": True,
            "available_models": {
                "claude-code": sorted(claude_code_models),
                "opencode": sorted(opencode_models),
                "null": [],
            },
        }

    # ──────────────────────────────────────────────────────────
    # Voice settings (dashboard surface — phase 3a)
    # ──────────────────────────────────────────────────────────

    @staticmethod
    def _voice_call_mode_available_models_static(active_brain: str) -> list[dict]:
        """Build the available-models list for the Voice tab's
        call-mode picker. Pulled out so the test surface is small —
        delegating to ``core.model_discovery`` means new models
        from a future Claude release surface automatically without
        touching this file.

        Per-entry shape (uniform across brains so the UI doesn't
        fork by brain kind):

            {
              "id": str,                       # model id (canonical form)
              "display_name": str | None,      # friendly label, e.g.
                                               #   "Claude Opus 4.7"
              "reasoning_levels": list[str],   # dynamic per-model
              "max_input_tokens": int | None,  # context window
              "max_tokens": int | None,        # max output tokens
              "provider": str | None,          # "anthropic" / "openrouter"
                                               #   / "github-copilot" / etc
              "free": bool,                    # opencode reports cost.input
                                               #   == cost.output == 0 → True
              "cost_input_per_million":  float | None,  # opencode only;
              "cost_output_per_million": float | None,  # null elsewhere
            }

        Every value comes from the brain's native discovery
        (Anthropic ``/v1/models`` for claude-code; ``opencode models
        --verbose`` for opencode). Nothing is hardcoded — adding a
        new model to either backend surfaces here automatically.

        Bare-alias filter: Anthropic's ``/v1/models`` returns both the
        full ID (e.g. ``claude-haiku-4-5-20251001``) AND the lowercase
        family alias (``haiku``, ``sonnet``, ``opus``). The aliases
        are valid CLI inputs but confusing in a picker (no reasoning
        metadata, look like UI bugs). Hidden by requiring claude-code
        IDs to start with ``claude-`` and opencode IDs to contain
        ``/``.
        """
        from core.model_discovery import (
            discover_claude_code_capabilities,
            discover_claude_code_models,
            discover_opencode_capabilities,
            discover_opencode_models,
        )

        def _from_caps(
            mid: str, caps: dict, *, default_provider: str | None,
        ) -> dict:
            """Project a capability entry into the picker wire format.

            ``default_provider`` is what we fall back to when the
            entry didn't ship a ``provider`` field — claude-code
            doesn't have per-model provider info because everything
            is anthropic, so we set it to ``"anthropic"`` by default.
            """
            entry = caps.get(mid) or {}
            return {
                "id": mid,
                "display_name": entry.get("display_name"),
                "reasoning_levels": entry.get("reasoning_levels", []),
                "max_input_tokens": entry.get("max_input_tokens"),
                "max_tokens": entry.get("max_tokens"),
                "provider": entry.get("provider") or default_provider,
                "free": bool(entry.get("free", False)),
                "cost_input_per_million": entry.get("cost_input_per_million"),
                "cost_output_per_million": entry.get("cost_output_per_million"),
            }

        if active_brain == "claude-code":
            ids = sorted(
                m for m in discover_claude_code_models()
                if m.startswith("claude-")
            )
            caps = discover_claude_code_capabilities()
            return [
                _from_caps(mid, caps, default_provider="anthropic")
                for mid in ids
            ]
        if active_brain == "opencode":
            ids = sorted(
                m for m in discover_opencode_models()
                if "/" in m
            )
            caps = discover_opencode_capabilities()
            return [
                _from_caps(mid, caps, default_provider=None)
                for mid in ids
            ]
        # null brain — no real model list to surface.
        return []

    def _voice_payload(self) -> dict:
        """Snapshot for the Voice tab. Combines the current ``voice.*``
        config with a filesystem walk for installed Piper models so
        the dashboard can render a full picker without further round-
        trips."""
        from core.voice.discovery import find_piper_voices
        from core.yaml_config import (
            brain_kind,
            voice_call_mode_model,
            voice_call_mode_reasoning_level,
            voice_enabled,
            voice_stt_provider,
            voice_tts_binary,
            voice_tts_provider,
            voice_tts_voice_model_path,
        )
        from core.model_discovery import (
            discover_claude_code_capabilities,
            discover_claude_code_models,
            discover_opencode_models,
        )
        # Include the parent dir of any currently-configured model in
        # the search paths so non-standard install locations still
        # surface in the picker.
        configured = voice_tts_voice_model_path()
        configured_resolved: str | None = None
        extra_paths = []
        if configured:
            try:
                resolved = Path(configured).expanduser()
                configured_resolved = str(resolved)
                if resolved.parent.is_dir():
                    extra_paths.append(resolved.parent)
            except (OSError, ValueError):
                pass
        voices = find_piper_voices(extra_paths=extra_paths)
        binary_raw = voice_tts_binary()
        binary_resolved: str | None = None
        if binary_raw:
            try:
                binary_resolved = str(Path(binary_raw).expanduser())
            except (OSError, ValueError):
                binary_resolved = binary_raw
        return {
            "enabled": voice_enabled(),
            "stt": {
                "provider": voice_stt_provider(),
                # Hardcoded today; once we add more STT providers,
                # this list expands. The UI uses it to populate the
                # dropdown.
                "available_providers": ["voxtype", "null"],
            },
            "tts": {
                "provider": voice_tts_provider(),
                "available_providers": ["piper", "null"],
                # Resolved (tilde-expanded) so the dashboard's voice
                # radio button can match against ``available_voices``
                # entries (which always carry resolved absolute paths
                # from the filesystem walk).
                "voice_model_path": configured_resolved,
                "binary": binary_resolved,
            },
            "available_voices": [
                {
                    "path": v.path,
                    "name": v.name,
                    "language": v.language,
                    "size": v.size,
                    "has_config": v.has_config,
                }
                for v in voices
            ],
            # Per-turn model override for voice call mode. Empty
            # string sentinel ("") in the wire format = "use brain
            # default" so the UI radio list can stay simple
            # (single source of truth, no separate "use default"
            # boolean).
            "call_mode": {
                "model": voice_call_mode_model() or "",
                # Empty string = "no --effort flag, model default";
                # symmetric with model field's empty-string sentinel.
                "reasoning_level": voice_call_mode_reasoning_level() or "",
                # Available models surfaced for the picker. Same
                # discovery the Models tab uses; cached 5 min
                # in core.model_discovery so this poll is cheap.
                # Per-model ``reasoning_levels`` come from
                # capability discovery (Anthropic /v1/models for
                # claude-code; opencode doesn't expose these so
                # we ship an empty list).
                "available_models": self._voice_call_mode_available_models_static(
                    brain_kind()
                ),
            },
        }

    def _voice_set(self, payload: dict) -> dict:
        """Partial update of the ``voice.*`` config keys. Accepts any
        subset of:

            {
              "enabled": bool,
              "stt": {"provider": str},
              "tts": {
                "provider": str,
                "voice_model_path": str | null,
                "binary": str | null,
              },
            }

        Writes through the same atomic + comment-preserving path the
        Models tab uses. Returns the post-write payload so the UI can
        pick up resolved values without a follow-up GET.
        """
        from core.yaml_config import _read_raw
        from core.yaml_config_writer import (
            atomic_write_yaml,
            backup_if_commented,
        )
        from core.paths import vexis_dir

        if not isinstance(payload, dict):
            raise HTTPException(400, "payload must be an object")

        current = _read_raw()
        # Defensive copy + ensure the section exists before merging.
        proposed = dict(current)
        voice_section = dict(proposed.get("voice") or {})

        if "enabled" in payload:
            if not isinstance(payload["enabled"], bool):
                raise HTTPException(400, "enabled must be bool")
            voice_section["enabled"] = payload["enabled"]

        if "stt" in payload:
            if not isinstance(payload["stt"], dict):
                raise HTTPException(400, "stt must be an object")
            stt = dict(voice_section.get("stt") or {})
            if "provider" in payload["stt"]:
                v = payload["stt"]["provider"]
                if not isinstance(v, str) or not v.strip():
                    raise HTTPException(400, "stt.provider must be a non-empty string")
                stt["provider"] = v.strip()
            voice_section["stt"] = stt

        if "tts" in payload:
            if not isinstance(payload["tts"], dict):
                raise HTTPException(400, "tts must be an object")
            tts = dict(voice_section.get("tts") or {})
            for key in ("provider", "voice_model_path", "binary"):
                if key not in payload["tts"]:
                    continue
                v = payload["tts"][key]
                if v is None or (isinstance(v, str) and not v.strip()):
                    # Empty/null means "unset this knob". Drop the key
                    # from config so the helper falls back to its
                    # default. Keeps the YAML uncluttered.
                    tts.pop(key, None)
                    continue
                if not isinstance(v, str):
                    raise HTTPException(400, f"tts.{key} must be a string or null")
                tts[key] = v.strip()
            voice_section["tts"] = tts

        if "call_mode" in payload:
            if not isinstance(payload["call_mode"], dict):
                raise HTTPException(400, "call_mode must be an object")
            call_mode = dict(voice_section.get("call_mode") or {})
            # Both knobs use the same null/empty/``default`` reset
            # semantics. Pulled out so model and reasoning_level
            # share the validation.
            for key in ("model", "reasoning_level"):
                if key not in payload["call_mode"]:
                    continue
                v = payload["call_mode"][key]
                if (
                    v is None
                    or (isinstance(v, str) and (
                        not v.strip() or v.strip().lower() == "default"
                    ))
                ):
                    call_mode.pop(key, None)
                elif isinstance(v, str):
                    call_mode[key] = v.strip()
                else:
                    raise HTTPException(
                        400,
                        f"call_mode.{key} must be a string or null",
                    )
            # Reasoning is meaningful only WITH a model — if the user
            # cleared model but kept reasoning_level, drop the orphan
            # so we don't pass --effort to whatever account-default
            # model happens to be (which might not support reasoning).
            if "model" not in call_mode and "reasoning_level" in call_mode:
                call_mode.pop("reasoning_level")
            # If the section ended up empty, drop it entirely so the
            # YAML doesn't sprout dangling empty objects.
            if call_mode:
                voice_section["call_mode"] = call_mode
            else:
                voice_section.pop("call_mode", None)

        proposed["voice"] = voice_section

        cfg_path = vexis_dir() / "config.yaml"
        backup_path = (
            backup_if_commented(cfg_path) if cfg_path.exists() else None
        )
        atomic_write_yaml(cfg_path, proposed)

        result = self._voice_payload()
        result["ok"] = True
        result["backup_path"] = str(backup_path) if backup_path else None
        return result

    @staticmethod
    def _proposed_set_subsystem(
        current: dict, subsystem: str, value: str,
    ) -> dict:
        """Build the proposed config for a per-subsystem set. Pure
        function; the writer never sees a partial edit. Mirrors
        ``transports.telegram.TelegramTransport._proposed_set_subsystem``
        — both surfaces produce the same shape so the validator
        sees identical inputs."""
        models = dict(current.get("models") or {})
        subs = dict(models.get("subsystems") or {})
        subs[subsystem] = value
        models["subsystems"] = subs
        return {**current, "models": models}

    def _build_goal_manager(self, session_uuid: str):
        """Construct a GoalManager bound to the live store.

        Mirrors the helper the Telegram transport uses
        (``transports/telegram.py:_build_goal_manager``) but here on
        the dashboard's side. Lazy-imports so bare test fixtures
        without the goal modules loaded still work.
        """
        from core.goal_manager import GoalManager
        from core.yaml_config import goals_max_turns
        return GoalManager(
            session_uuid=session_uuid,
            workspace=self._workspace,
            store=self._goals_store(),
            default_max_turns=goals_max_turns(),
        )

    async def _drop_dashboard_goal_continuations(self) -> None:
        """Drop any pending ``goal_continuation`` messages from every
        chat's queue. Called by the dashboard's pause and clear
        endpoints so a continuation queued before the user clicked
        the button doesn't sneak through after the state change.

        **Not called from resume** — Telegram's ``/goal resume``
        handler doesn't drop continuations either, so the dashboard
        and Telegram surfaces share identical resume semantics
        (write status=active, reset turns_used, no queue mutation).
        Adding a defensive drop here was a Day 5 oversight that
        introduced silent behavioral divergence between the two
        surfaces. See `tests/test_dashboard_goals_endpoints.py::
        test_post_resume_does_not_drop_continuations` for the
        regression pin.

        ``running_tasks`` may be None on bare test fixtures; guard.
        """
        if self._running_tasks is None:
            return
        try:
            await self._running_tasks.drop_messages_matching_all_chats(
                lambda m: m.origin == "goal_continuation"
            )
        except Exception:
            log.debug(
                "goals: drop_messages_matching_all_chats failed", exc_info=True
            )

    async def _goals_pause(self) -> dict:
        from core.goal_state import TerminalGoalError
        sid = self._active_session_uuid()
        if not sid:
            raise HTTPException(404, "no active session")
        mgr = self._build_goal_manager(sid)
        if not mgr.is_active():
            raise HTTPException(404, "no active goal to pause")
        try:
            mgr.pause(reason="dashboard-paused")
        except TerminalGoalError as exc:
            # Race: disk flipped to done/cleared between our __init__
            # load and the locked save. Tell the user explicitly so
            # the dashboard can refresh instead of showing stale state.
            raise HTTPException(
                409,
                f"Goal is already {exc.status} — refresh the dashboard.",
            ) from exc
        await self._drop_dashboard_goal_continuations()
        state = mgr.state
        assert state is not None  # we just paused it
        return self._goal_record_dict(sid, state)

    async def _goals_resume(self) -> dict:
        from core.goal_state import TerminalGoalError
        sid = self._active_session_uuid()
        if not sid:
            raise HTTPException(404, "no active session")
        mgr = self._build_goal_manager(sid)
        s = mgr.state
        if s is None or s.status != "paused":
            raise HTTPException(404, "no paused goal to resume")
        try:
            mgr.resume()
        except TerminalGoalError as exc:
            # Race: disk flipped to done/cleared between our __init__
            # load and the locked save.
            raise HTTPException(
                409,
                f"Goal is already {exc.status} — refresh the dashboard.",
            ) from exc
        # NO continuation drop here — parity with Telegram's
        # ``/goal resume`` handler (``transports/telegram.py:_on_goal``
        # under ``sub == "resume"``), which writes state and replies
        # but does not touch the queue. Both surfaces share identical
        # resume semantics by design.
        state = mgr.state
        assert state is not None
        return self._goal_record_dict(sid, state)

    async def _goals_clear(self) -> dict:
        sid = self._active_session_uuid()
        if not sid:
            raise HTTPException(404, "no active session")
        mgr = self._build_goal_manager(sid)
        if not mgr.has_goal():
            raise HTTPException(404, "no active goal to clear")
        # Snapshot the goal record BEFORE clearing so the response
        # carries the final state (status=cleared) the frontend can
        # use to update its UI without an extra fetch.
        mgr.clear()
        await self._drop_dashboard_goal_continuations()
        # Re-load from disk to read the cleared row (clear() drops
        # the in-memory state ref but persists status=cleared).
        state = self._goals_store().load(sid)
        if state is None:
            # Defensive — clear() should always leave a row.
            raise HTTPException(500, "goal record vanished after clear")
        return self._goal_record_dict(sid, state)

    def _browser_payload(self) -> dict:
        manager = self._browser.manager
        session_state = manager.state_for_dashboard()
        page_state = self._browser.state_for_dashboard()

        attach_mode = "cdp-attach" if session_state["attached_to_cdp"] else "owned-chromium"
        cdp_url = yaml_config.browser_cdp_url()

        profile_path = browser_profile_dir()
        size_bytes, size_at = self._browser_profile_size(profile_path)
        cookie_count = _count_cookies(profile_path)

        return {
            "session": {
                "state": session_state["state"],
                "current_url": page_state["current_url"],
                "current_title": page_state["current_title"],
                "started_at": session_state["started_at"],
                "last_activity_at": session_state["last_activity_at"],
                "headless": session_state["headless"],
                "attach_mode": attach_mode,
            },
            "profile": {
                "path": str(profile_path),
                "exists": profile_path.is_dir(),
                "size_bytes": size_bytes,
                "size_as_of": size_at,
                "cookie_count": cookie_count,
            },
            "recent_navigations": page_state["recent_navigations"],
            "recent_screenshots": _list_recent_screenshots(
                browser_screenshots_dir(self._workspace),
                BROWSER_SCREENSHOT_LIMIT,
            ),
            "config": {
                "profiles_dir": str(browser_profiles_dir()),
                "default_profile": browser_default_profile_name(),
                "headless": yaml_config.browser_headless(),
                "inactivity_timeout_seconds": (
                    yaml_config.browser_inactivity_timeout_seconds()
                ),
                "action_timeout_seconds": (
                    yaml_config.browser_action_timeout_seconds()
                ),
                "chromium_path": yaml_config.browser_chromium_path(),
                "cdp_url": cdp_url,
                "screenshot_include_base64": (
                    yaml_config.browser_screenshot_include_base64()
                ),
            },
        }

    def _browser_profile_size(self, profile_path: Path) -> tuple[int | None, str | None]:
        """Walk the profile dir, with a 30-second cache.

        Returns (size_bytes, iso_timestamp). Both are None when the
        directory doesn't exist (CDP-attach with no Vexis profile, or
        before the first session). The timestamp is what the UI labels
        ``as of`` so the user knows it's not perfectly live.
        """
        if not profile_path.is_dir():
            return None, None
        now = time.monotonic()
        cached = self._profile_size_cache
        if cached is not None:
            cached_at, cached_size, cached_iso = cached
            if now - cached_at < BROWSER_PROFILE_SIZE_TTL_SECONDS:
                return cached_size, cached_iso
        size = _walk_dir_size(profile_path)
        iso = datetime.now(timezone.utc).isoformat()
        self._profile_size_cache = (now, size, iso)
        return size, iso

    def _browser_screenshot_response(self, name: str) -> FileResponse:
        if not _BROWSER_SCREENSHOT_NAME_RE.match(name):
            raise HTTPException(400, "invalid screenshot filename")
        screenshots = browser_screenshots_dir(self._workspace).resolve()
        candidate = (screenshots / name).resolve()
        # Defense in depth — even though the regex blocks separators,
        # follow the resolved path and confirm it stays inside the
        # screenshots dir before reading.
        try:
            candidate.relative_to(screenshots)
        except ValueError as exc:
            raise HTTPException(400, "invalid screenshot path") from exc
        if not candidate.is_file():
            raise HTTPException(404, "screenshot not found")
        return FileResponse(candidate, media_type="image/png")

    # ----- Tailscale Serve plumbing -------------------------------------

    async def _configure_tailscale(self) -> None:
        dns = await _tailscale_dns()
        argv = [
            "tailscale",
            "serve",
            "--bg",
            "--https=443",
            f"--set-path={self._config.tailscale_path}",
            f"http://localhost:{self._config.port}",
        ]
        rc, stdout, stderr = await run_subprocess(
            "tailscale", argv, TAILSCALE_TIMEOUT_SECONDS
        )
        if rc != 0:
            err = (stderr or b"").decode(errors="replace").strip()
            out = (stdout or b"").decode(errors="replace").strip()
            raise _TailscaleError(
                f"tailscale serve failed (rc={rc}): "
                f"{err or out or '(no output)'}"
            )
        self._tailscale_dns = dns
        self._tailscale_url = f"https://{dns}{self._config.tailscale_path}"

    async def _teardown_tailscale(self) -> None:
        argv = [
            "tailscale",
            "serve",
            "--https=443",
            f"--set-path={self._config.tailscale_path}",
            "off",
        ]
        rc, _, stderr = await run_subprocess(
            "tailscale", argv, TAILSCALE_TIMEOUT_SECONDS
        )
        if rc != 0:
            raise _TailscaleError(
                f"tailscale serve off failed (rc={rc}): "
                f"{(stderr or b'').decode(errors='replace').strip()}"
            )
        self._tailscale_url = None


# ----------------------------------------------------------------------
# helpers (module-private)
# ----------------------------------------------------------------------


class _TailscaleError(RuntimeError):
    """Localised exception so failures here don't crash the daemon."""


async def _tailscale_dns() -> str:
    """Return the DNS name of this tailscale node, or raise."""
    rc, stdout, stderr = await run_subprocess(
        "tailscale",
        ["tailscale", "status", "--json"],
        TAILSCALE_TIMEOUT_SECONDS,
    )
    if rc != 0:
        raise _TailscaleError(
            f"tailscale status (rc={rc}): "
            f"{(stderr or b'').decode(errors='replace').strip()}"
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise _TailscaleError(f"tailscale returned invalid JSON: {exc}") from exc
    backend = payload.get("BackendState")
    if backend != "Running":
        raise _TailscaleError(
            f"tailscale backend state is {backend!r}, expected 'Running'"
        )
    dns = (payload.get("Self") or {}).get("DNSName", "").rstrip(".")
    if not dns:
        raise _TailscaleError("tailscale returned no DNSName for this node")
    return dns


def _walk_dir_size(root: Path) -> int:
    """Sum ``st_size`` recursively under ``root``. Best-effort.

    Files we can't stat (transient deletes, permission issues) are
    silently skipped. The Chromium profile mutates while we walk; the
    result is approximate, which is fine for a "size on disk" line on
    a dashboard refreshed every 30 seconds.
    """
    total = 0
    stack: list[Path] = [root]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat().st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def _count_cookies(profile_path: Path) -> int | None:
    """Read row count from the Chromium Cookies SQLite file.

    One try/except wraps everything: path resolution, sqlite connect,
    query. Newer Chromium uses ``Default/Network/Cookies``; older
    versions used ``Default/Cookies``. We try both. Any failure (file
    missing, schema change, lock contention, permission, ...) returns
    ``None`` and the UI renders ``—``. Discriminating failure modes
    is not useful — it's a count, either we got one or we didn't.
    """
    try:
        candidates = [
            profile_path / "Default" / "Network" / "Cookies",
            profile_path / "Default" / "Cookies",
        ]
        cookie_db = next((c for c in candidates if c.is_file()), None)
        if cookie_db is None:
            return None
        # Read-only URI plus immutable=1 so SQLite doesn't try to acquire
        # a write lock on a file Chromium may have open.
        uri = f"file:{cookie_db}?mode=ro&immutable=1"
        with sqlite3.connect(uri, uri=True, timeout=0.5) as conn:
            row = conn.execute("SELECT COUNT(*) FROM cookies").fetchone()
        return int(row[0]) if row else None
    except Exception:
        return None


def _list_recent_screenshots(directory: Path, limit: int) -> list[dict]:
    """Newest-first list of ``{filename, size_bytes, mtime}`` entries."""
    if not directory.is_dir():
        return []
    entries: list[tuple[float, str, int]] = []
    try:
        with os.scandir(directory) as it:
            for entry in it:
                if not entry.is_file(follow_symlinks=False):
                    continue
                if not entry.name.lower().endswith(".png"):
                    continue
                try:
                    st = entry.stat()
                except OSError:
                    continue
                entries.append((st.st_mtime, entry.name, st.st_size))
    except OSError:
        return []
    entries.sort(key=lambda row: row[0], reverse=True)
    return [
        {
            "filename": name,
            "size_bytes": size,
            "mtime": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
        }
        for mtime, name, size in entries[:limit]
    ]


def _archived_description(skills_root: Path, name: str) -> str:
    """Best-effort: read the SKILL.md frontmatter from the archive."""
    archive = skills_root / ".archive"
    if not archive.exists():
        return ""
    for entry in archive.iterdir():
        if not entry.is_dir():
            continue
        try:
            content = (entry / "SKILL.md").read_text(encoding="utf-8")
        except OSError:
            continue
        meta = parse_skill_md(content)
        if meta is not None and meta.name == name:
            return meta.description
    return ""


def _iter_curator_runs(root: Path):
    """Yield run metadata dicts, newest first."""
    if not root.exists():
        return
    folders = [p for p in root.iterdir() if p.is_dir()]
    folders.sort(key=lambda p: p.name, reverse=True)
    for folder in folders:
        run_json: dict | None = None
        try:
            run_json = json.loads(
                (folder / "run.json").read_text(encoding="utf-8")
            )
        except (FileNotFoundError, json.JSONDecodeError):
            run_json = None
        phase1 = (run_json or {}).get("phase1") or {}
        phase2 = (run_json or {}).get("phase2") or {}
        yield {
            "folder": folder.name,
            "started_at": (run_json or {}).get("started_at"),
            "finished_at": (run_json or {}).get("finished_at"),
            "phase1": {
                "checked": int(phase1.get("checked") or 0),
                "marked_stale": int(phase1.get("marked_stale") or 0),
                "reactivated": int(phase1.get("reactivated") or 0),
                "archived": int(phase1.get("archived") or 0),
            },
            "phase2_ran": bool(phase2.get("ran")),
            "phase2_archived": list(phase2.get("archived_names") or []),
            "phase2_created": list(phase2.get("created_names") or []),
            "phase2_error": phase2.get("error"),
        }


def _tail_log(path: Path, n: int) -> list[dict]:
    """Tail the last ``n`` lines of ``path`` and split each into level + msg.

    Returned in newest-first order so the dashboard can render the most
    recent line at the top without forcing the user to scroll. Vexis's
    ``setup_logging`` uses
    ``%(asctime)s %(levelname)s %(name)s: %(message)s`` so we parse
    that. Lines that don't match the format pass through as plain
    ``message`` rows so the dashboard never silently drops content.
    """
    if not path.exists():
        return []
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            # Walk backwards in 8 KiB chunks until we have enough \n.
            chunk = 8192
            buf = bytearray()
            pos = size
            seen = 0
            while pos > 0 and seen <= n:
                read_size = min(chunk, pos)
                pos -= read_size
                fh.seek(pos)
                block = fh.read(read_size)
                buf[:0] = block
                seen = buf.count(b"\n")
            text = buf.decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = deque(text.splitlines(), maxlen=n)
    rows: list[dict] = []
    for line in lines:
        parts = line.split(" ", 3)
        if len(parts) >= 4 and parts[2].endswith(":") is False:
            ts = " ".join(parts[:2])
            level = parts[2]
            rest = parts[3]
            # The format puts a colon after the logger name, not the level,
            # so split off "module: message" from rest.
            mod, _, msg = rest.partition(": ")
            rows.append(
                {
                    "ts": ts,
                    "level": level,
                    "logger": mod,
                    "message": msg or rest,
                }
            )
        else:
            rows.append({"ts": "", "level": "", "logger": "", "message": line})
    rows.reverse()
    return rows


def _parse_iso(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _hours(n: int):
    from datetime import timedelta

    return timedelta(hours=n)

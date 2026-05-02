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
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from core.background_tasks import BackgroundTasks
from core.curator import (
    CuratorController,
    load_state as load_curator_state,
)
from core.dashboard_token import clear_token, issue_token
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
from core import yaml_config
from core.yaml_config import (
    curator_archive_after_days,
    curator_enabled,
    curator_interval_hours,
    curator_stale_after_days,
)
from tools.browser import BrowserTools
from tools.browser.profile import (
    default_profile_name as browser_default_profile_name,
    profile_dir as browser_profile_dir,
    profiles_dir as browser_profiles_dir,
    screenshots_dir as browser_screenshots_dir,
)

log = logging.getLogger(__name__)

# Default port. Can be overridden by VEXIS_DASHBOARD_PORT in the env.
DEFAULT_DASHBOARD_PORT = 8766

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
        config: DashboardConfig,
    ) -> None:
        self._workspace = workspace
        self._sessions = sessions
        self._running_tasks = running_tasks
        self._background_tasks = background_tasks
        self._curator = curator
        self._browser = browser
        self._config = config

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

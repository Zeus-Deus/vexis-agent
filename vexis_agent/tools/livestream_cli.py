"""CLI front-end to the livestream side-process daemon.

`start` spawns a detached `python -m tools.livestream` process and waits
for it to write its state file. `stop` sends SIGTERM to the daemon (it
cleans up its tailscale serve mapping on shutdown). `status` reads the
state file. `touch` sends SIGUSR1, which resets the daemon's idle timer.

``--source`` on ``start`` accepts the same grammar as ``vexis-screenshot
--source`` (``host`` / ``sandbox`` / ``sandbox:<task-id>`` / omitted →
auto). The CLI resolves the source against the live host state (active
sandboxes via ``Sandbox.list_all``, ``VEXIS_SANDBOX_TASK_ID``) and
passes the result to the daemon via two env vars
(``VEXIS_LIVESTREAM_SOURCE_KIND`` / ``_TASK_ID``). The state file
records the resolved source so ``status`` can surface it.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from vexis_agent.tools.capture_source import (
    CaptureSourceError,
    RouterContext,
    caption_label,
    resolve_source,
)
from vexis_agent.tools.livestream import IDLE_TIMEOUT_SECONDS, state_file_path

START_TIMEOUT_SECONDS = 15.0
STOP_TIMEOUT_SECONDS = 5.0


def _read_state() -> dict | None:
    try:
        return json.loads(state_file_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _pid_alive(pid: int | None) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _seconds_until_idle_stop(state: dict) -> float | None:
    last = state.get("last_activity")
    if not last:
        return None
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return None
    elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
    return max(0.0, IDLE_TIMEOUT_SECONDS - elapsed)


def _resolve_source_for_cli(requested: str | None):
    """Build a CaptureSource from CLI args + live host state.

    Mirrors the helper in ``desktop_cli.py``; intentionally duplicated
    so each CLI module owns its own import surface without a
    shared-helper detour.
    """
    active: tuple[str, ...] = tuple()
    needs_active = requested is None or requested.lower().startswith("sandbox")
    if needs_active:
        try:
            from vexis_agent.tools.sandbox import Sandbox  # type: ignore[import-not-found]

            rows = Sandbox.list_all()
            active = tuple(r["task_id"] for r in rows if r.get("running"))
        except Exception:
            active = tuple()

    ctx = RouterContext(
        requested=requested,
        current_task_id=os.environ.get("VEXIS_SANDBOX_TASK_ID"),
        active_sandbox_task_ids=active,
    )
    return resolve_source(ctx)


def _cmd_start(requested_source: str | None = None) -> int:
    existing = _read_state()
    if existing and _pid_alive(existing.get("pid")):
        print(json.dumps(existing))
        return 0

    try:
        source = _resolve_source_for_cli(requested_source)
    except CaptureSourceError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    env = os.environ.copy()
    env["VEXIS_LIVESTREAM_SOURCE_KIND"] = source.kind
    if source.task_id:
        env["VEXIS_LIVESTREAM_SOURCE_TASK_ID"] = source.task_id
    else:
        env.pop("VEXIS_LIVESTREAM_SOURCE_TASK_ID", None)

    # cwd = repo root (three `.parent`s: file → tools/ → vexis_agent/ → repo).
    # Preserves the pre-Phase-2 behaviour of spawning the side-process with
    # the source-checkout root as cwd; the module load itself doesn't
    # depend on cwd, so a pipx install (no source checkout) just gets the
    # nearest enclosing dir, which is fine for stdout/stderr-DEVNULL'd
    # one-shot tooling.
    project_dir = Path(__file__).resolve().parent.parent.parent
    proc = subprocess.Popen(
        [sys.executable, "-m", "vexis_agent.tools.livestream"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        cwd=str(project_dir),
        start_new_session=True,
        env=env,
    )

    deadline = time.time() + START_TIMEOUT_SECONDS
    while time.time() < deadline:
        if proc.poll() is not None:
            print(
                f"livestream daemon exited prematurely (rc={proc.returncode})",
                file=sys.stderr,
            )
            return 1
        state = _read_state()
        if state and state.get("pid") == proc.pid and state.get("url"):
            # Decorate the state echo with a label so users see at a
            # glance which source is being streamed.
            state["source_label"] = caption_label(source)
            print(json.dumps(state))
            return 0
        time.sleep(0.1)

    try:
        proc.terminate()
    except Exception:
        pass
    print(
        f"livestream daemon failed to start within {START_TIMEOUT_SECONDS}s",
        file=sys.stderr,
    )
    return 1


def _cmd_stop() -> int:
    state = _read_state()
    if not state or not _pid_alive(state.get("pid")):
        if state:
            try:
                state_file_path().unlink(missing_ok=True)
            except Exception:
                pass
        print(json.dumps({"stopped": False, "reason": "not running"}))
        return 0

    pid = state["pid"]
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        try:
            state_file_path().unlink(missing_ok=True)
        except Exception:
            pass
        print(json.dumps({"stopped": False, "reason": "not running"}))
        return 0

    deadline = time.time() + STOP_TIMEOUT_SECONDS
    while time.time() < deadline and _pid_alive(pid):
        time.sleep(0.1)
    if _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            state_file_path().unlink(missing_ok=True)
        except Exception:
            pass
    print(json.dumps({"stopped": True}))
    return 0


def _cmd_status() -> int:
    state = _read_state()
    if not state or not _pid_alive(state.get("pid")):
        print(
            json.dumps(
                {
                    "running": False,
                    "url": None,
                    "started_at": None,
                    "last_activity": None,
                    "seconds_until_idle_stop": None,
                }
            )
        )
        return 0
    print(
        json.dumps(
            {
                "running": True,
                "url": state.get("url"),
                "started_at": state.get("started_at"),
                "last_activity": state.get("last_activity"),
                "seconds_until_idle_stop": _seconds_until_idle_stop(state),
            }
        )
    )
    return 0


def _cmd_touch() -> int:
    state = _read_state()
    if not state or not _pid_alive(state.get("pid")):
        print("livestream not running", file=sys.stderr)
        return 1
    try:
        os.kill(state["pid"], signal.SIGUSR1)
    except ProcessLookupError:
        print("livestream not running", file=sys.stderr)
        return 1
    print(json.dumps({"touched": True}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Vexis live-view stream control.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    start = sub.add_parser("start", help="Start the live-view stream.")
    start.add_argument(
        "--source",
        default=None,
        help=(
            "Stream source: 'host' (real desktop), 'sandbox' (latest active "
            "sandbox display), 'sandbox:<task-id>' (specific). Default: auto."
        ),
    )
    sub.add_parser("stop", help="Stop the live-view stream.")
    sub.add_parser("status", help="Report stream status as JSON.")
    sub.add_parser("touch", help="Reset the idle timer.")
    args = parser.parse_args()

    if args.cmd == "start":
        return _cmd_start(args.source)
    if args.cmd == "stop":
        return _cmd_stop()
    if args.cmd == "status":
        return _cmd_status()
    if args.cmd == "touch":
        return _cmd_touch()
    return 2


if __name__ == "__main__":
    sys.exit(main())

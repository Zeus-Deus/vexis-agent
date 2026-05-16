"""CLI wrapper around `capture_desktop()` so Vexis can invoke it via Bash.

``--source`` accepts:

* ``host`` (default when no other context applies) — capture the real desktop.
* ``sandbox`` — capture from the most-recently-active per-task Docker
  sandbox display.
* ``sandbox:<task-id>`` — capture from a specific sandbox.
* (omitted) — auto: prefer the current task's sandbox if
  ``VEXIS_SANDBOX_TASK_ID`` is set AND that sandbox is active; else host.

The router and the lock detector live in pure modules so this CLI stays
small. See ``docs/screenshot-routing.md``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from vexis_agent.tools.capture_source import (
    CaptureSourceError,
    RouterContext,
    caption_label,
    resolve_source,
)
from vexis_agent.tools.desktop import VALID_SCOPES, CaptureError, capture_desktop


async def _amain() -> int:
    parser = argparse.ArgumentParser(description="Capture screenshot + Hyprland state.")
    parser.add_argument("--scope", default="focused-monitor", choices=VALID_SCOPES)
    parser.add_argument(
        "--source",
        default=None,
        help=(
            "Capture source: 'host' (real desktop), 'sandbox' (latest active "
            "sandbox display), 'sandbox:<task-id>' (specific sandbox). "
            "Default: auto — prefer current task's sandbox if active, else host."
        ),
    )
    args = parser.parse_args()

    try:
        source = _resolve_source_for_cli(args.source)
    except CaptureSourceError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    try:
        result = await capture_desktop(args.scope, source=source)
    except CaptureError as exc:
        payload: dict = {"error": str(exc)}
        if exc.image_path is not None:
            payload["image_path"] = str(exc.image_path)
        print(json.dumps(payload), file=sys.stderr)
        return 1

    payload_out = {
        "image_path": str(result.image_path),
        "summary": result.summary,
        "state": result.state,
        "source": {
            "kind": source.kind,
            "task_id": source.task_id,
            "reason": source.reason,
            "label": caption_label(source),
        },
    }
    print(json.dumps(payload_out))
    return 0


def _resolve_source_for_cli(requested: str | None):
    """Build a :class:`CaptureSource` from CLI args + live host state.

    Lazy import of :class:`Sandbox.list_all` so the host-only path
    works even when docker isn't installed.
    """
    active: tuple[str, ...] = tuple()
    needs_active = requested is None or requested.lower().startswith("sandbox")
    if needs_active:
        try:
            from vexis_agent.tools.sandbox import Sandbox  # type: ignore[import-not-found]

            rows = Sandbox.list_all()
            active = tuple(r["task_id"] for r in rows if r.get("running"))
        except Exception:
            # Docker missing / list failed → empty active set. The
            # router will produce a clear error if the user asked for
            # sandbox; auto resolves to host.
            active = tuple()

    ctx = RouterContext(
        requested=requested,
        current_task_id=os.environ.get("VEXIS_SANDBOX_TASK_ID"),
        active_sandbox_task_ids=active,
    )
    return resolve_source(ctx)


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())

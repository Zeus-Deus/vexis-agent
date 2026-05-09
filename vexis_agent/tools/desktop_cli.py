"""CLI wrapper around `capture_desktop()` so Vexis can invoke it via Bash."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from vexis_agent.tools.desktop import VALID_SCOPES, CaptureError, capture_desktop


async def _amain() -> int:
    parser = argparse.ArgumentParser(description="Capture screenshot + Hyprland state.")
    parser.add_argument("--scope", default="focused-monitor", choices=VALID_SCOPES)
    args = parser.parse_args()

    try:
        result = await capture_desktop(args.scope)
    except CaptureError as exc:
        payload: dict = {"error": str(exc)}
        if exc.image_path is not None:
            payload["image_path"] = str(exc.image_path)
        print(json.dumps(payload), file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "image_path": str(result.image_path),
                "summary": result.summary,
                "state": result.state,
            }
        )
    )
    return 0


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())

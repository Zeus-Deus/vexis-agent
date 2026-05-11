"""Host-side coordinator for ``vexis-ui``.

Composes :mod:`vexis_agent.tools.sandbox` + :mod:`vexis_agent.tools.display`
with the AT-SPI runner script:

1. Locate the per-task sandbox.
2. Pull ``DISPLAY`` / ``WAYLAND_DISPLAY`` from ``vexis-display env``.
3. Ship the runner source into the sandbox via ``python3 -c`` and
   parse the JSON envelope back.

Doing it this way means the agent only needs to know task-id; we
handle the sandbox-display-runner dance.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from vexis_agent.tools.display import HeadlessDisplay
from vexis_agent.tools.sandbox import Sandbox, SandboxError
from vexis_agent.tools.sandbox.backend import ExecResult

from .runner_src import RUNNER_SOURCE


log = logging.getLogger(__name__)


class UIAction(str, Enum):
    SNAPSHOT = "snapshot"
    CLICK = "click"
    TYPE = "type"
    PRESS = "press"
    FOCUS = "focus"
    VISION = "vision-snapshot"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ATSPIError(RuntimeError):
    """Base for typed UI failures."""


class UIRuntimeError(ATSPIError):
    """Wraps any runner-side error into a typed shape."""


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass
class SnapshotResult:
    snapshot: str
    element_count: int
    stale: bool
    hint: str = ""

    def to_dict(self) -> dict:
        return {
            "snapshot": self.snapshot,
            "element_count": self.element_count,
            "stale": self.stale,
            "hint": self.hint,
        }


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def build_action_argv(action: UIAction, args: dict[str, Any]) -> list[str]:
    """Compose the ``python3 -c <runner>`` argv for a given UI action.

    Factored out so unit tests can pin the exact shell invocation
    without instantiating a Sandbox.
    """
    return [
        "python3",
        "-c",
        RUNNER_SOURCE + "\nmain()",
        action.value,
        json.dumps(args, ensure_ascii=False),
    ]


@dataclass
class UIDriver:
    """One UIDriver per task-id. Wraps the sandbox + display lookup."""

    task_id: str
    sandbox: Sandbox = None  # type: ignore[assignment]
    display: HeadlessDisplay = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.sandbox is None:
            self.sandbox = Sandbox(self.task_id)
        if self.display is None:
            self.display = HeadlessDisplay(self.task_id, sandbox=self.sandbox)

    # ----- env composition --------------------------------------------

    def _env(self) -> dict[str, str]:
        try:
            return self.display.env()
        except Exception as exc:
            log.debug("display env unavailable for task %s: %s", self.task_id, exc)
            return {}

    # ----- runner exec ------------------------------------------------

    def _exec_runner(
        self,
        action: UIAction,
        payload: dict[str, Any],
        *,
        timeout: float | None = 30,
    ) -> dict:
        argv = build_action_argv(action, payload)
        env = self._env()
        try:
            res: ExecResult = self.sandbox.exec(
                argv, env=env, auto_start=False, timeout=timeout
            )
        except SandboxError as exc:
            raise ATSPIError(
                f"sandbox not running for task {self.task_id!r}: {exc}"
            ) from exc
        # The runner's last stdout line is the JSON envelope. Anything
        # before it is debugging noise we tolerate but don't parse.
        stdout = res.stdout.strip()
        if not stdout:
            raise UIRuntimeError(
                f"runner produced no output: {res.stderr.strip() or 'silent failure'}"
            )
        last_line = stdout.splitlines()[-1]
        try:
            payload_back = json.loads(last_line)
        except json.JSONDecodeError as exc:
            raise UIRuntimeError(
                f"runner output was not JSON: {last_line!r} (err: {exc})"
            ) from exc
        if not payload_back.get("ok"):
            raise UIRuntimeError(payload_back.get("error", "unknown runner error"))
        return payload_back.get("result") or {}

    # ----- public API -------------------------------------------------

    def snapshot(self) -> SnapshotResult:
        data = self._exec_runner(UIAction.SNAPSHOT, {})
        return SnapshotResult(
            snapshot=data.get("snapshot", ""),
            element_count=int(data.get("element_count", 0)),
            stale=bool(data.get("stale", False)),
            hint=data.get("hint", ""),
        )

    def click(self, index: int) -> dict:
        return self._exec_runner(UIAction.CLICK, {"index": int(index)})

    def type_text(self, index: int, text: str) -> dict:
        return self._exec_runner(
            UIAction.TYPE, {"index": int(index), "text": str(text)}
        )

    def press(self, chord: str) -> dict:
        return self._exec_runner(UIAction.PRESS, {"chord": str(chord)})

    def focus(self, selector: str) -> dict:
        return self._exec_runner(UIAction.FOCUS, {"selector": str(selector)})

    def vision_snapshot(self, out_path: str | None = None) -> dict:
        return self._exec_runner(
            UIAction.VISION, {"out": out_path} if out_path else {}
        )

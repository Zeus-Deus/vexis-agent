"""Check definitions and runner for ``vexis-verify``.

A *check spec* is a YAML document with one or more named entries
describing how to assert "done":

.. code-block:: yaml

    checks:
      - name: tests-pass
        cmd: ["cargo", "test"]
        expect_exit: 0

      - name: binary-exists
        cmd: ["test", "-f", "/workspace/target/debug/myapp"]
        expect_exit: 0

      - name: greeting-printed
        cmd: ["/workspace/target/debug/myapp", "--greet"]
        expect_exit: 0
        expect_stdout_contains: "hello"

Each check is run inside the same sandbox the agent worked in (looked
up by ``task_id``). The runner short-circuits on the first failure
*by default* but ``run_all`` flips that to "always run every check."

The check vocabulary is deliberately small. Anything more elaborate
(JSON-matching, multi-line regex over a log) is the agent's job to
script as a shell pipeline inside the ``cmd`` itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from vexis_agent.tools.sandbox import Sandbox, SandboxError


# Recommended path inside the workspace for the agent to drop its
# acceptance criteria. Not enforced — callers may supply any path.
DEFAULT_CHECKS_FILENAME = "checks.yaml"


class VerifyError(RuntimeError):
    """Base for typed verify failures (loader-level)."""


class ChecksFileNotFound(VerifyError):
    """Raised when the checks YAML doesn't exist."""


class ChecksFileInvalid(VerifyError):
    """Raised when the checks YAML can't be parsed or doesn't match the
    expected shape."""


# ---------------------------------------------------------------------------
# Spec data classes
# ---------------------------------------------------------------------------


@dataclass
class CheckSpec:
    """One check's intent, as parsed from YAML."""

    name: str
    cmd: list[str] | str
    cwd: str | None = None
    timeout: float | None = None
    expect_exit: int | None = 0
    expect_stdout_contains: str | None = None
    expect_stdout_regex: str | None = None
    expect_stderr_contains: str | None = None
    expect_stderr_regex: str | None = None
    description: str | None = None

    def to_dict(self) -> dict:
        out = {
            "name": self.name,
            "cmd": self.cmd,
        }
        for opt in (
            "cwd",
            "timeout",
            "expect_exit",
            "expect_stdout_contains",
            "expect_stdout_regex",
            "expect_stderr_contains",
            "expect_stderr_regex",
            "description",
        ):
            v = getattr(self, opt)
            if v is not None:
                out[opt] = v
        return out


@dataclass
class CheckResult:
    """Outcome of one check execution."""

    name: str
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    failure_reason: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "passed": self.passed,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "failure_reason": self.failure_reason,
        }


@dataclass
class VerifyOutcome:
    """Aggregate summary returned by :func:`run_checks`."""

    task_id: str
    all_passed: bool
    results: list[CheckResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "all_passed": self.all_passed,
            "results": [r.to_dict() for r in self.results],
            "failed": [r.name for r in self.results if not r.passed],
        }

    def failure_summary(self) -> str:
        """A short human/agent-friendly summary, suitable for feeding
        back into the next-turn observation when a verify fails."""
        if self.all_passed:
            return "all checks passed"
        lines = [f"{len(self.results)} checks ran, "
                 f"{sum(1 for r in self.results if not r.passed)} failed:"]
        for r in self.results:
            if r.passed:
                continue
            lines.append(f"  - {r.name}: {r.failure_reason or 'failed'}")
        return "\n".join(lines)


# Convenience alias re-exported at the package level.
Check = CheckSpec


# ---------------------------------------------------------------------------
# YAML loader
# ---------------------------------------------------------------------------


def load_checks(path: str | Path) -> list[CheckSpec]:
    """Parse a checks YAML file.

    The top-level key ``checks:`` is required and must be a list. Each
    entry must have ``name`` (str) and ``cmd`` (list[str] or str). All
    other fields are optional.
    """
    p = Path(path)
    if not p.exists():
        raise ChecksFileNotFound(f"checks file not found: {p}")
    try:
        data = yaml.safe_load(p.read_text())
    except yaml.YAMLError as exc:
        raise ChecksFileInvalid(f"YAML parse error in {p}: {exc}") from exc
    if not isinstance(data, dict) or "checks" not in data:
        raise ChecksFileInvalid(
            f"{p}: expected top-level mapping with a 'checks:' key"
        )
    raw_list = data.get("checks")
    if not isinstance(raw_list, list) or not raw_list:
        raise ChecksFileInvalid(
            f"{p}: 'checks' must be a non-empty list"
        )
    specs: list[CheckSpec] = []
    for idx, entry in enumerate(raw_list):
        if not isinstance(entry, dict):
            raise ChecksFileInvalid(
                f"{p}: checks[{idx}] is not a mapping"
            )
        name = entry.get("name")
        cmd = entry.get("cmd")
        if not isinstance(name, str) or not name:
            raise ChecksFileInvalid(
                f"{p}: checks[{idx}] missing/invalid 'name'"
            )
        if not isinstance(cmd, (str, list)) or (
            isinstance(cmd, list) and not all(isinstance(x, str) for x in cmd)
        ):
            raise ChecksFileInvalid(
                f"{p}: checks[{idx}].cmd must be a string or list-of-strings"
            )
        specs.append(
            CheckSpec(
                name=name,
                cmd=cmd,
                cwd=entry.get("cwd"),
                timeout=_optional_number(entry.get("timeout")),
                # ``expect_exit`` defaults to 0; ``null`` in YAML means
                # "don't check exit code" (useful for commands whose
                # success is purely about stdout content).
                expect_exit=(
                    entry["expect_exit"] if "expect_exit" in entry else 0
                ),
                expect_stdout_contains=entry.get("expect_stdout_contains"),
                expect_stdout_regex=entry.get("expect_stdout_regex"),
                expect_stderr_contains=entry.get("expect_stderr_contains"),
                expect_stderr_regex=entry.get("expect_stderr_regex"),
                description=entry.get("description"),
            )
        )
    return specs


def _optional_number(v: Any) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    raise ChecksFileInvalid(f"expected number or null, got {v!r}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _evaluate(spec: CheckSpec, exit_code: int, stdout: str, stderr: str) -> tuple[bool, str | None]:
    """Return ``(passed, failure_reason)`` for one finished command.

    Failure reason is ``None`` on success, a one-line string explaining
    the first failing predicate otherwise (we report only the first so
    the agent's next-turn observation isn't a five-line essay)."""
    if spec.expect_exit is not None and exit_code != spec.expect_exit:
        return False, (
            f"expected exit {spec.expect_exit}, got {exit_code}"
        )
    if spec.expect_stdout_contains is not None:
        if spec.expect_stdout_contains not in stdout:
            return False, (
                f"stdout did not contain {spec.expect_stdout_contains!r}"
            )
    if spec.expect_stdout_regex is not None:
        if not re.search(spec.expect_stdout_regex, stdout):
            return False, (
                f"stdout did not match regex /{spec.expect_stdout_regex}/"
            )
    if spec.expect_stderr_contains is not None:
        if spec.expect_stderr_contains not in stderr:
            return False, (
                f"stderr did not contain {spec.expect_stderr_contains!r}"
            )
    if spec.expect_stderr_regex is not None:
        if not re.search(spec.expect_stderr_regex, stderr):
            return False, (
                f"stderr did not match regex /{spec.expect_stderr_regex}/"
            )
    return True, None


def run_checks(
    task_id: str,
    specs: list[CheckSpec],
    *,
    sandbox: Sandbox | None = None,
    fail_fast: bool = False,
) -> VerifyOutcome:
    """Run every spec inside ``task_id``'s sandbox.

    ``sandbox`` is injectable so tests don't have to round-trip through
    Docker; production callers pass ``None`` and we construct a real
    :class:`Sandbox` against the live container.

    ``fail_fast=True`` stops on the first failing check; the default
    runs every check so the agent's next-turn observation lists ALL
    failures at once (more efficient than ping-ponging one at a time).
    """
    sb = sandbox if sandbox is not None else Sandbox(task_id)
    results: list[CheckResult] = []
    overall = True
    for spec in specs:
        try:
            res = sb.exec(
                spec.cmd,
                cwd=spec.cwd,
                timeout=spec.timeout,
                # ``auto_start=False`` is the right default for verify:
                # if the sandbox is gone, that's a verify failure, not a
                # silent re-create that might mask a regression.
                auto_start=False,
            )
        except SandboxError as exc:
            results.append(
                CheckResult(
                    name=spec.name,
                    passed=False,
                    exit_code=-1,
                    stdout="",
                    stderr=str(exc),
                    failure_reason=f"sandbox error: {exc}",
                )
            )
            overall = False
            if fail_fast:
                break
            continue
        passed, reason = _evaluate(spec, res.exit_code, res.stdout, res.stderr)
        results.append(
            CheckResult(
                name=spec.name,
                passed=passed,
                exit_code=res.exit_code,
                stdout=res.stdout,
                stderr=res.stderr,
                failure_reason=reason,
            )
        )
        if not passed:
            overall = False
            if fail_fast:
                break
    return VerifyOutcome(task_id=task_id, all_passed=overall, results=results)

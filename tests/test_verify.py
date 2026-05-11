"""Unit tests for :mod:`vexis_agent.tools.verify`.

Cover the loader (happy + invalid paths), the predicate evaluator
(every expect_* knob the spec exposes), and the CLI's exit-code
contract (0 pass / 1 fail / 2 input-error).

Sandbox interaction is exercised via a tiny in-line stub — verify is
generic over anything with an ``exec`` method returning ``ExecResult``,
so we don't even need to import the real Sandbox here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vexis_agent.tools.sandbox.backend import ExecResult
from vexis_agent.tools.verify import (
    CheckResult,
    CheckSpec,
    VerifyOutcome,
    load_checks,
    run_checks,
)
from vexis_agent.tools.verify.checks import (
    ChecksFileInvalid,
    ChecksFileNotFound,
)
from vexis_agent.tools.verify.cli import main as cli_main


# ---------------------------------------------------------------------------
# Stub sandbox
# ---------------------------------------------------------------------------


class StubSandbox:
    """Anything with ``exec(cmd, cwd, timeout, auto_start) -> ExecResult``
    is good enough for ``run_checks``. The stub maps the first arg of cmd
    (or the whole string) to a queued response so tests can pin the
    sequence of outcomes precisely."""

    def __init__(self, responses: dict[str, ExecResult]):
        self.responses = responses
        self.calls: list[tuple] = []

    def exec(self, cmd, *, cwd=None, timeout=None, auto_start=True):
        self.calls.append((tuple(cmd) if isinstance(cmd, list) else cmd, cwd, timeout, auto_start))
        key = cmd if isinstance(cmd, str) else cmd[0]
        if key in self.responses:
            return self.responses[key]
        return ExecResult(("docker",), 0, "", "")


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "checks.yaml"
    p.write_text(text)
    return p


def test_load_checks_happy(tmp_path):
    p = _write_yaml(
        tmp_path,
        """
checks:
  - name: smoke
    cmd: ["true"]
  - name: assert-stdout
    cmd: ["echo", "hi"]
    expect_stdout_contains: "hi"
""",
    )
    specs = load_checks(p)
    assert [s.name for s in specs] == ["smoke", "assert-stdout"]
    assert specs[0].expect_exit == 0  # default
    assert specs[1].expect_stdout_contains == "hi"


def test_load_checks_missing_file(tmp_path):
    with pytest.raises(ChecksFileNotFound):
        load_checks(tmp_path / "nope.yaml")


def test_load_checks_rejects_non_mapping(tmp_path):
    p = _write_yaml(tmp_path, "[1,2,3]\n")
    with pytest.raises(ChecksFileInvalid):
        load_checks(p)


def test_load_checks_rejects_empty_list(tmp_path):
    p = _write_yaml(tmp_path, "checks: []\n")
    with pytest.raises(ChecksFileInvalid):
        load_checks(p)


def test_load_checks_rejects_missing_name(tmp_path):
    p = _write_yaml(
        tmp_path,
        """
checks:
  - cmd: ["true"]
""",
    )
    with pytest.raises(ChecksFileInvalid):
        load_checks(p)


def test_load_checks_rejects_non_string_cmd(tmp_path):
    p = _write_yaml(
        tmp_path,
        """
checks:
  - name: bad
    cmd: 42
""",
    )
    with pytest.raises(ChecksFileInvalid):
        load_checks(p)


def test_load_checks_explicit_null_exit(tmp_path):
    p = _write_yaml(
        tmp_path,
        """
checks:
  - name: any-exit-ok
    cmd: ["true"]
    expect_exit: null
""",
    )
    specs = load_checks(p)
    assert specs[0].expect_exit is None


# ---------------------------------------------------------------------------
# Predicate evaluator
# ---------------------------------------------------------------------------


def test_run_checks_passes_when_all_predicates_satisfied():
    specs = [
        CheckSpec(name="exit", cmd=["true"]),
        CheckSpec(name="stdout", cmd=["echoer"], expect_stdout_contains="hello"),
    ]
    stub = StubSandbox(
        {
            "true": ExecResult(("docker",), 0, "", ""),
            "echoer": ExecResult(("docker",), 0, "hello world\n", ""),
        }
    )
    outcome = run_checks("t1", specs, sandbox=stub)
    assert outcome.all_passed
    assert all(r.passed for r in outcome.results)


def test_run_checks_fails_on_wrong_exit():
    specs = [CheckSpec(name="exit", cmd=["fails"])]
    stub = StubSandbox({"fails": ExecResult(("docker",), 7, "", "")})
    outcome = run_checks("t1", specs, sandbox=stub)
    assert not outcome.all_passed
    assert outcome.results[0].failure_reason == "expected exit 0, got 7"


def test_run_checks_fails_on_missing_stdout_substring():
    specs = [CheckSpec(name="match", cmd=["e"], expect_stdout_contains="needle")]
    stub = StubSandbox({"e": ExecResult(("docker",), 0, "haystack\n", "")})
    outcome = run_checks("t1", specs, sandbox=stub)
    assert not outcome.all_passed
    assert "needle" in outcome.results[0].failure_reason


def test_run_checks_regex_stdout():
    specs = [CheckSpec(name="re", cmd=["e"], expect_stdout_regex=r"^line-\d+$")]
    stub = StubSandbox({"e": ExecResult(("docker",), 0, "line-42", "")})
    outcome = run_checks("t1", specs, sandbox=stub)
    assert outcome.all_passed


def test_run_checks_stderr_predicates():
    specs = [
        CheckSpec(
            name="err",
            cmd=["e"],
            expect_exit=None,
            expect_stderr_contains="WARN",
        ),
    ]
    stub = StubSandbox({"e": ExecResult(("docker",), 1, "", "WARN: thing\n")})
    outcome = run_checks("t1", specs, sandbox=stub)
    assert outcome.all_passed  # exit ignored, stderr matched


def test_run_checks_fail_fast_skips_remaining():
    specs = [
        CheckSpec(name="first", cmd=["bad"]),
        CheckSpec(name="second", cmd=["never"]),
    ]
    stub = StubSandbox(
        {
            "bad": ExecResult(("docker",), 1, "", ""),
            "never": ExecResult(("docker",), 0, "", ""),
        }
    )
    outcome = run_checks("t1", specs, sandbox=stub, fail_fast=True)
    assert not outcome.all_passed
    assert [r.name for r in outcome.results] == ["first"]


def test_failure_summary_lists_only_failed():
    outcome = VerifyOutcome(
        task_id="t",
        all_passed=False,
        results=[
            CheckResult(
                name="a", passed=True, exit_code=0, stdout="", stderr=""
            ),
            CheckResult(
                name="b",
                passed=False,
                exit_code=1,
                stdout="",
                stderr="",
                failure_reason="exit non-zero",
            ),
        ],
    )
    summary = outcome.failure_summary()
    assert "b: exit non-zero" in summary
    assert "a" not in summary.split("\n")[1]  # only one failure listed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_run_pass_exits_zero(monkeypatch, capsys, tmp_path):
    p = _write_yaml(
        tmp_path,
        """
checks:
  - name: ok
    cmd: ["true"]
""",
    )
    stub = StubSandbox({"true": ExecResult(("docker",), 0, "", "")})
    monkeypatch.setattr(
        "vexis_agent.tools.verify.cli.run_checks",
        lambda task_id, specs, fail_fast=False: run_checks(
            task_id, specs, sandbox=stub, fail_fast=fail_fast
        ),
    )
    rc = cli_main(["run", "tid", "--checks", str(p)])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 0
    assert out["ok"] is True
    assert out["result"]["all_passed"] is True


def test_cli_run_fail_exits_one(monkeypatch, capsys, tmp_path):
    p = _write_yaml(
        tmp_path,
        """
checks:
  - name: bad
    cmd: ["false"]
""",
    )
    stub = StubSandbox({"false": ExecResult(("docker",), 1, "", "")})
    monkeypatch.setattr(
        "vexis_agent.tools.verify.cli.run_checks",
        lambda task_id, specs, fail_fast=False: run_checks(
            task_id, specs, sandbox=stub, fail_fast=fail_fast
        ),
    )
    rc = cli_main(["run", "tid", "--checks", str(p)])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    # ok=True at the CLI envelope level (it ran), but the inner result
    # all_passed is False, and the CLI process exit is 1 so vexis-bg can
    # easily branch on it without parsing JSON.
    assert rc == 1
    assert out["result"]["all_passed"] is False


def test_cli_run_missing_file_exits_two(monkeypatch, capsys, tmp_path):
    rc = cli_main(["run", "tid", "--checks", str(tmp_path / "absent.yaml")])
    out = json.loads(capsys.readouterr().out.strip())
    assert rc == 2
    assert out["ok"] is False


def test_cli_template_writes_starter(tmp_path, capsys):
    target = tmp_path / "checks.yaml"
    rc = cli_main(["template", "--path", str(target)])
    out = json.loads(capsys.readouterr().out.strip())
    assert rc == 0
    assert out["ok"] is True
    assert target.exists()
    body = target.read_text()
    assert "checks:" in body
    assert "tests-pass" in body


def test_cli_template_refuses_overwrite_without_force(tmp_path, capsys):
    target = tmp_path / "checks.yaml"
    target.write_text("preexisting")
    rc = cli_main(["template", "--path", str(target)])
    out = json.loads(capsys.readouterr().out.strip())
    assert rc == 2
    assert "force" in out["error"]
    assert target.read_text() == "preexisting"

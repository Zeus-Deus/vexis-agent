"""``vexis-verify`` console script.

Wraps :mod:`vexis_agent.tools.verify.checks` for shell callers (and
``vexis-bg``'s post-claim hook). Output is a single JSON object on
stdout, mirroring the sandbox CLI's style.

Exit code summary:

* 0 — all checks passed
* 1 — one or more checks failed (the JSON shows which)
* 2 — invalid args / missing checks file / corrupt YAML
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .checks import (
    ChecksFileInvalid,
    ChecksFileNotFound,
    DEFAULT_CHECKS_FILENAME,
    VerifyError,
    load_checks,
    run_checks,
)


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def _fail(msg: str, exit_code: int = 1) -> int:
    _emit({"ok": False, "error": msg})
    return exit_code


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        specs = load_checks(args.checks)
    except ChecksFileNotFound as exc:
        return _fail(str(exc), exit_code=2)
    except ChecksFileInvalid as exc:
        return _fail(str(exc), exit_code=2)
    except VerifyError as exc:
        return _fail(str(exc), exit_code=2)

    try:
        outcome = run_checks(args.task_id, specs, fail_fast=args.fail_fast)
    except VerifyError as exc:
        return _fail(str(exc))

    _emit({"ok": True, "result": outcome.to_dict()})
    return 0 if outcome.all_passed else 1


def _cmd_template(args: argparse.Namespace) -> int:
    """Write a starter checks.yaml the agent can edit. Handy first
    step in a build-and-test task — the agent can dump the template,
    fill in the assertions for its specific task, save back, and the
    verify hook reads from there."""
    target = Path(args.path) if args.path else Path(DEFAULT_CHECKS_FILENAME)
    if target.exists() and not args.force:
        return _fail(
            f"refusing to overwrite {target} (pass --force to clobber)",
            exit_code=2,
        )
    target.write_text(_TEMPLATE)
    _emit({"ok": True, "result": {"path": str(target)}})
    return 0


_TEMPLATE = """# checks.yaml — Vexis post-claim verification.
#
# Each entry runs inside the sandbox the agent worked in. The default
# success predicate is `exit_code == 0`; set `expect_exit: null` to
# skip the exit check when only stdout content matters.

checks:
  - name: tests-pass
    description: "Run the project's test suite."
    cmd: ["sh", "-c", "cd /workspace && cargo test"]
    expect_exit: 0

  # - name: binary-exists
  #   cmd: ["test", "-f", "/workspace/target/debug/myapp"]
  #
  # - name: greeting-printed
  #   cmd: ["/workspace/target/debug/myapp", "--greet"]
  #   expect_stdout_contains: "hello"
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vexis-verify",
        description=(
            "Run post-claim acceptance checks inside a vexis-sandbox. "
            "Used by vexis-bg's done-hook and callable by hand."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Execute the checks file inside the sandbox.")
    p_run.add_argument("task_id")
    p_run.add_argument(
        "--checks",
        default=DEFAULT_CHECKS_FILENAME,
        help="Path to the YAML check spec (default: ./checks.yaml).",
    )
    p_run.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop on the first failing check instead of running all.",
    )
    p_run.set_defaults(func=_cmd_run)

    p_tpl = sub.add_parser(
        "template", help="Write a starter checks.yaml the agent can edit."
    )
    p_tpl.add_argument(
        "--path",
        default=None,
        help=f"Where to write the template (default: ./{DEFAULT_CHECKS_FILENAME}).",
    )
    p_tpl.add_argument(
        "--force",
        action="store_true",
        help="Overwrite any existing file at the target path.",
    )
    p_tpl.set_defaults(func=_cmd_template)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

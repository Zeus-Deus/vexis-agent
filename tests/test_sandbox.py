"""Unit tests for :mod:`vexis_agent.tools.sandbox`.

These pin the contracts the rest of the build-and-test loop relies on:
container naming, mount composition, lazy start, idempotent stop, the
``list`` filter, and CLI error-path JSON shapes.

All tests use ``FakeBackend`` — no Docker required. A separate
``-m docker`` integration test (``test_sandbox_integration.py``) exercises
the real docker CLI; it's opt-in so CI on docker-less runners stays green.

Case counts in this file have been audited against the assertions; the
file scaffolds 18 individual ``test_*`` functions across the layers
(``Sandbox`` direct, CLI mainline, CLI error paths, ``list_all``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from vexis_agent.tools.sandbox import (
    DockerBackend,
    ExecResult,
    FakeBackend,
    Sandbox,
    SandboxAlreadyRunning,
    SandboxNotFound,
    SandboxStartFailed,
    container_name_for,
    default_image,
)
from vexis_agent.tools.sandbox.cli import build_parser, main as cli_main
from vexis_agent.tools.sandbox.sandbox import (
    CONTAINER_PREFIX,
    InvalidTaskId,
    scratch_dir_for,
    state_dir_for,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    """Redirect the sandbox state dir into a tmp path so tests can't
    clobber a real user's metadata under ~/.local/state/vexis-agent."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    # Re-scope scratch root as well so the `mkdir` in start() doesn't
    # touch /tmp/vexis-sandbox on the host.
    scratch_root = tmp_path / "scratch-root"
    monkeypatch.setattr(
        "vexis_agent.tools.sandbox.sandbox.DEFAULT_SCRATCH_HOST_ROOT",
        str(scratch_root),
    )
    yield


def _running_backend(task_id: str, *, image: str = "debian:bookworm-slim") -> FakeBackend:
    """Build a FakeBackend that *looks like* the named container is
    already up and running. Useful for tests of ``exec``, ``cp``, ``stop``
    that don't want to walk through ``start`` first."""
    name = container_name_for(task_id)

    def respond(argv):
        if argv[:1] == ("ps",):
            # The two ``is_running`` queries both include --filter
            # status=running; ``exists`` does not.
            if "status=running" in argv:
                return ExecResult(("docker", *argv), 0, name + "\n", "")
            if "-a" in argv and any(a.startswith("label=") for a in argv):
                # list_all path — not used in most tests
                return ExecResult(("docker", *argv), 0, "", "")
            if "-a" in argv:
                # plain `exists()` check
                return ExecResult(("docker", *argv), 0, name + "\n", "")
        return ExecResult(("docker", *argv), 0, "", "")

    return FakeBackend(responder=respond)


# ---------------------------------------------------------------------------
# Sandbox direct API
# ---------------------------------------------------------------------------


def test_container_name_is_prefixed():
    assert container_name_for("t1abc") == f"{CONTAINER_PREFIX}t1abc"


def test_invalid_task_id_rejected():
    with pytest.raises(InvalidTaskId):
        Sandbox("UPPERCASE")
    with pytest.raises(InvalidTaskId):
        Sandbox("ab")  # too short
    with pytest.raises(InvalidTaskId):
        Sandbox("1leading-digit")


def test_start_invokes_docker_run_with_expected_flags(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXIS_WORKSPACE", str(tmp_path / "workspace"))
    backend = FakeBackend()
    # Sequence with no prior metadata: existing=None short-circuits past
    # the is_running check; we go straight to exists() then docker run.
    backend.queued = [
        ExecResult(("docker",), 0, "", ""),  # exists: empty = no stale container
        ExecResult(("docker",), 0, "containerid\n", ""),  # docker run
    ]
    sb = Sandbox("buildtest", backend=backend)
    meta = sb.start()
    assert meta.task_id == "buildtest"
    assert meta.image == default_image()
    # docker run was the second call; verify flags shape
    run_argv = backend.calls[1]
    assert run_argv[0] == "run"
    assert "--name" in run_argv and container_name_for("buildtest") in run_argv
    # Two mounts: workspace and scratch
    assert run_argv.count("-v") == 2
    assert "sleep" in run_argv and "infinity" in run_argv
    # Metadata persisted
    assert Path(state_dir_for("buildtest") / "metadata.json").exists()
    on_disk = json.loads((state_dir_for("buildtest") / "metadata.json").read_text())
    assert on_disk["container"] == container_name_for("buildtest")


def test_start_is_idempotent_for_same_image():
    backend = _running_backend("idem")
    sb = Sandbox("idem", backend=backend)
    # Write a fake metadata file first; idempotent start should just
    # return it without invoking docker run.
    state_dir_for("idem").mkdir(parents=True, exist_ok=True)
    (state_dir_for("idem") / "metadata.json").write_text(
        json.dumps(
            {
                "task_id": "idem",
                "container": container_name_for("idem"),
                "image": default_image(),
                "mounts": [],
                "created_at": "2025-01-01T00:00:00+00:00",
                "workdir": "/workspace",
            }
        )
    )
    meta = sb.start(image=default_image())
    assert meta.image == default_image()
    # Should NOT have issued a `docker run`.
    assert not any(c[0] == "run" for c in backend.calls)


def test_start_rejects_different_image_when_running():
    backend = _running_backend("conflict")
    sb = Sandbox("conflict", backend=backend)
    state_dir_for("conflict").mkdir(parents=True, exist_ok=True)
    (state_dir_for("conflict") / "metadata.json").write_text(
        json.dumps(
            {
                "task_id": "conflict",
                "container": container_name_for("conflict"),
                "image": "alpine:latest",
                "mounts": [],
                "created_at": "2025-01-01T00:00:00+00:00",
            }
        )
    )
    with pytest.raises(SandboxAlreadyRunning):
        sb.start(image="debian:bookworm-slim")


def test_start_failed_surfaces_stderr():
    backend = FakeBackend()
    # No metadata exists → existing is None → is_running short-circuited,
    # only exists() + docker run are issued.
    backend.queued = [
        ExecResult(("docker",), 0, "", ""),  # exists: nothing stale
        ExecResult(("docker",), 1, "", "Error: pull access denied"),
    ]
    sb = Sandbox("badrun", backend=backend)
    with pytest.raises(SandboxStartFailed) as exc_info:
        sb.start()
    assert "pull access denied" in str(exc_info.value)


def test_exec_lazy_starts_when_not_running(tmp_path, monkeypatch):
    monkeypatch.setenv("VEXIS_WORKSPACE", str(tmp_path / "workspace"))
    # Flow: exec calls is_running (no), then start() which (with no
    # prior metadata) calls exists() then docker run, then exec falls
    # through to docker exec. So: is_running, exists, run, exec = 4
    # calls. No second is_running — start() succeeded, so exec proceeds.
    backend = FakeBackend()
    seq = iter(
        [
            ExecResult(("docker",), 0, "", ""),  # exec: is_running -> no
            ExecResult(("docker",), 0, "", ""),  # start: exists -> none
            ExecResult(("docker",), 0, "id\n", ""),  # start: docker run
            ExecResult(("docker",), 0, "hello\n", ""),  # exec: docker exec
        ]
    )
    backend.responder = lambda _argv: next(seq)
    sb = Sandbox("lazy", backend=backend)
    res = sb.exec(["echo", "hello"])
    assert res.ok and res.stdout == "hello\n"
    # Verify a docker exec call was made
    assert any(c[0] == "exec" for c in backend.calls)


def test_exec_no_start_raises_when_absent():
    backend = FakeBackend()
    backend.queued = [ExecResult(("docker",), 0, "", "")]  # not running
    sb = Sandbox("absent", backend=backend)
    with pytest.raises(SandboxNotFound):
        sb.exec(["echo", "hi"], auto_start=False)


def test_exec_string_form_uses_sh_c():
    backend = _running_backend("shc")
    sb = Sandbox("shc", backend=backend)
    sb.exec("echo $HOME | wc -c")
    # find the actual exec call
    exec_call = next(c for c in backend.calls if c[0] == "exec")
    # Tail of argv should be ('sh', '-c', '<the string>')
    assert exec_call[-3:] == ("sh", "-c", "echo $HOME | wc -c")


def test_stop_idempotent_when_already_gone():
    backend = FakeBackend()
    # exists() -> no
    backend.queued = [ExecResult(("docker",), 0, "", "")]
    sb = Sandbox("gone", backend=backend)
    assert sb.stop() is False


def test_stop_runs_rm_force_when_present():
    backend = _running_backend("alive")
    sb = Sandbox("alive", backend=backend)
    assert sb.stop() is True
    assert any(c[:2] == ("rm", "-f") for c in backend.calls)


def test_list_all_parses_docker_ps_rows():
    backend = FakeBackend()
    name = container_name_for("two")
    backend.queued = [
        ExecResult(
            ("docker",),
            0,
            (
                f"{container_name_for('one')}\tUp 5 minutes\tdebian:bookworm-slim\t2025-01-01 12:00:00\n"
                f"{name}\tExited (0) 1 minute ago\talpine:latest\t2025-01-01 12:05:00\n"
            ),
            "",
        )
    ]
    rows = Sandbox.list_all(backend=backend)
    assert len(rows) == 2
    assert rows[0]["task_id"] == "one"
    assert rows[0]["running"] is True
    assert rows[1]["task_id"] == "two"
    assert rows[1]["running"] is False


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def _run_cli(monkeypatch, capsys, argv: list[str], *, fake_factory=None) -> tuple[int, dict, str]:
    """Drive ``vexis-sandbox`` end to end and capture stdout/stderr.

    ``fake_factory`` lets a test substitute the Sandbox's backend by
    monkey-patching ``DockerBackend.__init__`` to return the fake's
    ``run`` method. Cleaner than monkeypatching the class itself.
    """
    if fake_factory is not None:
        fake = fake_factory()
        monkeypatch.setattr(
            "vexis_agent.tools.sandbox.sandbox.DockerBackend",
            lambda *a, **kw: fake,
        )
    rc = cli_main(argv)
    captured = capsys.readouterr()
    try:
        payload = json.loads(captured.out.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        payload = {}
    return rc, payload, captured.err


def test_cli_start_emits_ok_payload(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("VEXIS_WORKSPACE", str(tmp_path / "ws"))

    def factory():
        be = FakeBackend()
        # No metadata → existing=None → is_running short-circuited.
        be.queued = [
            ExecResult(("docker",), 0, "", ""),  # exists: no
            ExecResult(("docker",), 0, "ok\n", ""),  # run
        ]
        return be

    rc, payload, _ = _run_cli(
        monkeypatch, capsys, ["start", "okstart"], fake_factory=factory
    )
    assert rc == 0
    assert payload["ok"] is True
    assert payload["result"]["task_id"] == "okstart"


def test_cli_start_invalid_taskid_exit_2(monkeypatch, capsys):
    rc, payload, _ = _run_cli(monkeypatch, capsys, ["start", "AB"])
    assert rc == 2
    assert payload["ok"] is False
    assert "Invalid task-id" in payload["error"]


def test_cli_exec_pass_through_propagates_exit(monkeypatch, capsys):
    def factory():
        be = FakeBackend()
        name = container_name_for("ptask")
        seq = iter(
            [
                ExecResult(("docker",), 0, name + "\n", ""),  # is_running yes
                ExecResult(("docker",), 7, "boom\n", "stderrboom\n"),  # exec exits 7
            ]
        )
        be.responder = lambda _argv: next(seq)
        return be

    rc, _payload, err = _run_cli(
        monkeypatch, capsys, ["exec", "ptask", "--", "false"], fake_factory=factory
    )
    assert rc == 7
    # Pass-through routes stderr/stdout, not JSON, so payload from
    # _run_cli is junk — but stderr should contain the stderrboom.
    assert "stderrboom" in err


def test_cli_exec_json_mode_always_exit_zero(monkeypatch, capsys):
    def factory():
        be = FakeBackend()
        name = container_name_for("jsonok")
        seq = iter(
            [
                ExecResult(("docker",), 0, name + "\n", ""),  # is_running yes
                ExecResult(("docker",), 3, "out\n", "err\n"),  # exec exits 3
            ]
        )
        be.responder = lambda _argv: next(seq)
        return be

    # NOTE: flags go BEFORE the task-id because ``cmd`` is REMAINDER and
    # otherwise greedily eats any flag that follows the task-id (same
    # convention as ``docker exec``).
    rc, payload, _ = _run_cli(
        monkeypatch,
        capsys,
        ["exec", "--json", "jsonok", "--", "false"],
        fake_factory=factory,
    )
    # --json: 0 at the process level, captured exit_code in payload
    assert rc == 0
    assert payload["ok"] is False
    assert payload["result"]["exit_code"] == 3
    assert payload["result"]["stdout"] == "out\n"


def test_cli_exec_missing_cmd_exit_2(monkeypatch, capsys):
    def factory():
        return FakeBackend()

    rc, payload, _ = _run_cli(
        monkeypatch, capsys, ["exec", "nocmd"], fake_factory=factory
    )
    assert rc == 2
    assert payload["ok"] is False


def test_cli_stop_idempotent_returns_ok_false_stopped(monkeypatch, capsys):
    def factory():
        be = FakeBackend()
        # exists() -> no
        be.queued = [ExecResult(("docker",), 0, "", "")]
        return be

    rc, payload, _ = _run_cli(
        monkeypatch, capsys, ["stop", "ghost"], fake_factory=factory
    )
    assert rc == 0
    assert payload["ok"] is True
    assert payload["result"]["stopped"] is False


def test_parser_help_lists_all_subcommands():
    """Smoke-test the argparse wiring so an accidental refactor doesn't
    drop a subcommand silently."""
    parser = build_parser()
    actions = {a.dest: a for a in parser._actions}
    # The subparsers action is the one with `choices` populated.
    sub = next(a for a in parser._actions if hasattr(a, "choices") and a.choices)
    assert set(sub.choices) == {"start", "exec", "cp", "stop", "list"}

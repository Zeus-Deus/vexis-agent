"""Tests for the ``vexis-bg`` CLI arg forwarding (tools/background_cli.py).

The CLI is a thin client: parse argv → build a ``bg_spawn`` op args dict
→ send it over the daemon control socket. We stub ``_send`` to capture
the args dict and assert the optional ``--model`` override is forwarded
only when set (absent key → daemon uses the account default).
"""

from __future__ import annotations

import pytest

from vexis_agent.tools import background_cli as cli


@pytest.fixture
def captured_send(monkeypatch):
    """Replace ``_send`` with a capture stub and set VEXIS_CHAT_ID so
    ``_resolve_chat_id`` doesn't exit. Returns the list of (op, args)."""
    calls: list[tuple[str, dict]] = []

    def fake_send(op: str, args: dict, timeout: float = 10.0) -> dict:
        calls.append((op, args))
        return {"ok": True, "result": {"name": args.get("name")}}

    monkeypatch.setattr(cli, "_send", fake_send)
    monkeypatch.setenv("VEXIS_CHAT_ID", "4242")
    return calls


def _run(monkeypatch, argv: list[str]) -> int:
    monkeypatch.setattr("sys.argv", ["vexis-bg", *argv])
    return cli.main()


def test_spawn_forwards_model_when_set(monkeypatch, captured_send):
    rc = _run(monkeypatch, ["spawn", "do-thing", "go do it", "--model", "opus"])
    assert rc == 0
    op, args = captured_send[-1]
    assert op == "bg_spawn"
    assert args["model"] == "opus"
    assert args["chat_id"] == 4242
    assert args["name"] == "do-thing"
    assert args["prompt"] == "go do it"


def test_spawn_omits_model_key_when_absent(monkeypatch, captured_send):
    rc = _run(monkeypatch, ["spawn", "do-thing", "go do it"])
    assert rc == 0
    _op, args = captured_send[-1]
    # No --model → the key must be absent so the daemon applies its
    # default (account-default model), not an empty/None override.
    assert "model" not in args


def test_spawn_model_composes_with_sandbox(monkeypatch, captured_send):
    rc = _run(
        monkeypatch,
        ["spawn", "build-it", "compile", "--model", "sonnet", "--sandbox"],
    )
    assert rc == 0
    _op, args = captured_send[-1]
    assert args["model"] == "sonnet"
    assert args["sandbox"] is True

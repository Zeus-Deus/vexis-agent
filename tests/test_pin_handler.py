"""Handler-level tests for /pin, /unpin, and the curator controller's
slash-command dispatch."""

from __future__ import annotations

from pathlib import Path

import pytest

from vexis_agent.core.curator import CuratorController
from vexis_agent.core.handler import MessageHandler
from vexis_agent.core.sessions import SessionStore
from vexis_agent.core.skills import PinStore


class _FakeBrain:
    async def respond(self, message: str, chat_id: int) -> str:  # pragma: no cover
        return ""


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "skills").mkdir()
    return ws


@pytest.fixture
def handler(workspace: Path, tmp_path: Path) -> MessageHandler:
    sessions = SessionStore(state_path=tmp_path / "session.json")
    return MessageHandler(
        brain=_FakeBrain(),
        sessions=sessions,
        allowed_user_id=42,
        workspace=workspace,
    )


@pytest.mark.anyio("asyncio")
async def test_pin_rejects_unauthorized(handler: MessageHandler):
    reply = await handler.handle_pin(user_id=999, name="alpha")
    assert reply is None  # silent rejection


@pytest.mark.anyio("asyncio")
async def test_pin_requires_name(handler: MessageHandler):
    reply = await handler.handle_pin(user_id=42, name="")
    assert reply is not None
    assert "Usage" in reply


@pytest.mark.anyio("asyncio")
async def test_pin_first_time_succeeds(handler: MessageHandler, workspace: Path):
    reply = await handler.handle_pin(user_id=42, name="alpha")
    assert reply is not None
    assert "Pinned" in reply
    assert PinStore(workspace / "skills").is_pinned("alpha")


@pytest.mark.anyio("asyncio")
async def test_pin_idempotent(handler: MessageHandler):
    await handler.handle_pin(user_id=42, name="alpha")
    second = await handler.handle_pin(user_id=42, name="alpha")
    assert second is not None
    assert "already pinned" in second


@pytest.mark.anyio("asyncio")
async def test_unpin_round_trip(handler: MessageHandler, workspace: Path):
    await handler.handle_pin(user_id=42, name="alpha")
    reply = await handler.handle_unpin(user_id=42, name="alpha")
    assert reply is not None
    assert "Unpinned" in reply
    assert not PinStore(workspace / "skills").is_pinned("alpha")


@pytest.mark.anyio("asyncio")
async def test_unpin_missing(handler: MessageHandler):
    reply = await handler.handle_unpin(user_id=42, name="alpha")
    assert reply is not None
    assert "not pinned" in reply


# ---------- /curator dispatch ----------


@pytest.mark.anyio("asyncio")
async def test_curator_status_reports_state(workspace: Path):
    ctrl = CuratorController(workspace=workspace)
    text = await ctrl.handle_telegram("status", [])
    assert "Curator:" in text
    assert "Last run:" in text


@pytest.mark.anyio("asyncio")
async def test_curator_pause_resume_round_trip(workspace: Path, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    ctrl = CuratorController(workspace=workspace)
    p = await ctrl.handle_telegram("pause", [])
    assert "paused" in p.lower()
    r = await ctrl.handle_telegram("resume", [])
    assert "resumed" in r.lower()


@pytest.mark.anyio("asyncio")
async def test_curator_restore_lists_when_no_arg(workspace: Path):
    ctrl = CuratorController(workspace=workspace)
    text = await ctrl.handle_telegram("restore", [])
    assert "No archived skills" in text

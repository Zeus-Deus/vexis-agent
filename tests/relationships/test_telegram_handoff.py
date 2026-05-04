"""v3b Day 3a: session_uuid handoff (Option B).

Covers:

- ``MessageHandler.next_user_turn_index`` correctly counts
  user-role lines in ``<encoded-cwd>/<session_uuid>.jsonl``.
  Edge cases: empty / no-user-lines / 5-user-lines / file
  doesn't exist.
- ``MessageHandler.claim_next_turn_index`` cursor-collision
  semantics: refuses on JSONL-not-advanced, restart-derived
  on next call.
- Atomicity: two parallel ``claim_next_turn_index`` calls
  cannot both pass the check.
- Transport hook fires INSIDE ``_drain_chat`` (per-iteration),
  not before claim. The brain receives the user's text after
  the hook reply lands.
- Real session_uuid is threaded through, NOT
  ``telegram-chat-{chat_id}``.
- Cursor-collision refusal: if the JSONL didn't advance, the
  next hook fire returns None, no shadow entry, no token,
  brain still proceeds.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from core.handler import MessageHandler
from core.transcripts import claude_session_jsonl_dir


# ---------------------------------------------------------------- helpers


def _write_jsonl(path: Path, *, user_count: int, assistant_count: int = 0) -> None:
    """Write a synthetic Claude-Code session JSONL with ``user_count``
    user-role messages and ``assistant_count`` assistant-role ones,
    interleaved alternately. Timestamps increment by 1 second per line.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    base = "2026-05-04T12:00:0{}Z"
    n = max(user_count, assistant_count)
    seq = 0
    for i in range(n):
        if i < user_count:
            lines.append(json.dumps({
                "type": "user",
                "uuid": f"u-{i}",
                "timestamp": base.format(seq),
                "message": {"role": "user", "content": f"user msg {i}"},
            }))
            seq += 1
        if i < assistant_count:
            lines.append(json.dumps({
                "type": "assistant",
                "uuid": f"a-{i}",
                "timestamp": base.format(seq),
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": f"asst msg {i}"}],
                },
            }))
            seq += 1
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


class _FakeSessionStore:
    """Minimal SessionStore stand-in — only `.get()` is used by
    MessageHandler's relationships accessors."""

    def __init__(self, uuid: str) -> None:
        self._uuid = uuid

    def get(self) -> str:
        return self._uuid


class _FakeBrain:
    async def respond(self, message: str, chat_id: int) -> str:
        return "brain-reply"


def _build_handler(workspace: Path, session_uuid: str) -> MessageHandler:
    """Construct a MessageHandler stripped of brain/notifier — just
    the SessionStore + workspace are needed for the accessors."""
    return MessageHandler(
        brain=_FakeBrain(),  # type: ignore[arg-type]
        sessions=_FakeSessionStore(session_uuid),  # type: ignore[arg-type]
        allowed_user_id=99,
        notifier=None,
        workspace=workspace,
    )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr("pathlib.Path.home", classmethod(lambda cls: home))
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


# ----------------------------------------------- next_user_turn_index


def test_next_user_turn_index_no_jsonl_returns_one(workspace: Path):
    h = _build_handler(workspace, "sess-fresh")
    assert h.next_user_turn_index("sess-fresh") == 1


def test_next_user_turn_index_zero_users_only_assistants(workspace: Path):
    pdir = claude_session_jsonl_dir(workspace)
    _write_jsonl(pdir / "sess-a.jsonl", user_count=0, assistant_count=2)
    h = _build_handler(workspace, "sess-a")
    assert h.next_user_turn_index("sess-a") == 1


def test_next_user_turn_index_five_user_lines(workspace: Path):
    pdir = claude_session_jsonl_dir(workspace)
    _write_jsonl(pdir / "sess-b.jsonl", user_count=5, assistant_count=4)
    h = _build_handler(workspace, "sess-b")
    assert h.next_user_turn_index("sess-b") == 6


def test_next_user_turn_index_workspace_none_returns_one(tmp_path: Path):
    """Test fixture path: handler constructed without workspace."""
    h = MessageHandler(
        brain=_FakeBrain(),  # type: ignore[arg-type]
        sessions=_FakeSessionStore("sess-x"),  # type: ignore[arg-type]
        allowed_user_id=99,
        notifier=None,
        workspace=None,
    )
    assert h.next_user_turn_index("sess-x") == 1


# ----------------------------------------------- claim_next_turn_index


def test_claim_first_call_returns_one_no_jsonl(workspace: Path):
    h = _build_handler(workspace, "sess-c")
    got = asyncio.run(h.claim_next_turn_index("sess-c"))
    assert got == 1


def test_claim_advances_after_jsonl_grows(workspace: Path):
    """Successful brain dispatch grows the JSONL by one user line; the
    next claim returns one past the new count."""
    pdir = claude_session_jsonl_dir(workspace)
    jsonl = pdir / "sess-d.jsonl"
    _write_jsonl(jsonl, user_count=0)  # JSONL exists with 0 user lines

    h = _build_handler(workspace, "sess-d")
    first = asyncio.run(h.claim_next_turn_index("sess-d"))
    assert first == 1

    # Simulate brain success: JSONL gains one user line.
    _write_jsonl(jsonl, user_count=1)
    second = asyncio.run(h.claim_next_turn_index("sess-d"))
    assert second == 2


def test_claim_collision_returns_none_when_jsonl_did_not_advance(workspace: Path):
    """The brain-error edge case: hook minted at index 1, brain
    didn't write, next hook should refuse."""
    h = _build_handler(workspace, "sess-e")
    first = asyncio.run(h.claim_next_turn_index("sess-e"))
    assert first == 1
    # JSONL didn't advance — second call sees proposed=1 again.
    second = asyncio.run(h.claim_next_turn_index("sess-e"))
    assert second is None


def test_claim_recovers_after_collision_when_jsonl_finally_advances(
    workspace: Path,
):
    """After a brain error + retry that succeeds, subsequent claims
    proceed at the right index."""
    pdir = claude_session_jsonl_dir(workspace)
    jsonl = pdir / "sess-f.jsonl"

    h = _build_handler(workspace, "sess-f")
    # First mint (cursor=1), brain errored — JSONL still empty.
    assert asyncio.run(h.claim_next_turn_index("sess-f")) == 1
    # Collision on retry (msg2 at same proposed=1).
    assert asyncio.run(h.claim_next_turn_index("sess-f")) is None
    # User retries the trigger; this time the brain wrote — the
    # JSONL advanced. Cursor should accept (msg3 at proposed=2 > last=1).
    _write_jsonl(jsonl, user_count=1)
    assert asyncio.run(h.claim_next_turn_index("sess-f")) == 2


def test_claim_cursor_is_per_session_uuid(workspace: Path):
    """Switching sessions doesn't carry the cursor over."""
    h = _build_handler(workspace, "sess-g")
    assert asyncio.run(h.claim_next_turn_index("sess-g")) == 1
    # A different session_uuid starts at its own cursor=0 default.
    assert asyncio.run(h.claim_next_turn_index("sess-h")) == 1


def test_claim_restart_safety(workspace: Path):
    """Cursor lives in memory; on a fresh handler instance the cursor
    is empty and ``next_user_turn_index`` rebuilds from the JSONL."""
    pdir = claude_session_jsonl_dir(workspace)
    jsonl = pdir / "sess-i.jsonl"
    _write_jsonl(jsonl, user_count=5)

    # First handler "lifetime"
    h1 = _build_handler(workspace, "sess-i")
    assert asyncio.run(h1.claim_next_turn_index("sess-i")) == 6

    # Daemon restart: a brand-new handler with no remembered state.
    h2 = _build_handler(workspace, "sess-i")
    # Pretend the brain wrote its turn before the restart, so JSONL
    # now has 6 user lines.
    _write_jsonl(jsonl, user_count=6)
    assert asyncio.run(h2.claim_next_turn_index("sess-i")) == 7


def test_claim_atomicity_under_concurrent_callers(workspace: Path):
    """Two parallel claims for the same session must not both pass.

    The drain loop already serialises per-chat in production, so this
    is defense in depth: ``claim_next_turn_index`` wraps the
    read-modify-write in ``_cursor_lock``. We exercise the lock by
    issuing many concurrent claims; exactly one should win the slot
    for proposed=1, the rest should see proposed <= last and refuse.
    """
    h = _build_handler(workspace, "sess-j")

    async def scenario() -> list[int | None]:
        return await asyncio.gather(
            *[h.claim_next_turn_index("sess-j") for _ in range(8)]
        )

    results = asyncio.run(scenario())
    # Exactly one returned 1; the other seven returned None because
    # by the time they ran, the cursor was already at 1.
    granted = [r for r in results if r is not None]
    refused = [r for r in results if r is None]
    assert len(granted) == 1
    assert granted[0] == 1
    assert len(refused) == 7


# ----------------------------------------------- transport-level handoff


class _FakeBot:
    def __init__(self) -> None:
        self.sent_messages: list[tuple[int, str]] = []
        self.typing_calls = 0

    async def send_chat_action(self, _chat_id: int, _action: Any) -> None:
        self.typing_calls += 1

    async def send_message(
        self, *, chat_id: int, text: str, parse_mode: Any = None, **_kw: Any
    ) -> None:
        self.sent_messages.append((chat_id, text))


class _RecordingRelationships:
    """RelationshipsCurator stand-in that records the (session_uuid,
    turn_index) it was called with and returns a stub TurnLevelResult."""

    def __init__(self, reply_text: str | None) -> None:
        from core.relationships.curator import TurnLevelResult
        self.calls: list[tuple[str, int]] = []
        self._reply_text = reply_text
        self._result_cls = TurnLevelResult
        self.counter_increments: list[str] = []

    async def process_user_turn(
        self,
        text: str,
        *,
        session_uuid: str,
        turn_index: int,
        chat_id: int | None = None,
    ):
        self.calls.append((session_uuid, turn_index))
        if self._reply_text is None:
            return self._result_cls(staged=False, reply_text=None)
        return self._result_cls(
            staged=True,
            reply_text=self._reply_text,
            person_slug="sarah",
            fact_count=1,
            verdict="ADD",
            matched=True,
        )

    def increment_counter(self, name: str, by: int = 1) -> None:
        for _ in range(by):
            self.counter_increments.append(name)


class _FakeLearningController:
    def __init__(self, relationships) -> None:
        self.relationships_curator = relationships


def _make_transport_with_curator(
    handler, allowed_user_id: int, relationships
):
    from transports.telegram import TelegramTransport
    from core.running_tasks import RunningTasks
    t = TelegramTransport.__new__(TelegramTransport)
    t._handler = handler  # type: ignore[attr-defined]
    t._allowed_user_id = allowed_user_id  # type: ignore[attr-defined]
    t._running_tasks = RunningTasks()  # type: ignore[attr-defined]
    t._learning_curator = _FakeLearningController(relationships)  # type: ignore[attr-defined]
    return t


class _OrderingHandler:
    """Records the order of (hook_call, handle_call) so tests can
    assert receipt-then-reply ordering."""

    def __init__(self) -> None:
        self.order: list[str] = []
        self.last_text: str | None = None
        self.last_user_id: int | None = None
        self.last_chat_id: int | None = None
        self._handler_real = None  # set by helper

    async def handle(self, user_id: int, chat_id: int, text: str) -> str | None:
        self.order.append("handler")
        self.last_user_id = user_id
        self.last_chat_id = chat_id
        self.last_text = text
        return "brain-reply"


def test_hook_fires_inside_drain_with_real_session_uuid(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
):
    """End-to-end: relationships hook receives the brain's real
    session_uuid (NOT 'telegram-chat-...'), with a JSONL-derived
    turn_index. Hook reply arrives BEFORE brain reply on Telegram."""

    pdir = claude_session_jsonl_dir(workspace)
    _write_jsonl(pdir / "real-uuid-aaa.jsonl", user_count=2)

    # Real handler with a recording relationships stub.
    handler = _build_handler(workspace, "real-uuid-aaa")
    handler._brain = _FakeBrain()  # patched
    # Override handle so we can record ordering.
    ordering = _OrderingHandler()

    async def patched_handle(user_id: int, chat_id: int, text: str):
        ordering.order.append("handler")
        ordering.last_text = text
        return "brain-reply"

    handler.handle = patched_handle  # type: ignore[assignment]

    relationships = _RecordingRelationships(reply_text="Got it — staged Sarah.")
    transport = _make_transport_with_curator(handler, 99, relationships)
    bot = _FakeBot()

    asyncio.run(transport._dispatch_to_brain(bot, 42, 99, "remember Sarah likes jazz"))

    # Relationships called with the real session_uuid + correct turn_index.
    assert relationships.calls == [("real-uuid-aaa", 3)]

    # Bot received the staged-ack BEFORE the brain reply.
    sent_texts = [t for _cid, t in bot.sent_messages]
    assert sent_texts[0] == "Got it — staged Sarah."
    assert sent_texts[1] == "brain-reply"

    # The brain handler ran with the user's verbatim text (NOT the
    # staged-ack body).
    assert ordering.last_text == "remember Sarah likes jazz"


def test_cursor_collision_skips_staging_silently(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
):
    """If the JSONL doesn't advance between two hook fires, the
    second hook gets None back from claim_next_turn_index and
    skips staging — no relationships call, no Telegram receipt,
    brain still receives the user's text."""

    handler = _build_handler(workspace, "sess-coll")

    async def patched_handle(user_id: int, chat_id: int, text: str):
        return "brain-reply"

    handler.handle = patched_handle  # type: ignore[assignment]

    relationships = _RecordingRelationships(reply_text="Got it — staged Sarah.")
    transport = _make_transport_with_curator(handler, 99, relationships)
    bot = _FakeBot()

    # First message: mints at index 1, JSONL never advances (test
    # doesn't simulate a real claude -p write).
    asyncio.run(transport._dispatch_to_brain(bot, 42, 99, "msg1"))
    assert len(relationships.calls) == 1
    assert relationships.calls[0][1] == 1

    # Second message: cursor still at 1, JSONL still has 0 user
    # lines, claim_next_turn_index returns None. Hook short-circuits.
    asyncio.run(transport._dispatch_to_brain(bot, 42, 99, "msg2"))
    assert len(relationships.calls) == 1  # NO new call to relationships
    assert "cursor_collision" in relationships.counter_increments

    # The first ack was sent; no second ack on the collided turn.
    sent_texts = [t for _cid, t in bot.sent_messages]
    assert sent_texts.count("Got it — staged Sarah.") == 1
    # Brain reply still landed for both turns.
    assert sent_texts.count("brain-reply") == 2

"""v3b Day 3a: session_uuid handoff (Option B).

Covers:

- ``MessageHandler.next_user_turn_index`` correctly counts
  user-role turns in the active brain's session transcript.
  Edge cases: empty / no-user-turns / 5-user-turns / session
  has no transcript yet.
- ``MessageHandler.claim_next_turn_index`` cursor-collision
  semantics: refuses on transcript-not-advanced, restart-derived
  on next call.
- Atomicity: two parallel ``claim_next_turn_index`` calls
  cannot both pass the check.
- Transport hook fires INSIDE ``_drain_chat`` (per-iteration),
  not before claim. The brain receives the user's text after
  the hook reply lands.
- Real session_uuid is threaded through, NOT
  ``telegram-chat-{chat_id}``.
- Cursor-collision refusal: if the transcript didn't advance, the
  next hook fire returns None, no shadow entry, no token,
  brain still proceeds.

Brain parity: every turn-index test runs against BOTH real brains
(claude-code JSONL + opencode SQLite) via the ``brain`` fixture.
``next_user_turn_index`` routes through ``brain.iter_messages`` —
this suite is the regression guard that it never reads
``claude_session_jsonl_dir`` directly again.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from vexis_agent.core.brain.claude_code import ClaudeCodeBrain
from vexis_agent.core.brain.opencode import OpenCodeBrain
from vexis_agent.core.handler import MessageHandler
from vexis_agent.core.running_tasks import RunningTasks
from vexis_agent.core.sessions import SessionStore

from tests._brain_seed import seed_transcript


# ---------------------------------------------------------------- helpers


def _turns(user_count: int, assistant_count: int = 0) -> list[tuple[str, str]]:
    """Build an interleaved (role, text) turn list — ``user_count``
    user turns and ``assistant_count`` assistant turns, alternating
    user-first (mirrors the legacy ``_write_jsonl`` interleave)."""
    turns: list[tuple[str, str]] = []
    n = max(user_count, assistant_count)
    for i in range(n):
        if i < user_count:
            turns.append(("user", f"user msg {i}"))
        if i < assistant_count:
            turns.append(("assistant", f"asst msg {i}"))
    return turns


class _FakeSessionStore:
    """Minimal SessionStore stand-in — only `.get()` is used by
    MessageHandler's relationships accessors."""

    def __init__(self, uuid: str) -> None:
        self._uuid = uuid

    def get(self) -> str:
        return self._uuid


class _FakeBrainRespond:
    """Wraps a real brain but stubs ``respond`` so the transport-level
    tests never spawn a CLI. Transcript reads still hit the real
    brain's ``iter_messages``."""

    def __init__(self, real_brain: Any) -> None:
        self._real = real_brain

    async def respond(self, message: str, chat_id: int) -> str:
        return "brain-reply"

    def iter_messages(self, session_id: str):
        return self._real.iter_messages(session_id)


@pytest.fixture(params=["claude_code", "opencode"])
def brain(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    """A real brain (claude-code or opencode) constructed against tmp
    paths. ``seed_transcript`` lays down transcripts the brain's
    ``iter_messages`` reads back — JSONL for claude-code, opencode.db
    rows for opencode (the autouse ``_isolate_opencode_db`` fixture
    already points the reader at a tmp path)."""
    workspace = tmp_path / f"ws-{request.param}"
    workspace.mkdir(parents=True, exist_ok=True)
    session = SessionStore(tmp_path / "sessions.json")
    if request.param == "claude_code":
        return ClaudeCodeBrain(
            workspace=workspace,
            session=session,
            running_tasks=RunningTasks(),
        )
    return OpenCodeBrain(
        workspace=workspace,
        session=session,
        running_tasks=RunningTasks(),
    )


@pytest.fixture
def workspace(brain: Any) -> Path:
    """The handler's workspace — just needs to be non-None so the
    accessors take the brain-routed path rather than the
    workspace-None test shortcut."""
    return brain._workspace


def _build_handler(
    brain: Any, workspace: Path, session_uuid: str
) -> MessageHandler:
    """Construct a MessageHandler stripped of notifier — the real
    brain (for transcript reads) + SessionStore + workspace are what
    the relationships accessors need."""
    return MessageHandler(
        brain=brain,
        sessions=_FakeSessionStore(session_uuid),  # type: ignore[arg-type]
        allowed_user_id=99,
        notifier=None,
        workspace=workspace,
    )


# ----------------------------------------------- next_user_turn_index


def test_next_user_turn_index_no_transcript_returns_one(
    brain: Any, workspace: Path
):
    h = _build_handler(brain, workspace, "sess-fresh")
    assert h.next_user_turn_index("sess-fresh") == 1


def test_next_user_turn_index_zero_users_only_assistants(
    brain: Any, workspace: Path
):
    seed_transcript(brain, "sess-a", _turns(user_count=0, assistant_count=2))
    h = _build_handler(brain, workspace, "sess-a")
    assert h.next_user_turn_index("sess-a") == 1


def test_next_user_turn_index_five_user_lines(brain: Any, workspace: Path):
    seed_transcript(brain, "sess-b", _turns(user_count=5, assistant_count=4))
    h = _build_handler(brain, workspace, "sess-b")
    assert h.next_user_turn_index("sess-b") == 6


def test_next_user_turn_index_workspace_none_returns_one(
    brain: Any, tmp_path: Path
):
    """Test fixture path: handler constructed without workspace."""
    h = MessageHandler(
        brain=brain,
        sessions=_FakeSessionStore("sess-x"),  # type: ignore[arg-type]
        allowed_user_id=99,
        notifier=None,
        workspace=None,
    )
    assert h.next_user_turn_index("sess-x") == 1


# ----------------------------------------------- claim_next_turn_index


def test_claim_first_call_returns_one_no_transcript(
    brain: Any, workspace: Path
):
    h = _build_handler(brain, workspace, "sess-c")
    got = asyncio.run(h.claim_next_turn_index("sess-c"))
    assert got == 1


def test_claim_advances_after_transcript_grows(brain: Any, workspace: Path):
    """Successful brain dispatch grows the transcript by one user
    turn; the next claim returns one past the new count."""
    seed_transcript(brain, "sess-d", _turns(user_count=0))

    h = _build_handler(brain, workspace, "sess-d")
    first = asyncio.run(h.claim_next_turn_index("sess-d"))
    assert first == 1

    # Simulate brain success: transcript gains one user turn.
    seed_transcript(brain, "sess-d", _turns(user_count=1))
    second = asyncio.run(h.claim_next_turn_index("sess-d"))
    assert second == 2


def test_claim_collision_returns_none_when_transcript_did_not_advance(
    brain: Any, workspace: Path
):
    """The brain-error edge case: hook minted at index 1, brain
    didn't write, next hook should refuse."""
    h = _build_handler(brain, workspace, "sess-e")
    first = asyncio.run(h.claim_next_turn_index("sess-e"))
    assert first == 1
    # Transcript didn't advance — second call sees proposed=1 again.
    second = asyncio.run(h.claim_next_turn_index("sess-e"))
    assert second is None


def test_claim_recovers_after_collision_when_transcript_finally_advances(
    brain: Any, workspace: Path
):
    """After a brain error + retry that succeeds, subsequent claims
    proceed at the right index."""
    h = _build_handler(brain, workspace, "sess-f")
    # First mint (cursor=1), brain errored — transcript still empty.
    assert asyncio.run(h.claim_next_turn_index("sess-f")) == 1
    # Collision on retry (msg2 at same proposed=1).
    assert asyncio.run(h.claim_next_turn_index("sess-f")) is None
    # User retries the trigger; this time the brain wrote — the
    # transcript advanced. Cursor should accept (proposed=2 > last=1).
    seed_transcript(brain, "sess-f", _turns(user_count=1))
    assert asyncio.run(h.claim_next_turn_index("sess-f")) == 2


def test_claim_cursor_is_per_session_uuid(brain: Any, workspace: Path):
    """Switching sessions doesn't carry the cursor over."""
    h = _build_handler(brain, workspace, "sess-g")
    assert asyncio.run(h.claim_next_turn_index("sess-g")) == 1
    # A different session_uuid starts at its own cursor=0 default.
    assert asyncio.run(h.claim_next_turn_index("sess-h")) == 1


def test_claim_restart_safety(brain: Any, workspace: Path):
    """Cursor lives in memory; on a fresh handler instance the cursor
    is empty and ``next_user_turn_index`` rebuilds from the
    transcript."""
    seed_transcript(brain, "sess-i", _turns(user_count=5))

    # First handler "lifetime"
    h1 = _build_handler(brain, workspace, "sess-i")
    assert asyncio.run(h1.claim_next_turn_index("sess-i")) == 6

    # Daemon restart: a brand-new handler with no remembered state.
    h2 = _build_handler(brain, workspace, "sess-i")
    # Pretend the brain wrote its turn before the restart, so the
    # transcript now has 6 user turns.
    seed_transcript(brain, "sess-i", _turns(user_count=6))
    assert asyncio.run(h2.claim_next_turn_index("sess-i")) == 7


def test_claim_atomicity_under_concurrent_callers(
    brain: Any, workspace: Path
):
    """Two parallel claims for the same session must not both pass.

    The drain loop already serialises per-chat in production, so this
    is defense in depth: ``claim_next_turn_index`` wraps the
    read-modify-write in ``_cursor_lock``. We exercise the lock by
    issuing many concurrent claims; exactly one should win the slot
    for proposed=1, the rest should see proposed <= last and refuse.
    """
    h = _build_handler(brain, workspace, "sess-j")

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
        from vexis_agent.core.relationships.curator import TurnLevelResult
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
    from vexis_agent.transports.telegram import TelegramTransport
    from vexis_agent.core.running_tasks import RunningTasks
    t = TelegramTransport.__new__(TelegramTransport)
    t._handler = handler  # type: ignore[attr-defined]
    t._allowed_user_id = allowed_user_id  # type: ignore[attr-defined]
    t._running_tasks = RunningTasks()  # type: ignore[attr-defined]
    t._learning_curator = _FakeLearningController(relationships)  # type: ignore[attr-defined]
    # Streaming defaults OFF in this fixture so the relationships
    # handoff tests stay on the buffered ``handler.handle`` path
    # (which is what their _OrderingHandler exercises). Streaming
    # would route through ``handler.stream`` instead and these
    # tests don't model that surface.
    t._streaming_enabled = False  # type: ignore[attr-defined]
    t._streaming_min_interval = 1.0  # type: ignore[attr-defined]
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
    brain: Any, workspace: Path, monkeypatch: pytest.MonkeyPatch
):
    """End-to-end: relationships hook receives the brain's real
    session_uuid (NOT 'telegram-chat-...'), with a transcript-derived
    turn_index. Hook reply arrives BEFORE brain reply on Telegram.

    v3c Day 4a: this test exercises the explicit-consent fast lane,
    which is runtime-disabled by default. Flag flipped on for the
    duration of this test.
    """

    monkeypatch.setattr(
        "vexis_agent.core.yaml_config.relationships_explicit_consent_enabled",
        lambda: True,
    )
    seed_transcript(brain, "real-uuid-aaa", _turns(user_count=2))

    # Real handler; respond() stubbed so no CLI spawn, iter_messages
    # still routes to the real brain.
    handler = _build_handler(brain, workspace, "real-uuid-aaa")
    handler._brain = _FakeBrainRespond(brain)  # type: ignore[assignment]
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
    brain: Any, workspace: Path, monkeypatch: pytest.MonkeyPatch
):
    """If the transcript doesn't advance between two hook fires, the
    second hook gets None back from claim_next_turn_index and
    skips staging — no relationships call, no Telegram receipt,
    brain still receives the user's text.

    v3c Day 4a: explicit-consent flag flipped on (this test exercises
    the legacy explicit-consent path).
    """

    monkeypatch.setattr(
        "vexis_agent.core.yaml_config.relationships_explicit_consent_enabled",
        lambda: True,
    )
    handler = _build_handler(brain, workspace, "sess-coll")
    handler._brain = _FakeBrainRespond(brain)  # type: ignore[assignment]

    async def patched_handle(user_id: int, chat_id: int, text: str):
        return "brain-reply"

    handler.handle = patched_handle  # type: ignore[assignment]

    relationships = _RecordingRelationships(reply_text="Got it — staged Sarah.")
    transport = _make_transport_with_curator(handler, 99, relationships)
    bot = _FakeBot()

    # First message: mints at index 1, transcript never advances (test
    # doesn't simulate a real brain write).
    asyncio.run(transport._dispatch_to_brain(bot, 42, 99, "msg1"))
    assert len(relationships.calls) == 1
    assert relationships.calls[0][1] == 1

    # Second message: cursor still at 1, transcript still has 0 user
    # turns, claim_next_turn_index returns None. Hook short-circuits.
    asyncio.run(transport._dispatch_to_brain(bot, 42, 99, "msg2"))
    assert len(relationships.calls) == 1  # NO new call to relationships
    assert "cursor_collision" in relationships.counter_increments

    # The first ack was sent; no second ack on the collided turn.
    sent_texts = [t for _cid, t in bot.sent_messages]
    assert sent_texts.count("Got it — staged Sarah.") == 1
    # Brain reply still landed for both turns.
    assert sent_texts.count("brain-reply") == 2

"""Tests for the Codemux orchestration watcher.

The watcher's hot path is the polling loop interacting with the source
plugin and the registry. Real Codemux MCP / Telegram bot are out of
reach in unit tests, so we drive the loop with:

  * ``FakeSource`` — a programmable Source plugin. Tests push bytes
    onto it to simulate output landing, flip ``alive=False`` to test
    the death-transition path, and assert which calls happened.

  * ``WatcherRegistry`` written to a tmp_path JSON — exercises real
    persistence so the daemon-restart test reloads the same file.

  * A fake notify callback that just records ``(chat_id, text)``
    tuples. The poller's debounce contract is asserted against this
    record, not the live Telegram bot.

Pin counts:
  * 1 idle transition → 1 notify call.
  * idle → running → idle within debounce window → still 1 notify call.
  * Two idle transitions outside the window → 2 notify calls.

These pins map directly to the spec's "ONE notification per idle
transition" + "do NOT re-notify if state oscillates" contract.

Conditional-activation is tested by patching
``setup_wizard.detect_mcp_servers`` to omit / include the
``codemux`` entry and asserting ``codemux_mcp_configured()`` flips.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import pytest

from vexis_agent.core.watcher import (
    UNAVAILABLE_MESSAGE,
    WatchStatus,
    WatcherController,
    WatcherRegistry,
    render_idle_notification,
)
from vexis_agent.core.watcher.poller import WatcherPoller, _AgentRuntimeState
from vexis_agent.core.watcher.registry import (
    DEFAULT_IDLE_AFTER_SECONDS,
    DuplicateName,
    UnknownName,
    WatchedAgent,
    _utcnow_iso,
)
from vexis_agent.core.watcher.sources import (
    Source,
    SourceDescription,
    SourceUnavailable,
    clear_sources,
    get_source,
    register_source,
)


class FakeSource(Source):
    source_type = "fake"

    def __init__(self) -> None:
        self.bytes_to_return: dict[str, bytes] = {}
        self.alive_map: dict[str, bool] = {}
        self.reads: list[str] = []
        self.unavailable: set[str] = set()

    async def read_recent_output(self, identifier: str) -> bytes:
        self.reads.append(identifier)
        if identifier in self.unavailable:
            raise SourceUnavailable(f"{identifier} gone")
        return self.bytes_to_return.get(identifier, b"")

    async def is_alive(self, identifier: str) -> bool:
        return self.alive_map.get(identifier, True)

    async def describe(self, identifier: str) -> SourceDescription:
        return SourceDescription(title=f"fake-{identifier}")


@pytest.fixture(autouse=True)
def _reset_source_registry():
    clear_sources()
    yield
    clear_sources()


def _make_registry(tmp_path: Path) -> WatcherRegistry:
    return WatcherRegistry(state_path=tmp_path / "watcher-registry.json")


def _register(
    registry: WatcherRegistry,
    *,
    name: str = "ws-a",
    identifier: str = "workspace-1",
    chat_id: int = 1234,
    idle_after_seconds: int = 30,
    source_type: str = "fake",
) -> WatchedAgent:
    agent = WatchedAgent(
        name=name,
        source_type=source_type,
        identifier=identifier,
        agent_kind="claude-code",
        chat_id=chat_id,
        registered_at=_utcnow_iso(),
        idle_after_seconds=idle_after_seconds,
    )
    asyncio.run(registry.register(agent))
    return agent


# ---------- legacy "unavailable" message ------------------------------------
# The codemux_mcp_configured() gate was removed in Phase B — the codemux
# add-on's manifest now declares the MCP requirement, and ``vexis-addons
# doctor`` surfaces the missing MCP. UNAVAILABLE_MESSAGE remains for
# back-compat with vexis-watch CLI callers.


def test_unavailable_message_is_user_facing():
    assert "codemux" in UNAVAILABLE_MESSAGE.lower()


# ---------- registry persistence --------------------------------------------


def test_registry_round_trips_through_disk(tmp_path):
    reg = _make_registry(tmp_path)
    _register(reg, name="ws-a")
    _register(reg, name="ws-b")
    # New registry instance reads the JSON the first wrote.
    fresh = _make_registry(tmp_path)
    names = sorted(a.name for a in fresh.list())
    assert names == ["ws-a", "ws-b"]


def test_registry_rejects_duplicate_names(tmp_path):
    reg = _make_registry(tmp_path)
    _register(reg, name="dup")
    with pytest.raises(DuplicateName):
        _register(reg, name="dup")


def test_registry_unregister_unknown_raises(tmp_path):
    reg = _make_registry(tmp_path)
    with pytest.raises(UnknownName):
        asyncio.run(reg.unregister("nope"))


def test_registry_mute_flag_persists(tmp_path):
    reg = _make_registry(tmp_path)
    _register(reg, name="ws-a")
    asyncio.run(reg.set_muted("ws-a", True))
    fresh = _make_registry(tmp_path)
    assert fresh.get("ws-a").muted is True


# ---------- polling loop ----------------------------------------------------


def _build_poller(
    registry: WatcherRegistry,
    fake: FakeSource,
    *,
    notify_log: Optional[list[tuple[int, str]]] = None,
    oscillation_window_seconds: float = 60.0,
) -> WatcherPoller:
    async def _notify(chat_id: int, text: str) -> None:
        if notify_log is not None:
            notify_log.append((chat_id, text))

    register_source(fake)
    return WatcherPoller(
        registry,
        notify=_notify,
        poll_interval_seconds=0.01,
        oscillation_window_seconds=oscillation_window_seconds,
        source_lookup=get_source,
    )


def test_one_idle_transition_fires_one_notification(tmp_path):
    """Spec acceptance 1 (unit-scale): walk away → one ping."""
    reg = _make_registry(tmp_path)
    _register(reg, name="ws-a", chat_id=42, idle_after_seconds=0)
    fake = FakeSource()
    fake.bytes_to_return["workspace-1"] = b"build started\n"

    notifs: list[tuple[int, str]] = []
    poller = _build_poller(reg, fake, notify_log=notifs)

    async def go():
        # First tick: output observed, agent stays running.
        await poller.tick()
        # Second tick: no new bytes, idle_after=0 → transition + notify.
        await poller.tick()
        # Third tick: still no new bytes, already idle → no second notify.
        await poller.tick()

    asyncio.run(go())
    assert len(notifs) == 1
    chat_id, text = notifs[0]
    assert chat_id == 42
    assert "ws-a" in text
    assert "claude-code" in text


def test_idle_oscillation_within_window_is_debounced(tmp_path):
    """Spec contract: idle → active → idle inside the window → no re-ping."""
    reg = _make_registry(tmp_path)
    _register(reg, name="ws-a", chat_id=42, idle_after_seconds=0)
    fake = FakeSource()
    fake.bytes_to_return["workspace-1"] = b"hello\n"

    notifs: list[tuple[int, str]] = []
    poller = _build_poller(
        reg, fake, notify_log=notifs, oscillation_window_seconds=3600,
    )

    async def go():
        await poller.tick()       # observe initial output
        await poller.tick()       # idle → notify #1
        # New output lands → back to running. NO notification (we never
        # ping on running, only on idle).
        fake.bytes_to_return["workspace-1"] = b"hello\nstep 2\n"
        await poller.tick()
        # Same hash again → idle again. Inside the window → debounced.
        await poller.tick()

    asyncio.run(go())
    assert len(notifs) == 1, f"debounce broken — got {notifs!r}"


def test_idle_outside_debounce_window_re_notifies(tmp_path):
    """Two distinct idle transitions far apart → two pings."""
    reg = _make_registry(tmp_path)
    _register(reg, name="ws-a", chat_id=42, idle_after_seconds=0)
    fake = FakeSource()
    fake.bytes_to_return["workspace-1"] = b"first\n"

    notifs: list[tuple[int, str]] = []
    poller = _build_poller(
        reg, fake, notify_log=notifs, oscillation_window_seconds=0.0,
    )

    async def go():
        await poller.tick()                # observe
        await poller.tick()                # notify #1
        fake.bytes_to_return["workspace-1"] = b"second\n"
        await poller.tick()                # observe (running again)
        await poller.tick()                # idle → notify #2 (window=0)

    asyncio.run(go())
    assert len(notifs) == 2


def test_muted_agent_does_not_notify(tmp_path):
    reg = _make_registry(tmp_path)
    _register(reg, name="ws-a", chat_id=42, idle_after_seconds=0)
    asyncio.run(reg.set_muted("ws-a", True))
    fake = FakeSource()
    fake.bytes_to_return["workspace-1"] = b"x"

    notifs: list[tuple[int, str]] = []
    poller = _build_poller(reg, fake, notify_log=notifs)

    async def go():
        await poller.tick()
        await poller.tick()

    asyncio.run(go())
    assert notifs == []


def test_dead_source_transitions_agent_to_dead(tmp_path):
    reg = _make_registry(tmp_path)
    _register(reg, name="ws-a", chat_id=42)
    fake = FakeSource()
    fake.alive_map["workspace-1"] = False

    notifs: list[tuple[int, str]] = []
    poller = _build_poller(reg, fake, notify_log=notifs)

    asyncio.run(poller.tick())
    assert reg.get("ws-a").status == WatchStatus.DEAD.value
    # Death is silent — no Telegram ping, by spec.
    assert notifs == []


def test_source_unavailable_marks_dead(tmp_path):
    reg = _make_registry(tmp_path)
    _register(reg, name="ws-a", chat_id=42)
    fake = FakeSource()
    fake.unavailable.add("workspace-1")
    notifs: list[tuple[int, str]] = []
    poller = _build_poller(reg, fake, notify_log=notifs)
    asyncio.run(poller.tick())
    assert reg.get("ws-a").status == WatchStatus.DEAD.value
    assert notifs == []


# ---------- header injection -----------------------------------------------
# WatcherController.header_block was removed in Phase B; the codemux
# add-on now supplies its own header via
# ctx.register_system_prompt_block. Equivalent coverage lives in
# tests/test_codemux_addon_header.py (added in Phase B).


# ---------- end-to-end via WatcherController.tail --------------------------


def test_tail_reads_through_source_plugin(tmp_path):
    reg = _make_registry(tmp_path)
    _register(reg, name="ws-a", identifier="workspace-7")
    fake = FakeSource()
    fake.bytes_to_return["workspace-7"] = (b"a\nb\nc\nd\ne\nf\n")
    register_source(fake)
    controller = WatcherController(
        registry=reg,
    )
    text = asyncio.run(controller.tail("ws-a", lines=3))
    assert text == "d\ne\nf"


# ---------- pluggable source contract ---------------------------------------


def test_new_source_plugs_in_with_zero_core_changes(tmp_path):
    """Spec acceptance 7: a new source = one new file + register call."""
    class PtyFake(Source):
        source_type = "fake-pty"
        async def read_recent_output(self, identifier: str) -> bytes:
            return b"pty output"
        async def is_alive(self, identifier: str) -> bool:
            return True
        async def describe(self, identifier: str) -> SourceDescription:
            return SourceDescription(repo_path=f"/pty/{identifier}")

    register_source(PtyFake())
    reg = _make_registry(tmp_path)
    controller = WatcherController(
        registry=reg,
    )

    async def go():
        await controller.register_agent(
            name="pty-1",
            source_type="fake-pty",
            identifier="pid-42",
            agent_kind="aider",
            chat_id=99,
        )
        return await controller.tail("pty-1", lines=10)

    out = asyncio.run(go())
    assert "pty output" in out


# ---------- notification rendering ------------------------------------------


def test_idle_notification_carries_handle_and_kind():
    agent = WatchedAgent(
        name="ws-a",
        source_type="codemux",
        identifier="workspace-1",
        agent_kind="claude-code",
        chat_id=1,
        registered_at=_utcnow_iso(),
        idle_after_seconds=DEFAULT_IDLE_AFTER_SECONDS,
        last_line="cargo build finished",
        goal_hint="refactor logging",
    )
    text = render_idle_notification(agent, idle_seconds=1140)
    assert "ws-a" in text
    assert "claude-code" in text
    assert "19m" in text
    assert "refactor logging" in text
    assert "cargo build finished" in text
    # User-facing reply hints (per spec LAYER 1d):
    assert "tail ws-a" in text
    assert "peek ws-a" in text
    assert "mute ws-a" in text

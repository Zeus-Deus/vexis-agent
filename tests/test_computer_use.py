"""Computer-use model selection — the dynamic ("Codex-style") model
switch and its opt-in gating.

Coverage map:

* ``yaml_config`` readers — defaults + sentinel coercion.
* ``core.computer_use`` runtime-state round-trip (the UIDriver →
  handler IPC seam).
* ``resolve_computer_use_override`` — every branch of the decision:
  no opt-in, opt-in but no activity, pinned, dynamic-wins-on-rich,
  dynamic-falls-back-on-vision, sub-threshold, stale.
* ``computer_use_payload`` / ``computer_use_set`` — dashboard wire.
* ``MessageHandler`` integration — explicit override (voice) wins;
  pure chat with no UI activity is bit-for-bit untouched; a fresh
  rich snapshot makes a foreground turn pick up the override.

Isolation: every test points ``VEXIS_HOME`` at ``tmp_path`` so the
config file AND the runtime-state file both live in the sandbox.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from vexis_agent.core import computer_use as cu
from vexis_agent.core import yaml_config as yc


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("VEXIS_HOME", str(tmp_path))
    return tmp_path


def _write_config(home: Path, body: str) -> None:
    (home / "config.yaml").write_text(body, encoding="utf-8")


# ──────────────────────────────────────────────────────────────────
# Config readers
# ──────────────────────────────────────────────────────────────────


def test_config_readers_default_when_unset(_isolated_home: Path) -> None:
    assert yc.computer_use_model() is None
    assert yc.computer_use_reasoning_level() is None
    assert yc.computer_use_dynamic_enabled() is False
    assert yc.computer_use_dynamic_model() is None
    assert yc.computer_use_dynamic_reasoning_level() is None
    assert (
        yc.computer_use_dynamic_min_elements()
        == yc.DEFAULT_COMPUTER_USE_MIN_ELEMENTS
    )


def test_config_readers_parse_values(_isolated_home: Path) -> None:
    _write_config(
        _isolated_home,
        "computer_use:\n"
        "  model: claude-sonnet-4-6\n"
        "  reasoning_level: medium\n"
        "  dynamic:\n"
        "    enabled: true\n"
        "    model: claude-haiku-4-5\n"
        "    reasoning_level: low\n"
        "    min_elements: 9\n",
    )
    assert yc.computer_use_model() == "claude-sonnet-4-6"
    assert yc.computer_use_reasoning_level() == "medium"
    assert yc.computer_use_dynamic_enabled() is True
    assert yc.computer_use_dynamic_model() == "claude-haiku-4-5"
    assert yc.computer_use_dynamic_reasoning_level() == "low"
    assert yc.computer_use_dynamic_min_elements() == 9


@pytest.mark.parametrize("sentinel", ["", "   ", "default", "DEFAULT"])
def test_model_knob_sentinels_coerce_to_none(
    _isolated_home: Path, sentinel: str,
) -> None:
    _write_config(
        _isolated_home, f"computer_use:\n  model: '{sentinel}'\n",
    )
    assert yc.computer_use_model() is None


def test_dynamic_min_elements_rejects_nonsense(_isolated_home: Path) -> None:
    # A 0/negative knob would make every snapshot "rich" — _int_or_default
    # rejects anything below minimum=1 and falls back to the default.
    _write_config(
        _isolated_home,
        "computer_use:\n  dynamic:\n    min_elements: 0\n",
    )
    assert (
        yc.computer_use_dynamic_min_elements()
        == yc.DEFAULT_COMPUTER_USE_MIN_ELEMENTS
    )


# ──────────────────────────────────────────────────────────────────
# Runtime state round-trip
# ──────────────────────────────────────────────────────────────────


def test_runtime_state_round_trip(_isolated_home: Path) -> None:
    assert cu.read_runtime_state() is None  # absent file
    cu.record_snapshot_activity(
        element_count=12, used_vision_fallback=False, stale=False,
    )
    state = cu.read_runtime_state()
    assert state is not None
    assert state["element_count"] == 12
    assert state["used_vision_fallback"] is False
    assert state["stale"] is False
    assert isinstance(state["ts"], (int, float))


def test_read_runtime_state_tolerates_garbage(_isolated_home: Path) -> None:
    (_isolated_home / cu._RUNTIME_STATE_FILENAME).write_text(
        "not json{", encoding="utf-8",
    )
    assert cu.read_runtime_state() is None


# ──────────────────────────────────────────────────────────────────
# resolve_computer_use_override — the decision matrix
# ──────────────────────────────────────────────────────────────────


def test_resolve_no_optin_is_noop(_isolated_home: Path) -> None:
    # Nothing configured: even with a fresh rich snapshot the override
    # must stay (None, None) — pure-chat behaviour preserved.
    cu.record_snapshot_activity(
        element_count=50, used_vision_fallback=False, stale=False,
    )
    assert cu.resolve_computer_use_override() == (None, None)


def test_resolve_pinned_but_no_activity_is_noop(_isolated_home: Path) -> None:
    _write_config(_isolated_home, "computer_use:\n  model: claude-haiku-4-5\n")
    # No runtime-state file at all — this is not a computer-use turn.
    assert cu.resolve_computer_use_override() == (None, None)


def test_resolve_pinned_applies_on_fresh_activity(
    _isolated_home: Path,
) -> None:
    _write_config(
        _isolated_home,
        "computer_use:\n  model: claude-haiku-4-5\n  reasoning_level: high\n",
    )
    cu.record_snapshot_activity(
        element_count=3, used_vision_fallback=False, stale=False,
    )
    assert cu.resolve_computer_use_override() == ("claude-haiku-4-5", "high")


def test_resolve_dynamic_wins_on_rich_tree(_isolated_home: Path) -> None:
    _write_config(
        _isolated_home,
        "computer_use:\n"
        "  model: claude-sonnet-4-6\n"
        "  dynamic:\n"
        "    enabled: true\n"
        "    model: claude-haiku-4-5\n"
        "    min_elements: 5\n",
    )
    cu.record_snapshot_activity(
        element_count=20, used_vision_fallback=False, stale=False,
    )
    assert cu.resolve_computer_use_override() == ("claude-haiku-4-5", None)


def test_resolve_dynamic_falls_back_on_vision_snapshot(
    _isolated_home: Path,
) -> None:
    # Dynamic on, but the last snapshot was the screenshot fallback —
    # vision IS needed, so the pinned (vision-capable) model applies.
    _write_config(
        _isolated_home,
        "computer_use:\n"
        "  model: claude-sonnet-4-6\n"
        "  dynamic:\n"
        "    enabled: true\n"
        "    model: claude-haiku-4-5\n",
    )
    cu.record_snapshot_activity(
        element_count=0, used_vision_fallback=True, stale=False,
    )
    assert cu.resolve_computer_use_override() == ("claude-sonnet-4-6", None)


def test_resolve_sub_threshold_tree_is_not_rich(_isolated_home: Path) -> None:
    _write_config(
        _isolated_home,
        "computer_use:\n"
        "  model: claude-sonnet-4-6\n"
        "  dynamic:\n"
        "    enabled: true\n"
        "    model: claude-haiku-4-5\n"
        "    min_elements: 10\n",
    )
    # 4 elements < threshold of 10 → not rich → pinned model.
    cu.record_snapshot_activity(
        element_count=4, used_vision_fallback=False, stale=False,
    )
    assert cu.resolve_computer_use_override() == ("claude-sonnet-4-6", None)


def test_resolve_dynamic_without_dynamic_model_uses_pinned(
    _isolated_home: Path,
) -> None:
    _write_config(
        _isolated_home,
        "computer_use:\n"
        "  model: claude-sonnet-4-6\n"
        "  dynamic:\n"
        "    enabled: true\n",
    )
    cu.record_snapshot_activity(
        element_count=20, used_vision_fallback=False, stale=False,
    )
    assert cu.resolve_computer_use_override() == ("claude-sonnet-4-6", None)


def test_resolve_dynamic_only_no_models_is_noop(_isolated_home: Path) -> None:
    # Dynamic enabled but neither a dynamic nor a pinned model set —
    # the opt-in guard passes (dynamic_enabled) but there's nothing to
    # resolve to, so we leave the brain default alone.
    _write_config(
        _isolated_home,
        "computer_use:\n  dynamic:\n    enabled: true\n",
    )
    cu.record_snapshot_activity(
        element_count=20, used_vision_fallback=False, stale=False,
    )
    assert cu.resolve_computer_use_override() == (None, None)


def test_resolve_ignores_stale_activity(_isolated_home: Path) -> None:
    _write_config(_isolated_home, "computer_use:\n  model: claude-haiku-4-5\n")
    # Hand-write a runtime-state file with an old timestamp.
    (_isolated_home / cu._RUNTIME_STATE_FILENAME).write_text(
        json.dumps({
            "element_count": 20,
            "used_vision_fallback": False,
            "stale": False,
            "ts": time.time() - (cu.ACTIVITY_TTL_SECONDS + 60),
        }),
        encoding="utf-8",
    )
    assert cu.resolve_computer_use_override() == (None, None)


def test_resolve_stale_tree_flag_is_not_rich(_isolated_home: Path) -> None:
    # ``stale=True`` (empty AT-SPI tree) is fresh-in-time but not a
    # rich tree → dynamic does not fire; pinned applies.
    _write_config(
        _isolated_home,
        "computer_use:\n"
        "  model: claude-sonnet-4-6\n"
        "  dynamic:\n"
        "    enabled: true\n"
        "    model: claude-haiku-4-5\n",
    )
    cu.record_snapshot_activity(
        element_count=0, used_vision_fallback=False, stale=True,
    )
    assert cu.resolve_computer_use_override() == ("claude-sonnet-4-6", None)


# ──────────────────────────────────────────────────────────────────
# Dashboard payload + writer
# ──────────────────────────────────────────────────────────────────


@pytest.fixture
def _stub_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep payload tests hermetic — no network / subprocess to the
    real model-discovery layer."""
    monkeypatch.setattr(
        "vexis_agent.core.model_discovery.available_models_for_picker",
        lambda _brain: [
            {
                "id": "claude-haiku-4-5",
                "display_name": "Claude Haiku 4.5",
                "reasoning_levels": [],
                "max_input_tokens": 200000,
                "max_tokens": 8192,
                "provider": "anthropic",
                "free": False,
                "cost_input_per_million": None,
                "cost_output_per_million": None,
            },
        ],
    )


def test_payload_shape(_isolated_home: Path, _stub_discovery: None) -> None:
    _write_config(
        _isolated_home,
        "computer_use:\n  model: claude-haiku-4-5\n  dynamic:\n    enabled: true\n",
    )
    cu.record_snapshot_activity(
        element_count=7, used_vision_fallback=False, stale=False,
    )
    payload = cu.computer_use_payload()
    assert payload["model"] == "claude-haiku-4-5"
    assert payload["dynamic"]["enabled"] is True
    assert payload["dynamic"]["min_elements"] == 5  # default
    assert len(payload["available_models"]) == 1
    activity = payload["last_activity"]
    assert activity is not None
    assert activity["element_count"] == 7
    assert activity["fresh"] is True
    assert activity["rich"] is True  # 7 >= default threshold 5


def test_payload_last_activity_none_when_no_snapshot(
    _isolated_home: Path, _stub_discovery: None,
) -> None:
    assert cu.computer_use_payload()["last_activity"] is None


def test_set_round_trips_and_drops_orphan_reasoning(
    _isolated_home: Path, _stub_discovery: None,
) -> None:
    # reasoning_level with no model is meaningless — the writer drops
    # the orphan so we never pass --effort to the account default.
    result = cu.computer_use_set({"model": "", "reasoning_level": "high"})
    assert result["ok"] is True
    assert result["model"] == ""
    assert result["reasoning_level"] == ""
    # The on-disk config must not carry the orphan key either.
    raw = (_isolated_home / "config.yaml").read_text(encoding="utf-8")
    assert "reasoning_level" not in raw


def test_set_persists_dynamic_block(
    _isolated_home: Path, _stub_discovery: None,
) -> None:
    cu.computer_use_set({
        "model": "claude-sonnet-4-6",
        "dynamic": {"enabled": True, "model": "claude-haiku-4-5",
                    "min_elements": 12},
    })
    assert yc.computer_use_model() == "claude-sonnet-4-6"
    assert yc.computer_use_dynamic_enabled() is True
    assert yc.computer_use_dynamic_model() == "claude-haiku-4-5"
    assert yc.computer_use_dynamic_min_elements() == 12


def test_set_drops_min_elements_at_default(
    _isolated_home: Path, _stub_discovery: None,
) -> None:
    # Setting min_elements back to the built-in default removes the
    # key so the YAML stays uncluttered.
    cu.computer_use_set({
        "dynamic": {"enabled": True, "min_elements": 12},
    })
    cu.computer_use_set({
        "dynamic": {"min_elements": yc.DEFAULT_COMPUTER_USE_MIN_ELEMENTS},
    })
    raw = (_isolated_home / "config.yaml").read_text(encoding="utf-8")
    assert "min_elements" not in raw


@pytest.mark.parametrize(
    "bad",
    [
        {"model": 123},
        {"dynamic": "not-an-object"},
        {"dynamic": {"enabled": "yes"}},
        {"dynamic": {"min_elements": 0}},
        {"dynamic": {"min_elements": "lots"}},
    ],
)
def test_set_rejects_malformed_payload(
    _isolated_home: Path, _stub_discovery: None, bad: dict,
) -> None:
    with pytest.raises(ValueError):
        cu.computer_use_set(bad)


# ──────────────────────────────────────────────────────────────────
# Brain-agnostic — works for opencode / null, not just claude-code
# ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("brain_kind", ["claude-code", "opencode", "null"])
def test_payload_does_not_crash_for_any_brain(
    _isolated_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    brain_kind: str,
) -> None:
    # The picker's model list is brain-specific, but the payload shape
    # is uniform — the dashboard never forks by brain kind. Discovery
    # is stubbed so the test stays hermetic regardless of which brain
    # binaries happen to be installed.
    monkeypatch.setattr(
        "vexis_agent.core.model_discovery.available_models_for_picker",
        lambda _b: [],
    )
    _write_config(_isolated_home, f"brain:\n  kind: {brain_kind}\n")
    payload = cu.computer_use_payload()
    assert set(payload) >= {
        "model", "reasoning_level", "dynamic", "available_models",
        "last_activity",
    }


def test_resolution_is_brain_agnostic(_isolated_home: Path) -> None:
    # The override is a plain per-turn model id — exactly like voice
    # call mode. The brain does any tier/native translation
    # downstream, so an opencode-shaped ``provider/model`` id passes
    # through resolution untouched.
    _write_config(
        _isolated_home,
        "brain:\n  kind: opencode\n"
        "computer_use:\n  model: anthropic/claude-haiku-4-5\n",
    )
    cu.record_snapshot_activity(
        element_count=12, used_vision_fallback=False, stale=False,
    )
    assert cu.resolve_computer_use_override() == (
        "anthropic/claude-haiku-4-5",
        None,
    )


# ──────────────────────────────────────────────────────────────────
# MessageHandler integration
# ──────────────────────────────────────────────────────────────────


def test_handler_override_explicit_model_wins(_isolated_home: Path) -> None:
    # Voice-call-mode style: an explicit caller override is passed
    # straight through, untouched, even if computer-use config + a
    # fresh rich snapshot would otherwise substitute something.
    from vexis_agent.core.handler import MessageHandler

    _write_config(_isolated_home, "computer_use:\n  model: claude-haiku-4-5\n")
    cu.record_snapshot_activity(
        element_count=20, used_vision_fallback=False, stale=False,
    )
    assert MessageHandler._apply_computer_use_override(
        "claude-opus-4-7", "max",
    ) == ("claude-opus-4-7", "max")


def test_handler_override_noop_for_plain_chat(_isolated_home: Path) -> None:
    # No opt-in, no UI activity → (None, None): Telegram + text chat
    # are bit-for-bit unchanged.
    from vexis_agent.core.handler import MessageHandler

    assert MessageHandler._apply_computer_use_override(None, None) == (
        None,
        None,
    )


def test_handler_override_applies_on_computer_use_turn(
    _isolated_home: Path,
) -> None:
    from vexis_agent.core.handler import MessageHandler

    _write_config(
        _isolated_home,
        "computer_use:\n  model: claude-haiku-4-5\n  reasoning_level: low\n",
    )
    cu.record_snapshot_activity(
        element_count=8, used_vision_fallback=False, stale=False,
    )
    assert MessageHandler._apply_computer_use_override(None, None) == (
        "claude-haiku-4-5",
        "low",
    )


@pytest.mark.anyio("asyncio")
async def test_handler_handle_threads_override_to_brain(
    _isolated_home: Path,
) -> None:
    """End-to-end: a real MessageHandler.handle() turn with a fresh
    rich snapshot must reach BrainNull.respond with the substituted
    model — and a turn with no UI activity must reach it with None."""
    from vexis_agent.core.brain.null import BrainNull
    from vexis_agent.core.handler import MessageHandler
    from vexis_agent.core.sessions import SessionStore

    brain = BrainNull(responses=["r0", "r1"])
    sessions = SessionStore.__new__(SessionStore)
    sessions._state_path = Path("/dev/null")  # type: ignore[attr-defined]
    sessions._active = "test"  # type: ignore[attr-defined]
    sessions._sessions = {  # type: ignore[attr-defined]
        "test": {
            "uuid": "00000000-0000-0000-0000-000000000000",
            "initialized": True,
            "created_at": "2026-05-08T00:00:00+00:00",
        },
    }
    handler = MessageHandler(
        brain=brain, sessions=sessions, allowed_user_id=42, notifier=None,
    )

    # Turn 1 — no opt-in, no activity: brain sees None.
    await handler.handle(42, 1, "plain chat")
    _m, _c, model, reasoning = brain.calls()[0]
    assert model is None and reasoning is None

    # Turn 2 — opt in + a fresh rich snapshot: brain sees the override.
    _write_config(
        _isolated_home, "computer_use:\n  model: claude-haiku-4-5\n",
    )
    cu.record_snapshot_activity(
        element_count=15, used_vision_fallback=False, stale=False,
    )
    await handler.handle(42, 1, "click the save button")
    _m, _c, model, reasoning = brain.calls()[1]
    assert model == "claude-haiku-4-5"

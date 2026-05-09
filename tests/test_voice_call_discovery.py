"""Tests for the dynamic-discovery surface that feeds the Voice
tab's call-mode picker.

Two contracts pinned here:

1. ``_extract_claude_code_reasoning_levels`` iterates over
   ``effort.keys()`` rather than a hardcoded tuple. If Anthropic
   ever returns a new level (``xhigh``, ``ultra``, anything),
   it must surface in the picker without a code change. These
   tests synthesise levels Anthropic doesn't actually ship today
   to prove the extraction is dynamic.

2. ``_extract_claude_code_context_info`` pulls ``display_name``,
   ``max_input_tokens``, and ``max_tokens`` directly from the
   /v1/models entry. Missing or malformed fields must degrade
   gracefully (None, not a crash) — Anthropic's API has been
   known to drop fields on older models.

Plus integration: ``_voice_call_mode_available_models_static``
ships every field for both brains in the uniform shape the UI
expects.
"""

from __future__ import annotations

import pytest

from core.model_discovery import (
    _extract_claude_code_context_info,
    _extract_claude_code_reasoning_levels,
)
from core.web_server import WebDashboard


# ──────────────────────────────────────────────────────────────────
# Reasoning level extraction — must be dynamic
# ──────────────────────────────────────────────────────────────────


def test_reasoning_levels_picks_up_arbitrary_keys() -> None:
    """The extraction must NOT depend on a hardcoded list of level
    names. Synthesise a payload with levels Anthropic doesn't ship
    today and confirm they survive."""
    entry = {
        "id": "future-model",
        "capabilities": {
            "effort": {
                "supported": True,
                "low": {"supported": True},
                "medium": {"supported": True},
                "xhigh": {"supported": True},   # not currently shipped
                "ultra": {"supported": True},   # not currently shipped
                "ludicrous": {"supported": False},  # ignored — supported=False
            },
        },
    }
    levels = _extract_claude_code_reasoning_levels(entry)
    # Order isn't specified — sort for stable assertion.
    assert sorted(levels) == ["low", "medium", "ultra", "xhigh"]


def test_reasoning_levels_excludes_meta_keys() -> None:
    """``supported`` is a meta-key on the effort block — must not
    appear as a level even though it's a child of effort."""
    entry = {
        "capabilities": {
            "effort": {
                "supported": True,
                "low": {"supported": True},
            },
        },
    }
    levels = _extract_claude_code_reasoning_levels(entry)
    assert "supported" not in levels
    assert levels == ["low"]


def test_reasoning_levels_empty_when_effort_disabled() -> None:
    """``capabilities.effort.supported: False`` (haiku-style)
    returns an empty list — picker uses this as the signal to skip
    the reasoning step entirely."""
    entry = {
        "capabilities": {
            "effort": {
                "supported": False,
                "low": {"supported": False},
                "medium": {"supported": False},
            },
        },
    }
    assert _extract_claude_code_reasoning_levels(entry) == []


def test_reasoning_levels_robust_to_missing_fields() -> None:
    """Real responses sometimes omit chunks. Must NOT raise."""
    assert _extract_claude_code_reasoning_levels({}) == []
    assert _extract_claude_code_reasoning_levels({"capabilities": {}}) == []
    assert _extract_claude_code_reasoning_levels(
        {"capabilities": {"effort": "not-a-dict"}},
    ) == []
    # Wrong type at the entry level
    assert _extract_claude_code_reasoning_levels("garbage") == []
    assert _extract_claude_code_reasoning_levels(None) == []


def test_reasoning_levels_skips_non_dict_level_payloads() -> None:
    """Defensive — if a level's value isn't an object with
    ``supported``, skip it rather than crash."""
    entry = {
        "capabilities": {
            "effort": {
                "supported": True,
                "low": {"supported": True},
                "broken": "string-not-dict",
                "also-broken": ["list-not-dict"],
            },
        },
    }
    assert _extract_claude_code_reasoning_levels(entry) == ["low"]


# ──────────────────────────────────────────────────────────────────
# Context info extraction — display name + token windows
# ──────────────────────────────────────────────────────────────────


def test_context_info_pulls_all_three_fields() -> None:
    entry = {
        "display_name": "Claude Opus 4.7",
        "max_input_tokens": 1_000_000,
        "max_tokens": 128_000,
    }
    info = _extract_claude_code_context_info(entry)
    assert info["display_name"] == "Claude Opus 4.7"
    assert info["max_input_tokens"] == 1_000_000
    assert info["max_tokens"] == 128_000


def test_context_info_returns_none_for_missing() -> None:
    """All three fields are optional — older models in the API
    don't always carry them. None propagates so the UI can show '—'
    rather than render zero or crash."""
    info = _extract_claude_code_context_info({})
    assert info == {
        "display_name": None,
        "max_input_tokens": None,
        "max_tokens": None,
    }


def test_context_info_rejects_malformed_values() -> None:
    """Wrong types coerce to None rather than propagating bad data
    to the picker."""
    info = _extract_claude_code_context_info({
        "display_name": 12345,             # not a string
        "max_input_tokens": "1M",          # not an int
        "max_tokens": -1,                  # negative — meaningless
    })
    assert info["display_name"] is None
    assert info["max_input_tokens"] is None
    assert info["max_tokens"] is None


def test_context_info_strips_whitespace_in_display_name() -> None:
    info = _extract_claude_code_context_info({
        "display_name": "  Claude Sonnet  ",
    })
    assert info["display_name"] == "Claude Sonnet"


def test_context_info_drops_empty_string_display_name() -> None:
    """Whitespace-only display name → None, not empty string. Saves
    the UI from rendering an empty heading line."""
    info = _extract_claude_code_context_info({"display_name": "   "})
    assert info["display_name"] is None


# ──────────────────────────────────────────────────────────────────
# Picker payload — uniform shape across brains, all fields wired
# ──────────────────────────────────────────────────────────────────


def test_picker_payload_includes_all_fields_for_claude_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synthesise a discovery response and check every field flows
    through to the picker payload. Pins the wire format so a UI
    refactor can't quietly drop a field."""
    monkeypatch.setattr(
        "core.model_discovery.discover_claude_code_models",
        lambda: {"claude-opus-4-7", "claude-haiku-4-5", "haiku"},
    )
    monkeypatch.setattr(
        "core.model_discovery.discover_claude_code_capabilities",
        lambda: {
            "claude-opus-4-7": {
                "reasoning_levels": ["low", "medium", "high", "max"],
                "display_name": "Claude Opus 4.7",
                "max_input_tokens": 1_000_000,
                "max_tokens": 128_000,
            },
            "claude-haiku-4-5": {
                "reasoning_levels": [],
                "display_name": "Claude Haiku 4.5",
                "max_input_tokens": 200_000,
                "max_tokens": 64_000,
            },
        },
    )
    out = WebDashboard._voice_call_mode_available_models_static("claude-code")
    # Bare alias filtered.
    ids = [m["id"] for m in out]
    assert "haiku" not in ids
    # Both real models present.
    assert "claude-opus-4-7" in ids
    assert "claude-haiku-4-5" in ids
    # Look at one entry in detail.
    opus = next(m for m in out if m["id"] == "claude-opus-4-7")
    assert opus["display_name"] == "Claude Opus 4.7"
    assert opus["reasoning_levels"] == ["low", "medium", "high", "max"]
    assert opus["max_input_tokens"] == 1_000_000
    assert opus["max_tokens"] == 128_000
    # Haiku has no reasoning — empty list, not missing key.
    haiku = next(m for m in out if m["id"] == "claude-haiku-4-5")
    assert haiku["reasoning_levels"] == []
    assert haiku["max_input_tokens"] == 200_000


def test_picker_payload_uniform_shape_across_brains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The frontend type ``AvailableModel`` is shared between
    claude-code and opencode — both brains must produce the same
    keys in every entry."""
    monkeypatch.setattr(
        "core.model_discovery.discover_opencode_models",
        lambda: {"anthropic/claude-sonnet-4-7", "openai/gpt-5", "bare-no-slash"},
    )
    monkeypatch.setattr(
        "core.model_discovery.discover_opencode_capabilities",
        lambda: {
            "anthropic/claude-sonnet-4-7": {
                "reasoning_levels": ["low", "high"],
                "display_name": "Claude Sonnet 4.7",
                "max_input_tokens": 1_000_000,
                "max_tokens": 64_000,
            },
        },
    )
    out = WebDashboard._voice_call_mode_available_models_static("opencode")
    # Filter rule: opencode IDs must contain "/".
    ids = [m["id"] for m in out]
    assert "bare-no-slash" not in ids
    # Every entry must carry every field — uniform shape.
    expected_keys = {
        "id", "display_name", "reasoning_levels",
        "max_input_tokens", "max_tokens",
    }
    for entry in out:
        assert set(entry.keys()) == expected_keys, (
            f"opencode picker entry {entry['id']} missing fields"
        )


def test_picker_payload_handles_missing_capability_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model present in the model list but absent from
    capabilities (e.g. discovery cache miss) should still appear
    with all fields set to safe defaults."""
    monkeypatch.setattr(
        "core.model_discovery.discover_claude_code_models",
        lambda: {"claude-orphan-model"},
    )
    monkeypatch.setattr(
        "core.model_discovery.discover_claude_code_capabilities",
        lambda: {},  # capabilities cache empty / failed
    )
    out = WebDashboard._voice_call_mode_available_models_static("claude-code")
    assert len(out) == 1
    entry = out[0]
    assert entry["id"] == "claude-orphan-model"
    assert entry["display_name"] is None
    assert entry["reasoning_levels"] == []
    assert entry["max_input_tokens"] is None
    assert entry["max_tokens"] is None


# ──────────────────────────────────────────────────────────────────
# OpenCode parser — context window from `opencode models --verbose`
# ──────────────────────────────────────────────────────────────────


def test_opencode_parser_extracts_context_and_display_name() -> None:
    """The verbose output's ``limit.context`` becomes
    max_input_tokens, ``limit.output`` becomes max_tokens, ``name``
    becomes display_name. Pin the schema mapping."""
    from core.model_discovery import _parse_opencode_verbose
    raw = """anthropic/claude-test
{
  "id": "claude-test",
  "providerID": "anthropic",
  "name": "Claude Test Model",
  "limit": {"context": 200000, "output": 64000},
  "variants": {
    "thinking": {"thinking": true},
    "fast": {"reasoningEffort": "low"}
  }
}
"""
    parsed = _parse_opencode_verbose(raw)
    assert "anthropic/claude-test" in parsed
    entry = parsed["anthropic/claude-test"]
    assert entry["display_name"] == "Claude Test Model"
    assert entry["max_input_tokens"] == 200_000
    assert entry["max_tokens"] == 64_000
    # variants → reasoning levels (sorted)
    assert entry["reasoning_levels"] == ["fast", "thinking"]


def test_opencode_parser_handles_missing_limits() -> None:
    """A model entry without a ``limit`` block must not crash —
    just leaves the token fields as None."""
    from core.model_discovery import _parse_opencode_verbose
    raw = """opencode/lite
{
  "id": "lite",
  "providerID": "opencode",
  "name": "Lite",
  "variants": {}
}
"""
    parsed = _parse_opencode_verbose(raw)
    entry = parsed["opencode/lite"]
    assert entry["display_name"] == "Lite"
    assert entry["max_input_tokens"] is None
    assert entry["max_tokens"] is None
    assert entry["reasoning_levels"] == []

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

import json

import pytest

from core.model_discovery import (
    _extract_claude_code_context_info,
    _extract_claude_code_reasoning_levels,
    _parse_claude_code_effort_help,
)
from core.web_server import WebDashboard


# ──────────────────────────────────────────────────────────────────
# Reasoning level extraction — must be dynamic
# ──────────────────────────────────────────────────────────────────


def test_reasoning_levels_uses_cli_list_when_provided() -> None:
    """When the CLI canonical list is passed in (the production
    path), it wins over the API. This is the exact case where
    ``/v1/models`` ships low/medium/high/max but ``claude --help``
    advertises xhigh too — picker must show xhigh."""
    entry = {
        "capabilities": {
            "effort": {
                "supported": True,
                "low": {"supported": True},
                "medium": {"supported": True},
                "high": {"supported": True},
                "max": {"supported": True},
            },
        },
    }
    cli = ["low", "medium", "high", "xhigh", "max"]
    assert _extract_claude_code_reasoning_levels(entry, cli_levels=cli) == cli


def test_reasoning_levels_falls_back_to_api_when_no_cli() -> None:
    """When CLI probe failed (empty list), API-listed levels are
    the fallback — picker degrades gracefully rather than going
    empty for reasoning-capable models."""
    entry = {
        "capabilities": {
            "effort": {
                "supported": True,
                "low": {"supported": True},
                "medium": {"supported": True},
                "high": {"supported": True},
                "max": {"supported": True},
            },
        },
    }
    levels = _extract_claude_code_reasoning_levels(entry, cli_levels=[])
    assert sorted(levels) == ["high", "low", "max", "medium"]


def test_reasoning_levels_falls_back_picks_up_arbitrary_api_keys() -> None:
    """In the fallback (API-only) path, extraction is still dynamic
    over effort.keys(). New API levels surface without code change."""
    entry = {
        "capabilities": {
            "effort": {
                "supported": True,
                "low": {"supported": True},
                "ultra": {"supported": True},   # not in any hardcoded list
                "ludicrous": {"supported": False},  # ignored
            },
        },
    }
    levels = _extract_claude_code_reasoning_levels(entry, cli_levels=None)
    assert sorted(levels) == ["low", "ultra"]


def test_reasoning_levels_cli_does_not_override_unsupported_models() -> None:
    """Even when the CLI advertises levels, models with
    ``effort.supported: false`` (haiku-style) must return an empty
    list — the API gate is per-model and CLI levels are a
    universal vocabulary, not a per-model assertion."""
    haiku_like = {
        "capabilities": {
            "effort": {
                "supported": False,
                "low": {"supported": False},
            },
        },
    }
    cli = ["low", "medium", "high", "xhigh", "max"]
    assert _extract_claude_code_reasoning_levels(haiku_like, cli_levels=cli) == []


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


# ──────────────────────────────────────────────────────────────────
# CLI help parser — canonical source of truth for effort levels
# ──────────────────────────────────────────────────────────────────


def test_cli_parser_extracts_real_help_shape() -> None:
    """The shape ``claude --help`` actually outputs today (2026-05).
    Pinned here so a CLI schema change surfaces as a test failure
    rather than an empty picker."""
    help_text = (
        "  --effort <level>                      "
        "Effort level for the current session "
        "(low, medium, high, xhigh, max)\n"
        "  --some-other-flag                     irrelevant\n"
    )
    assert _parse_claude_code_effort_help(help_text) == [
        "low", "medium", "high", "xhigh", "max",
    ]


def test_cli_parser_handles_line_wrap() -> None:
    """``argparse`` wraps long help lines; the parser uses re.S so
    the parenthesised list survives across a newline."""
    help_text = (
        "--effort <level>                                  Effort level\n"
        "                                                  for the\n"
        "                                                  current\n"
        "                                                  session\n"
        "                                                  (low, medium, high, xhigh, max)\n"
    )
    assert _parse_claude_code_effort_help(help_text) == [
        "low", "medium", "high", "xhigh", "max",
    ]


def test_cli_parser_picks_up_future_levels() -> None:
    """If a future CLI release adds ``ultra`` to the accept-set,
    the parser must surface it without code change. This is the
    contract — CLI is canonical, no hardcoded fallback list."""
    help_text = (
        "  --effort <level>   Effort level (low, medium, high, xhigh, ultra, max, ludicrous)\n"
    )
    assert _parse_claude_code_effort_help(help_text) == [
        "low", "medium", "high", "xhigh", "ultra", "max", "ludicrous",
    ]


def test_cli_parser_returns_empty_when_no_match() -> None:
    """No ``--effort`` line in help → empty list. Caller falls
    through to API-extracted levels rather than crashing."""
    help_text = "Usage: claude [options]\n  --model <name>   pick model\n"
    assert _parse_claude_code_effort_help(help_text) == []


def test_cli_parser_returns_empty_for_malformed() -> None:
    """No parens, no list → empty. Defensive against schema drift."""
    assert _parse_claude_code_effort_help(
        "--effort <level>   Effort level for the session\n",
    ) == []
    assert _parse_claude_code_effort_help("") == []


def test_cli_parser_strips_whitespace_around_levels() -> None:
    """Levels separated by ``,`` may have arbitrary whitespace —
    must round-trip clean strings."""
    help_text = (
        "--effort <level>   Effort level (  low ,  medium  ,high   , xhigh, max  )\n"
    )
    assert _parse_claude_code_effort_help(help_text) == [
        "low", "medium", "high", "xhigh", "max",
    ]


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
    # Every entry must carry every field — uniform shape across brains.
    # Adding a field here means the AvailableModel TypeScript interface
    # AND every consumer site (CallModePicker render, search filter,
    # cost formatter) needs the matching update.
    expected_keys = {
        "id", "display_name", "reasoning_levels",
        "max_input_tokens", "max_tokens",
        "provider", "free",
        "cost_input_per_million", "cost_output_per_million",
    }
    for entry in out:
        assert set(entry.keys()) == expected_keys, (
            f"opencode picker entry {entry['id']} missing fields"
        )


def test_picker_xhigh_surfaces_when_cli_advertises_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The user-flagged regression: ``xhigh`` must appear in the
    picker for reasoning-capable models even when ``/v1/models``
    only lists low/medium/high/max. Pinned here because it's a
    real-world correctness property — the CLI accepts xhigh
    (verified by ``claude --effort xhigh -p hi`` returning 0) and
    the picker must reflect that.
    """
    # Synthesise the actual API shape (no xhigh) — same as live data.
    api_response = {
        "data": [
            {
                "id": "claude-opus-4-7",
                "display_name": "Claude Opus 4.7",
                "max_input_tokens": 1_000_000,
                "max_tokens": 128_000,
                "capabilities": {
                    "effort": {
                        "supported": True,
                        "low": {"supported": True},
                        "medium": {"supported": True},
                        "high": {"supported": True},
                        "max": {"supported": True},
                    },
                },
            },
        ],
    }

    # Stub the HTTP fetch + the CLI probe so the discovery can run
    # without network or subprocess.
    import io
    import core.model_discovery as md
    md.invalidate_discovery_cache()

    class _FakeResp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **kw: _FakeResp(json.dumps(api_response).encode()),
    )
    monkeypatch.setattr(
        "core.model_discovery._build_anthropic_request_headers",
        lambda: {"Authorization": "Bearer test"},
    )
    monkeypatch.setattr(
        "core.model_discovery._discover_claude_code_effort_levels_uncached",
        lambda: ["low", "medium", "high", "xhigh", "max"],
    )

    caps = md.discover_claude_code_capabilities()
    levels = caps["claude-opus-4-7"]["reasoning_levels"]
    # CLI list wins — xhigh appears even though API didn't list it.
    assert levels == ["low", "medium", "high", "xhigh", "max"]
    assert "xhigh" in levels, (
        "xhigh must surface from CLI help even when /v1/models omits it"
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


def test_opencode_parser_extracts_provider_and_free_badge() -> None:
    """``providerID`` becomes the picker's provider tag. Free badge
    fires ONLY for opencode-provider models with both costs at 0 —
    that's the Zen tier, universally free for any vexis user.
    Other providers with 0-cost models (github-copilot freebies
    that need a Copilot subscription, openrouter freebies that
    bill against the user's own OpenRouter key) do NOT get the
    badge because they're not universally free."""
    from core.model_discovery import _parse_opencode_verbose
    raw = """opencode/free-model
{
  "id": "free-model",
  "providerID": "opencode",
  "name": "Free Model",
  "limit": {"context": 200000, "output": 64000},
  "cost": {"input": 0, "output": 0, "cache": {"read": 0, "write": 0}},
  "variants": {}
}
openrouter/anthropic/claude-3.5-haiku
{
  "id": "anthropic/claude-3.5-haiku",
  "providerID": "openrouter",
  "name": "Claude 3.5 Haiku",
  "limit": {"context": 200000, "output": 8192},
  "cost": {"input": 0.8, "output": 4, "cache": {"read": 0.08, "write": 1}},
  "variants": {}
}
"""
    parsed = _parse_opencode_verbose(raw)
    free = parsed["opencode/free-model"]
    paid = parsed["openrouter/anthropic/claude-3.5-haiku"]
    # Provider sourced from providerID, not the prefix.
    assert free["provider"] == "opencode"
    assert paid["provider"] == "openrouter"
    # Free badge.
    assert free["free"] is True
    assert paid["free"] is False
    # Costs surfaced as floats per million tokens.
    assert paid["cost_input_per_million"] == 0.8
    assert paid["cost_output_per_million"] == 4.0
    # Free model also reports its 0-cost (UI uses the badge, not
    # the cost line, but the data is there).
    assert free["cost_input_per_million"] == 0.0


def test_opencode_free_badge_only_on_opencode_provider() -> None:
    """The badge is gated to ``providerID == "opencode"``. A
    github-copilot model at 0/0 cost is NOT free because it
    requires a Copilot subscription. An openrouter model at 0/0 is
    NOT free because the user pays via their OpenRouter API key.
    Both must NOT carry the badge.
    """
    from core.model_discovery import _parse_opencode_verbose
    raw = """github-copilot/claude-haiku-4.5
{
  "id": "claude-haiku-4.5",
  "providerID": "github-copilot",
  "name": "Claude Haiku 4.5",
  "cost": {"input": 0, "output": 0},
  "variants": {}
}
openrouter/some-zero-cost-model
{
  "id": "some-zero-cost-model",
  "providerID": "openrouter",
  "name": "Zero Cost",
  "cost": {"input": 0, "output": 0},
  "variants": {}
}
opencode/big-pickle
{
  "id": "big-pickle",
  "providerID": "opencode",
  "name": "Big Pickle",
  "cost": {"input": 0, "output": 0},
  "variants": {}
}
"""
    parsed = _parse_opencode_verbose(raw)
    # Copilot freebie — no badge (requires subscription).
    assert parsed["github-copilot/claude-haiku-4.5"]["free"] is False
    # OpenRouter freebie — no badge (user has their own API key).
    assert parsed["openrouter/some-zero-cost-model"]["free"] is False
    # Opencode/Zen — yes badge (universally free).
    assert parsed["opencode/big-pickle"]["free"] is True


def test_opencode_picker_sorts_free_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Picker contract: free opencode models pinned to the top of
    the list. Within free / non-free buckets the alphabetical id
    order is preserved (sorted is stable). 237 models is too many
    to scroll through, so floating the free tier saves a scroll."""
    monkeypatch.setattr(
        "core.model_discovery.discover_opencode_models",
        lambda: {
            "openrouter/zzz-model",
            "opencode/aaa-free",
            "github-copilot/foo",
            "opencode/zzz-free",
            "opencode/aaa-paid",  # hypothetical paid opencode
        },
    )
    monkeypatch.setattr(
        "core.model_discovery.discover_opencode_capabilities",
        lambda: {
            "opencode/aaa-free": {
                "provider": "opencode", "free": True,
                "reasoning_levels": [],
            },
            "opencode/zzz-free": {
                "provider": "opencode", "free": True,
                "reasoning_levels": [],
            },
            "opencode/aaa-paid": {
                "provider": "opencode", "free": False,
                "reasoning_levels": [],
            },
            "github-copilot/foo": {
                "provider": "github-copilot", "free": False,
                "reasoning_levels": [],
            },
            "openrouter/zzz-model": {
                "provider": "openrouter", "free": False,
                "reasoning_levels": [],
            },
        },
    )
    out = WebDashboard._voice_call_mode_available_models_static("opencode")
    ids = [m["id"] for m in out]
    # First two: free opencode entries, alphabetical within the bucket.
    assert ids[:2] == ["opencode/aaa-free", "opencode/zzz-free"]
    # Then paid, alphabetical across the rest.
    assert ids[2:] == [
        "github-copilot/foo",
        "opencode/aaa-paid",
        "openrouter/zzz-model",
    ]


def test_opencode_parser_falls_back_to_id_prefix_when_provider_missing() -> None:
    """Older opencode versions might omit ``providerID``. Fall back
    to the prefix in the full id (everything before the first ``/``)
    so the picker still gets a provider tag."""
    from core.model_discovery import _parse_opencode_verbose
    raw = """legacy-provider/model-x
{
  "id": "model-x",
  "name": "Legacy",
  "variants": {}
}
"""
    parsed = _parse_opencode_verbose(raw)
    entry = parsed["legacy-provider/model-x"]
    assert entry["provider"] == "legacy-provider"
    # Without cost, free is False (zero-cost is a positive signal,
    # absence-of-cost-info is not).
    assert entry["free"] is False


def test_opencode_parser_partial_zero_cost_not_free() -> None:
    """A model with input=0 but output>0 is NOT free — covers
    weird cache-only freebies and prevents accidental "free" badges
    on models that charge for output."""
    from core.model_discovery import _parse_opencode_verbose
    raw = """oddprovider/cache-only
{
  "id": "cache-only",
  "providerID": "oddprovider",
  "name": "Cache Only",
  "cost": {"input": 0, "output": 1.5},
  "variants": {}
}
"""
    parsed = _parse_opencode_verbose(raw)
    assert parsed["oddprovider/cache-only"]["free"] is False


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

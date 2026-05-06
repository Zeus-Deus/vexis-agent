"""Tests for the Phase B tier-resolution helpers in core.yaml_config.

Two functions, two layers of mapping:

- ``subsystem_tier(name)`` — subsystem (e.g. "curator") → abstract tier
  ("small") OR legacy raw model name ("claude-haiku-3-5"). Resolution:
  models.subsystems.<name> → models.<name> (legacy) → built-in default.

- ``model_for_tier(brain_kind, tier)`` — abstract tier → brain-native
  model id (e.g. ("claude-code", "small") → "haiku"). Legacy raw
  strings pass through untranslated. ``None`` / ``"default"`` mean
  "no --model flag, use brain's native default."

Together they cover three user shapes:

1. New schema: ``models.subsystems.curator: small`` →
   ``subsystem_tier("curator")`` returns ``"small"`` →
   ``model_for_tier("claude-code", "small")`` returns ``"haiku"``.
2. Legacy raw model: ``models.curator: haiku`` →
   ``subsystem_tier("curator")`` returns ``"haiku"`` →
   ``model_for_tier("claude-code", "haiku")`` returns ``"haiku"``
   (passes through, since "haiku" is NOT an abstract tier).
3. Default: nothing configured → built-in default per subsystem +
   built-in default tier→model map per brain.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core import yaml_config


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path: Path):
    """Each test gets a clean tmp config path so writes between tests
    don't leak. Avoids relying on the user's real ~/.vexis/config.yaml
    during tests. ``_read_raw`` re-opens the file on every call so no
    cache invalidation is needed."""
    cfg_path = tmp_path / "vexis" / "config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(yaml_config, "_config_path", lambda: cfg_path)
    yield


def _write_config(monkeypatch, content: str) -> None:
    cfg_path = yaml_config._config_path()
    cfg_path.write_text(content, encoding="utf-8")


# ──────────────────────────────────────────────────────────────────
# subsystem_tier — resolution order (new → legacy → default → None)
# ──────────────────────────────────────────────────────────────────


def test_subsystem_tier_returns_default_when_unconfigured(monkeypatch):
    """No config file, no key set — falls back to DEFAULT_SUBSYSTEM_TIERS."""
    assert yaml_config.subsystem_tier("curator") == "small"
    assert yaml_config.subsystem_tier("goal_judge") == "large"
    assert yaml_config.subsystem_tier("relationships_classifier") == "tiny"


def test_subsystem_tier_returns_none_for_unknown_subsystem(monkeypatch):
    """Unknown subsystem name → None (caller decides how to handle —
    typically passes None to spawn_aux which means "no --model")."""
    assert yaml_config.subsystem_tier("nonexistent_subsystem") is None


def test_subsystem_tier_reads_new_schema_first(monkeypatch):
    _write_config(
        monkeypatch,
        """
models:
  subsystems:
    curator: large
""",
    )
    assert yaml_config.subsystem_tier("curator") == "large"


def test_subsystem_tier_falls_back_to_legacy_raw_string(monkeypatch):
    """Pre-Phase-B configs used ``models.<name>: <raw-string>``.
    The shim returns the raw string verbatim — model_for_tier will
    pass it through to --model untranslated."""
    _write_config(
        monkeypatch,
        """
models:
  curator: claude-haiku-3-5
""",
    )
    assert yaml_config.subsystem_tier("curator") == "claude-haiku-3-5"


def test_subsystem_tier_new_schema_wins_over_legacy(monkeypatch):
    """When BOTH ``models.subsystems.curator`` and ``models.curator``
    are set, the new schema wins. Lets users migrate piecemeal —
    add the new key and the old one stops mattering."""
    _write_config(
        monkeypatch,
        """
models:
  curator: claude-haiku-3-5     # legacy
  subsystems:
    curator: large              # new — should win
""",
    )
    assert yaml_config.subsystem_tier("curator") == "large"


def test_subsystem_tier_strips_whitespace(monkeypatch):
    _write_config(
        monkeypatch,
        """
models:
  subsystems:
    curator: "  medium  "
""",
    )
    assert yaml_config.subsystem_tier("curator") == "medium"


def test_subsystem_tier_empty_string_falls_through_to_default(monkeypatch):
    """An empty / whitespace-only value is treated as unset."""
    _write_config(
        monkeypatch,
        """
models:
  subsystems:
    curator: "   "
""",
    )
    assert yaml_config.subsystem_tier("curator") == "small"  # default


# ──────────────────────────────────────────────────────────────────
# model_for_tier — abstract tier → brain-native model
# ──────────────────────────────────────────────────────────────────


def test_model_for_tier_default_claude_code_mapping(monkeypatch):
    """No config — built-in DEFAULT_TIER_MAP_CLAUDE_CODE wins."""
    assert yaml_config.model_for_tier("claude-code", "tiny") == "haiku"
    assert yaml_config.model_for_tier("claude-code", "small") == "haiku"
    assert yaml_config.model_for_tier("claude-code", "medium") == "sonnet"
    assert yaml_config.model_for_tier("claude-code", "large") == "sonnet"


def test_model_for_tier_config_override_per_brain(monkeypatch):
    """Users override the tier mapping when a new model lands."""
    _write_config(
        monkeypatch,
        """
models:
  tiers:
    claude-code:
      large: claude-sonnet-5
      medium: claude-sonnet-4-7
""",
    )
    assert yaml_config.model_for_tier("claude-code", "large") == "claude-sonnet-5"
    assert yaml_config.model_for_tier("claude-code", "medium") == "claude-sonnet-4-7"
    # Tiers not overridden fall back to the built-in default.
    assert yaml_config.model_for_tier("claude-code", "small") == "haiku"


def test_model_for_tier_passes_legacy_raw_strings_through(monkeypatch):
    """A non-abstract value (e.g. legacy raw model name) passes through
    untranslated. This is what makes the legacy-key back-compat work:
    subsystem_tier returns "claude-haiku-3-5", model_for_tier returns
    the same string, brain's spawn_aux then runs --model claude-haiku-3-5."""
    assert yaml_config.model_for_tier("claude-code", "haiku") == "haiku"
    assert yaml_config.model_for_tier("claude-code", "sonnet") == "sonnet"
    assert (
        yaml_config.model_for_tier("claude-code", "claude-haiku-3-5")
        == "claude-haiku-3-5"
    )


def test_model_for_tier_default_sentinel_returns_none(monkeypatch):
    """Sentinel "default" means "let the brain pick its own default"
    — translates to no --model flag at spawn time."""
    assert yaml_config.model_for_tier("claude-code", "default") is None
    assert yaml_config.model_for_tier("claude-code", "DEFAULT") is None  # case-insensitive


def test_model_for_tier_none_input_returns_none(monkeypatch):
    assert yaml_config.model_for_tier("claude-code", None) is None


def test_model_for_tier_empty_string_returns_none(monkeypatch):
    assert yaml_config.model_for_tier("claude-code", "") is None
    assert yaml_config.model_for_tier("claude-code", "   ") is None


def test_model_for_tier_unknown_brain_kind_returns_none_for_abstract(monkeypatch):
    """Abstract tiers + unknown brain → None. Phase C will add
    ``models.tiers.opencode.<tier>`` defaults; until then, opencode
    abstract-tier lookups return None and fall through to the brain's
    native default."""
    assert yaml_config.model_for_tier("opencode", "small") is None


def test_model_for_tier_unknown_brain_kind_passes_raw_through(monkeypatch):
    """Even with an unknown brain kind, raw-string tier values still
    pass through. This means a user with ``models.tiers.opencode.large:
    anthropic/claude-sonnet-4`` works in Phase B (the config is read,
    the value returned), and a legacy ``models.curator: anthropic/...``
    keeps working when the brain switches to opencode."""
    _write_config(
        monkeypatch,
        """
models:
  tiers:
    opencode:
      small: anthropic/claude-haiku-3-5
""",
    )
    assert (
        yaml_config.model_for_tier("opencode", "small")
        == "anthropic/claude-haiku-3-5"
    )


# ──────────────────────────────────────────────────────────────────
# End-to-end: subsystem_tier → model_for_tier composition
# ──────────────────────────────────────────────────────────────────


def test_e2e_new_schema_resolves_to_brain_native_model(monkeypatch):
    _write_config(
        monkeypatch,
        """
models:
  subsystems:
    curator: large
  tiers:
    claude-code:
      large: claude-sonnet-5
""",
    )
    tier = yaml_config.subsystem_tier("curator")
    assert tier == "large"
    assert yaml_config.model_for_tier("claude-code", tier) == "claude-sonnet-5"


def test_e2e_legacy_raw_model_passes_through_unchanged(monkeypatch):
    """Existing user with ``models.curator: haiku`` keeps working —
    the resolver returns "haiku", model_for_tier passes it through,
    brain shells ``--model haiku``. Same byte-identical behaviour as
    pre-Phase-B."""
    _write_config(
        monkeypatch,
        """
models:
  curator: haiku
""",
    )
    tier = yaml_config.subsystem_tier("curator")
    assert tier == "haiku"
    assert yaml_config.model_for_tier("claude-code", tier) == "haiku"


def test_e2e_unset_subsystem_uses_default_tier_and_default_mapping(monkeypatch):
    """User has no ``models:`` section at all — defaults all the way
    through. coherence_judge defaults to "small" → claude-code "small"
    defaults to "haiku"."""
    tier = yaml_config.subsystem_tier("coherence_judge")
    assert tier == "small"
    assert yaml_config.model_for_tier("claude-code", tier) == "haiku"


def test_e2e_unknown_subsystem_returns_none_all_the_way(monkeypatch):
    """Unknown subsystem → None tier → None model. Caller (brain.spawn_aux)
    sees None and skips the --model flag — uses brain's native default."""
    tier = yaml_config.subsystem_tier("there_is_no_such_subsystem")
    assert tier is None
    assert yaml_config.model_for_tier("claude-code", tier) is None

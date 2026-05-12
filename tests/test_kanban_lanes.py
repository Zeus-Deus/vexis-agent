"""Lane resolver tests.

Pinned behaviours:

  * Built-in defaults always resolve.
  * User config overrides default with the same name (full
    replacement, not deep merge).
  * Unknown lane raises LaneNotFoundError with a hint listing
    known names.
  * Malformed user lane that has a default fallback returns the
    default (with a warning); malformed lane with no default
    raises InvalidLaneSpecError via list_lanes (which skips it)
    or via resolve_lane (which raises).
  * tier values are validated (one of tiny/small/medium/large or
    "default" or None) — invalid tier silently falls back.
  * resolve_lane(None) returns the ``default`` built-in.
  * Hot reload: editing the YAML between calls is reflected on
    next call (no caching).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vexis_agent.core.kanban.constants import (
    DEFAULT_DISPATCH_INTERVAL_SECONDS,
    DEFAULT_FAILURE_LIMIT,
    DEFAULT_MAX_CONCURRENT_WORKERS,
    DEFAULT_MAX_RUNTIME_SECONDS,
)
from vexis_agent.core.kanban.lanes import (
    DEFAULT_LANES,
    InvalidLaneSpecError,
    LaneNotFoundError,
    LaneSpec,
    kanban_default_max_runtime_seconds,
    kanban_dispatch_interval_seconds,
    kanban_enabled,
    kanban_failure_limit,
    kanban_max_concurrent_workers,
    lane_names,
    list_lanes,
    resolve_lane,
)


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_yaml_config(monkeypatch, tmp_path):
    """The autouse fixture in conftest patches ``paths.vexis_dir`` but
    ``yaml_config`` did ``from paths import vexis_dir`` at import time,
    so its captured reference still points at the real ``~/.vexis/``.
    Patch ``yaml_config.vexis_dir`` directly so reads land in tmp_path
    too — same posture as ``tests/test_yaml_config_models.py``."""
    private_root = tmp_path / "_vexis_isolated"
    private_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "vexis_agent.core.yaml_config.vexis_dir", lambda: private_root,
    )
    yield


def _write_config(tmp_path: Path, body: str) -> None:
    """Write ``~/.vexis/config.yaml`` to the patched location."""
    cfg = tmp_path / "_vexis_isolated" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(body, encoding="utf-8")


# ──────────────────────────────────────────────────────────────────
# Built-in defaults
# ──────────────────────────────────────────────────────────────────


def test_default_lanes_have_expected_names() -> None:
    """Pin the names so renames force a docs / dashboard review."""
    expected = {"research", "implementation", "review", "ops", "triage", "default"}
    assert set(DEFAULT_LANES.keys()) == expected


def test_default_lanes_have_nonempty_system_prompts() -> None:
    """A lane with no system prompt is just a tier override — that's
    not enough lane definition to ship as a default."""
    for name, spec in DEFAULT_LANES.items():
        assert spec.system_prompt.strip(), f"{name} has empty system_prompt"


def test_default_lanes_resolve_without_user_config() -> None:
    """Empty config → defaults still work."""
    for name in DEFAULT_LANES:
        spec = resolve_lane(name)
        assert spec.name == name


def test_resolve_lane_none_returns_default(tmp_path: Path) -> None:
    spec = resolve_lane(None)
    assert spec.name == "default"


# ──────────────────────────────────────────────────────────────────
# User overrides
# ──────────────────────────────────────────────────────────────────


def test_user_override_replaces_default(tmp_path: Path) -> None:
    _write_config(tmp_path, """
kanban:
  lanes:
    research:
      tier: tiny
      system_prompt: "Custom research prompt"
      skills: ["my-skill"]
    """)
    spec = resolve_lane("research")
    assert spec.tier == "tiny"
    assert spec.system_prompt == "Custom research prompt"
    assert spec.skills == ["my-skill"]


def test_user_can_define_new_lane(tmp_path: Path) -> None:
    _write_config(tmp_path, """
kanban:
  lanes:
    debug:
      tier: large
      system_prompt: "You debug."
      skills: ["shell"]
    """)
    spec = resolve_lane("debug")
    assert spec.name == "debug"
    assert spec.tier == "large"
    assert "debug" in lane_names()


def test_unknown_lane_raises_with_hint(tmp_path: Path) -> None:
    with pytest.raises(LaneNotFoundError) as excinfo:
        resolve_lane("nonexistent")
    msg = str(excinfo.value)
    assert "nonexistent" in msg
    # Hint should mention at least one known lane name.
    assert "research" in msg or "implementation" in msg


# ──────────────────────────────────────────────────────────────────
# Tier validation
# ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tier", ["tiny", "small", "medium", "large"])
def test_valid_tiers_accepted(tmp_path: Path, tier: str) -> None:
    _write_config(tmp_path, f"""
kanban:
  lanes:
    custom:
      tier: {tier}
      system_prompt: "x"
    """)
    spec = resolve_lane("custom")
    assert spec.tier == tier


def test_default_tier_string_means_let_brain_pick(tmp_path: Path) -> None:
    _write_config(tmp_path, """
kanban:
  lanes:
    custom:
      tier: default
      system_prompt: "x"
    """)
    spec = resolve_lane("custom")
    assert spec.tier is None


def test_invalid_tier_falls_back_to_default(tmp_path: Path) -> None:
    """User typed garbage tier → use the default lane's tier."""
    _write_config(tmp_path, """
kanban:
  lanes:
    research:
      tier: humongous
    """)
    spec = resolve_lane("research")
    assert spec.tier == DEFAULT_LANES["research"].tier  # medium


def test_invalid_tier_on_new_lane_uses_none(tmp_path: Path) -> None:
    _write_config(tmp_path, """
kanban:
  lanes:
    new:
      tier: humongous
      system_prompt: "x"
    """)
    spec = resolve_lane("new")
    assert spec.tier is None  # no default to fall back to


# ──────────────────────────────────────────────────────────────────
# Skills + system_prompt validation
# ──────────────────────────────────────────────────────────────────


def test_skills_must_be_list(tmp_path: Path) -> None:
    _write_config(tmp_path, """
kanban:
  lanes:
    research:
      skills: "not-a-list"
    """)
    spec = resolve_lane("research")
    # Falls back to default's skills (empty list in shipped defaults).
    assert spec.skills == DEFAULT_LANES["research"].skills


def test_skills_filters_non_strings(tmp_path: Path) -> None:
    _write_config(tmp_path, """
kanban:
  lanes:
    custom:
      skills: ["good", 123, "also-good"]
      system_prompt: "x"
    """)
    spec = resolve_lane("custom")
    assert spec.skills == ["good", "also-good"]


def test_system_prompt_falls_back_to_default(tmp_path: Path) -> None:
    """Override only the tier; system prompt should keep the default's."""
    _write_config(tmp_path, """
kanban:
  lanes:
    research:
      tier: small
    """)
    spec = resolve_lane("research")
    assert spec.tier == "small"
    assert spec.system_prompt == DEFAULT_LANES["research"].system_prompt


# ──────────────────────────────────────────────────────────────────
# Malformed config
# ──────────────────────────────────────────────────────────────────


def test_lane_value_not_a_mapping_falls_back_when_default_exists(
    tmp_path: Path,
) -> None:
    _write_config(tmp_path, """
kanban:
  lanes:
    research: "this should be a mapping"
    """)
    spec = resolve_lane("research")
    assert spec == DEFAULT_LANES["research"]


def test_lane_value_not_a_mapping_with_no_default_raises(
    tmp_path: Path,
) -> None:
    _write_config(tmp_path, """
kanban:
  lanes:
    new: "garbage"
    """)
    with pytest.raises(InvalidLaneSpecError):
        resolve_lane("new")


def test_list_lanes_skips_malformed_with_no_default(tmp_path: Path) -> None:
    _write_config(tmp_path, """
kanban:
  lanes:
    new: "garbage"
    """)
    names = lane_names()
    assert "new" not in names
    assert "research" in names  # defaults still present


# ──────────────────────────────────────────────────────────────────
# Hot reload (no caching)
# ──────────────────────────────────────────────────────────────────


def test_yaml_edit_hot_reloads(tmp_path: Path) -> None:
    """Edit the YAML between two resolve_lane calls; the second
    call must see the new value. Same posture as subsystem_tier()
    per the CLAUDE.md Invariant."""
    _write_config(tmp_path, """
kanban:
  lanes:
    research:
      tier: small
    """)
    first = resolve_lane("research")
    assert first.tier == "small"
    _write_config(tmp_path, """
kanban:
  lanes:
    research:
      tier: large
    """)
    second = resolve_lane("research")
    assert second.tier == "large"


# ──────────────────────────────────────────────────────────────────
# list_lanes / lane_names
# ──────────────────────────────────────────────────────────────────


def test_list_lanes_includes_defaults_only_when_no_user_config(
    tmp_path: Path,
) -> None:
    names = lane_names()
    for d in DEFAULT_LANES:
        assert d in names


def test_list_lanes_dedupes_user_overrides(tmp_path: Path) -> None:
    """Override one default — list shouldn't show two ``research`` entries."""
    _write_config(tmp_path, """
kanban:
  lanes:
    research:
      tier: tiny
    """)
    names = lane_names()
    assert names.count("research") == 1


# ──────────────────────────────────────────────────────────────────
# Kanban-level config accessors
# ──────────────────────────────────────────────────────────────────


def test_kanban_enabled_default_true(tmp_path: Path) -> None:
    assert kanban_enabled() is True


def test_kanban_enabled_user_can_disable(tmp_path: Path) -> None:
    _write_config(tmp_path, """
kanban:
  enabled: false
    """)
    assert kanban_enabled() is False


def test_max_concurrent_workers_default(tmp_path: Path) -> None:
    assert kanban_max_concurrent_workers() == DEFAULT_MAX_CONCURRENT_WORKERS


def test_max_concurrent_workers_user_override(tmp_path: Path) -> None:
    _write_config(tmp_path, """
kanban:
  max_concurrent_workers: 5
    """)
    assert kanban_max_concurrent_workers() == 5


def test_max_concurrent_workers_invalid_falls_back(tmp_path: Path) -> None:
    _write_config(tmp_path, """
kanban:
  max_concurrent_workers: -1
    """)
    assert kanban_max_concurrent_workers() == DEFAULT_MAX_CONCURRENT_WORKERS


def test_dispatch_interval_seconds_floor(tmp_path: Path) -> None:
    """Floor at 5s — misconfig of 1 falls back to default."""
    _write_config(tmp_path, """
kanban:
  dispatch_interval_seconds: 1
    """)
    assert kanban_dispatch_interval_seconds() == DEFAULT_DISPATCH_INTERVAL_SECONDS


def test_dispatch_interval_seconds_valid_override(tmp_path: Path) -> None:
    _write_config(tmp_path, """
kanban:
  dispatch_interval_seconds: 30
    """)
    assert kanban_dispatch_interval_seconds() == 30


def test_failure_limit_default(tmp_path: Path) -> None:
    assert kanban_failure_limit() == DEFAULT_FAILURE_LIMIT


def test_default_max_runtime_seconds_floor(tmp_path: Path) -> None:
    _write_config(tmp_path, """
kanban:
  default_max_runtime_seconds: 5
    """)
    assert kanban_default_max_runtime_seconds() == DEFAULT_MAX_RUNTIME_SECONDS


# ──────────────────────────────────────────────────────────────────
# LaneSpec serialisation
# ──────────────────────────────────────────────────────────────────


def test_lane_spec_to_dict_round_trip() -> None:
    import json
    spec = LaneSpec(
        name="x", tier="medium", skills=["a"],
        system_prompt="prompt", description="desc",
    )
    raw = json.dumps(spec.to_dict())
    parsed = json.loads(raw)
    assert parsed == {
        "name": "x", "tier": "medium", "skills": ["a"],
        "system_prompt": "prompt", "description": "desc",
    }

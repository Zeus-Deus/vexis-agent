"""Day 1 of model-management UX — validator engine table tests.

The validator is pure: ``validate_models_config(config,
brain_kind, *, available_models_per_brain=None) -> list[ValidationFinding]``.
Tests pass synthetic config dicts directly (no file I/O, no
monkeypatching of ``_read_raw``) and assert on the returned
findings list.

Coverage matrix:
- (brain ∈ {claude-code, opencode, null, unknown})
- × (config-shape ∈ {empty, legacy-only, new-schema-only, mixed,
   tier-overrides-only, invalid-keys, invalid-brain})
- × (subsystem ∈ DEFAULT_SUBSYSTEM_TIERS keys)

Plus a refactor contract test: the new
``subsystem_tier_from_config`` and ``model_for_tier_from_config``
pure helpers must produce byte-identical results to the public
``subsystem_tier`` / ``model_for_tier`` against the same config
dict (mocked through ``_read_raw``).

Design citation: ``.plans/model-management-ux-research.md`` §6 Day 1.
"""

from __future__ import annotations

import pytest

from vexis_agent.core.model_validator import (
    CLAUDE_CODE_FORMAT_FIX_TEMPLATE,
    DEAD_KNOB_FIX_TEMPLATE,
    EMPTY_TIER_FIX_TEMPLATE,
    INVALID_REASONING_FIX_TEMPLATE,
    OPENCODE_FORMAT_FIX_TEMPLATE,
    UNKNOWN_BRAIN_FIX,
    UNKNOWN_MODEL_FIX_TEMPLATE,
    UNKNOWN_SUBSYSTEM_FIX_TEMPLATE,
    ValidationFinding,
    _live_subsystem_callers,
    _reset_live_callers_cache,
    build_resolution_table,
    log_findings,
    validate_models_config,
)
from vexis_agent.core.yaml_config import DEFAULT_SUBSYSTEM_TIERS


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _findings_by_severity(
    findings: list[ValidationFinding],
) -> dict[str, list[ValidationFinding]]:
    out: dict[str, list[ValidationFinding]] = {
        "error": [], "warning": [], "info": [],
    }
    for f in findings:
        out[f.severity].append(f)
    return out


def _has_finding(
    findings: list[ValidationFinding], *,
    severity: str | None = None,
    subsystem: str | None = None,
    problem_substring: str | None = None,
) -> bool:
    for f in findings:
        if severity is not None and f.severity != severity:
            continue
        if subsystem is not None and f.subsystem != subsystem:
            continue
        if (
            problem_substring is not None
            and problem_substring not in f.problem
        ):
            continue
        return True
    return False


# ──────────────────────────────────────────────────────────────────
# ValidationFinding shape pins
# ──────────────────────────────────────────────────────────────────


def test_validation_finding_is_frozen():
    """Frozen so findings can't be mutated after construction —
    they're read-many across surfaces."""
    f = ValidationFinding(
        severity="error", subsystem="curator",
        problem="x", suggested_fix="y",
    )
    with pytest.raises(Exception):
        f.severity = "warning"  # type: ignore[misc]


def test_validation_finding_required_fields():
    """All four fields required; subsystem can be None for
    whole-config issues."""
    f = ValidationFinding(
        severity="warning", subsystem=None, problem="x", suggested_fix="y",
    )
    assert f.subsystem is None
    assert f.severity == "warning"


# ──────────────────────────────────────────────────────────────────
# Empty / minimal configs
# ──────────────────────────────────────────────────────────────────


def test_empty_config_no_brain_findings_only_dead_knob_info():
    """A config with no models block at all gets only the rule-7
    dead-knob info findings (one per dead subsystem). All other
    rules are silent because there's nothing to validate."""
    findings = validate_models_config({}, "claude-code")
    severities = _findings_by_severity(findings)
    assert severities["error"] == []
    assert severities["warning"] == []
    # All info-level findings are dead-knob (rule 7).
    assert all(
        "no live spawn caller" in f.problem
        for f in severities["info"]
    )


def test_empty_models_block_same_as_no_block():
    """``models: {}`` is equivalent to no models block — the
    defaults from DEFAULT_SUBSYSTEM_TIERS take over per-subsystem
    and resolve cleanly on claude-code."""
    findings_a = validate_models_config({}, "claude-code")
    findings_b = validate_models_config({"models": {}}, "claude-code")
    assert len(findings_a) == len(findings_b)


# ──────────────────────────────────────────────────────────────────
# Rule 1 — brain.kind validity (severity: warning)
# ──────────────────────────────────────────────────────────────────


def test_rule1_unknown_brain_kind_warns():
    config = {"brain": {"kind": "claudecode"}}  # missing dash
    findings = validate_models_config(config, "claude-code")
    matches = [
        f for f in findings
        if "brain.kind" in f.problem and f.severity == "warning"
    ]
    assert len(matches) == 1
    assert UNKNOWN_BRAIN_FIX == matches[0].suggested_fix


def test_rule1_severity_is_warning_not_error_matches_daemon_fallback():
    """Pin: severity is WARNING per the audit ask. Matches the
    daemon's brain_kind() actual behavior (warns + falls back, doesn't
    crash). Slash refusal is policy, not severity-driven."""
    config = {"brain": {"kind": "made-up"}}
    findings = validate_models_config(config, "claude-code")
    brain_findings = [f for f in findings if f.subsystem is None and "brain.kind" in f.problem]
    assert all(f.severity == "warning" for f in brain_findings)


def test_rule1_known_brain_kinds_silent():
    for kind in ("claude-code", "opencode", "null"):
        config = {"brain": {"kind": kind}}
        findings = validate_models_config(config, kind)
        assert not any("brain.kind" in f.problem for f in findings)


def test_rule1_empty_brain_kind_silent():
    """An empty/missing brain.kind isn't a typo — daemon defaults
    to claude-code. Don't warn for the no-config case."""
    config = {"brain": {"kind": ""}}
    findings = validate_models_config(config, "claude-code")
    assert not any("brain.kind" in f.problem for f in findings)


# ──────────────────────────────────────────────────────────────────
# Rule 2 — subsystem name validity
# ──────────────────────────────────────────────────────────────────


def test_rule2_unknown_legacy_key_warns():
    config = {"models": {"made_up_subsystem": "sonnet"}}
    findings = validate_models_config(config, "claude-code")
    assert _has_finding(
        findings, severity="warning",
        problem_substring="models.made_up_subsystem is not a recognised",
    )


def test_rule2_known_legacy_key_silent_for_rule2():
    """A known legacy subsystem key passes rule 2 (it's recognised);
    other rules may still fire."""
    config = {"models": {"learning_review": "sonnet"}}
    findings = validate_models_config(config, "claude-code")
    rule2 = [
        f for f in findings
        if "is not a recognised" in f.problem and f.subsystem is None
    ]
    assert rule2 == []


def test_rule2_brain_subsystems_tiers_top_level_keys_silent():
    """The three known top-level special keys (brain, subsystems,
    tiers) under models: don't trip rule 2."""
    config = {
        "models": {
            "brain": "default",
            "subsystems": {},
            "tiers": {},
        }
    }
    findings = validate_models_config(config, "claude-code")
    rule2 = [f for f in findings if "is not a recognised" in f.problem]
    assert rule2 == []


def test_rule2_unknown_new_schema_key_warns():
    config = {"models": {"subsystems": {"made_up": "small"}}}
    findings = validate_models_config(config, "claude-code")
    assert _has_finding(
        findings, severity="warning", subsystem="made_up",
        problem_substring="models.subsystems.made_up is not a recognised",
    )


def test_rule2_known_new_schema_key_silent_for_rule2():
    config = {"models": {"subsystems": {"curator": "small"}}}
    findings = validate_models_config(config, "claude-code")
    rule2 = [
        f for f in findings
        if "is not a recognised" in f.problem and f.subsystem == "curator"
    ]
    assert rule2 == []


# ──────────────────────────────────────────────────────────────────
# Rule 3 — empty resolution (defense in depth)
# ──────────────────────────────────────────────────────────────────


def test_rule3_normal_resolution_no_empty_finding():
    """Default config resolves cleanly; no rule-3 findings."""
    findings = validate_models_config({}, "claude-code")
    assert not _has_finding(
        findings, severity="error",
        problem_substring="resolves to an empty",
    )


# ──────────────────────────────────────────────────────────────────
# Rule 4 — opencode requires provider/model shape
# ──────────────────────────────────────────────────────────────────


def test_rule4_legacy_alias_on_opencode_is_error():
    """The bug docs/migration.md documents: legacy raw-string
    'sonnet' on opencode would crash with 'Model not found:
    sonnet/.'. Validator must catch pre-write."""
    config = {
        "brain": {"kind": "opencode"},
        "models": {"learning_review": "sonnet"},
    }
    findings = validate_models_config(config, "opencode")
    assert _has_finding(
        findings, severity="error", subsystem="learning_review",
        problem_substring="bare alias",
    )


def test_rule4_provider_model_shape_on_opencode_passes():
    config = {
        "brain": {"kind": "opencode"},
        "models": {"learning_review": "anthropic/claude-haiku-3-5"},
    }
    findings = validate_models_config(config, "opencode")
    assert not _has_finding(
        findings, severity="error", subsystem="learning_review",
    )


def test_rule4_abstract_tier_on_opencode_passes():
    """Abstract tiers resolve via DEFAULT_TIER_MAP_OPENCODE which
    produces provider/model shape — no rule-4 finding fires."""
    config = {
        "brain": {"kind": "opencode"},
        "models": {"subsystems": {"learning_review": "small"}},
    }
    findings = validate_models_config(config, "opencode")
    assert not _has_finding(
        findings, severity="error", subsystem="learning_review",
    )


def test_rule4_only_fires_on_opencode_brain():
    """Same legacy raw-string 'sonnet' on claude-code is the
    expected production case — no rule-4 finding."""
    config = {"models": {"learning_review": "sonnet"}}
    findings = validate_models_config(config, "claude-code")
    assert not _has_finding(
        findings, severity="error", subsystem="learning_review",
        problem_substring="bare alias",
    )


def test_rule4_suggested_fix_carries_subsystem_and_model_id():
    config = {
        "brain": {"kind": "opencode"},
        "models": {"goal_judge": "opus"},
    }
    findings = validate_models_config(config, "opencode")
    matches = [
        f for f in findings
        if f.severity == "error" and f.subsystem == "goal_judge"
    ]
    assert len(matches) == 1
    assert "opus" in matches[0].suggested_fix
    assert "goal_judge" in matches[0].suggested_fix


# ──────────────────────────────────────────────────────────────────
# Rule 5 — claude-code with provider/model shape (warning)
# ──────────────────────────────────────────────────────────────────


def test_rule5_provider_model_on_claude_code_warns():
    config = {
        "brain": {"kind": "claude-code"},
        "models": {"learning_review": "anthropic/claude-haiku-3-5"},
    }
    findings = validate_models_config(config, "claude-code")
    assert _has_finding(
        findings, severity="warning", subsystem="learning_review",
        problem_substring="provider/model shape",
    )


def test_rule5_only_warns_does_not_error():
    """Per §4 rule 5: claude-code MAY accept the slashy id as a
    full model name; warn but don't refuse."""
    config = {
        "brain": {"kind": "claude-code"},
        "models": {"learning_review": "anthropic/claude-haiku-3-5"},
    }
    findings = validate_models_config(config, "claude-code")
    rule5 = [
        f for f in findings
        if f.subsystem == "learning_review"
        and "provider/model shape" in f.problem
    ]
    assert all(f.severity == "warning" for f in rule5)


# ──────────────────────────────────────────────────────────────────
# Rule 6 — available-models membership (advisory, requires data)
# ──────────────────────────────────────────────────────────────────


def test_rule6_silent_when_no_discovery_data():
    """Without discovery data, rule 6 is silently skipped."""
    config = {"models": {"learning_review": "claude-mythical-7000"}}
    findings = validate_models_config(config, "claude-code")
    assert not _has_finding(
        findings, problem_substring="isn't in the discovered set",
    )


def test_rule6_resolved_id_in_discovered_set_passes():
    config = {
        "brain": {"kind": "opencode"},
        "models": {"subsystems": {"curator": "small"}},
    }
    findings = validate_models_config(
        config, "opencode",
        available_models_per_brain={
            "opencode": {"anthropic/claude-haiku-3-5", "openai/gpt-4o"},
        },
    )
    assert not _has_finding(
        findings, severity="warning", subsystem="curator",
        problem_substring="isn't in the discovered set",
    )


# ──────────────────────────────────────────────────────────────────
# format_resolution_display (polish-pass display rules, 2026-05-08)
# ──────────────────────────────────────────────────────────────────


def test_format_resolution_display_default_includes_resolved():
    """Pin polish-pass ask 1 + 4: unconfigured subsystem renders
    as ``(default → <resolved>)`` so user sees what reset would
    give them. Mirrors dashboard's ``formatConfiguredCell`` TS
    helper — both surfaces share the same rules."""
    from vexis_agent.core.model_validator import format_resolution_display
    assert format_resolution_display(None, "haiku") == "(default → haiku)"
    assert format_resolution_display(None, "sonnet") == "(default → sonnet)"


def test_format_resolution_display_default_with_no_resolved():
    """Edge case: subsystem has no brain default (e.g. unknown
    subsystem). Falls back to bare ``(default)`` rather than
    rendering a junk arrow."""
    from vexis_agent.core.model_validator import format_resolution_display
    assert format_resolution_display(None, None) == "(default)"


def test_format_resolution_display_passthrough_drops_arrow():
    """Pin polish-pass ask 2: when configured == resolved
    (picker-written model resolves to itself, OR legacy alias
    passthrough), drop the redundant ``X → X`` arrow. Single
    string."""
    from vexis_agent.core.model_validator import format_resolution_display
    # Picker-written: resolves to itself.
    assert (
        format_resolution_display("claude-haiku-4-5-20251001", "claude-haiku-4-5-20251001")
        == "claude-haiku-4-5-20251001"
    )
    # Legacy alias passthrough on claude-code.
    assert format_resolution_display("sonnet", "sonnet") == "sonnet"


def test_format_resolution_display_translation_keeps_arrow():
    """Translation case: tier or unknown alias → resolved model
    name. Show the arrow."""
    from vexis_agent.core.model_validator import format_resolution_display
    assert format_resolution_display("small", "haiku") == "small → haiku"
    assert (
        format_resolution_display("large", "anthropic/claude-sonnet-4")
        == "large → anthropic/claude-sonnet-4"
    )


def test_format_resolution_display_translation_with_no_resolved():
    """Configured but resolved is None (rare — e.g. tier with no
    brain mapping). Show ``X → <brain default>`` so the user
    sees the brain-default fallthrough."""
    from vexis_agent.core.model_validator import format_resolution_display
    assert (
        format_resolution_display("default", None)
        == "default → <brain default>"
    )


def test_rule6_opencode_unknown_id_is_error_post_day_4():
    """Day 4 of model picker UX promoted opencode rule 6 from
    warning → error: opencode rejects unknown model ids at spawn
    with 'Model not found', so the validator should refuse the
    write rather than warn-and-let-it-spawn. claude-code stays
    at warning (covered by the next test) because its discovery
    is curated and goes stale between Anthropic releases."""
    config = {
        "brain": {"kind": "opencode"},
        "models": {"subsystems": {"curator": "anthropic/totally-fake"}},
    }
    findings = validate_models_config(
        config, "opencode",
        available_models_per_brain={
            "opencode": {"anthropic/claude-haiku-3-5", "openai/gpt-4o"},
        },
    )
    assert _has_finding(
        findings, severity="error", subsystem="curator",
        problem_substring="isn't in the discovered set",
    )
    # Pin the inverse: NO warning-level rule-6 finding (so the
    # promotion is exhaustive — we didn't accidentally double-emit).
    assert not _has_finding(
        findings, severity="warning", subsystem="curator",
        problem_substring="isn't in the discovered set",
    )


def test_rule6_claude_code_unknown_id_stays_warning():
    """Pin the per-brain split: claude-code's curated in-process
    list goes stale between Anthropic releases, so refusing a
    newly-released model id would block the user from picking it.
    Rule 6 stays advisory (warning) on claude-code; the spawn
    itself errors gracefully on truly unknown names."""
    config = {
        "brain": {"kind": "claude-code"},
        "models": {"subsystems": {"curator": "claude-mythical-7000"}},
    }
    findings = validate_models_config(
        config, "claude-code",
        available_models_per_brain={
            "claude-code": {"haiku", "sonnet", "opus", "claude-haiku-4-5"},
        },
    )
    assert _has_finding(
        findings, severity="warning", subsystem="curator",
        problem_substring="isn't in the discovered set",
    )
    # And NOT promoted to error.
    assert not _has_finding(
        findings, severity="error", subsystem="curator",
        problem_substring="isn't in the discovered set",
    )


def test_rule6_opencode_abstract_tier_not_flagged():
    """Day 4: rule 6 skips abstract tiers (tiny/small/medium/large).
    Tiers are validated by the tier-resolution layer, not by
    membership in the discovered set — the discovered set holds
    raw model ids only. Without this skip a user picking 'small'
    on opencode would trip the membership check because 'small'
    isn't in ``opencode models`` output."""
    config = {
        "brain": {"kind": "opencode"},
        "models": {"subsystems": {"curator": "small"}},
    }
    findings = validate_models_config(
        config, "opencode",
        available_models_per_brain={
            "opencode": {"anthropic/claude-haiku-3-5", "openai/gpt-4o"},
        },
    )
    # 'small' resolves via DEFAULT_TIER_MAP_OPENCODE to
    # anthropic/claude-haiku-3-5 (which IS in the discovered set
    # above), so no rule 6 finding fires regardless of severity.
    assert not _has_finding(
        findings, subsystem="curator",
        problem_substring="isn't in the discovered set",
    )


def test_rule6_opencode_discovered_id_not_flagged():
    """Pin: a configured id that IS in the discovered set passes
    silently. Sanity check that promotion didn't break the
    happy path."""
    config = {
        "brain": {"kind": "opencode"},
        "models": {"subsystems": {"curator": "anthropic/claude-haiku-3-5"}},
    }
    findings = validate_models_config(
        config, "opencode",
        available_models_per_brain={
            "opencode": {"anthropic/claude-haiku-3-5", "openai/gpt-4o"},
        },
    )
    assert not _has_finding(
        findings, subsystem="curator",
        problem_substring="isn't in the discovered set",
    )


# ──────────────────────────────────────────────────────────────────
# Rule 7 — dead-knob hygiene (info-level)
# ──────────────────────────────────────────────────────────────────


def test_rule7_finds_known_dead_knob_migration_classifier():
    """Today migration_classifier is the one declared-but-not-wired
    subsystem (Day 9 brain-abstraction audit finding). Pin so the
    rule fires for it. After the cleanup pass referenced in the
    suggested-fix, this test would need updating — that's the
    signal the cleanup actually landed."""
    _reset_live_callers_cache()
    findings = validate_models_config({}, "claude-code")
    assert _has_finding(
        findings, severity="info", subsystem="migration_classifier",
        problem_substring="no live spawn caller",
    )


def test_rule7_live_subsystems_silent():
    """Subsystems with real spawn_aux callers (curator, goal_judge,
    coherence_judge, etc.) don't fire rule 7."""
    _reset_live_callers_cache()
    findings = validate_models_config({}, "claude-code")
    info_subsystems = {
        f.subsystem for f in findings if f.severity == "info"
    }
    # Live consumers per the live-callers grep:
    live = {
        "curator", "coherence_judge", "goal_judge",
        "relationships_extractor", "relationships_classifier",
        "learning_review", "learning_triage",
    }
    assert info_subsystems.isdisjoint(live), (
        f"rule 7 false positive on live subsystems: "
        f"{info_subsystems & live}"
    )


def test_rule7_cache_can_be_reset():
    """The test hook clears the cache so a future test that wants
    to monkeypatch the source tree can re-scan."""
    _live_subsystem_callers()  # populate cache
    _reset_live_callers_cache()
    # Re-scan happens on next call without raising.
    callers = _live_subsystem_callers()
    assert isinstance(callers, set)


def test_live_subsystem_callers_finds_expected_set():
    """Pin the live-callers scan against the actual repo state.
    Catches a refactor that accidentally drops a spawn_aux site."""
    _reset_live_callers_cache()
    callers = _live_subsystem_callers()
    expected_live = {
        "curator", "coherence_judge", "goal_judge",
        "relationships_extractor", "relationships_classifier",
        "learning_review", "learning_triage",
    }
    assert expected_live.issubset(callers), (
        f"missing live callers: {expected_live - callers}"
    )


# ──────────────────────────────────────────────────────────────────
# Cross-rule integration — the production config from Day 7's audit
# ──────────────────────────────────────────────────────────────────


def test_production_config_on_claude_code_clean():
    """The real production config (legacy raw-strings, brain.kind
    claude-code) should produce no errors and no warnings other
    than the dead-knob info finding for migration_classifier."""
    config = {
        "brain": {"kind": "claude-code"},
        "models": {
            "brain": "default",
            "learning_review": "sonnet",
            "learning_triage": "haiku",
            "coherence_judge": "sonnet",
            "migration_classifier": "sonnet",
        },
    }
    _reset_live_callers_cache()
    findings = validate_models_config(config, "claude-code")
    by_sev = _findings_by_severity(findings)
    assert by_sev["error"] == [], (
        f"unexpected errors: {by_sev['error']}"
    )
    assert by_sev["warning"] == [], (
        f"unexpected warnings: {by_sev['warning']}"
    )
    # migration_classifier is the only expected info finding.
    info_subs = {f.subsystem for f in by_sev["info"]}
    assert "migration_classifier" in info_subs


def test_production_config_flipped_to_opencode_surfaces_format_errors():
    """Same config, brain.kind=opencode → three legacy raw-strings
    crash. Validator must catch all three pre-write."""
    config = {
        "brain": {"kind": "opencode"},
        "models": {
            "brain": "default",
            "learning_review": "sonnet",
            "learning_triage": "haiku",
            "coherence_judge": "sonnet",
            "migration_classifier": "sonnet",
        },
    }
    _reset_live_callers_cache()
    findings = validate_models_config(config, "opencode")
    error_subs = {
        f.subsystem for f in findings if f.severity == "error"
    }
    assert error_subs >= {"learning_review", "learning_triage", "coherence_judge"}


# ──────────────────────────────────────────────────────────────────
# Refactor contract — pure helpers match public functions
# ──────────────────────────────────────────────────────────────────


def test_subsystem_tier_from_config_matches_public(monkeypatch):
    """Day 1 refactor: ``subsystem_tier_from_config`` is the pure
    helper the validator uses; the public ``subsystem_tier`` is now
    a one-line delegate that reads from disk. They MUST produce
    byte-identical results given the same models section dict.
    Pin the contract."""
    from vexis_agent.core import yaml_config

    test_configs = [
        {},
        {"learning_review": "sonnet"},
        {"subsystems": {"curator": "small"}},
        {"subsystems": {"curator": "small"}, "learning_review": "sonnet"},
        {"subsystems": {"goal_judge": "  large  "}},
        {"subsystems": {"goal_judge": ""}},
        {"subsystems": {"goal_judge": None}},
    ]
    for models_section in test_configs:
        monkeypatch.setattr(
            yaml_config, "_read_raw",
            lambda ms=models_section: {"models": ms},
        )
        for name in DEFAULT_SUBSYSTEM_TIERS:
            from_config = yaml_config.subsystem_tier_from_config(
                models_section, name,
            )
            from_disk = yaml_config.subsystem_tier(name)
            assert from_config == from_disk, (
                f"drift: name={name!r}, "
                f"models={models_section!r}, "
                f"from_config={from_config!r}, from_disk={from_disk!r}"
            )


def test_model_for_tier_from_config_matches_public(monkeypatch):
    """Same refactor contract for the tier→native helper."""
    from vexis_agent.core import yaml_config

    test_configs = [
        ({}, "claude-code", "small"),
        ({}, "claude-code", "large"),
        ({}, "opencode", "tiny"),
        ({}, "opencode", None),
        ({"tiers": {"opencode": {"large": "openai/gpt-4o"}}}, "opencode", "large"),
        ({"tiers": {"claude-code": {"medium": "opus"}}}, "claude-code", "medium"),
        ({}, "claude-code", "default"),
        ({}, "claude-code", "sonnet"),  # raw-string passthrough
        ({}, "future-brain", "small"),  # unknown brain
    ]
    for models_section, brain_kind, tier in test_configs:
        monkeypatch.setattr(
            yaml_config, "_read_raw",
            lambda ms=models_section: {"models": ms},
        )
        from_config = yaml_config.model_for_tier_from_config(
            models_section, brain_kind, tier,
        )
        from_disk = yaml_config.model_for_tier(brain_kind, tier)
        assert from_config == from_disk, (
            f"drift: brain={brain_kind!r}, tier={tier!r}, "
            f"models={models_section!r}, "
            f"from_config={from_config!r}, from_disk={from_disk!r}"
        )


# ──────────────────────────────────────────────────────────────────
# log_findings — startup wiring sanity
# ──────────────────────────────────────────────────────────────────


def test_log_findings_empty_no_op(caplog):
    """No findings → no log lines."""
    import logging
    caplog.set_level(logging.INFO, logger="vexis_agent.core.model_validator")
    log_findings([])
    assert caplog.records == []


def test_log_findings_emits_at_severity_levels(caplog):
    """Each finding logs at its severity level so users can grep
    daemon logs by level."""
    import logging
    caplog.set_level(logging.INFO, logger="vexis_agent.core.model_validator")
    findings = [
        ValidationFinding("error", "x", "err msg", "fix1"),
        ValidationFinding("warning", "y", "warn msg", "fix2"),
        ValidationFinding("info", None, "info msg", "fix3"),
    ]
    log_findings(findings)
    # 3 records, one per finding.
    assert len(caplog.records) == 3
    levels = {rec.levelname for rec in caplog.records}
    assert levels == {"ERROR", "WARNING", "INFO"}
    # Each line includes the suggested_fix copy.
    fixes_in_logs = " ".join(rec.message for rec in caplog.records)
    assert "fix1" in fixes_in_logs
    assert "fix2" in fixes_in_logs
    assert "fix3" in fixes_in_logs


# ──────────────────────────────────────────────────────────────────
# Suggested-fix template constants — pinned for Day 2 import
# ──────────────────────────────────────────────────────────────────


def test_suggested_fix_constants_exported():
    """Day 2's BrainModelNotFoundError imports these constants from
    core.model_validator so the spawn-site backstop emits the same
    copy the validator does. Pin existence of every template."""
    assert isinstance(UNKNOWN_BRAIN_FIX, str) and UNKNOWN_BRAIN_FIX
    assert "{known}" in UNKNOWN_SUBSYSTEM_FIX_TEMPLATE
    assert "{subsystem}" in EMPTY_TIER_FIX_TEMPLATE
    assert "{model_id}" in OPENCODE_FORMAT_FIX_TEMPLATE
    assert "{subsystem}" in OPENCODE_FORMAT_FIX_TEMPLATE
    assert "{model_id}" in CLAUDE_CODE_FORMAT_FIX_TEMPLATE
    assert "{model_id}" in UNKNOWN_MODEL_FIX_TEMPLATE
    assert "{brain_kind}" in UNKNOWN_MODEL_FIX_TEMPLATE
    assert "{subsystem}" in DEAD_KNOB_FIX_TEMPLATE
    assert "{level}" in INVALID_REASONING_FIX_TEMPLATE
    assert "{brain_kind}" in INVALID_REASONING_FIX_TEMPLATE
    assert "{levels}" in INVALID_REASONING_FIX_TEMPLATE


# ──────────────────────────────────────────────────────────────────
# Determinism — same config + brain produces same findings list
# ──────────────────────────────────────────────────────────────────


def test_findings_order_is_deterministic():
    """Daemon log shouldn't churn between starts when nothing
    changed — same config produces same finding order."""
    config = {
        "brain": {"kind": "opencode"},
        "models": {
            "learning_review": "sonnet",
            "coherence_judge": "haiku",
            "goal_judge": "opus",
        },
    }
    _reset_live_callers_cache()
    a = validate_models_config(config, "opencode")
    b = validate_models_config(config, "opencode")
    assert [
        (f.severity, f.subsystem, f.problem) for f in a
    ] == [
        (f.severity, f.subsystem, f.problem) for f in b
    ]


# ──────────────────────────────────────────────────────────────────
# Rule 8 — foreground (chat) model validity (models.brain)
#
# models.brain drives the foreground turn since the model-switching
# UX work (it was display-only before). A bogus value breaks every
# chat turn, so it earns the same model-existence checks subsystems
# get (rules 4/5/6). Findings carry subsystem="brain".
# ──────────────────────────────────────────────────────────────────


def test_rule8_default_foreground_silent():
    """``models.brain: default`` (account default) fires nothing."""
    config = {"models": {"brain": "default"}}
    findings = validate_models_config(config, "claude-code")
    assert not _has_finding(findings, subsystem="brain")


def test_rule8_unset_foreground_silent():
    """No models.brain key at all → account default → silent."""
    findings = validate_models_config({}, "claude-code")
    assert not _has_finding(findings, subsystem="brain")


def test_rule8_valid_tier_foreground_silent():
    """An abstract tier resolves cleanly and fires nothing."""
    config = {"models": {"brain": "large"}}
    findings = validate_models_config(config, "claude-code")
    assert not _has_finding(findings, subsystem="brain")


def test_rule8_bare_alias_on_opencode_is_error():
    """Same trap as rule 4 but for the chat model: a bare alias on
    opencode would crash the foreground turn with 'Model not found'."""
    config = {
        "brain": {"kind": "opencode"},
        "models": {"brain": "sonnet"},
    }
    findings = validate_models_config(config, "opencode")
    assert _has_finding(
        findings, severity="error", subsystem="brain",
        problem_substring="bare alias",
    )


def test_rule8_provider_model_shape_on_opencode_passes():
    config = {
        "brain": {"kind": "opencode"},
        "models": {"brain": "anthropic/claude-haiku-3-5"},
    }
    findings = validate_models_config(config, "opencode")
    assert not _has_finding(
        findings, severity="error", subsystem="brain",
    )


def test_rule8_unknown_model_on_opencode_is_error_with_shared_fix():
    """A typo'd model id, when discovery data is supplied, is a
    hard error on opencode and carries the shared suggested_fix copy
    (same source as subsystems + the spawn-site backstop)."""
    config = {
        "brain": {"kind": "opencode"},
        "models": {"brain": "anthropic/made-up-model"},
    }
    findings = validate_models_config(
        config, "opencode",
        available_models_per_brain={
            "opencode": {"anthropic/claude-haiku-3-5"},
        },
    )
    brain_errors = [
        f for f in findings
        if f.subsystem == "brain" and f.severity == "error"
    ]
    assert brain_errors
    assert brain_errors[0].suggested_fix == UNKNOWN_MODEL_FIX_TEMPLATE.format(
        model_id="anthropic/made-up-model", brain_kind="opencode",
    )


def test_rule8_resolution_table_surfaces_foreground_block():
    """build_resolution_table carries a foreground block with the raw
    configured value, resolved id, and its own findings."""
    config = {"models": {"brain": "sonnet"}}
    table = build_resolution_table(config, "claude-code")
    assert "foreground" in table
    fg = table["foreground"]
    assert fg["configured"] == "sonnet"
    assert fg["resolved_model_id"] == "sonnet"  # raw passthrough on cc
    assert isinstance(fg["findings"], list)


def test_rule8_resolution_table_foreground_default_resolves_none():
    """Unset models.brain → configured None, resolved None (= account
    default / no --model flag)."""
    table = build_resolution_table({}, "claude-code")
    fg = table["foreground"]
    assert fg["configured"] is None
    assert fg["resolved_model_id"] is None


# ──────────────────────────────────────────────────────────────────
# Issue #50 — dict-shaped models.brain + rule 9 (reasoning effort)
#
# ``models.brain`` grew the same {model, reasoning} dict shape the
# subsystems accept. Two properties under test: (a) the model half of
# a dict is validated exactly like the plain string it replaces —
# rule 8 must NOT mistake a dict for "unset" and skip; (b) a configured
# ``reasoning:`` level is checked (warning) against the brain's
# discovered effort vocabulary, applied to both models.brain and every
# models.subsystems.<name>, and silently skipped when no vocabulary was
# discovered so it never false-positives offline.
# ──────────────────────────────────────────────────────────────────


_CC_LEVELS = {"claude-code": {"low", "medium", "high", "xhigh", "max"}}


def test_rule8_dict_shaped_brain_validates_model_half_on_opencode():
    """A dict-shaped models.brain on opencode with a bare-alias model
    still trips rule 8 — the dict must be unpacked, not treated as
    unset. Regression pin for the pre-#50 behaviour where a dict fell
    through to configured=None and skipped ALL validation."""
    config = {
        "brain": {"kind": "opencode"},
        "models": {"brain": {"model": "sonnet", "reasoning": "low"}},
    }
    findings = validate_models_config(config, "opencode")
    assert _has_finding(
        findings, severity="error", subsystem="brain",
        problem_substring="bare alias",
    )


def test_rule8_dict_shaped_brain_valid_model_silent():
    """A dict whose model half is a clean provider/model id on opencode
    fires no model-half error."""
    config = {
        "brain": {"kind": "opencode"},
        "models": {"brain": {
            "model": "anthropic/claude-haiku-3-5", "reasoning": "low",
        }},
    }
    findings = validate_models_config(config, "opencode")
    assert not _has_finding(findings, severity="error", subsystem="brain")


def test_rule9_foreground_bogus_reasoning_warns_when_vocab_supplied():
    """A hand-edited bogus effort level on the chat brain warns when
    the vocabulary is supplied. Carries subsystem='brain' + the shared
    INVALID_REASONING_FIX_TEMPLATE copy."""
    config = {"models": {"brain": {"model": "sonnet", "reasoning": "ultra"}}}
    findings = validate_models_config(
        config, "claude-code",
        available_reasoning_levels_per_brain=_CC_LEVELS,
    )
    matches = [
        f for f in findings
        if f.subsystem == "brain" and "reasoning effort" in f.problem
    ]
    assert len(matches) == 1
    assert matches[0].severity == "warning"
    assert matches[0].suggested_fix == INVALID_REASONING_FIX_TEMPLATE.format(
        level="ultra", brain_kind="claude-code",
        levels="high, low, max, medium, xhigh",
    )


def test_rule9_reasoning_only_dict_still_validated():
    """The reporter's ``{reasoning: ...}`` shape (no model → account
    default) still gets its effort level checked — a bogus level here
    is exactly the hand-edit mistake rule 9 exists for."""
    config = {"models": {"brain": {"reasoning": "bogus"}}}
    findings = validate_models_config(
        config, "claude-code",
        available_reasoning_levels_per_brain=_CC_LEVELS,
    )
    assert _has_finding(
        findings, severity="warning", subsystem="brain",
        problem_substring="reasoning effort",
    )


def test_rule9_valid_foreground_reasoning_silent():
    """A level in the discovered vocabulary fires nothing."""
    config = {"models": {"brain": {"model": "sonnet", "reasoning": "high"}}}
    findings = validate_models_config(
        config, "claude-code",
        available_reasoning_levels_per_brain=_CC_LEVELS,
    )
    assert not _has_finding(
        findings, subsystem="brain", problem_substring="reasoning effort",
    )


def test_rule9_silent_when_no_vocabulary_supplied():
    """No vocabulary at all (the pure-function / offline call shape) →
    rule 9 is silently skipped even on a clearly-bogus level. Never
    false-positive when we can't see the valid set."""
    config = {"models": {"brain": {"model": "sonnet", "reasoning": "ultra"}}}
    findings = validate_models_config(config, "claude-code")
    assert not _has_finding(
        findings, problem_substring="reasoning effort",
    )


def test_rule9_silent_when_brain_vocabulary_empty():
    """Vocabulary supplied but the active brain's set is empty (binary
    missing / offline) → skipped. Empty means 'can't see the levels',
    not 'every level is invalid'."""
    config = {"models": {"brain": {"model": "sonnet", "reasoning": "ultra"}}}
    findings = validate_models_config(
        config, "claude-code",
        available_reasoning_levels_per_brain={"claude-code": set()},
    )
    assert not _has_finding(
        findings, problem_substring="reasoning effort",
    )


def test_rule9_subsystem_bogus_reasoning_warns():
    """The same check applies per-subsystem under the dict shape."""
    config = {
        "models": {"subsystems": {
            "curator": {"model": "small", "reasoning": "ultra"},
        }},
    }
    findings = validate_models_config(
        config, "claude-code",
        available_reasoning_levels_per_brain=_CC_LEVELS,
    )
    assert _has_finding(
        findings, severity="warning", subsystem="curator",
        problem_substring="reasoning effort",
    )


def test_rule9_subsystem_reasoning_survives_model_early_continue():
    """Rule 9 must fire even when the model-half check ``continue``s
    early (rule 4 on opencode). A dict carrying a bare-alias model AND
    a bogus reasoning on opencode surfaces BOTH the model error and the
    reasoning warning."""
    config = {
        "brain": {"kind": "opencode"},
        "models": {"subsystems": {
            "curator": {"model": "sonnet", "reasoning": "ultra"},
        }},
    }
    findings = validate_models_config(
        config, "opencode",
        available_reasoning_levels_per_brain={
            "opencode": {"low", "high"},
        },
    )
    assert _has_finding(
        findings, severity="error", subsystem="curator",
        problem_substring="bare alias",
    )
    assert _has_finding(
        findings, severity="warning", subsystem="curator",
        problem_substring="reasoning effort",
    )


def test_resolution_table_carries_reasoning_on_both_surfaces():
    """build_resolution_table exposes the configured effort level on the
    foreground block AND every subsystem row (None when unset / plain
    string). Dict-shaped configured value renders the MODEL half, not
    the raw dict."""
    config = {
        "models": {
            "brain": {"model": "sonnet", "reasoning": "low"},
            "subsystems": {
                "curator": {"model": "small", "reasoning": "high"},
            },
        },
    }
    table = build_resolution_table(config, "claude-code")
    fg = table["foreground"]
    assert fg["configured"] == "sonnet"   # model half, not the dict
    assert fg["reasoning"] == "low"
    assert fg["resolved_model_id"] == "sonnet"

    curator = next(r for r in table["subsystems"] if r["name"] == "curator")
    assert curator["configured"] == "small"
    assert curator["reasoning"] == "high"
    # A plain-string / unset subsystem carries reasoning=None.
    other = next(r for r in table["subsystems"] if r["name"] == "goal_judge")
    assert other["reasoning"] is None


def test_resolution_table_reasoning_only_brain_renders_default_model():
    """The reporter's shape in the table: model half normalises to None
    (account default → '(default)') while the effort survives."""
    config = {"models": {"brain": {"reasoning": "low"}}}
    table = build_resolution_table(config, "claude-code")
    fg = table["foreground"]
    assert fg["configured"] is None        # account default
    assert fg["resolved_model_id"] is None
    assert fg["reasoning"] == "low"

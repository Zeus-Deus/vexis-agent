"""Model-config validation engine — Day 1 of model-management UX.

Pure function ``validate_models_config(config, brain_kind, *,
available_models_per_brain=None)`` returning a list of
``ValidationFinding`` records. Used by:

  - **Daemon startup** (this Day 1 commit) — ``main.py`` invokes the
    validator after reading ``brain.kind`` and logs each finding at
    severity-appropriate level. Doesn't crash; same posture as
    ``brain_kind()`` falling back on a typo.
  - **Day 2: ``/model`` slash command** — every ``/model set`` runs
    the validator on the proposed-config-after-this-edit and refuses
    to write on ``error``-severity findings.
  - **Day 4: dashboard saves** — same gate, server-side.

Day 1 ships **zero user-facing value**. The only observable effect
is new lines in the daemon's startup log file (``~/.vexis/logs/``)
when the user's existing config has issues. There is no slash
command, no dashboard surface, no behavior change to any in-flight
flow. This is deliberate — the validator is the foundation every
other surface depends on; landing it as one tight, testable commit
keeps the per-day blast radius bounded. Greppable for
``model_validator`` to confirm wiring.

The seven rules are documented in
``.plans/model-management-ux-research.md`` §4 "Validation engine."
The suggested-fix template constants are exported so Day 2's
``BrainModelNotFoundError`` (per the §4 "Spawn-site error
vocabulary" section) can import the same copy — the validator and
the spawn-site backstop must emit identical wording so the user
sees a coherent error story across both surfaces.

Design citation: ``.plans/model-management-ux-research.md`` §4 + §6
Day 1.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from core.yaml_config import (
    DEFAULT_SUBSYSTEM_TIERS,
    VALID_BRAIN_KINDS,
    model_for_tier_from_config,
    subsystem_tier_from_config,
)

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# Public dataclass
# ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ValidationFinding:
    """One severity-tagged finding from the validator.

    ``severity``: ``"error"`` | ``"warning"`` | ``"info"``.
        - ``error``: the slash command and dashboard refuse to write.
          Daemon startup logs at ``ERROR`` level.
        - ``warning``: surfaces in the UI but writes proceed. Daemon
          startup logs at ``WARNING`` level. Matches the daemon's
          existing fall-through-to-default posture.
        - ``info``: hygiene observation. Daemon startup logs at
          ``INFO`` level. Surfaces in the dashboard's "advisory" row.

    ``subsystem``: subsystem name when the finding is per-subsystem;
        ``None`` for whole-config issues (e.g. invalid
        ``brain.kind``).

    ``problem``: human-readable description of what's wrong.

    ``suggested_fix``: actionable text. Same string the spawn-site
        backstop (Day 2's ``BrainModelNotFoundError``) emits when it
        catches the same condition at runtime — see §4 "Spawn-site
        error vocabulary."
    """

    severity: str
    subsystem: str | None
    problem: str
    suggested_fix: str


# ──────────────────────────────────────────────────────────────────
# Suggested-fix template constants
#
# Single source of truth for the wording the validator AND the Day 2
# spawn-site BrainModelNotFoundError emit. Day 2 imports these so
# the exception's ``suggested_fix`` field carries the identical copy
# the validator would have shown pre-write.
# ──────────────────────────────────────────────────────────────────


UNKNOWN_BRAIN_FIX = (
    "Set brain.kind to one of: claude-code, opencode, null. "
    "Daemon falls back to claude-code on invalid input."
)

UNKNOWN_SUBSYSTEM_FIX_TEMPLATE = (
    "Remove the unknown key. Known subsystems: {known}"
)

EMPTY_TIER_FIX_TEMPLATE = (
    "models.subsystems.{subsystem} (or legacy models.{subsystem}) "
    "resolves to empty. Set to a tier "
    "(tiny/small/medium/large) or remove the key."
)

OPENCODE_FORMAT_FIX_TEMPLATE = (
    "'{model_id}' is a bare alias; opencode requires "
    "provider/model shape. Switch to abstract tier 'small' "
    "(resolves to anthropic/claude-haiku-3-5) or pick an explicit "
    "provider/model from /model list opencode. "
    "Run: /model set {subsystem} small"
)

CLAUDE_CODE_FORMAT_FIX_TEMPLATE = (
    "'{model_id}' contains '/' (opencode's provider/model shape). "
    "claude-code expects an alias (sonnet/opus/haiku) or a full "
    "name (claude-sonnet-4-6). If you intended this exact id, "
    "this warning is informational only — claude may still accept "
    "it as a full model id."
)

UNKNOWN_MODEL_FIX_TEMPLATE = (
    "'{model_id}' is not in the discovered model set for "
    "{brain_kind}. May be a stale alias or a typo. Run "
    "/model list {brain_kind} to see what's available."
)

# Spawn-site backstop for claude-code's "model doesn't exist or no
# access" error. The validator can't pre-catch this case for
# claude-code (no live model-discovery probe; rule 6 only fires
# when ``available_models_per_brain`` is supplied which Day 4 only
# wires for opencode). Day 2's BrainModelNotFoundError imports this
# constant when claude-code's spawn_aux detects the on-stdout
# rejection — keeps the suggested_fix wording consistent with the
# rest of the validator's vocabulary.
CLAUDE_CODE_MODEL_NOT_FOUND_FIX_TEMPLATE = (
    "claude-code rejected '{model_id}' for {subsystem}: model may "
    "not exist or you may not have access. Try a known alias "
    "(haiku/sonnet/opus) or a current full name from "
    "https://docs.anthropic.com/claude/models. "
    "Run: /model set {subsystem} small  (resolves to haiku)"
)

DEAD_KNOB_FIX_TEMPLATE = (
    "The '{subsystem}' subsystem is declared in "
    "DEFAULT_SUBSYSTEM_TIERS but no live "
    "subsystem_tier(\"{subsystem}\") caller exists in core/. "
    "Safe to remove from your config; the constant should be "
    "cleaned up in a separate maintenance pass alongside the "
    "CLAUDE.md reorganisation."
)


# ──────────────────────────────────────────────────────────────────
# Top-level keys under ``models:`` that are NOT subsystem names.
# Used by rule 2 to skip false positives when scanning legacy keys.
# ──────────────────────────────────────────────────────────────────


# Note ``brain`` is here rather than in ``DEFAULT_SUBSYSTEM_TIERS``
# because it's a foreground-display-only knob (read by
# ``model_brain()`` for the dashboard, never by ``subsystem_tier``
# for an aux spawn). The validator treats it as a known special key
# so ``models.brain: default`` doesn't trip rule 2.
LEGACY_TOP_LEVEL_KEYS: frozenset[str] = frozenset({
    "brain",        # foreground-display only
    "subsystems",   # new-schema sub-block
    "tiers",        # per-brain tier override sub-block
})


# ──────────────────────────────────────────────────────────────────
# Live-caller scan (rule 7 input)
# ──────────────────────────────────────────────────────────────────


_LIVE_CALLERS_CACHE: set[str] | None = None
_LIVE_CALLER_PATTERN = re.compile(
    r'subsystem_tier\(\s*["\']([\w_]+)["\']'
)


def _live_subsystem_callers() -> set[str]:
    """Return the set of subsystem names with a live
    ``subsystem_tier(<name>)`` call site anywhere in ``core/``.

    Cached on first call; the source tree doesn't change at runtime.
    Tests can clear the cache via :func:`_reset_live_callers_cache`.

    Implementation: literal-string regex grep across all .py files
    under ``core/``. False-positive surface: a docstring or comment
    mentioning the literal call shape — acceptable for v1, since the
    interesting signal is the EMPTY set per name (which a comment
    can't fake into present-but-dead).

    Excludes ``core/model_validator.py`` itself so the test pattern
    we'd write to verify rule 7 doesn't accidentally count as a live
    caller.
    """
    global _LIVE_CALLERS_CACHE
    if _LIVE_CALLERS_CACHE is not None:
        return _LIVE_CALLERS_CACHE

    callers: set[str] = set()
    core_dir = Path(__file__).parent

    for py_path in core_dir.rglob("*.py"):
        if py_path.name == "model_validator.py":
            continue
        try:
            text = py_path.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in _LIVE_CALLER_PATTERN.finditer(text):
            callers.add(match.group(1))

    _LIVE_CALLERS_CACHE = callers
    return callers


def _reset_live_callers_cache() -> None:
    """Test hook: clear the live-callers cache so unit tests can
    re-scan after monkeypatching the source tree (rare). Production
    code never calls this — the cache is correct for the daemon's
    lifetime."""
    global _LIVE_CALLERS_CACHE
    _LIVE_CALLERS_CACHE = None


# ──────────────────────────────────────────────────────────────────
# Per-rule helpers
# ──────────────────────────────────────────────────────────────────


def _check_brain_kind(
    config: dict, brain_kind: str,
) -> list[ValidationFinding]:
    """Rule 1: ``brain.kind`` validity.

    Severity ``warning`` — matches the daemon's actual fallback
    behavior at ``yaml_config.brain_kind()`` (warns + falls back to
    ``claude-code`` on unknown value, doesn't crash). The slash
    command separately refuses to write on this case as a *policy*
    decision (typos are user-hostile to recover from), but the
    severity itself is warning, not error.
    """
    brain_block = config.get("brain") if isinstance(config, dict) else None
    if not isinstance(brain_block, dict):
        return []
    raw_kind = brain_block.get("kind")
    if not isinstance(raw_kind, str) or not raw_kind.strip():
        return []
    cleaned = raw_kind.strip()
    if cleaned in VALID_BRAIN_KINDS:
        return []
    return [
        ValidationFinding(
            severity="warning",
            subsystem=None,
            problem=(
                f"brain.kind={raw_kind!r} is not a recognised brain. "
                f"Daemon will fall back to 'claude-code' at startup."
            ),
            suggested_fix=UNKNOWN_BRAIN_FIX,
        )
    ]


def _check_known_subsystems(
    config: dict, brain_kind: str,
) -> list[ValidationFinding]:
    """Rule 2: subsystem name validity.

    Two sub-checks:
      - 2a (legacy raw-string): every key under ``models:`` that
        isn't a known top-level special (``brain``, ``subsystems``,
        ``tiers``) and isn't a known subsystem is ignored at
        runtime — surface as a warning so the user knows their
        config has dead keys.
      - 2b (new schema): every key under ``models.subsystems.<name>``
        must be a known subsystem; same warning shape.

    ``models.brain`` is a known special key (foreground-display
    only); doesn't trip this rule.
    """
    findings: list[ValidationFinding] = []
    models = config.get("models") if isinstance(config, dict) else None
    if not isinstance(models, dict):
        return findings

    known_csv = ", ".join(sorted(DEFAULT_SUBSYSTEM_TIERS.keys()))

    # 2a: scan legacy raw-string keys at the top level of ``models:``.
    for key in models:
        if not isinstance(key, str):
            continue
        if key in LEGACY_TOP_LEVEL_KEYS:
            continue
        if key in DEFAULT_SUBSYSTEM_TIERS:
            continue
        findings.append(
            ValidationFinding(
                severity="warning",
                subsystem=None,
                problem=(
                    f"models.{key} is not a recognised subsystem; "
                    f"the value is ignored at runtime."
                ),
                suggested_fix=UNKNOWN_SUBSYSTEM_FIX_TEMPLATE.format(
                    known=known_csv,
                ),
            )
        )

    # 2b: scan models.subsystems.<name>.
    subs = models.get("subsystems")
    if isinstance(subs, dict):
        for key in subs:
            if not isinstance(key, str):
                continue
            if key in DEFAULT_SUBSYSTEM_TIERS:
                continue
            findings.append(
                ValidationFinding(
                    severity="warning",
                    subsystem=key,
                    problem=(
                        f"models.subsystems.{key} is not a "
                        f"recognised subsystem; the value is ignored "
                        f"at runtime."
                    ),
                    suggested_fix=UNKNOWN_SUBSYSTEM_FIX_TEMPLATE.format(
                        known=known_csv,
                    ),
                )
            )

    return findings


def _check_per_subsystem(
    config: dict,
    brain_kind: str,
    available_models_per_brain: dict[str, set[str]] | None,
) -> list[ValidationFinding]:
    """Rules 3, 4, 5, 6 — all per-subsystem and require resolving
    the configured tier through the brain. Bundled into one pass so
    each subsystem is resolved exactly once.
    """
    findings: list[ValidationFinding] = []
    models = config.get("models") if isinstance(config, dict) else None
    models_section = models if isinstance(models, dict) else {}

    discovered = (
        available_models_per_brain.get(brain_kind, set())
        if available_models_per_brain
        else set()
    )

    for subsystem in DEFAULT_SUBSYSTEM_TIERS:
        tier = subsystem_tier_from_config(models_section, subsystem)
        resolved = model_for_tier_from_config(
            models_section, brain_kind, tier,
        )

        # Rule 3: empty-string resolution. ``subsystem_tier_from_config``
        # already filters empty strings to ``None`` so the resolved
        # value can only be empty if some upstream code path bypasses
        # the helper. Defense in depth.
        if isinstance(resolved, str) and not resolved.strip():
            findings.append(
                ValidationFinding(
                    severity="error",
                    subsystem=subsystem,
                    problem=(
                        f"{subsystem} resolves to an empty model id."
                    ),
                    suggested_fix=EMPTY_TIER_FIX_TEMPLATE.format(
                        subsystem=subsystem,
                    ),
                )
            )
            continue

        # Rule 3-corollary: ``None`` resolved value means "let the
        # brain pick its native default" — that's intentional, no
        # finding fires for it.
        if resolved is None:
            continue

        # Rule 4: opencode requires provider/model shape.
        if brain_kind == "opencode" and "/" not in resolved:
            findings.append(
                ValidationFinding(
                    severity="error",
                    subsystem=subsystem,
                    problem=(
                        f"{subsystem} resolves to bare alias "
                        f"{resolved!r} on opencode; the spawn would "
                        f"fail with 'Model not found: {resolved}/.'."
                    ),
                    suggested_fix=OPENCODE_FORMAT_FIX_TEMPLATE.format(
                        model_id=resolved, subsystem=subsystem,
                    ),
                )
            )
            continue

        # Rule 5: claude-code with provider/model shape is suspicious.
        if brain_kind == "claude-code" and "/" in resolved:
            findings.append(
                ValidationFinding(
                    severity="warning",
                    subsystem=subsystem,
                    problem=(
                        f"{subsystem} resolves to {resolved!r} "
                        f"(provider/model shape) on claude-code; "
                        f"unusual."
                    ),
                    suggested_fix=CLAUDE_CODE_FORMAT_FIX_TEMPLATE.format(
                        model_id=resolved,
                    ),
                )
            )
            # Don't ``continue`` — rule 6 may still fire for the same
            # subsystem if discovery is enabled. Both findings together
            # give the user the full picture.

        # Rule 6: available-models membership (advisory; only fires
        # when discovery data is supplied).
        if discovered and resolved not in discovered:
            findings.append(
                ValidationFinding(
                    severity="warning",
                    subsystem=subsystem,
                    problem=(
                        f"{subsystem} resolves to {resolved!r} but "
                        f"that id isn't in the discovered set for "
                        f"{brain_kind}."
                    ),
                    suggested_fix=UNKNOWN_MODEL_FIX_TEMPLATE.format(
                        model_id=resolved, brain_kind=brain_kind,
                    ),
                )
            )

    return findings


def _check_dead_knobs(
    config: dict, brain_kind: str,
) -> list[ValidationFinding]:
    """Rule 7: dead-knob hygiene.

    Generic check — for every name in ``DEFAULT_SUBSYSTEM_TIERS``
    that has no live ``subsystem_tier(<name>)`` caller in ``core/``,
    surface an info-level finding. Today this fires for
    ``migration_classifier`` (the Day 9 audit finding); after the
    cleanup pass referenced in the suggested-fix copy it would fire
    for nothing — but the rule stays as a tripwire for "did anyone
    re-add a knob without wiring it?" hygiene drift.
    """
    findings: list[ValidationFinding] = []
    callers = _live_subsystem_callers()
    for subsystem in sorted(DEFAULT_SUBSYSTEM_TIERS):
        if subsystem in callers:
            continue
        findings.append(
            ValidationFinding(
                severity="info",
                subsystem=subsystem,
                problem=(
                    f"{subsystem} declared in DEFAULT_SUBSYSTEM_TIERS "
                    f"but no live spawn caller reads it."
                ),
                suggested_fix=DEAD_KNOB_FIX_TEMPLATE.format(
                    subsystem=subsystem,
                ),
            )
        )
    return findings


# ──────────────────────────────────────────────────────────────────
# Public entry
# ──────────────────────────────────────────────────────────────────


def validate_models_config(
    config: dict,
    brain_kind: str,
    *,
    available_models_per_brain: dict[str, set[str]] | None = None,
) -> list[ValidationFinding]:
    """Run all seven rules against a config dict + brain kind.

    Pure function — no disk I/O, no global state mutation (the
    rule-7 live-callers cache is read-only after first init; tests
    that need a fresh scan call :func:`_reset_live_callers_cache`).

    Args:
      config: the full ``~/.vexis/config.yaml`` parsed dict, OR a
        proposed-after-this-edit dict the slash command builds before
        committing the write. The validator doesn't care which.
      brain_kind: the brain to validate against. The daemon passes
        the result of ``brain_kind()`` here; the slash command
        passes the brain that's about to be active (which may be
        the new value if the user is changing brain.kind in the
        same edit).
      available_models_per_brain: optional discovery data.
        ``{"opencode": {"anthropic/claude-haiku-3-5", ...}}``. When
        provided, rule 6 fires; when None or empty, rule 6 is
        silently skipped (the format-shape rules 4 and 5 still
        fire as best-effort proxies).

    Returns the findings in deterministic order: per-rule, then
    alphabetical by subsystem within rules where it matters.
    Stable order so the daemon log doesn't churn between starts
    when nothing changed.
    """
    findings: list[ValidationFinding] = []
    findings.extend(_check_brain_kind(config, brain_kind))
    findings.extend(_check_known_subsystems(config, brain_kind))
    findings.extend(
        _check_per_subsystem(
            config, brain_kind, available_models_per_brain,
        )
    )
    findings.extend(_check_dead_knobs(config, brain_kind))
    return findings


def brain_instance_to_kind(brain: object) -> str:
    """Map a brain instance to its canonical ``brain.kind`` string.

    Used by the ``check_brain_kind_consistency`` canary so the
    dashboard payload and the slash command's status text can
    both compute the running kind without coupling to the brain
    class hierarchy. Returns ``"<unknown>"`` for any brain class
    not in the registered set.
    """
    name = type(brain).__name__
    return _BRAIN_CLASS_TO_KIND.get(name, "<unknown>")


_BRAIN_CLASS_TO_KIND: dict[str, str] = {
    "ClaudeCodeBrain": "claude-code",
    "OpenCodeBrain": "opencode",
    "BrainNull": "null",
}


def check_brain_kind_consistency(
    on_disk_kind: str, running_kind: str,
) -> ValidationFinding | None:
    """Return a warning finding if the on-disk ``brain.kind`` and
    the running brain instance disagree, else ``None``.

    Day 5's "user edited brain.kind and forgot to restart"
    canary. Fires whenever the dashboard or slash surface is
    refreshed (5 s poll cadence on the dashboard; per-invocation
    on the slash). At daemon startup the two always match by
    construction (``main.py`` reads ``brain.kind`` and
    instantiates accordingly) — the canary fires only after the
    user mutates the on-disk value while the daemon is running.

    Severity ``warning`` — matches the daemon's existing
    fall-back-to-default posture for ``brain.kind`` issues. The
    suggested fix is the literal restart instruction.
    """
    if on_disk_kind == running_kind:
        return None
    return ValidationFinding(
        severity="warning",
        subsystem=None,
        problem=(
            f"on-disk brain.kind={on_disk_kind!r} differs from the "
            f"running brain class ({running_kind!r}). The current "
            f"daemon process is still using {running_kind!r}; the "
            f"new value will take effect on next restart."
        ),
        suggested_fix=(
            f"Restart vexis (e.g. systemctl --user restart "
            f"vexis-agent) to switch from {running_kind!r} to "
            f"{on_disk_kind!r}."
        ),
    )


def build_resolution_table(
    config: dict,
    brain_kind: str,
    *,
    available_models_per_brain: dict[str, set[str]] | None = None,
    running_brain_kind: str | None = None,
) -> dict:
    """Single source of truth for the resolution-table data the
    ``/model status`` slash command, the ``GET /api/v1/models``
    endpoint, and the dashboard Models tab all consume.

    Both the slash command's text rendering and the API endpoint's
    JSON response derive from this dict. The contract test in
    ``tests/test_models_api.py`` pins the per-subsystem resolution
    data byte-for-byte across both surfaces — drift surfaces as a
    test failure.

    Returns a structured dict keyed for JSON serialisation:

    .. code-block:: python

        {
            "brain_kind": "claude-code",
            "subsystems": [
                {
                    "name": "curator",
                    "configured": "small",   # raw value or None
                    "resolved_tier": "small",
                    "resolved_model_id": "haiku",
                    "findings": [...],       # findings for this subsystem
                },
                ...
            ],
            "tier_overrides": {
                "tiny":   {"configured": None,    "default": "haiku"},
                "small":  {"configured": None,    "default": "haiku"},
                "medium": {"configured": "opus",  "default": "sonnet"},
                "large":  {"configured": None,    "default": "sonnet"},
            },
            "brain_inventory": ["claude-code", "null", "opencode"],
            "global_findings": [...],   # whole-config findings (subsystem=None)
        }

    Findings are emitted as plain dicts (severity/subsystem/problem/
    suggested_fix) rather than ValidationFinding instances so the
    payload serialises cleanly.

    Args same as :func:`validate_models_config`.
    """
    models_section = (
        config.get("models") if isinstance(config, dict) else None
    )
    if not isinstance(models_section, dict):
        models_section = {}

    findings = validate_models_config(
        config, brain_kind,
        available_models_per_brain=available_models_per_brain,
    )

    # Bucket findings by subsystem so per-row payloads carry only
    # their own findings; whole-config findings (subsystem=None) go
    # into the top-level global_findings list.
    by_subsystem: dict[str | None, list[ValidationFinding]] = {}
    for f in findings:
        by_subsystem.setdefault(f.subsystem, []).append(f)

    def _finding_dict(f: ValidationFinding) -> dict:
        return {
            "severity": f.severity,
            "subsystem": f.subsystem,
            "problem": f.problem,
            "suggested_fix": f.suggested_fix,
        }

    subs_block = models_section.get("subsystems") if isinstance(
        models_section, dict
    ) else None
    subs_block_dict = subs_block if isinstance(subs_block, dict) else {}

    subsystem_rows: list[dict] = []
    for name in sorted(DEFAULT_SUBSYSTEM_TIERS):
        # Configured value: NEW schema wins over legacy raw-string.
        configured: str | None = None
        v = subs_block_dict.get(name)
        if isinstance(v, str) and v.strip():
            configured = v.strip()
        else:
            v = models_section.get(name)
            if isinstance(v, str) and v.strip():
                configured = v.strip()

        resolved_tier = subsystem_tier_from_config(models_section, name)
        resolved_model_id = model_for_tier_from_config(
            models_section, brain_kind, resolved_tier,
        )

        subsystem_rows.append({
            "name": name,
            "configured": configured,
            "resolved_tier": resolved_tier,
            "resolved_model_id": resolved_model_id,
            "findings": [
                _finding_dict(f) for f in by_subsystem.get(name, [])
            ],
        })

    # Tier overrides per the configured brain. Computed against the
    # built-in default map so the payload tells the user "you set X
    # vs the default Y." Only the active brain's overrides surface
    # — the dashboard's tier-override editor (Day 4) will handle the
    # cross-brain case via per-tab tabs.
    tier_overrides: dict[str, dict[str, str | None]] = {}
    default_map: dict[str, str] = {}
    if brain_kind == "claude-code":
        from core.yaml_config import DEFAULT_TIER_MAP_CLAUDE_CODE
        default_map = DEFAULT_TIER_MAP_CLAUDE_CODE
    elif brain_kind == "opencode":
        from core.yaml_config import DEFAULT_TIER_MAP_OPENCODE
        default_map = DEFAULT_TIER_MAP_OPENCODE

    tiers_section = models_section.get("tiers") if isinstance(
        models_section, dict
    ) else None
    brain_tier_block = (
        tiers_section.get(brain_kind) if isinstance(tiers_section, dict) else None
    )
    brain_tier_block = (
        brain_tier_block if isinstance(brain_tier_block, dict) else {}
    )
    for tier in ("tiny", "small", "medium", "large"):
        v = brain_tier_block.get(tier)
        configured_override: str | None = (
            v.strip() if isinstance(v, str) and v.strip() else None
        )
        tier_overrides[tier] = {
            "configured": configured_override,
            "default": default_map.get(tier),
        }

    # Whole-config findings (subsystem=None) — brain.kind validity,
    # unknown-legacy-key warnings, etc.
    global_findings = [
        _finding_dict(f) for f in by_subsystem.get(None, [])
    ]

    # Day 5 canary: surface the "user edited brain.kind and forgot
    # to restart" warning inline alongside the validator findings.
    # When ``running_brain_kind`` is supplied (dashboard payload or
    # slash status), compare against the on-disk ``brain_kind``
    # parameter and append a warning if they disagree.
    if running_brain_kind is not None:
        consistency = check_brain_kind_consistency(
            brain_kind, running_brain_kind,
        )
        if consistency is not None:
            global_findings.append(_finding_dict(consistency))

    return {
        "brain_kind": brain_kind,
        "subsystems": subsystem_rows,
        "tier_overrides": tier_overrides,
        "brain_inventory": sorted(VALID_BRAIN_KINDS),
        "global_findings": global_findings,
    }


def log_findings(findings: list[ValidationFinding]) -> None:
    """Emit findings to the daemon log at severity-appropriate
    levels. Day 1's startup wiring; Day 2+ surfaces use the
    findings list directly.
    """
    for f in findings:
        prefix = f"model_validator [{f.subsystem or '<global>'}]"
        msg = f"{prefix}: {f.problem} | fix: {f.suggested_fix}"
        if f.severity == "error":
            log.error(msg)
        elif f.severity == "warning":
            log.warning(msg)
        else:
            log.info(msg)

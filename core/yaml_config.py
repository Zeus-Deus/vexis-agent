"""Tiny optional config layer at ``~/.vexis/config.yaml``.

The env-var / .env config in ``core/config.py`` is the source of
truth for daemon credentials and workspace location. This file
supplements it with values that are nicer to keep in YAML.

Canonical schema reference (every block is optional; missing file
→ all defaults; malformed file → warning + defaults):

    # ── brain abstraction (Phase C, see docs/brains.md) ─────────
    brain:
      kind: claude-code           # or "opencode" or "null"; default
                                  # claude-code. Selects which agent
                                  # CLI vexis spawns under. Opencode
                                  # is opt-in — flipping requires the
                                  # legacy-keys → tier-schema migration
                                  # documented in docs/migration.md.

    # ── memory limits ───────────────────────────────────────────
    memory:
      memory_char_limit: 2200
      user_char_limit: 1375

    # ── learning curator ────────────────────────────────────────
    curator:
      enabled: true
      interval_hours: 168
      min_idle_hours: 2
      stale_after_days: 30
      archive_after_days: 90

    # ── per-subsystem model selection ───────────────────────────
    # Phase B (post-rollout) schema: subsystems pick an abstract
    # tier; per-brain tier maps translate to native model ids.
    # See docs/brains.md "Models" + docs/migration.md "Switching
    # to opencode: minimal config" for the legacy raw-string
    # back-compat shim and migration recipe.
    models:
      brain: default              # foreground display only;
                                  # foreground spawn never passes
                                  # --model
      subsystems:                 # NEW (Phase B+): abstract tiers
        learning_review: small
        learning_triage: tiny
        coherence_judge: small
        relationships_extractor: medium
        relationships_classifier: tiny
        goal_judge: large
        curator: small
      tiers:                      # NEW (Phase C): per-brain
        claude-code:              # tier→native overrides; only
          large: sonnet           # set if the built-in defaults
        opencode:                 # at DEFAULT_TIER_MAP_<brain>
          large: anthropic/claude-sonnet-4
          medium: anthropic/claude-sonnet-3-7
      # Legacy raw-string keys (pre-Phase-B) still work on
      # claude-code via passthrough but break opencode (which
      # requires provider/model shape) — see docs/migration.md.

    # ── goals (v3d, see docs/goals.md) ──────────────────────────
    goals:
      enabled: true
      max_turns: 20

    # ── relationships (v3c, see docs/relationships.md) ──────────
    relationships:
      explicit_consent_enabled: false
      approval_hint_enabled: true
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from core.paths import vexis_dir

log = logging.getLogger(__name__)

DEFAULT_MEMORY_CHAR_LIMIT = 2200
DEFAULT_USER_CHAR_LIMIT = 1375
DEFAULT_CURATOR_INTERVAL_HOURS = 168
DEFAULT_CURATOR_MIN_IDLE_HOURS = 2
DEFAULT_CURATOR_STALE_AFTER_DAYS = 30
DEFAULT_CURATOR_ARCHIVE_AFTER_DAYS = 90
DEFAULT_BROWSER_INACTIVITY_TIMEOUT_SECONDS = 120
DEFAULT_BROWSER_ACTION_TIMEOUT_SECONDS = 120
DEFAULT_LEARNING_TICK_INTERVAL_MINUTES = 5
DEFAULT_LEARNING_IDLE_THRESHOLD_MINUTES = 25
DEFAULT_LEARNING_FAILURE_COOLDOWN_HOURS = 1
DEFAULT_LEARNING_MAX_ENTRIES_PER_SESSION = 2
# /goal feature defaults. Enabled by default at v3d Day 4 release
# after the eval gate passed (see ``tests/test_goal_eval.py`` and
# ``docs/goals.md`` for thresholds + how to re-run). Disable via
# ``goals.enabled: false`` in ``~/.vexis/config.yaml`` to silence
# the slash command and the post-turn hook without code changes.
# 20-turn budget mirrors Hermes (`hermes_cli/config.py`) and matches
# ``core.goal_state.DEFAULT_MAX_TURNS``.
DEFAULT_GOALS_ENABLED = True
DEFAULT_GOALS_MAX_TURNS = 20
# /model UX feature flag. Default ON since Day 5 (the rollout
# close after dogfood cleared). The slash command + dashboard
# edit affordances are first-class production surfaces; the
# YAML-edit-and-restart workflow remains supported but is no
# longer required. Set ``model_ux.enabled: false`` in
# ``~/.vexis/config.yaml`` to silence both surfaces explicitly.
# The spawn-site BrainModelNotFoundError backstop fires
# regardless of this flag because it's catching real spawn
# errors that should always have actionable messaging.
DEFAULT_MODEL_UX_ENABLED = True
# Day 4 v2 calibration: raised from 280 → 400. Day 4 eval surfaced
# the LLM consistently producing 290-340 char lessons for technical
# content (multilingual RAG, cinema-time-bound, code-review brevity).
# These were good lessons — specific without being manifestos —
# but the 280 cap rejected them. 400 keeps the manifesto defense
# intact (a single-paragraph rule fits comfortably) while admitting
# legitimate technical detail. The prompt still pushes for ≤300
# typical with 400 as the ceiling.
DEFAULT_LEARNING_MAX_ENTRY_CHARS = 400


def _config_path() -> Path:
    return vexis_dir() / "config.yaml"


def _read_raw() -> dict[str, Any]:
    path = _config_path()
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except FileNotFoundError:
        return {}
    except (OSError, yaml.YAMLError) as exc:
        log.warning("Could not parse %s (%s); using defaults", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _section(name: str) -> dict[str, Any]:
    section = _read_raw().get(name)
    return section if isinstance(section, dict) else {}


def _int_or_default(value: Any, default: int, *, minimum: int = 1) -> int:
    if isinstance(value, bool):
        return default
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return default
    return ivalue if ivalue >= minimum else default


def memory_char_limit() -> int:
    return _int_or_default(
        _section("memory").get("memory_char_limit"),
        DEFAULT_MEMORY_CHAR_LIMIT,
        minimum=64,
    )


def user_char_limit() -> int:
    return _int_or_default(
        _section("memory").get("user_char_limit"),
        DEFAULT_USER_CHAR_LIMIT,
        minimum=64,
    )


def curator_enabled() -> bool:
    raw = _section("curator").get("enabled", True)
    return bool(raw)


def curator_interval_hours() -> int:
    return _int_or_default(
        _section("curator").get("interval_hours"),
        DEFAULT_CURATOR_INTERVAL_HOURS,
    )


def curator_min_idle_hours() -> int:
    return _int_or_default(
        _section("curator").get("min_idle_hours"),
        DEFAULT_CURATOR_MIN_IDLE_HOURS,
        minimum=0,
    )


def curator_stale_after_days() -> int:
    return _int_or_default(
        _section("curator").get("stale_after_days"),
        DEFAULT_CURATOR_STALE_AFTER_DAYS,
    )


def curator_archive_after_days() -> int:
    return _int_or_default(
        _section("curator").get("archive_after_days"),
        DEFAULT_CURATOR_ARCHIVE_AFTER_DAYS,
    )


def _str_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def browser_profiles_dir() -> str | None:
    return _str_or_none(_section("browser").get("profiles_dir"))


def browser_default_profile() -> str | None:
    return _str_or_none(_section("browser").get("default_profile"))


def browser_headless() -> bool:
    raw = _section("browser").get("headless", False)
    return bool(raw)


def browser_inactivity_timeout_seconds() -> int:
    return _int_or_default(
        _section("browser").get("inactivity_timeout_seconds"),
        DEFAULT_BROWSER_INACTIVITY_TIMEOUT_SECONDS,
        minimum=10,
    )


def browser_action_timeout_seconds() -> int:
    return _int_or_default(
        _section("browser").get("action_timeout_seconds"),
        DEFAULT_BROWSER_ACTION_TIMEOUT_SECONDS,
        minimum=5,
    )


def browser_chromium_path() -> str | None:
    return _str_or_none(_section("browser").get("chromium_path"))


def browser_cdp_url() -> str | None:
    """When set, attach to a user-launched Chrome instead of spawning one.

    Example value: ``http://localhost:9222``. The user is responsible
    for launching Chrome with ``--remote-debugging-port=9222`` and
    keeping it alive; Vexis will not kill the externally-launched
    process on shutdown.
    """
    return _str_or_none(_section("browser").get("cdp_url"))


def learning_enabled() -> bool:
    raw = _section("learning").get("enabled", True)
    return bool(raw)


def learning_tick_interval_minutes() -> int:
    return _int_or_default(
        _section("learning").get("tick_interval_minutes"),
        DEFAULT_LEARNING_TICK_INTERVAL_MINUTES,
        minimum=1,
    )


def learning_idle_threshold_minutes() -> int:
    return _int_or_default(
        _section("learning").get("idle_threshold_minutes"),
        DEFAULT_LEARNING_IDLE_THRESHOLD_MINUTES,
        minimum=1,
    )


def learning_failure_cooldown_hours() -> int:
    return _int_or_default(
        _section("learning").get("failure_cooldown_hours"),
        DEFAULT_LEARNING_FAILURE_COOLDOWN_HOURS,
        minimum=0,
    )


def learning_shadow_mode() -> bool:
    """Default True until the eval (§7.4) and one-week soak give a green-light.

    When True, the curator writes proposed entries to MEMORY-SHADOW.md
    (a non-injected file the user reviews). When False, writes go to
    MEMORY.md and land in every future session's system prompt — so
    flipping this is the live-mode switch.
    """
    raw = _section("learning").get("shadow_mode", True)
    return bool(raw)


def learning_max_entries_per_session() -> int:
    return _int_or_default(
        _section("learning").get("max_entries_per_session"),
        DEFAULT_LEARNING_MAX_ENTRIES_PER_SESSION,
        minimum=1,
    )


def learning_max_entry_chars() -> int:
    return _int_or_default(
        _section("learning").get("max_entry_chars"),
        DEFAULT_LEARNING_MAX_ENTRY_CHARS,
        minimum=32,
    )


def learning_triage_enabled() -> bool:
    """Two-tier review feature gate. When True (default), a cheap
    haiku triage call decides whether to run the full sonnet review;
    when False, every eligible session gets the full review (legacy
    behavior). Lets the user disable triage from config without code
    changes if quality regresses."""
    raw = _section("learning").get("triage_enabled", True)
    return bool(raw)


def model_ux_enabled() -> bool:
    """``/model`` slash command + dashboard tab feature gate.

    Default ON since Day 5 (rollout close). The spawn-site
    ``BrainModelNotFoundError`` backstop fires regardless of this
    flag because it's catching real spawn errors that should
    always have actionable messaging — the flag only gates the
    user-facing UX surfaces (slash + dashboard).

    Override via ``model_ux.enabled: false`` in
    ``~/.vexis/config.yaml`` to silence both surfaces explicitly.
    """
    raw = _section("model_ux").get("enabled", DEFAULT_MODEL_UX_ENABLED)
    return bool(raw)


def goals_enabled() -> bool:
    """/goal feature gate. Default OFF (Day 1-3 development). Day 4
    release flips ``DEFAULT_GOALS_ENABLED`` to True after the eval
    gate passes. Until then, the goal hook in the drain loop and the
    ``/goal`` slash command are no-ops at the daemon-config level so
    a user who finds the partially-shipped feature can't accidentally
    invoke it.
    """
    raw = _section("goals").get("enabled", DEFAULT_GOALS_ENABLED)
    return bool(raw)


def goals_max_turns() -> int:
    """Max continuation turns before /goal auto-pauses the loop.

    Protects against judge false negatives (goal actually done but
    judge says continue) and unbounded model spend on fuzzy /
    unachievable goals. ``/goal resume`` resets the counter to 0
    (per `.plans/goal-command-research.md` §4) so the user gets
    another budget without manual config edits.
    """
    return _int_or_default(
        _section("goals").get("max_turns"),
        DEFAULT_GOALS_MAX_TURNS,
        minimum=1,
    )


def browser_screenshot_include_base64() -> bool:
    """Whether ``vexis-browse screenshot`` includes ``image_base64`` by
    default. Off because most harnesses (including Claude Code) read
    the image via the file path with the Read tool, and a multi-MB
    base64 line breaks asyncio.StreamReader's default buffer when it
    rides through the brain's stream-json output. CLI callers can opt
    in per-call with ``--include-base64``.
    """
    raw = _section("browser").get("screenshot_include_base64", False)
    return bool(raw)


# --------------------------------------------------------------------
# [models] — per-subsystem model tier for claude -p subprocess calls
# --------------------------------------------------------------------
#
# Without this block every internal claude -p call (learning review,
# coherence judge, migration classifier) runs against the account's
# default model — typically Opus 4.7 — and competes for plan tokens
# with the user-facing brain. The defaults below pin internal calls
# to Sonnet (cheaper, fast enough for these use cases) while leaving
# the brain on the account default.
#
# ``"default"`` is a sentinel meaning "do not pass --model; use whatever
# claude -p picks on its own". Use this for the brain so user
# conversations track the account's chosen capability tier without
# the daemon second-guessing it.

DEFAULT_MODEL_BRAIN = "default"
DEFAULT_MODEL_LEARNING_REVIEW = "sonnet"
DEFAULT_MODEL_LEARNING_TRIAGE = "haiku"
DEFAULT_MODEL_COHERENCE_JUDGE = "sonnet"
DEFAULT_MODEL_MIGRATION_CLASSIFIER = "sonnet"
DEFAULT_MODEL_RELATIONSHIPS_CLASSIFIER = "sonnet"
# /goal judge — runs after every brain turn in a chat with an active
# standing goal. Sonnet is the right tier here: the judgment is a
# strict yes/no over a (sometimes long) assistant response, and a
# false-positive "done" silently stalls the loop, so we don't want
# haiku-level reliability. Override to ``haiku`` via
# ``models.goal_judge`` in ``~/.vexis/config.yaml`` if the workspace
# values cost over reliability.
DEFAULT_MODEL_GOAL_JUDGE = "sonnet"
# v3c silent-extraction default. Originally haiku per the §4.1
# patch (cheap-model preference). Flipped to sonnet at v3c Day 5
# release gate after eval runs at haiku stalled at 83% positive
# (below the 85% release threshold) on multi-person + strong-cue
# fixtures, even after substring-OR + 60s-timeout + relational-
# referent-prompt fixes. Sonnet hit 100% on the same corpus.
# Cost trade-off accepted: ~7-8 sonnet calls per chatty user-day
# at the current tick volume vs. unreliable extractions at haiku.
# Override to haiku in ``~/.vexis/config.yaml`` if cost matters
# more than reliability for a given workspace.
DEFAULT_MODEL_RELATIONSHIPS_EXTRACTOR = "sonnet"


def _model_tier(key: str, default: str) -> str:
    """Read one model-tier string from the ``[models]`` section.

    Falls back to ``default`` on missing key, non-string values, or
    empty strings. This matches the rest of yaml_config's posture —
    a malformed config never blocks the daemon, it just falls through.
    """
    raw = _read_raw().get("models")
    section = raw if isinstance(raw, dict) else {}
    value = section.get(key, default)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def model_brain() -> str:
    return _model_tier("brain", DEFAULT_MODEL_BRAIN)


def model_learning_review() -> str:
    return _model_tier("learning_review", DEFAULT_MODEL_LEARNING_REVIEW)


def model_learning_triage() -> str:
    """Tier used for the cheap pre-review skim that decides whether to
    spawn the full learning review at all. Defaults to haiku because
    triage is a yes/no judgment over a transcript, not a structured
    classification task."""
    return _model_tier("learning_triage", DEFAULT_MODEL_LEARNING_TRIAGE)


def model_coherence_judge() -> str:
    return _model_tier("coherence_judge", DEFAULT_MODEL_COHERENCE_JUDGE)


def model_migration_classifier() -> str:
    return _model_tier("migration_classifier", DEFAULT_MODEL_MIGRATION_CLASSIFIER)


def model_relationships_classifier() -> str:
    return _model_tier(
        "relationships_classifier", DEFAULT_MODEL_RELATIONSHIPS_CLASSIFIER
    )


def model_goal_judge() -> str:
    return _model_tier("goal_judge", DEFAULT_MODEL_GOAL_JUDGE)


def model_relationships_extractor() -> str:
    """v3c silent-extraction subprocess. Haiku-default per the
    research-doc patch (§4.1 + commit `e2c6155`). Override to
    sonnet via ``models.relationships_extractor`` in
    ``~/.vexis/config.yaml`` if extraction quality is poor."""
    return _model_tier(
        "relationships_extractor", DEFAULT_MODEL_RELATIONSHIPS_EXTRACTOR
    )


# v3c relationships section helpers — gates for the silent queue
# pipeline and the explicit-consent fast lane.


def relationships_explicit_consent_enabled() -> bool:
    """Default: ``False``. When True, the legacy v3b explicit-
    consent path runs on every Telegram message (per-turn regex
    matrix + cursor-claim + classifier subprocess). When False
    (the v3c default), ``_run_relationships_hook`` short-circuits
    at function entry — zero per-message cost — and the candidate
    queue is the only path to RELATIONSHIPS.md.

    Documented in CLAUDE.md as "legacy explicit-consent path."
    """
    raw = _section("relationships").get("explicit_consent_enabled", False)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.strip().lower() in ("true", "yes", "1", "on"):
        return True
    return False


def relationships_approval_hint_enabled() -> bool:
    """Default: ``True``. After a successful approval (slash command
    or dashboard), Vexis appends a one-line hint reminding the user
    that the new fact takes effect on the next session — they may
    want to ``/clear`` to flush the brain's cached prompt. Once the
    user has the mental model, they can flip this off via
    ``relationships.approval_hint_enabled: false`` in
    ``~/.vexis/config.yaml``.
    """
    raw = _section("relationships").get("approval_hint_enabled", True)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.strip().lower() in (
        "false", "no", "0", "off",
    ):
        return False
    return True


def resolve_model_flag(tier: str) -> list[str]:
    """Translate a model-tier string into ``claude -p`` argv flags.

    Returns ``["--model", "<tier>"]`` for any concrete tier
    (``"sonnet"``, ``"haiku"``, ``"opus"``, or a full model id like
    ``"claude-sonnet-4-6"``). Returns ``[]`` for the ``"default"``
    sentinel (or any empty / falsy value), letting ``claude -p`` pick
    its own default.

    Empty list rather than a None return so callers can splat with
    ``*resolve_model_flag(...)`` directly into argv composition without
    a conditional.

    .. deprecated::
        Phase B of the brain abstraction prefers
        :func:`subsystem_tier` + :func:`model_for_tier` so model names
        are brain-agnostic at the subsystem level. ``resolve_model_flag``
        stays for now so legacy direct-spawn paths keep working
        unchanged; once Phase C lands, callers should funnel through
        ``Brain.spawn_aux`` instead and never construct argv themselves.
    """
    if not isinstance(tier, str):
        return []
    cleaned = tier.strip()
    if not cleaned or cleaned.lower() == "default":
        return []
    return ["--model", cleaned]


# --------------------------------------------------------------------
# [models.subsystems] + [models.tiers] — brain-agnostic tier mapping
# --------------------------------------------------------------------
#
# Phase B of the brain abstraction (.plans/brain-abstraction-research.md
# §4 "Tier-name resolution") splits the model-config story in two:
#
#   1. ``models.subsystems.<name>`` maps each subsystem (curator,
#      goal_judge, etc.) to an abstract size tier (``tiny`` /
#      ``small`` / ``medium`` / ``large``). Subsystem code passes
#      this string to ``Brain.spawn_aux(..., model_tier=...)``.
#
#   2. ``models.tiers.<brain-kind>.<tier>`` maps each abstract tier
#      to a brain-native model identifier (``haiku`` for claude-code,
#      ``anthropic/claude-haiku-3-5`` for opencode, etc.).
#      The brain implementation reads its own row at spawn time.
#
# Together: a subsystem says "I want small," each brain knows what
# small means for it, and config edits to switch model versions touch
# one place per brain — not every subsystem.
#
# Back-compat: legacy ``models.<subsystem-name>: <raw-string>`` keys
# (e.g. ``models.curator: claude-haiku-3-5``) still work. The
# subsystem reads ``subsystem_tier(name)`` which returns whatever's
# configured (tier OR raw string); ``model_for_tier`` recognises
# raw strings (anything not in ``ABSTRACT_TIERS``) and passes them
# through untranslated. The legacy keys live alongside the new schema
# for users who haven't migrated.

ABSTRACT_TIERS: frozenset[str] = frozenset({"tiny", "small", "medium", "large"})

# Default tier per subsystem. Picked per ``.plans/brain-abstraction-research.md``
# §4 — quality-sensitive subsystems (goal_judge) get ``large``; cost-sensitive
# high-volume ones (relationships_classifier, learning_triage) get ``tiny``.
# Override via ``models.subsystems.<name>`` in ``~/.vexis/config.yaml``.
DEFAULT_SUBSYSTEM_TIERS: dict[str, str] = {
    "curator": "small",
    "coherence_judge": "small",
    "goal_judge": "large",
    "relationships_extractor": "medium",
    "relationships_classifier": "tiny",
    "learning_review": "small",
    "learning_triage": "tiny",
    "migration_classifier": "small",
}

# Default tier→model map for the claude-code brain. The keys are the
# four abstract tiers; the values are claude-code-native aliases the
# CLI already understands (``--model haiku`` / ``--model sonnet``).
# Override via ``models.tiers.claude-code.<tier>`` in
# ``~/.vexis/config.yaml`` — useful when a new model release lands
# (one-line edit promotes every "large"-tier subsystem in one go).
DEFAULT_TIER_MAP_CLAUDE_CODE: dict[str, str] = {
    "tiny": "haiku",
    "small": "haiku",
    "medium": "sonnet",
    "large": "sonnet",
}

# Default tier→model map for the opencode brain. OpenCode requires
# ``provider/model`` shape rather than bare aliases — the resolved
# string is passed verbatim to ``opencode run --model <id>``.
# Override via ``models.tiers.opencode.<tier>`` in
# ``~/.vexis/config.yaml``. Mirrors the claude-code defaults
# (cheap-tier → haiku, mid+large → sonnet) since the cost / quality
# story is symmetric across providers; a user paying for the
# Anthropic OAuth subscription via OpenCode gets the same models as
# claude-code does.
DEFAULT_TIER_MAP_OPENCODE: dict[str, str] = {
    "tiny": "anthropic/claude-haiku-3-5",
    "small": "anthropic/claude-haiku-3-5",
    "medium": "anthropic/claude-sonnet-3-7",
    "large": "anthropic/claude-sonnet-4",
}


def subsystem_tier_from_config(
    models_section: Any, name: str
) -> str | None:
    """Pure-function variant of :func:`subsystem_tier` that takes the
    ``models`` section dict directly rather than reading from disk.

    Day 1 of model UX (model_management-ux-research.md §6 Day 1)
    extracts this so ``core.model_validator`` can validate hypothetical
    configs (the proposed-config-after-this-edit shape Day 2's slash
    command needs) without monkeypatching ``_read_raw``. Public
    :func:`subsystem_tier` is now a one-line delegate.

    Same resolution order as :func:`subsystem_tier`:
      1. ``models.subsystems.<name>``
      2. ``models.<name>`` (legacy raw-string)
      3. ``DEFAULT_SUBSYSTEM_TIERS[name]``
      4. ``None``
    """
    section = models_section if isinstance(models_section, dict) else {}

    # Path 1: new schema, models.subsystems.<name>
    subs = section.get("subsystems")
    if isinstance(subs, dict):
        v = subs.get(name)
        if isinstance(v, str) and v.strip():
            return v.strip()

    # Path 2: legacy raw-string key, models.<name>
    v = section.get(name)
    if isinstance(v, str) and v.strip():
        return v.strip()

    # Path 3: per-subsystem default
    return DEFAULT_SUBSYSTEM_TIERS.get(name)


def subsystem_tier(name: str) -> str | None:
    """Return the configured tier (or legacy raw model) for a subsystem.

    Reads ``~/.vexis/config.yaml`` on every call (no caching — the
    file is small and config edits should propagate at the next
    spawn boundary). Pure-function variant exposed as
    :func:`subsystem_tier_from_config` for the validator.

    Resolution order:
      1. ``models.subsystems.<name>`` — new schema, returned as-is.
         Expected to be one of the abstract tiers but raw model names
         are also accepted.
      2. ``models.<name>`` — legacy back-compat. Returned as-is;
         ``model_for_tier`` will pass it through to the brain
         untranslated.
      3. ``DEFAULT_SUBSYSTEM_TIERS[name]`` — the per-subsystem default
         from the constant above.
      4. ``None`` — only when ``name`` is unknown AND no legacy key
         is set. The brain's ``spawn_aux`` treats ``None`` as "use the
         brain's native default model" (no ``--model`` flag).

    Subsystem callers pass the result directly to
    ``Brain.spawn_aux(prompt, model_tier=subsystem_tier("curator"))``;
    the brain implementation handles tier→native translation.
    """
    return subsystem_tier_from_config(_read_raw().get("models"), name)


def model_for_tier_from_config(
    models_section: Any, brain_kind: str, tier: str | None,
) -> str | None:
    """Pure-function variant of :func:`model_for_tier` that takes the
    ``models`` section dict directly rather than reading from disk.

    Day 1 of model UX extracts this so ``core.model_validator`` can
    validate hypothetical configs without monkeypatching
    ``_read_raw``. Public :func:`model_for_tier` is now a one-line
    delegate.
    """
    if tier is None:
        return None
    cleaned = tier.strip()
    if not cleaned or cleaned.lower() == "default":
        return None

    # Legacy raw-model string — pass through untranslated.
    if cleaned not in ABSTRACT_TIERS:
        return cleaned

    # Abstract tier — look up in config first, then per-brain defaults.
    if isinstance(models_section, dict):
        tiers_section = models_section.get("tiers")
        if isinstance(tiers_section, dict):
            brain_section = tiers_section.get(brain_kind)
            if isinstance(brain_section, dict):
                v = brain_section.get(cleaned)
                if isinstance(v, str) and v.strip():
                    return v.strip()

    if brain_kind == "claude-code":
        return DEFAULT_TIER_MAP_CLAUDE_CODE.get(cleaned)
    if brain_kind == "opencode":
        return DEFAULT_TIER_MAP_OPENCODE.get(cleaned)

    return None


def model_for_tier(brain_kind: str, tier: str | None) -> str | None:
    """Translate an abstract tier or legacy raw-model into the brain's
    native model identifier.

    Returns:
      * ``None`` when ``tier`` is ``None``, ``""``, or the literal
        sentinel ``"default"`` — the brain's ``spawn_aux`` should not
        pass a ``--model`` flag (use whatever the brain CLI picks on
        its own).
      * The mapped native model id (e.g. ``"haiku"``, ``"sonnet"``)
        for an abstract tier (``tiny`` / ``small`` / ``medium`` /
        ``large``). Resolution order: ``models.tiers.<brain-kind>.<tier>``
        then the per-brain default constant
        (``DEFAULT_TIER_MAP_CLAUDE_CODE`` for claude-code).
      * The ``tier`` string itself unchanged when it is *not* an
        abstract tier — covers the legacy-raw-model case
        (``models.curator: claude-haiku-3-5`` passes
        ``"claude-haiku-3-5"`` through as-is so the brain just shells
        ``--model claude-haiku-3-5``).

    For the unknown brain-kind case (anything other than
    ``"claude-code"`` in Phase B), abstract tiers fall through to
    ``None`` — Phase C will add ``models.tiers.opencode.<tier>``
    defaults.

    Pure-function variant exposed as :func:`model_for_tier_from_config`
    for the validator (which works on hypothetical config dicts,
    not the on-disk file).
    """
    return model_for_tier_from_config(
        _read_raw().get("models"), brain_kind, tier,
    )


# --------------------------------------------------------------------
# [brain] — which agent CLI to spawn under
# --------------------------------------------------------------------
#
# ``brain.kind`` selects the implementation ``main.py`` instantiates.
# Three values:
#   - ``claude-code`` (default) — ``ClaudeCodeBrain`` against the
#     ``claude`` CLI binary. Pre-Phase-C behaviour, unchanged.
#   - ``opencode`` — ``OpenCodeBrain`` against the ``opencode`` CLI
#     binary. Phase C scaffold (Day 3); transcript readback lands
#     Day 4. Foreground turns work end-to-end on Day 3; the
#     curator's per-tick eligibility scan sees no sessions until
#     Day 4 lands the SQL reader, which is the right answer
#     anyway since OpenCode hasn't run any vexis sessions yet at
#     Day 3.
#   - ``null`` — ``BrainNull``, the test fake. Useful for a vexis
#     that's running but should never spawn a real model (e.g.
#     dashboard-only smoke).
#
# The flag is read by ``main.py`` once at startup. Changes require
# a daemon restart.

VALID_BRAIN_KINDS: frozenset[str] = frozenset(
    {"claude-code", "opencode", "null"}
)


def brain_kind() -> str:
    """Read ``brain.kind`` from ``~/.vexis/config.yaml``.

    Default ``"claude-code"``. Unknown values fall back to the
    default with a warning so a typo can't strand a user without a
    brain to spawn under. Validation happens here rather than at
    construction time so the warning fires once at startup, not on
    every brain method call.
    """
    raw = _section("brain").get("kind", "claude-code")
    if not isinstance(raw, str) or not raw.strip():
        return "claude-code"
    cleaned = raw.strip()
    if cleaned not in VALID_BRAIN_KINDS:
        log.warning(
            "Unknown brain.kind=%r in config.yaml; falling back to "
            "'claude-code'. Valid values: %s",
            cleaned, sorted(VALID_BRAIN_KINDS),
        )
        return "claude-code"
    return cleaned

"""Lanes — vexis's lightweight replacement for Hermes profiles.

A **lane** is the worker-type discriminator for a kanban task. It is
NOT a separate identity — same brain, same memory, same learning
curator. A lane = ``(system_prompt, skills, tier)``: three knobs
that tell the dispatcher how to spawn a worker for a task assigned
to that lane.

See ``.plans/kanban-research.md`` §4 for the design rationale (why
lanes ≠ Hermes profiles).

User config under ``kanban.lanes:`` in ``~/.vexis/config.yaml``
overrides defaults or adds new lanes. Built-in lanes are bundled
in code (:data:`DEFAULT_LANES`) so an empty config still gives the
user a working board.

Resolution rules (:func:`resolve_lane`):

  1. If the name appears in the user's ``kanban.lanes:`` map, that
     wins (full replacement, not deep merge — the lane is one
     coherent thing).
  2. Else if the name is a built-in default, return that.
  3. Else raise :class:`LaneNotFoundError` so the dispatcher /
     MCP tool surfaces a clear error instead of silently picking
     a wrong lane.

The dispatcher reads lanes per-spawn (no caching), so YAML edits
hot-reload at the next dispatcher tick. Same hot-reload posture as
``subsystem_tier()`` per the CLAUDE.md Invariant.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from vexis_agent.core.yaml_config import _section

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────
# LaneSpec
# ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LaneSpec:
    """One lane definition.

    ``name`` — lane identifier; matches ``tasks.lane`` in the DB.
    ``tier`` — abstract size tier passed to ``brain.spawn_aux``
        (one of ``tiny``/``small``/``medium``/``large``, or ``None``
        meaning "let the brain pick"). Per the CLAUDE.md Invariant
        the brain translates tier → native model id.
    ``skills`` — list of skill names the worker is allowed to use.
        Empty list = no extra skills (the worker still gets the
        kanban_* MCP tools — those are the worker contract).
    ``system_prompt`` — text appended after ``KANBAN_WORKER_PREFIX``
        in the worker's first user-turn. Tells the worker its
        persona and constraints. Multi-line OK.
    ``description`` — short human-readable label for the dashboard
        lane picker. Optional.
    """

    name: str
    tier: str | None = None
    skills: list[str] = field(default_factory=list)
    system_prompt: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tier": self.tier,
            "skills": list(self.skills),
            "system_prompt": self.system_prompt,
            "description": self.description,
        }


# ──────────────────────────────────────────────────────────────────
# Errors
# ──────────────────────────────────────────────────────────────────


class LaneError(Exception):
    """Base for lane-resolution errors."""


class LaneNotFoundError(LaneError):
    """The given lane name is neither a default nor in user config.

    Carries an actionable hint listing the known lane names so the
    dashboard / MCP tool error message tells the user how to fix it.
    """

    def __init__(self, name: str, known: list[str]) -> None:
        self.name = name
        self.known = known
        hint = ", ".join(sorted(known)) if known else "(none)"
        super().__init__(
            f"unknown lane: {name!r}. Known lanes: {hint}. Define new "
            f"lanes under kanban.lanes: in ~/.vexis/config.yaml."
        )


class InvalidLaneSpecError(LaneError):
    """A user-supplied lane definition is malformed (wrong types, etc).

    Falls back to the built-in default (if one exists by the same
    name) at resolution time; this exception is raised by
    :func:`list_lanes` when the caller wants a strict listing.
    """


# ──────────────────────────────────────────────────────────────────
# Built-in defaults
# ──────────────────────────────────────────────────────────────────

# Default lanes ship with vexis so a fresh install with no
# ``kanban.lanes:`` config still has a working board. Names + tiers
# + skill bundles deliberately match the spec doc (§4).
#
# Tier choices reflect the work each lane does:
#   - triage: classification + routing → tiny/small is plenty
#   - research / review / ops: medium thinking
#   - implementation: large (codegen-heavy)
#
# Skills are placeholder string identifiers — the spawn site
# (Phase 3) translates these into the brain's tool allowlist.
# Today's vexis has no formal skill registry past USER.md, so the
# default lanes ship with empty skills and the system_prompt
# carries the persona; lane.skills becomes load-bearing once the
# skill index lands.
DEFAULT_LANES: dict[str, LaneSpec] = {
    "research": LaneSpec(
        name="research",
        tier="medium",
        skills=[],
        description="Reads sources, summarises, cites. Does not edit code.",
        system_prompt=(
            "You research. Read sources, summarise findings, cite where "
            "things came from. Do not edit the codebase unless the task "
            "explicitly says so. Use kanban_complete with a structured "
            "summary when you're done."
        ),
    ),
    "implementation": LaneSpec(
        name="implementation",
        tier="large",
        skills=[],
        description="Implements small, well-scoped code changes.",
        system_prompt=(
            "You implement. Write small, well-scoped code changes. Add "
            "or update tests when you change behaviour. Run the tests. "
            "Report what you changed and what's still outstanding via "
            "kanban_complete."
        ),
    ),
    "review": LaneSpec(
        name="review",
        tier="medium",
        skills=[],
        description="Critiques code, identifies risk, suggests improvements. Read-only.",
        system_prompt=(
            "You review. Read the changes referenced by the task, "
            "critique them, identify risk, suggest improvements. Do not "
            "edit files. Summarise via kanban_complete; if blocked or "
            "missing context, kanban_block with a clear reason."
        ),
    ),
    "ops": LaneSpec(
        name="ops",
        tier="medium",
        skills=[],
        description="Runs commands, checks service health, reports status.",
        system_prompt=(
            "You operate. Run commands, check service health, report "
            "status. Confirm before destructive actions; if a destructive "
            "step is required and unconfirmed, kanban_block with the "
            "command you'd run and ask the user. Use kanban_complete "
            "with the captured output."
        ),
    ),
    "triage": LaneSpec(
        name="triage",
        tier="small",
        skills=[],
        description="Classifies and routes; doesn't do the work itself.",
        system_prompt=(
            "You triage. Classify the task, decide which specialised "
            "lane should do it, and use kanban_create to fan out child "
            "tasks to the right lane. Do not do the work yourself. "
            "kanban_complete this triage task with a one-line summary "
            "of how you decomposed it."
        ),
    ),
    # Fallback when a task has no lane assigned. Generic prompt; the
    # dispatcher uses this when ``task.lane is None``.
    "default": LaneSpec(
        name="default",
        tier=None,           # let brain pick
        skills=[],
        description="Generic worker; used when no lane is assigned.",
        system_prompt=(
            "You are a kanban worker. Read the task title and body, do "
            "the work, and use kanban_complete with a short summary "
            "when done. If blocked, use kanban_block with a clear reason."
        ),
    ),
}


# ──────────────────────────────────────────────────────────────────
# Resolution
# ──────────────────────────────────────────────────────────────────


_VALID_TIERS: frozenset[str] = frozenset({"tiny", "small", "medium", "large"})


def _coerce_lane(name: str, raw: Any) -> LaneSpec:
    """Build a LaneSpec from a user-config dict. Tolerant: missing
    fields fall back to defaults; wrong-type fields log a warning
    and use the default for that field. Empty/None ``raw`` is
    treated as "use defaults entirely" (returns the default lane
    by that name, or a bare lane if none).
    """
    default = DEFAULT_LANES.get(name)
    if not isinstance(raw, dict):
        if default is not None:
            return default
        raise InvalidLaneSpecError(
            f"lane {name!r} in user config is not a mapping"
        )
    tier_raw = raw.get("tier")
    tier: str | None
    if tier_raw is None:
        tier = default.tier if default else None
    elif isinstance(tier_raw, str) and (
        tier_raw.lower() in _VALID_TIERS or tier_raw.lower() == "default"
    ):
        tier = (
            None
            if tier_raw.lower() == "default"
            else tier_raw.lower()
        )
    else:
        log.warning(
            "kanban.lanes.%s.tier %r is not a valid tier "
            "(tiny/small/medium/large/default); using default",
            name, tier_raw,
        )
        tier = default.tier if default else None
    skills_raw = raw.get("skills", [])
    if isinstance(skills_raw, list):
        skills = [str(s) for s in skills_raw if isinstance(s, str)]
    else:
        log.warning(
            "kanban.lanes.%s.skills is not a list; ignoring", name,
        )
        skills = list(default.skills) if default else []
    sp_raw = raw.get("system_prompt")
    if isinstance(sp_raw, str):
        system_prompt = sp_raw
    elif sp_raw is None:
        system_prompt = default.system_prompt if default else ""
    else:
        log.warning(
            "kanban.lanes.%s.system_prompt is not a string; ignoring",
            name,
        )
        system_prompt = default.system_prompt if default else ""
    desc_raw = raw.get("description")
    if isinstance(desc_raw, str):
        description = desc_raw
    elif desc_raw is None:
        description = default.description if default else ""
    else:
        description = default.description if default else ""
    return LaneSpec(
        name=name,
        tier=tier,
        skills=skills,
        system_prompt=system_prompt,
        description=description,
    )


def _user_lanes_raw() -> dict[str, Any]:
    """Return the raw ``kanban.lanes:`` map from disk, or ``{}``.

    Re-reads each call (no cache) so YAML edits hot-reload at the
    next dispatcher tick — same posture as ``subsystem_tier()``.
    """
    section = _section("kanban")
    lanes = section.get("lanes")
    return lanes if isinstance(lanes, dict) else {}


def resolve_lane(name: str | None) -> LaneSpec:
    """Return the LaneSpec for ``name``.

    ``name=None`` resolves to the ``default`` lane (the dispatcher
    uses this when a task has no lane assigned).

    Raises :class:`LaneNotFoundError` if ``name`` is neither a
    user-defined lane nor a built-in default. The error message
    lists the known lane names so the caller can surface an
    actionable hint.
    """
    if name is None:
        return DEFAULT_LANES["default"]
    user = _user_lanes_raw()
    if name in user:
        try:
            return _coerce_lane(name, user[name])
        except InvalidLaneSpecError as exc:
            log.warning(
                "kanban.lanes.%s is malformed (%s); falling back to default",
                name, exc,
            )
            if name in DEFAULT_LANES:
                return DEFAULT_LANES[name]
            raise
    if name in DEFAULT_LANES:
        return DEFAULT_LANES[name]
    known = list(set(DEFAULT_LANES.keys()) | set(user.keys()))
    raise LaneNotFoundError(name, known)


def list_lanes() -> list[LaneSpec]:
    """All known lanes (user overrides win over defaults). Sorted by
    name for stable rendering. Used by the dashboard lane picker
    and the Telegram ``/kanban add --lane=?`` autocomplete (future).
    """
    user = _user_lanes_raw()
    names: set[str] = set(DEFAULT_LANES.keys()) | set(user.keys())
    out: list[LaneSpec] = []
    for n in sorted(names):
        try:
            out.append(resolve_lane(n))
        except LaneError:
            # Malformed user lane that has no default — skip from list
            # but keep it surfaceable through resolve_lane (raises).
            log.warning(
                "skipping lane %r in list_lanes due to malformed config",
                n,
            )
    return out


def lane_names() -> list[str]:
    """Just the names. Cheaper than :func:`list_lanes` when the
    caller doesn't need the full specs (validation paths)."""
    return [lane.name for lane in list_lanes()]


# ──────────────────────────────────────────────────────────────────
# Kanban-level config accessors (mirrors yaml_config.py pattern)
# ──────────────────────────────────────────────────────────────────


def kanban_enabled() -> bool:
    raw = _section("kanban").get("enabled", True)
    return bool(raw)


def kanban_max_concurrent_workers() -> int:
    """Default 2 — bounded by your brain's rate limit. Bump only if
    you've measured the cost (each worker is a fresh prompt-cache
    miss for the system prompt)."""
    from vexis_agent.core.kanban.constants import (
        DEFAULT_MAX_CONCURRENT_WORKERS,
    )
    raw = _section("kanban").get("max_concurrent_workers")
    if not isinstance(raw, int) or isinstance(raw, bool):
        return DEFAULT_MAX_CONCURRENT_WORKERS
    if raw < 1:
        return DEFAULT_MAX_CONCURRENT_WORKERS
    return raw


def kanban_dispatch_interval_seconds() -> int:
    from vexis_agent.core.kanban.constants import (
        DEFAULT_DISPATCH_INTERVAL_SECONDS,
    )
    raw = _section("kanban").get("dispatch_interval_seconds")
    if not isinstance(raw, int) or isinstance(raw, bool):
        return DEFAULT_DISPATCH_INTERVAL_SECONDS
    # Floor at 5s so we don't peg the CPU on misconfig.
    if raw < 5:
        return DEFAULT_DISPATCH_INTERVAL_SECONDS
    return raw


def kanban_failure_limit() -> int:
    from vexis_agent.core.kanban.constants import DEFAULT_FAILURE_LIMIT
    raw = _section("kanban").get("failure_limit")
    if not isinstance(raw, int) or isinstance(raw, bool):
        return DEFAULT_FAILURE_LIMIT
    if raw < 1:
        return DEFAULT_FAILURE_LIMIT
    return raw


def kanban_default_max_runtime_seconds() -> int:
    from vexis_agent.core.kanban.constants import (
        DEFAULT_MAX_RUNTIME_SECONDS,
    )
    raw = _section("kanban").get("default_max_runtime_seconds")
    if not isinstance(raw, int) or isinstance(raw, bool):
        return DEFAULT_MAX_RUNTIME_SECONDS
    if raw < 30:
        return DEFAULT_MAX_RUNTIME_SECONDS
    return raw


def kanban_claim_ttl_seconds() -> int:
    from vexis_agent.core.kanban.constants import DEFAULT_CLAIM_TTL_SECONDS
    raw = _section("kanban").get("claim_ttl_seconds")
    if not isinstance(raw, int) or isinstance(raw, bool):
        return DEFAULT_CLAIM_TTL_SECONDS
    if raw < 30:
        return DEFAULT_CLAIM_TTL_SECONDS
    return raw


__all__ = [
    "DEFAULT_LANES",
    "InvalidLaneSpecError",
    "LaneError",
    "LaneNotFoundError",
    "LaneSpec",
    "kanban_claim_ttl_seconds",
    "kanban_default_max_runtime_seconds",
    "kanban_dispatch_interval_seconds",
    "kanban_enabled",
    "kanban_failure_limit",
    "kanban_max_concurrent_workers",
    "lane_names",
    "list_lanes",
    "resolve_lane",
]

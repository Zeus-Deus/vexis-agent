"""ConsentToken minting + verification + in-memory PendingTokens
registry.

Per research doc §3.4:

- One token per ``(session_uuid, turn_index, person_slug)`` —
  carries N facts (single-turn, NOT single-fact).
- Tokens live in-memory only. Never persisted, never logged.
- Daemon restart drops the registry; recovery is a bounded
  classifier re-run on the source turn (see
  ``core/relationships/curator.py`` ``recover_after_restart``).
- Mint API takes the source turn and the extracted fact list;
  ``fact_ids`` are derived deterministically from the fact texts
  so promotion can verify "no third fact was sneaked into the
  shadow file under this token."

Threat-scanner bypass at ``core/learning_review.py:1150`` is
suspended for ``target_file="relationships"`` only when the
caller has verified a token via ``verify_for_promotion`` here.
The scanner itself doesn't see tokens; the call site in the
RelationshipsCurator wraps the scan call only after a successful
verify.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# Verdict labels match Verdict in core/relationships/triggers.py;
# duplicated here as plain strings to avoid an import cycle when
# the curator threads them through.
_ALLOWED_VERDICTS = frozenset({"ADD", "DELETE", "SUPERSEDE"})


def _fact_id(fact_text: str) -> str:
    """Deterministic fact-id from the canonical fact text.

    Used so the promotion check can refuse a shadow file that's
    been edited to contain a fact the original consent didn't
    cover (e.g. an attacker appending "is HIV positive" to
    "likes mystery novels" inside the YAML's bullet list).
    """
    return hashlib.sha256(fact_text.strip().encode("utf-8")).hexdigest()[:16]


def derive_fact_ids(facts: list[str]) -> tuple[str, ...]:
    return tuple(_fact_id(f) for f in facts)


@dataclass(frozen=True)
class ConsentToken:
    """In-memory consent token. Single-turn, per-person, multi-fact.

    The token is opaque to callers EXCEPT for ``person_slug`` (the
    RelationshipsCurator uses it to route the write into the right
    shadow H2 section). Callers that want to know whether a token
    covers a given fact use ``verify_for_promotion`` rather than
    inspecting ``fact_ids`` directly — keeps the verification
    surface centralized.
    """

    session_uuid: str
    turn_index: int
    classifier_verdict: str
    person_slug: str
    fact_ids: tuple[str, ...]
    issued_at: datetime

    def __repr__(self) -> str:  # avoid leaking facts in logs
        return (
            f"ConsentToken(person={self.person_slug!r}, "
            f"turn={self.turn_index}, n_facts={len(self.fact_ids)}, "
            f"verdict={self.classifier_verdict})"
        )


class ConsentError(PermissionError):
    """Raised when a token-protected operation can't proceed."""


def mint(
    *,
    session_uuid: str,
    turn_index: int,
    classifier_verdict: str,
    person_slug: str,
    facts: list[str],
) -> ConsentToken:
    """Construct a ConsentToken from a verified-trigger turn.

    This is the ONLY function in the codebase that constructs
    ConsentToken instances. The dataclass is frozen but Python
    has no enforceable "private constructor"; the convention plus
    the fact that ``ConsentToken`` is named-imported only inside
    ``core/relationships/`` is the boundary. A fuzz test asserts
    callers outside this package don't construct tokens directly.
    """
    if not session_uuid or not isinstance(session_uuid, str):
        raise ConsentError("mint: session_uuid required")
    if not isinstance(turn_index, int) or turn_index < 0:
        raise ConsentError("mint: turn_index must be a non-negative int")
    if classifier_verdict not in _ALLOWED_VERDICTS:
        raise ConsentError(
            f"mint: classifier_verdict must be one of {_ALLOWED_VERDICTS}, "
            f"got {classifier_verdict!r}"
        )
    if not person_slug or not isinstance(person_slug, str):
        raise ConsentError("mint: person_slug required")
    if not facts:
        raise ConsentError(
            "mint: facts list must contain at least one fact "
            "(zero-fact triggers are routed as NONE upstream)"
        )
    return ConsentToken(
        session_uuid=session_uuid,
        turn_index=turn_index,
        classifier_verdict=classifier_verdict,
        person_slug=person_slug,
        fact_ids=derive_fact_ids(facts),
        issued_at=datetime.now(timezone.utc),
    )


def verify_for_promotion(
    token: ConsentToken | None,
    *,
    person_slug: str,
    facts: list[str],
) -> None:
    """Raise ConsentError if the token doesn't cover this promotion.

    Checks: token presence, person_slug match, fact_ids superset
    (every fact in ``facts`` must be covered by the token; extra
    tokens-covered facts are fine — promotion of a subset is
    allowed). The check is "every shadow fact has a covering
    token entry" so a tampered shadow file with an extra fact
    fails fast.
    """
    if token is None:
        raise ConsentError(
            f"no consent token for relationships write (person_slug={person_slug!r})"
        )
    if token.person_slug != person_slug:
        raise ConsentError(
            f"consent token person_slug mismatch: "
            f"token={token.person_slug!r}, requested={person_slug!r}"
        )
    if not facts:
        raise ConsentError("verify_for_promotion: facts list is empty")
    token_set = set(token.fact_ids)
    requested = derive_fact_ids(facts)
    missing = [
        facts[i] for i, fid in enumerate(requested) if fid not in token_set
    ]
    if missing:
        # Show truncated previews; don't echo full content into logs.
        previews = [f[:60] for f in missing]
        raise ConsentError(
            f"consent token does not cover {len(missing)} requested fact(s): "
            f"{previews}"
        )


# --------------------------------------------------------------------
# In-memory pending-tokens registry
# --------------------------------------------------------------------


@dataclass
class PendingTokens:
    """Process-local map of (session_uuid, turn_index, person_slug)
    → ConsentToken.

    A new instance is created per LearningController construction
    (one per daemon process). Daemon restart loses the registry;
    recovery is a bounded classifier re-run handled by the
    RelationshipsCurator at startup.
    """

    _store: dict[tuple[str, int, str], ConsentToken] = field(default_factory=dict)

    def add(self, token: ConsentToken) -> None:
        key = (token.session_uuid, token.turn_index, token.person_slug)
        self._store[key] = token

    def get(
        self, *, session_uuid: str, turn_index: int, person_slug: str
    ) -> ConsentToken | None:
        return self._store.get((session_uuid, turn_index, person_slug))

    def consume(
        self, *, session_uuid: str, turn_index: int, person_slug: str
    ) -> ConsentToken | None:
        return self._store.pop(
            (session_uuid, turn_index, person_slug), None
        )

    def __len__(self) -> int:
        return len(self._store)

    def keys(self) -> list[tuple[str, int, str]]:
        return list(self._store.keys())

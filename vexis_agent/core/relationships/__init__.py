"""RELATIONSHIPS.md curator + silent-extraction queue.

Days 1-4: trigger detector, ConsentToken, store, candidate
queue, extractor, dashboard panel. See
``.plans/relationships-v3c-research.md`` for the full design.

Day 5 (release): the v3c shipping checklist + this module's
``RELATIONSHIPS_USER_SEED_*`` constants below, which
``main.py`` uses to install a one-line meta-system context
into USER.md the first time the daemon boots.
"""

# v3c Day 5 — USER.md seed installed by main.py at daemon startup.
# Idempotent: ``MemoryStore.ensure_seed`` checks for
# ``RELATIONSHIPS_USER_SEED_MARKER`` in existing entries and
# skips the install if present. The marker substring is unique
# enough to dedup across daemon restarts without ever
# false-matching unrelated user text.

RELATIONSHIPS_USER_SEED_MARKER = (
    "silent relationships extraction default"
)

RELATIONSHIPS_USER_SEED_TEXT = (
    "User runs Vexis with the silent relationships extraction "
    "default. Vexis silently captures third-party relationship "
    "facts during normal conversation, queues them as candidates, "
    "and surfaces eligible candidates for approval via the "
    "dashboard or `/learning relationships-pending` slash command. "
    "Approved facts land in RELATIONSHIPS.md and become visible "
    "to the brain on the next session spawn (after `/clear`)."
)

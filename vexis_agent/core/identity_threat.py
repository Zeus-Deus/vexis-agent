"""USER.md-specific threat scanner: religion / politics / sexuality /
self-harm / mental-health + named third parties.

These patterns guard content that would land in USER.md (the user's
durable identity profile, re-spoken in every session forever). The
bar is higher than the base ``core/memory.py`` 12-pattern scanner —
identity content the user almost certainly didn't intend to
immortalize from a single observation gets dropped here.

This module is intentionally a peer of ``core/memory.py`` rather
than a child of ``core/learning_review.py``: the latter is the
content-classification layer (curator-only); this scanner needs to
fire at the ``MemoryStore`` boundary too so non-curator paths
(migration script, future hand-CLI) can't bypass it. Living in
``learning_review.py`` would create a circular import —
``memory.py`` imports nothing from ``learning_review``, but
``learning_review`` imports ``MemoryStore``.

Scope of patterns (Day 3 / v2 §3.4):
  - religion / faith
  - politics / ideology
  - sexuality / orientation / pronouns
  - self-harm / mental-health disclosure
  - named third parties (people other than the user)

Posture is conservative — false positives drop the candidate (the
LLM produces another next session); false negatives embed identity
claims the user can't easily un-remember.
"""

from __future__ import annotations

import re


# --------------------------------------------------------------------
# Religion / politics / sexuality / self-harm / mental-health
# --------------------------------------------------------------------


_USER_MD_THREAT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Religion / faith
    (re.compile(
        r"\buser\s+(?:is|identifies\s+as)\s+(?:a\s+|an\s+)?"
        r"(christian|catholic|protestant|muslim|islamic|jewish|hindu|"
        r"buddhist|sikh|atheist|agnostic|pagan|mormon|jehovah)",
        re.I,
    ), "user:religion"),
    (re.compile(r"\buser\s+(?:practices?|believes?\s+in|follows?)\s+\w+ism\b", re.I),
     "user:religion"),
    (re.compile(r"\buser\s+(?:prays?|attends?\s+(?:church|mosque|synagogue|temple))",
                re.I),
     "user:religion"),
    # Politics / ideology
    (re.compile(
        r"\buser\s+(?:is|votes?|voted|leans?|identifies\s+as)\s+(?:a\s+|an\s+)?"
        r"(conservative|liberal|democrat|republican|leftist|right[\s-]?wing|"
        r"left[\s-]?wing|progressive|libertarian|socialist|communist|fascist|"
        r"maga|woke)",
        re.I,
    ), "user:politics"),
    (re.compile(r"\buser\s+supports?\s+(?:the\s+)?(\w+\s+)?(party|candidate)\b", re.I),
     "user:politics"),
    # Sexuality / orientation / gender identity
    (re.compile(
        r"\buser\s+(?:is|identifies\s+as)\s+(?:a\s+|an\s+)?"
        r"(gay|lesbian|bisexual|bi|straight|heterosexual|homosexual|"
        r"asexual|ace|pansexual|queer|trans(?:gender)?|nonbinary|"
        r"non-binary|enby|cis(?:gender)?)",
        re.I,
    ), "user:sexuality"),
    (re.compile(r"\buser'?s?\s+(?:sexual|romantic)\s+(?:orientation|preference)\b",
                re.I),
     "user:sexuality"),
    (re.compile(r"\buser'?s?\s+(?:preferred\s+)?pronouns?\b", re.I),
     "user:sexuality"),
    # Self-harm / mental-health disclosure. Hard reject — these are
    # never appropriate for an automated system to immortalize.
    (re.compile(r"\b(suicidal|suicide|self[\s-]?harm)\b", re.I),
     "user:self-harm"),
    (re.compile(
        r"\buser\s+(?:struggles?|deals?|copes?|battles?|fights?)\s+with\s+"
        r"(depression|anxiety|ptsd|trauma|addiction|alcoholism|"
        r"bipolar|schizophrenia|eating\s+disorder|ocd|adhd)",
        re.I,
    ), "user:mental-health"),
    (re.compile(
        r"\buser\s+(?:is\s+)?(?:in\s+therapy|seeing\s+a\s+therapist|"
        r"on\s+(?:antidepressants|ssris|adhd\s+medication|lithium))",
        re.I,
    ), "user:mental-health"),
    (re.compile(r"\buser'?s?\s+mental\s+health\b", re.I),
     "user:mental-health"),
)


# --------------------------------------------------------------------
# Named third parties (people other than the user)
# --------------------------------------------------------------------
#
# Five patterns + an allowlist post-filter. The allowlist is the
# load-bearing part — it filters capitalized non-person tokens
# (orgs, products, technologies, weekday/month names, sentence-start
# words) so "User uses Linux" / "When asked, …" don't false-positive.
#
# Adversarial cases the scanner handles (see
# tests/test_learning_review.py::test_named_third_party_*):
#   "User's wife Sarah prefers terse answers"      → REJECTED (A)
#   "Sarah on the team uses Vim"                   → REJECTED (C)
#   "User had a meeting with the Sarah Team Lead"  → REJECTED (D)
#   "User mentioned Sarah in passing"              → REJECTED (E)
#   "User is named John"                           → ALLOWED (self)
#   "User works for Anthropic"                     → ALLOWED (org)


# Capitalized non-person tokens that look like names but aren't —
# orgs, products, technologies, places, weekday/month names, common
# sentence-start words, and self-reference. The post-filter skips
# matches whose captured name is in this set.
#
# Conservatism dial: this list filters FALSE POSITIVES out of the
# scanner. Keep it intentionally short so we err toward over-rejecting
# (drop the candidate; the LLM produces another next session) rather
# than under-rejecting (immortalize a third-party fact in USER.md).
# Add tokens here only when a real false positive is observed.
_NON_PERSON_CAPITALIZED: frozenset[str] = frozenset({
    # Self-reference
    "User", "Vexis",
    # Common sentence-start / topic-head words. Day 4 eval surfaced
    # these as false positives — "When asked" and "Code reviews"
    # both matched Pattern C and rejected legit IDENTITY/PROCEDURAL
    # lessons.
    "When", "If", "Before", "After", "During", "While", "Until",
    "For", "To", "From", "With", "Without", "Once", "Whenever",
    "Always", "Never", "Sometimes", "Often", "Usually", "Default",
    "Use", "Avoid", "Skip", "Don", "Do", "Both", "Either",
    "Then", "Otherwise", "Note", "Tip", "Warning", "Important",
    "How", "What", "Why", "Where", "Who", "Whose", "Which",
    "Code", "Tests", "Test", "Build", "PR", "API", "CI", "CD",
    "URL", "JSON", "YAML", "HTTP", "HTTPS", "TCP", "UDP",
    "Memory", "Skills", "Skill", "Session", "Sessions", "Tasks",
    # Common AI / dev orgs
    "Claude", "Anthropic", "OpenAI", "Google", "Microsoft", "Apple",
    "Amazon", "Meta", "Nvidia", "Intel",
    # Hosting / infra
    "Hetzner", "Cloudflare", "Tailscale", "Wireguard", "AWS", "Azure",
    # Apps
    "Telegram", "Slack", "Discord", "GitHub", "GitLab", "Bitbucket",
    "Notion", "Linear", "Jira", "Figma", "Zoom",
    # OS / desktop
    "Linux", "Hyprland", "Wayland", "Arch", "Ubuntu", "Debian",
    "Fedora", "MacOS", "Windows", "Omarchy", "Gnome",
    # Languages / runtimes
    "Python", "TypeScript", "JavaScript", "Rust", "Java", "Kotlin",
    "Swift", "Ruby", "Elixir", "Bun", "Deno", "Node",
    # Data / tools
    "Postgres", "PostgreSQL", "MySQL", "SQLite", "Redis", "Docker",
    "Kubernetes", "Terraform", "Ansible", "Nix", "Vim", "Emacs",
    "VSCode", "Neovim",
    # Calendar
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
    "Sunday", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
})


# IMPORTANT: every verb / role alternation below must end with ``\b``
# (closing word boundary) so a partial-prefix match like "use" inside
# "user" can't trigger the scanner.

# Pattern A: explicit relational possessive — "user's <role> <Name>".
_THIRD_PARTY_POSSESSIVE_RE = re.compile(
    r"\buser'?s?\s+"
    r"(?:wife|husband|spouse|partner|girlfriend|boyfriend|fianc[eé]e?|"
    r"son|daughter|child|kid|sister|brother|mother|mom|father|dad|"
    r"parent|grandmother|grandfather|cousin|aunt|uncle|niece|nephew|"
    r"friend|colleague|coworker|teammate|teammates|"
    r"boss|manager|report|reports|client|customer|"
    r"team\s+lead|tech\s+lead|lead|"
    r"therapist|doctor|dentist|trainer)\b"
    r"(?:\s+(?:is\s+|named\s+|called\s+))?\s+([A-Z][a-z]+)\b",
    re.I,
)

# Pattern B: relationship verbs about the user — "user is married to <Name>".
_THIRD_PARTY_RELATION_RE = re.compile(
    r"\buser\s+(?:is\s+)?(?:married|engaged|divorced|dating|seeing|"
    r"interviewing|hired)\b\s+(?:to\s+|with\s+)?([A-Z][a-z]+)\b",
    re.I,
)

# Pattern C: third-party-as-subject — "<Name> + person-verb".
_THIRD_PARTY_SUBJECT_RE = re.compile(
    r"\b([A-Z][a-z]+)\s+"
    r"(?:on\s+(?:the|our|the\s+\w+)\s+team\s+\w+|"
    r"uses?|used|prefers?|preferred|likes?|liked|loves?|loved|"
    r"hates?|hated|said|says|told|tells|asked|asks|answered|answers|"
    r"wants|wanted|thinks|thought|believes?|believed|"
    r"works?|worked|knows?|knew|"
    r"mentions?|mentioned|texted|emailed|called|messaged|"
    r"sent|gave|gives|made|makes|wrote|writes)\b"
)

# Pattern D: interaction with a named third party.
_THIRD_PARTY_INTERACTION_RE = re.compile(
    r"\b(?:meet(?:ing|s|ings)?|call(?:s|ed|ing)?|chat(?:s|ted|ting)?|"
    r"spoke|talked|talking|messaged|emailed|texted|"
    r"discuss(?:ed|ing|ion)?|met)\b\s+(?:with|to)\s+(?:the\s+)?([A-Z][a-z]+)\b"
)

# Pattern E: user-as-subject + transitive verb + named object.
_THIRD_PARTY_TRANSITIVE_RE = re.compile(
    r"\buser\s+(?:mentioned|met|emailed|texted|messaged|called|"
    r"introduced|saw|spoke\s+with|spoke\s+to|talked\s+to|talked\s+with|"
    r"asked|told)\b\s+(?:to\s+)?([A-Z][a-z]+)\b",
    re.I,
)

_THIRD_PARTY_PATTERNS: tuple[re.Pattern[str], ...] = (
    _THIRD_PARTY_POSSESSIVE_RE,
    _THIRD_PARTY_RELATION_RE,
    _THIRD_PARTY_SUBJECT_RE,
    _THIRD_PARTY_INTERACTION_RE,
    _THIRD_PARTY_TRANSITIVE_RE,
)


def check_named_third_party(text: str) -> str | None:
    """Return ``"user:named-third-party"`` if ``text`` mentions an
    identifiable third-party human by name; None otherwise.

    Five-pattern scanner with allowlist post-filter:
      A. "user's <role> <Name>"        — possessive relational
      B. "user is married to <Name>"   — user's own relational verb
      C. "<Name> + verb"               — third-party as subject
      D. "(meeting|call) with <Name>"  — interaction
      E. "user mentioned <Name>"       — user-as-subject transitive

    Each match's captured group is checked against
    ``_NON_PERSON_CAPITALIZED`` — orgs, products, technologies,
    weekdays — so "User uses Linux" / "User works for Anthropic"
    don't false-positive. Only when the captured token is
    capitalized AND not in the allowlist do we reject.

    Uses ``finditer`` so a sentence with both a benign org mention
    and a real name (e.g. "User uses Anthropic and Sarah likes
    Linux") still rejects on the Sarah match.
    """
    for pat in _THIRD_PARTY_PATTERNS:
        for m in pat.finditer(text):
            if not m.lastindex:
                continue
            name = m.group(1)
            if not name:
                continue
            # Must start with an uppercase letter in the source —
            # protects against re.I lowering "uses" or similar verbs
            # being captured by a permissive pattern.
            if not name[0].isupper():
                continue
            if name in _NON_PERSON_CAPITALIZED:
                continue
            return "user:named-third-party"
    return None


# --------------------------------------------------------------------
# Composed entry point
# --------------------------------------------------------------------


def scan_user_identity_content(content: str) -> str | None:
    """Return a pattern id if ``content`` matches a USER.md-specific
    threat pattern; None otherwise.

    Composed scanner: runs the religion/politics/sexuality/self-harm
    pattern set plus the named-third-party check. Used by:
      - ``MemoryStore._scan_for_threats`` when ``target == "user"``
        — covers any future writer to USER.md without
        going through the curator's ``_validate_lesson``.
      - ``learning_review._scan_lesson_for_sensitive_content`` when
        ``target_file == "user"`` — applied to lesson + scope at
        validation time before any write decision.
      - ``scripts/migrate_shadow_to_v2._apply_identity`` — applied
        to the migrated entry's lesson + scope before queue insert,
        so migration-flow IDENTITY claims get the same scan the
        curator hot path applies.

    Does NOT run the base 12-pattern injection/exfil set from
    ``core/memory.py:_scan_for_threats`` — that runs unconditionally
    on every memory write. This scanner is the layer ON TOP of that,
    fired only when content is destined for USER.md.
    """
    for pattern, pid in _USER_MD_THREAT_PATTERNS:
        if pattern.search(content):
            return pid
    third_party = check_named_third_party(content)
    if third_party:
        return third_party
    return None


__all__ = [
    "_USER_MD_THREAT_PATTERNS",
    "_THIRD_PARTY_PATTERNS",
    "_NON_PERSON_CAPITALIZED",
    "check_named_third_party",
    "scan_user_identity_content",
]

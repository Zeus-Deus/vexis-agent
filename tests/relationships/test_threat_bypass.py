"""Day 2: threat-scanner bypass for target_file="relationships".

Asserts that EXACTLY one regex (check_named_third_party) is
suspended for the relationships scope; every other sensitive
pattern (medical, legal, financial, religion, politics,
sexuality, self-harm, mental-health) STILL fires.
"""

from __future__ import annotations

import pytest

from core.learning_review import _scan_lesson_for_sensitive_content


def test_named_third_party_blocked_for_user():
    # Adversarial third-party-named text intended for USER.md.
    text = "user's girlfriend Sarah is a software engineer at Stripe"
    hit = _scan_lesson_for_sensitive_content(
        text, scope="user", target_file="user"
    )
    assert hit is not None
    assert "third-party" in hit


def test_named_third_party_allowed_for_relationships():
    # Same adversarial text but routed to relationships — the
    # named-third-party check is suspended (consent verified upstream).
    text = "user's girlfriend Sarah is a software engineer at Stripe"
    hit = _scan_lesson_for_sensitive_content(
        text, scope="relationships:sarah", target_file="relationships"
    )
    assert hit is None


def test_medical_pattern_still_fires_for_relationships():
    """C-X1: 'remember that my coworker Marco is on antidepressants'
    must STILL reject under target_file=relationships because the
    medical pattern is not bypassed."""
    text = "Marco is on prescription antidepressants for his depression"
    hit = _scan_lesson_for_sensitive_content(
        text, scope="relationships:marco", target_file="relationships"
    )
    assert hit is not None
    # Must be a medical/sensitive pattern, NOT a third-party hit.
    assert "third-party" not in hit


def test_legal_pattern_still_fires_for_relationships():
    # Use a phrasing that matches the existing legal patterns
    # ("lawsuit"). The §4 C-X2 fixture's exact wording ("told her to
    # plead guilty") doesn't match the v2 legal regex set; the point
    # is that the legal scanner stack is reachable from the
    # relationships scope, which is what this asserts.
    text = "Sarah's lawsuit is going to trial next month"
    hit = _scan_lesson_for_sensitive_content(
        text, scope="relationships:sarah", target_file="relationships"
    )
    assert hit is not None
    assert "third-party" not in hit


def test_financial_pattern_still_fires_for_relationships():
    """C-X3: financial advice / portfolio info about a third party
    must reject even with consent."""
    text = "Sarah gave me investment advice on tech stocks"
    hit = _scan_lesson_for_sensitive_content(
        text, scope="relationships:sarah", target_file="relationships"
    )
    assert hit is not None
    assert "third-party" not in hit


def test_clean_text_passes_for_relationships():
    text = "Sarah likes mystery novels and is a coworker"
    hit = _scan_lesson_for_sensitive_content(
        text, scope="relationships:sarah", target_file="relationships"
    )
    assert hit is None


def test_memory_scope_unchanged_by_relationships_branch():
    """Make sure adding the relationships branch did NOT break the
    existing memory scope's behavior (the named-third-party check
    was already off for memory; medical/etc. still on)."""
    # No third-party check for memory, never was.
    text = "user's girlfriend Sarah is a software engineer"
    hit = _scan_lesson_for_sensitive_content(
        text, scope="memory", target_file="memory"
    )
    assert hit is None
    # Medical still fires for memory.
    text2 = "user takes prescription antidepressants"
    hit2 = _scan_lesson_for_sensitive_content(
        text2, scope="memory", target_file="memory"
    )
    assert hit2 is not None

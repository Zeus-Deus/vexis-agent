"""Tests for core.brain.claude_code.audit_destructive_mentions — the heuristic
that classifies Vexis's textual response as 'asked first' vs 'just ran it'."""

from __future__ import annotations

from vexis_agent.core.brain.claude_code import audit_destructive_mentions


def _classify(text: str) -> list[tuple[str, bool]]:
    return list(audit_destructive_mentions(text))


def test_no_destructive_mention_yields_nothing() -> None:
    assert _classify("Done, sir. The file has been moved.") == []


def test_question_mark_classifies_as_asked() -> None:
    out = _classify("Should I run `rm -rf /tmp/cache` to clear it?")
    assert out == [("recursive/forced rm", True)]


def test_past_tense_report_classifies_as_ran() -> None:
    out = _classify("Done, sir. I ran `rm -rf /tmp/cache` and it's gone.")
    assert out == [("recursive/forced rm", False)]


def test_asking_phrase_without_question_mark() -> None:
    out = _classify("I'm about to run rm -rf /tmp/old. Confirm before I proceed.")
    assert out and out[0] == ("recursive/forced rm", True)


def test_force_push_reported_after() -> None:
    out = _classify("Pushed. I used git push --force to overwrite the remote.")
    assert out == [("force push", False)]


def test_force_push_asked_first() -> None:
    out = _classify("Want me to do a git push --force on origin/main?")
    assert out == [("force push", True)]


def test_multiple_mentions_reported_separately() -> None:
    text = (
        "First: should I run `rm -rf old/`? "
        "Second, I went ahead and did `git reset --hard HEAD~1` already."
    )
    out = _classify(text)
    reasons = [r for r, _ in out]
    assert "recursive/forced rm" in reasons
    assert "hard reset" in reasons
    asked_map = {r: a for r, a in out}
    assert asked_map["recursive/forced rm"] is True
    assert asked_map["hard reset"] is False


def test_question_in_different_sentence_does_not_leak() -> None:
    # The destructive mention is in a past-tense sentence; the '?' is in the
    # next sentence and shouldn't classify the prior mention as asked.
    text = "I ran rm -rf foo and it is gone. Anything else, sir?"
    out = _classify(text)
    assert out == [("recursive/forced rm", False)]


def test_newline_separates_sentences() -> None:
    # Newlines also break sentences — a question on the next line shouldn't
    # leak into the previous one's classification.
    text = "I ran rm -rf old\nAnything else?"
    out = _classify(text)
    assert out == [("recursive/forced rm", False)]


def test_logging_emits_info_lines(caplog) -> None:
    """Smoke test: respond()'s call site should produce greppable INFO lines.
    We verify the format here without spawning subprocess."""
    import logging

    from vexis_agent.core.brain import claude_code

    caplog.set_level(logging.INFO, logger="vexis_agent.core.brain.claude_code")
    response = "I ran `rm -rf old/`. Should I also `git push --force origin main`?"
    for reason, asked in audit_destructive_mentions(response):
        if asked:
            claude_code.log.info("Vexis confirmed before destructive: %s", reason)
        else:
            claude_code.log.info("Vexis ran without confirm: %s", reason)

    messages = [r.message for r in caplog.records]
    assert any("Vexis ran without confirm: recursive/forced rm" in m for m in messages)
    assert any("Vexis confirmed before destructive: force push" in m for m in messages)

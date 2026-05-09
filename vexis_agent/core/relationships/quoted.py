"""Strip quoted/code-fenced/inline-backtick spans before regex or
classifier passes.

Audit (May 2026): no analogous utility lives in
``core.learning_review`` or ``core.learning_curator`` today. Day 1
introduces this here; later phases may lift it to a shared
location once a second caller appears.

Order of operations matters:

1. Fenced code blocks first (``` … ``` and ~~~ … ~~~). A blockquote
   marker inside a fence isn't a real blockquote; an inline
   backtick span inside a fence isn't a real inline span.
2. Then blockquote lines (lines starting with optional whitespace
   then ``>``). Per CommonMark a blockquote can span multiple
   lines until a blank line; we strip the marker prefix per line
   and let the rest fall away when the line becomes empty.
3. Then inline backtick spans (``…``). These don't survive across
   newlines.

Stripped content is replaced with a single space so word-boundary
regexes around the strip don't accidentally fuse two words.
"""

from __future__ import annotations

import re

# Triple-fenced code blocks, both backtick and tilde flavors, with
# optional info string. ``re.DOTALL`` so ``.`` matches newlines.
_FENCE_RE = re.compile(
    r"(?:^|\n)[ \t]*(```|~~~)[^\n]*\n.*?\n[ \t]*\1[ \t]*(?=\n|$)",
    re.DOTALL,
)

# A blockquote line: optional indent, ``>``, optional space, rest.
# Applied per-line in a loop rather than via a single regex because
# CommonMark blockquote semantics are line-oriented.
_BLOCKQUOTE_LINE_RE = re.compile(r"^[ \t]{0,3}>[ \t]?(.*)$")

# Inline backtick spans. Matches one or more backticks delimiting
# a span that does NOT contain that exact run of backticks.
# Conservative: handles the common ``…`` and `…` cases without
# trying to be CommonMark-perfect.
_INLINE_BACKTICK_RE = re.compile(r"(`+)(?:(?!\1).)+\1")


def strip_quoted_blocks(text: str) -> str:
    """Return ``text`` with code fences, blockquotes, and inline
    backtick spans removed.

    Replacement is a single space so a stripped span between two
    words doesn't fuse them into a single token. Empty input
    returns empty string.
    """
    if not text:
        return ""

    out = _FENCE_RE.sub(" ", text)

    lines: list[str] = []
    for line in out.split("\n"):
        if _BLOCKQUOTE_LINE_RE.match(line):
            # Drop the entire blockquote line — the marker plus its
            # rest. We replace with empty string (not space) because
            # the surrounding newlines already separate tokens.
            lines.append("")
        else:
            lines.append(line)
    out = "\n".join(lines)

    out = _INLINE_BACKTICK_RE.sub(" ", out)

    return out

"""Pattern-matching tripwire for destructive Bash commands.

Pure: no I/O, no logging, no side effects. Built in Step 6, wired into
PreToolUse hooks in Step 6.5.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyVerdict:
    requires_confirmation: bool
    reason: str = ""


# The rm regex requires BOTH recursive (r/R) and force (f/F) flags. `rm -r`
# alone or `rm -f` alone is not flagged — combined recursive+force is the
# canonical "lose data instantly" pattern. The trailing lookahead anchors
# the flag block to a shell separator so it doesn't bleed into filenames
# like `-rfile.txt`.
_RM_RECURSIVE_FORCE = re.compile(
    r"\brm\s+("
    r"-[a-zA-Z]*[rR][a-zA-Z]*[fF][a-zA-Z]*"  # combined, r before f
    r"|-[a-zA-Z]*[fF][a-zA-Z]*[rR][a-zA-Z]*"  # combined, f before r
    r"|-[rR]\s+-[fF]"  # split: -r ... -f
    r"|-[fF]\s+-[rR]"  # split: -f ... -r
    r")(?=\s|$|[;|&])"
)


DESTRUCTIVE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (_RM_RECURSIVE_FORCE, "recursive/forced rm"),
    (re.compile(r"\bdd\s+(if|of)="), "dd to/from device"),
    (re.compile(r"(curl|wget)\s+[^|;&]+\|\s*(ba)?sh\b"), "pipe remote script to shell"),
    (re.compile(r"\bmkfs(\.\w+)?\s+"), "filesystem creation"),
    (re.compile(r"\bchmod\s+-R\s+0*777\b"), "wide recursive chmod 777"),
    (re.compile(r"\bgit\s+push\s+(-f|--force)\b"), "force push"),
    (re.compile(r"\bgit\s+reset\s+--hard\b"), "hard reset"),
    (re.compile(r">\s*/dev/(sd|nvme|hd|mmcblk)\w*"), "raw device write"),
    (re.compile(r"\bsudo\b"), "sudo invocation"),
]


def check_command(command: str) -> SafetyVerdict:
    """Return whether a Bash command requires confirmation before running."""
    for pattern, reason in DESTRUCTIVE_PATTERNS:
        if pattern.search(command):
            return SafetyVerdict(requires_confirmation=True, reason=reason)
    return SafetyVerdict(requires_confirmation=False)

"""Regression gate: ``vexis_agent/core/`` must not import from
``vexis_agent.addons.codemux`` (or any other add-on).

The whole point of the Phase B extraction was to flip codemux from a
hardcoded core feature into a bundled add-on. If a future change
re-introduces a ``from vexis_agent.addons.codemux import ...`` line in
core, the addon-isolation contract is broken — every add-on would
need vexis-agent core to know about it, defeating the architecture.

This test grep-scans every ``.py`` under ``vexis_agent/core/`` for
add-on imports and fails loudly if one slips in. Adding a new
hardcoded codemux line in main.py or telegram.py is an automatic
test failure with a pointer at the file:line that broke the rule.

Allowlist: a small set of well-known back-compat re-exports stay
permitted (the UNAVAILABLE_MESSAGE re-export in
``core/watcher/__init__.py`` is the only one today). Add to
``ALLOWED_IMPORTS`` ONLY when the alternative is materially worse
for users, and explain why in a comment.
"""

from __future__ import annotations

import re
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parent.parent / "vexis_agent" / "core"

#: Patterns that constitute a forbidden import. Matched against every
#: line of every .py file under CORE_ROOT. ``re.search`` semantics
#: (anywhere in the line counts).
FORBIDDEN_IMPORT_PATTERNS = [
    re.compile(r"\bfrom\s+vexis_agent\.addons\b"),
    re.compile(r"\bimport\s+vexis_agent\.addons\b"),
]

#: One-off back-compat re-exports that are deliberately allowed.
#: Format: ``(relative_path_from_repo, line_substring)``. The line
#: must contain the substring AND match a forbidden pattern — only
#: that exact pairing is whitelisted.
ALLOWED_IMPORTS = {
    # The watcher's __init__.py re-exports the codemux add-on's
    # UNAVAILABLE_MESSAGE so legacy ``vexis-watch`` callers print
    # the wording users already know. Phase B+ may retire this
    # bridge once vexis-watch ships its own friendlier "no source
    # registered" copy.
    ("vexis_agent/core/watcher/__init__.py",
     "from vexis_agent.addons.codemux import UNAVAILABLE_MESSAGE"),
}


def test_core_does_not_import_from_addons():
    repo_root = Path(__file__).resolve().parent.parent
    violations: list[str] = []

    for py_file in sorted(CORE_ROOT.rglob("*.py")):
        rel = py_file.relative_to(repo_root)
        rel_str = str(rel)
        for lineno, raw_line in enumerate(
            py_file.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = raw_line.strip()
            # Skip comments — they're not imports, just prose.
            if line.startswith("#"):
                continue
            for pat in FORBIDDEN_IMPORT_PATTERNS:
                if pat.search(line):
                    # Allowlisted exact pairing?
                    if any(
                        rel_str == allowed_path and allowed_substr in line
                        for allowed_path, allowed_substr in ALLOWED_IMPORTS
                    ):
                        break
                    violations.append(f"  {rel}:{lineno}: {raw_line!r}")
                    break

    assert not violations, (
        "core/ must not import from vexis_agent.addons. Found:\n"
        + "\n".join(violations)
        + "\n\nMove the offending file into an add-on (under "
        "vexis_agent/addons/<name>/), or use the AddonRuntime's "
        "service-lookup pattern (PluginContext.get_service) to "
        "talk between core and the add-on. See docs/addons.md."
    )


# NOTE: A second test that grepped for the literal ``"codemux"`` in
# core was tried and removed — it was too aggressive, flagging
# docstring examples in core/addons/context.py and core/addons/
# manifest.py where "codemux" is just the canonical example used to
# illustrate the add-on system. The import contract above is the
# meaningful regression gate; docstring drift doesn't matter.

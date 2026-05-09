"""Allow ``python -m vexis_agent`` as an alias for the Typer CLI.

Lets users invoke the package without needing the ``vexis-agent``
console script on PATH — useful inside containers, CI, or when the
console-script's PATH entry has been clobbered. Functionally
identical to ``python -m vexis_agent.cli``.
"""

from vexis_agent.cli import app

if __name__ == "__main__":
    app()

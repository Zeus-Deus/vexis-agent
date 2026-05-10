"""vexis-agent — Telegram bot + agent CLI bridge for Linux desktops.

The package is the runtime artifact: ``vexis_agent.main`` is the daemon
entry point and ``vexis_agent.cli:app`` is the Typer app exposed via the
``vexis-agent`` console script declared in ``pyproject.toml``. Source
modules live under ``vexis_agent/core``, ``vexis_agent/tools`` and
``vexis_agent/transports``.

User state (config, learning curator state, logs, daemon pid) lives at
``~/.vexis/`` (overridable via ``VEXIS_HOME``); the workspace
(``~/vexis-workspace/`` by default; overridable via ``VEXIS_WORKSPACE``)
holds gittable agent memory + skills. Code dir ≠ data dir — pipx
upgrades only touch the venv.
"""

__version__ = "0.1.4"

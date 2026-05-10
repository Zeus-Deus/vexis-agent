"""Daemon supervision helpers — systemd unit rendering, install detection,
update orchestration, and the ``vexis-agent doctor`` checks.

Phase 3 of the packaging effort populates this package. Each submodule
ships a small surface (pure functions where possible) so the Typer
``service`` / ``update`` / ``doctor`` commands stay thin and the logic
is exercisable in tests without spawning real systemctl or pipx.
"""

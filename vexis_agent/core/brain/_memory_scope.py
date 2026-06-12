"""Per-subagent memory isolation for ``claude -p`` spawns.

The 2026-06-12 incident: a single ``claude -p`` subagent ran a
pathological ``ugrep`` that ballooned to 2.4 GiB. Because the bot and
every subprocess it spawns shared one systemd cgroup, the runaway
pushed the cgroup over its ``MemoryHigh`` watermark and the kernel
throttled *every* task in the cgroup — including the bot's own loop —
into uninterruptible ``D`` sleep (``__mem_cgroup_handle_over_high``).
The bot stopped answering Telegram even though it hadn't crashed.

The fix: run each ``claude -p`` spawn (and therefore its whole tool
subtree) inside its own transient ``systemd-run --scope`` carrying a
``MemoryMax``. A runaway tool then OOM-kills *inside its own scope*,
the bot's cgroup is untouched, and the bot keeps serving. The scope is
placed under :data:`VEXIS_SLICE` so the shipped slice unit's aggregate
``MemoryMax`` still bounds the bot + all concurrent subagents for
host protection (see ``daemon/systemd.py`` and ``docs/memory-isolation.md``).

``MemoryMax`` caps *real* memory (RSS + page cache charged to the
cgroup), not virtual address space — so unlike an ``RLIMIT_AS`` it
won't spuriously break Chromium/node, which map large virtual regions
they never fault in.
"""

from __future__ import annotations

import logging
import shutil

from vexis_agent.core.yaml_config import brain_subprocess_memory_max

log = logging.getLogger(__name__)

# Single source of truth for the slice name. ``daemon/systemd.py``
# imports this so the shipped ``vexis-agent.slice`` unit and the
# ``--slice`` flag here can never drift apart.
VEXIS_SLICE = "vexis-agent.slice"

# Per-scope swap allowance. A small cushion so a transient spike can
# spill briefly instead of OOM-ing on the first overshoot, without
# letting a runaway thrash gigabytes of swap. Not user-tunable — the
# user-facing knob is the RSS cap (``brain.subprocess_memory_max``).
_SCOPE_SWAP_MAX = "512M"

# Cache the "systemd-run missing" warning so a non-systemd host (or a
# container) doesn't log it on every single spawn.
_warned_missing = False


def wrap_with_memory_scope(argv: list[str]) -> list[str]:
    """Return ``argv`` wrapped in a memory-capped ``systemd-run`` scope.

    No-ops (returns ``argv`` unchanged) when:

    * scoping is disabled (``brain.subprocess_memory_max`` set to
      ``none``/``0``), or
    * ``systemd-run`` is not on PATH (non-systemd host / container) —
      degrade gracefully rather than failing the spawn.

    Otherwise prepends::

        systemd-run --user --scope --quiet --collect
            --slice vexis-agent.slice
            -p MemoryMax=<cap> -p MemorySwapMax=512M --

    ``--user`` targets the per-user manager the bot already runs under;
    ``--scope`` runs the command synchronously in the foreground with
    inherited stdio (so the caller still captures stdout/stderr and
    waits normally); ``--collect`` garbage-collects the transient scope
    even if the command exits non-zero / is OOM-killed. The process
    group is preserved, so the callers' existing ``os.killpg``-based
    cancel/timeout path still reaches the real ``claude`` process.
    """
    cap = brain_subprocess_memory_max()
    if not cap:
        return argv

    if shutil.which("systemd-run") is None:
        global _warned_missing
        if not _warned_missing:
            _warned_missing = True
            log.debug(
                "systemd-run not on PATH — skipping per-subagent memory "
                "scoping (brain.subprocess_memory_max=%s ignored on this host)",
                cap,
            )
        return argv

    return [
        "systemd-run",
        "--user",
        "--scope",
        "--quiet",
        "--collect",
        "--slice",
        VEXIS_SLICE,
        "-p",
        f"MemoryMax={cap}",
        "-p",
        f"MemorySwapMax={_SCOPE_SWAP_MAX}",
        "--",
        *argv,
    ]

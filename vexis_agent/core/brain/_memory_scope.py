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

Graceful degradation (issue #47): scoping is skipped, rather than
failing the spawn, in three cases — the cap is disabled, ``systemd-run``
is absent from PATH, or the binary is present but a scope can't actually
be created here (a container with no running systemd / no user manager /
no D-Bus session bus). The last case can't be read off PATH, so it is
answered by a one-time probe: try to build a trivial scope and remember
whether it worked. Bare-metal deploys keep full protection; containers
degrade to unscoped spawns instead of dying on every turn.
"""

from __future__ import annotations

import logging
import shutil
import subprocess

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

# Timeout for the one-time usability probe. On a healthy user manager
# the trivial scoped ``true`` returns in tens of ms; on a container with
# no session bus ``systemd-run`` fails immediately. The timeout only
# bites when the bus itself hangs — a few seconds is plenty and keeps
# the single blocking call this makes on the event loop bounded.
_PROBE_TIMEOUT_SECONDS = 5.0

# Cache the "systemd-run missing" note so a non-systemd host (or a
# container) doesn't log it on every single spawn. An absent binary is
# the *expected* case (e.g. a macOS dev box), hence DEBUG not WARNING.
_warned_missing = False

# One-time systemd-usability verdict, cached for the process lifetime:
#   None  = not yet probed
#   True  = a real scope was created — scope every spawn
#   False = binary present but a scope could not be created (no bus / no
#           user manager / a property systemd rejected) — spawn unscoped
# ``shutil.which("systemd-run")`` only proves the binary exists, not
# that it works: in a plain container the binary is on PATH yet every
# scoped spawn exits 1 with "Failed to connect to bus" (issue #47). We
# answer the only question that actually matters — "can this process
# create a memory-capped user scope right now?" — by trying once and
# remembering. No Docker/env sniffing: the probe *is* the detection.
_systemd_usable: bool | None = None


def _memory_scope_prefix(cap: str) -> list[str]:
    """The ``systemd-run`` argv prefix (through the ``--`` terminator)
    for a scope carrying ``cap``.

    Shared by the real wrap and the usability probe so the probe
    exercises the *exact* invocation shape a real spawn would — catching
    every failure mode it would hit (no bus, no user manager, a property
    systemd rejects), not just one guessed cause.
    """
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
    ]


def _first_stderr_line(stderr: bytes | None) -> str:
    """First non-blank line of ``stderr`` (decoded, trimmed, capped) —
    the "why" shown in the disabled-scoping warning."""
    if not stderr:
        return ""
    for line in stderr.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return ""


def _probe_systemd_usable(cap: str) -> bool:
    """Run a trivial command through the real scope shape once to decide
    whether systemd scoping actually works in this process.

    Blocking; called at most once per process (result cached in
    :data:`_systemd_usable`). Never raises — every failure mode
    (nonzero exit, ``TimeoutExpired``, ``OSError``, anything unexpected)
    means "unusable" and the caller degrades to an unwrapped spawn.

    Probes with the *live* configured ``cap``, not a fixed known-good
    value. Constraint: a cap systemd rejects (e.g. a ``"2X"`` typo) must
    degrade to an unwrapped spawn just like a busless host — it must NOT
    let the real spawn hard-fail. Probing with a fixed cap would pass
    here and then let the bad live cap exit-1 on every brain turn,
    reintroducing the exact issue-#47 freeze this guard exists to
    prevent. The cost is accepted deliberately: a bad cap present at the
    first wrapped spawn keeps scoping off for the process lifetime even
    after it's corrected; a daemon restart re-probes.
    """
    why = ""
    try:
        cp = subprocess.run(
            [*_memory_scope_prefix(cap), "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
        if cp.returncode == 0:
            return True
        why = _first_stderr_line(cp.stderr)
    except subprocess.TimeoutExpired:
        why = f"probe timed out after {_PROBE_TIMEOUT_SECONDS:g}s"
    except OSError as exc:
        why = str(exc)
    except Exception as exc:  # noqa: BLE001 - the probe must never escape
        why = f"unexpected {type(exc).__name__}: {exc}"

    log.warning(
        "per-subagent memory scoping DISABLED for this process: "
        "systemd-run is present but a probe scope failed (%s). Spawns "
        "will run unscoped — safe, but a runaway tool is no longer "
        "isolated to its own cgroup. This is expected in containers "
        "with no running systemd/user bus. Set "
        "brain.subprocess_memory_max: none to silence this, or run the "
        "daemon under a systemd user manager to restore isolation "
        "(re-probed only on restart).",
        why or "no error output",
    )
    return False


def wrap_with_memory_scope(argv: list[str]) -> list[str]:
    """Return ``argv`` wrapped in a memory-capped ``systemd-run`` scope.

    No-ops (returns ``argv`` unchanged) when:

    * scoping is disabled (``brain.subprocess_memory_max`` set to
      ``none``/``0``),
    * ``systemd-run`` is not on PATH (non-systemd host / typical
      container), or
    * ``systemd-run`` is present but not *usable* here — a one-time
      probe could not actually create a scope (no session bus, no user
      manager, or a cap value systemd rejects). Issue #47: a plain
      container ships the binary but has no running systemd, so every
      scoped spawn would exit 1; degrade to an unwrapped spawn instead.

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

    # Binary present != systemd usable. Probe once (blocking) and cache
    # the verdict for the process lifetime. Every spawn site calls this
    # on the event-loop thread, and the blocking probe parks that loop
    # until it returns, so no second coroutine can enter here mid-probe:
    # this check-then-set needs no lock (the same single-thread
    # guarantee the _warned_missing latch above already relies on).
    global _systemd_usable
    if _systemd_usable is None:
        _systemd_usable = _probe_systemd_usable(cap)
    if not _systemd_usable:
        return argv

    return [*_memory_scope_prefix(cap), *argv]

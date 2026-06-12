# Per-subagent memory isolation

## TL;DR

Every `claude -p` spawn — the foreground chat turn *and* every aux
subsystem — runs inside its own memory-capped `systemd-run --scope`
(`brain.subprocess_memory_max`, default `2G`). The bot service and all
those scopes live under one shared `vexis-agent.slice` whose hard
`MemoryMax` bounds their combined footprint. A runaway tool therefore
OOM-kills **inside its own scope** instead of taking the bot down with
it. No `MemoryHigh` anywhere — that throttle is what caused the freeze.

## The incident (2026-06-12)

The bot stopped answering Telegram. It had **not** crashed — `systemctl
--user status vexis-agent` said `active (running)`, and the journal
showed it still polling `getUpdates` and sending "typing…" keepalives.
The main process was stuck in uninterruptible (`D`) sleep with
`wchan = __mem_cgroup_handle_over_high`.

Root cause: a `claude -p` subagent ran a pathological
`ugrep -o .{0,200}<pat>{0,200}` that buffered **2.4 GiB** of matches.
Because the bot and *every* process it spawns shared a single systemd
cgroup (`vexis-agent.service`), that one runaway pushed the cgroup past
the `MemoryHigh=2560M` set in a hand-added drop-in. The kernel's
response to a cgroup exceeding `memory.high` is to **throttle every
task in it** — so the bot's own event loop got parked in reclaim
sleep. Killing the grep instantly un-froze the bot.

`earlyoom` (installed the same week) never fired, correctly: it watches
*global* memory and the box had ~63% free. The pressure was
cgroup-local, invisible to a global OOM daemon.

## Two things were wrong

1. **One shared cgroup.** A single misbehaving tool inside a subagent
   could throttle/freeze the whole bot.
2. **`MemoryHigh` throttles instead of killing**, and the memory
   policy was a hand-rolled drop-in the installer never wrote — fresh
   installs had zero protection.

## The fix

### a) Per-subagent scope (`core/brain/_memory_scope.py`)

`wrap_with_memory_scope(argv)` prepends:

```
systemd-run --user --scope --quiet --collect \
    --slice vexis-agent.slice \
    -p MemoryMax=<cap> -p MemorySwapMax=512M -- <argv>
```

It is applied at all three `ClaudeCodeBrain` spawn sites:
`_attempt_respond`, `_attempt_astream`, and `spawn_aux`. A
`test_memory_scope.py` check (`inspect.getsource`) fails if a spawn
site ever loses the wrap.

Key properties (all verified on-box and pinned by tests):

- **Capability-preserving.** `MemoryMax` caps *real* memory (RSS +
  cgroup page cache), not virtual address space — so unlike an
  `RLIMIT_AS` it does not break Chromium/node, which map large virtual
  regions they never fault in. 2 GiB is far above normal grep /
  Playwright / build usage; only a genuinely runaway tool is killed.
- **Isolated blast radius.** A runaway OOM-kills the single biggest
  process *inside its own scope*; the bot's cgroup is untouched.
- **Cancel/timeout still work.** The scope preserves the process
  group, so the foreground `os.killpg` cancel/timeout path reaches the
  real `claude` through the wrapper. For aux, `subprocess.run`'s
  timeout (SIGKILL to the `systemd-run` client) also tears the scope
  down — no orphan.
- **Graceful degradation.** No-ops (returns argv unchanged) when the
  cap is disabled (`brain.subprocess_memory_max: none`) or when
  `systemd-run` is not on PATH (non-systemd host / container).

### b) Aggregate-cap slice (`daemon/systemd.py`)

`install_user_unit()` writes `vexis-agent.slice` (rendered by
`render_user_slice()`) and adds `Slice=vexis-agent.slice` to the bot
unit. The slice carries a single hard `MemoryMax` (default `5G`, +1G
swap) and **no `MemoryHigh`** — a hard cap OOM-kills the offender; a
high watermark would re-introduce the throttle-freeze. The bot service
itself sets no memory limit (the slice owns host protection; the bot's
own RSS is tiny). The installer also removes the superseded
hand-rolled `50-memory-limit.conf` drop-in.

`SLICE_NAME` (daemon) must equal `VEXIS_SLICE` (the wrapper's `--slice`
target); a drift-guard test enforces it. If they diverge, scopes land
under a transient *uncapped* slice and the aggregate cap silently does
nothing.

## Configuration

```yaml
brain:
  subprocess_memory_max: 2G   # per-subagent RSS cap; "none"/0 disables.
                              # Hot-reloaded per spawn.
```

The slice's aggregate `MemoryMax`/`MemorySwapMax` are constants in
`daemon/systemd.py` (`SLICE_MEMORY_MAX` / `SLICE_MEMORY_SWAP_MAX`);
adjust per-machine by editing the installed `vexis-agent.slice` or the
defaults. `earlyoom` remains the global last line of defence — this
change just makes it almost never needed.

## Operational notes

- **`Slice=` membership needs a restart.** Changing the unit's
  `Slice=` takes effect on a service *restart*, not a bare
  `daemon-reload`. `vexis-agent service install` followed by a restart
  applies it.
- **Sizing.** On the 7.5 GB home box, slice `5G` leaves ~2.5 GB for the
  docker AI stack + OS. Per-subagent `2G` kills the multi-GB grep from
  the incident while leaving headroom for a real browser session.
- **opencode brain.** Only `claude-code` spawns are wrapped today.
  `opencode` runs unscoped — a follow-up should apply the same wrapper
  to its spawn sites if that brain sees production use.

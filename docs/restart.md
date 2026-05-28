# Restarting the agent (`/restart`)

The daemon runs on top of an agent CLI (claude-code by default). Two
kinds of change need the **process** to come back up before they take
effect:

- **A new brain CLI version / a different account-default model.** The
  foreground turn passes no `--model` flag, so it uses whatever the brain
  CLI is configured for. Each turn already spawns a fresh `claude -p`, so
  a CLI upgrade is usually picked up on the next message *without* any
  restart. A restart guarantees it.
- **`brain.kind`** (claude-code ↔ opencode ↔ null). This is bound once at
  startup (`main.py`, where the `Brain` instance is constructed) and only
  a restart re-reads it. The dashboard surfaces a canary warning when the
  on-disk `brain.kind` diverges from the running brain.

Everything else under `models.*` (subsystem tiers, per-subsystem
overrides) hot-reloads on the next aux spawn and needs no restart.

## `/restart`

Send `/restart` in Telegram. The bot acks, then re-execs the daemon in
place. **Your conversation is preserved** — sessions live on disk
(claude `--resume` / `opencode.db`), so the next message you send
continues exactly where you left off, now running the latest CLI / model
/ `brain.kind`.

```
you:  /restart
bot:  🔄 Restarting now — our conversation is saved. Give me a few
      seconds, then send a message and I'll pick up right where we left off…
you:  (wait a few seconds)
you:  which model are you?
bot:  <the new model>
```

## How it works

1. `_on_restart` (allowed-user-only) acks, then calls
   `TelegramTransport.request_restart()`, which flips `_restart_requested`
   and trips the run-loop's `_shutdown_event` (deferred one tick via
   `call_soon` so the PTB handler returns before teardown — `Application.
   stop()` joins in-flight handlers).
2. `TelegramTransport.run()` returns; the run-loop's `finally` tears down
   every socket the daemon owns (Telegram polling, control socket,
   dashboard, watcher, curators, background tasks).
3. `_run()` returns `True`; `main()` calls `_exec_restart()`, which
   `os.execv`s `python -m vexis_agent.cli run` — byte-for-byte the
   systemd unit's `ExecStart`, so the restart lands on the same launch
   path production already uses. The PID is unchanged.
4. The daemon PID-lock fd is `O_CLOEXEC` (Python fds are non-inheritable
   since PEP 446), so the `flock` releases at the execv boundary and the
   fresh image re-acquires it. Because the PID is unchanged, the lock's
   stale-vs-alive check (`existing == getpid()`) passes instead of
   tripping the "already running" guard.

Under systemd the MainPID is preserved, so the unit stays `active`
across the swap. `vexis-agent service restart` (full `systemctl`
restart) remains available and is equivalent in effect.

### If the re-exec ever fails

`os.execv` only returns on failure (success replaces the image). On the
rare failure, `_exec_restart` logs loudly and re-raises so the process
exits non-zero — under systemd that trips the unit's
`Restart=on-failure` (`RestartSec=5`) and the daemon respawns within a
few seconds. So the daemon never ends up silently dead-but-not-
responding under the supervised (systemd) path. A foreground
`vexis-agent run` has no supervisor, so the loud log is the operator's
signal to relaunch.

The re-exec target booting real daemon code (rather than no-opping) is
verified by `scripts/restart_smoke.py` and the Dockerfile's third stage,
which runs the exact `python -m vexis_agent.cli run` command and asserts
it reaches `_run()` startup.

## Tests

- `tests/test_restart_command.py` — argv builder, `request_restart`
  flag/event, `_on_restart` gating + ack.
- `scripts/restart_smoke.py` — end-to-end smoke of the execv + lock +
  control-socket-rebind primitive (run in Docker; see the script header).

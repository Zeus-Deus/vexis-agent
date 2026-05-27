# Schedules — pre-run script + wake gate

Lets a cron-style monitoring schedule cost ~0 when idle.

## TL;DR

Every `/schedule` fire normally wakes a brain turn. With `--script
<name>` the schedule first runs a user-supplied script under
`~/.vexis/scripts/`. If the script's last stdout line is
`{"wakeAgent": false}` the brain turn is **skipped** — no LLM call,
no tokens spent. Otherwise the script's stdout is **prepended** to
the prompt and the brain wakes as usual.

This is the single biggest cost lever on the schedule subsystem.
A "check disk every 10 min" or "any new mail?" schedule should
cost effectively nothing on the 95% of ticks where the answer is
"no change."

## Creating a schedule with a script

Three surfaces, all routing through `vexis-agent schedule create`:

**Telegram:**
```
/schedule every 5m --script check_mail.sh ping me if new mail
```
The slash command pre-extracts `--script` / `--script-timeout`
tokens before forwarding to the brain so the brain knows to pass
those flags to its tool call.

**Brain-callable CLI (what the brain actually runs):**
```
vexis-agent schedule create \
  --expr "every 5m" \
  --prompt "ping me if new mail" \
  --chat-id 12345 \
  --script check_mail.sh \
  --script-timeout 30
```

**Defaults:**
- `--script-timeout` defaults to `120` seconds (matches Hermes
  upstream). Hard-clamped to `(0, 3600]` — values outside that
  range coerce to the default.
- No script = behaves exactly as today (zero regression).

## The wake-gate contract

The runner looks at the **last non-empty line** of the script's
stdout. The truth table:

| Last line                          | Brain wakes? | Prepended stdout? |
|-----------------------------------|--------------|-------------------|
| `{"wakeAgent": false}`            | **No**       | n/a               |
| `{"wakeAgent": true}`             | Yes          | Yes (line stripped) |
| `{"other": "data"}`               | Yes          | Yes (full stdout) |
| Not JSON / not a dict             | Yes          | Yes (full stdout) |
| Empty / whitespace only           | Yes          | None (no banner)  |

**Default-to-wake on anything malformed.** A typo in a user's gate
must not silently disable their monitor.

When the brain wakes, the prompt it sees is:

```
[script output]
<script stdout, gate line removed>
[end script output]

<original schedule prompt>
```

The `[script output]` markers are deliberately distinct from
`CURATOR_REVIEW_PROMPT_PREFIX` / `SUMMARY_PREFIX` /
`KANBAN_WORKER_PREFIX` so the content-prefix recursion guard does
NOT catch script-enriched fires as aux-fork transcripts — they
remain foreground turns the curator may review for lessons.

## Example scripts

Drop into `~/.vexis/scripts/` and `chmod +x`.

**`check_mail.sh` — IMAP count vs. previous tick:**

```bash
#!/bin/bash
# Cache the previous count under /tmp; emit wakeAgent:true only when it grows.
STATE=/tmp/vexis-mail-last-count
CUR=$(curl -s -u "$USER:$PASS" "imaps://mail.example.com/INBOX" \
        | grep -c '^\* ' || echo 0)
PREV=$(cat "$STATE" 2>/dev/null || echo 0)
echo "$CUR" > "$STATE"

if [ "$CUR" -gt "$PREV" ]; then
  echo "Mail count went from $PREV to $CUR ($((CUR - PREV)) new)."
  echo '{"wakeAgent": true}'
else
  echo '{"wakeAgent": false}'
fi
```

**`disk_spike.sh` — wake only when usage crosses a threshold:**

```bash
#!/bin/bash
THRESH=85
USAGE=$(df / --output=pcent | tail -n 1 | tr -dc '0-9')
if [ "$USAGE" -ge "$THRESH" ]; then
  echo "Root disk is $USAGE% full (threshold: $THRESH%)."
  echo '{"wakeAgent": true}'
else
  echo '{"wakeAgent": false}'
fi
```

**`pr_review.sh` (Python) — wake on a new PR:**

```python
#!/usr/bin/env python3
import json
import subprocess
import pathlib

state_file = pathlib.Path("/tmp/vexis-pr-known")
known = set(state_file.read_text().split()) if state_file.exists() else set()

out = subprocess.check_output(
    ["gh", "pr", "list", "--state", "open", "--json", "number"],
    text=True,
)
current = {str(pr["number"]) for pr in json.loads(out)}
new = current - known
state_file.write_text("\n".join(current))

if new:
    print(f"New PRs since last check: {sorted(new)}")
    print(json.dumps({"wakeAgent": True}))
else:
    print(json.dumps({"wakeAgent": False}))
```

## Security model

Vexis is **single-user by design** (CLAUDE.md). The wake-gate
feature does NOT sandbox scripts further — they run as the daemon
user, same uid as everything else in vexis. What the runner DOES
enforce:

- **Path confinement.** `--script` must resolve to a file inside
  `~/.vexis/scripts/`. The runner does `resolve()` (follows
  symlinks) + `is_relative_to(scripts_dir)`. Absolute paths,
  `../foo` traversal, and symlinks pointing outside are all
  rejected before any subprocess fires. `cli_schedule.create`
  validates the path at create time too so the brain (or CLI
  user) gets immediate feedback instead of discovering at first
  tick.
- **Curated environment.** The subprocess does NOT inherit the
  daemon's full `os.environ`. It receives only:
  - `PATH`, `HOME` (from the daemon).
  - `LANG` / `LC_ALL` / `LC_CTYPE` if set (unicode handling).
  - `VEXIS_SCHEDULE_ID`, `VEXIS_SCHEDULE_NAME`,
    `VEXIS_SCHEDULE_TICK_TS` (script metadata).

  Notably absent: `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`,
  and any other secret the daemon happens to hold. A buggy script
  that does `env | curl ...` leaks nothing useful. See
  `tests/test_schedule_script.py::test_secrets_not_leaked_to_script_env`.
- **Hard timeout.** `script_timeout_seconds` (default 120, max
  3600) is the wall-clock kill. On timeout the brain turn is
  SKIPPED — an unresponsive monitor must not pin an LLM open.

## Failure modes

| Failure                              | Brain | `last_status` | `consecutive_errors` |
|--------------------------------------|-------|---------------|----------------------|
| Path escapes scripts dir             | skip  | `error`       | unchanged            |
| Script missing / not executable      | skip  | `error`       | unchanged            |
| Subprocess exec failure (OSError)    | skip  | `error`       | unchanged            |
| Timeout (killed)                     | skip  | `error`       | unchanged            |
| Non-zero exit                        | **wake** | `ok` (set by brain outcome) | unchanged |
| Wake gate `{"wakeAgent": false}`     | skip  | `ok`          | reset to 0           |

**Script-side failures do NOT count toward
`consecutive_errors`.** A buggy gate that keeps timing out would
otherwise auto-pause a legitimate schedule at the threshold —
hostile UX (we'd hide the user's monitor from them). Only
brain-side enqueue / response failures roll up to the auto-pause
counter.

**Non-zero exit deliberately wakes the brain.** A monitor script
that errors out is itself a real signal — the monitored system
might be broken. Silently skipping on exit code would mask
outages. The script's stdout AND stderr are passed to the brain
with a `[script exited N; stderr:]` marker so the model sees the
failure cause.

## Debugging

Live log lines to grep for in the daemon log:

- `running pre-run script` — gate fired, subprocess starting.
- `pre-run script gated wake` — gate said `wakeAgent: false`,
  brain skipped. This is the steady-state happy path.
- `pre-run script timed out` — hit the timeout cap.
- `pre-run script stderr:` — captured stderr (always logged at
  INFO so you can debug a flaky script).
- `script path rejected` — path-confinement failure (security
  rail tripped).

Inspect a single schedule's history:

```
vexis-agent schedule show <id> --output text
```

Shows `last_fire_at`, `last_status`, `last_error` — the
`last_error` field is where script failure reasons land.

To smoke-test a script standalone (same env vars the manager
would inject):

```bash
VEXIS_SCHEDULE_ID=test \
VEXIS_SCHEDULE_NAME=test \
VEXIS_SCHEDULE_TICK_TS=$(date -u +%FT%TZ) \
  ~/.vexis/scripts/check_mail.sh
```

## Out of scope (future work)

- **`no_agent: true`** — script-only schedules that NEVER wake
  the brain, even when the gate says yes. Separate issue.
- **`context_from`** — chaining a schedule's output into another
  schedule's prompt. Separate issue.
- **Script discovery / templates** — a `~/.vexis/scripts/`
  examples directory shipped with vexis. The user writes scripts
  themselves for now; the examples above are the docs.

## Reference

- Source pattern: Hermes-agent `cron/jobs.py:551-980` —
  `--script`, `wakeAgent: false`, `no_agent` mode, `context_from`.
- Tests: `tests/test_schedule_script.py` — covers all five
  acceptance cases plus round-trip + slash-flag parser.

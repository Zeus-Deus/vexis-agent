# Modular subsystems (issue #39)

vexis-agent can be embedded as a headless backend — web transport
only, no desktop, no Telegram. To support that, **every core subsystem
is individually switchable via `~/.vexis/config.yaml`**, default-on, so
a deployment composes itself by turning off what it does not want.

This follows the add-on loader's `enabled` / `disabled` model and the
"core stays modular" posture already used for `curator`, `kanban`,
`goals`, and `schedules`. There is **no shipped profile/preset** — a
hardcoded "business" bundle would ship to every user. Composition is
per-deployment config only.

## The contract

- **Default = on.** A config that mentions none of these keys boots
  byte-for-byte like the pre-issue daemon.
- **Each subsystem is independent.** Turning one off never forces
  another off (except where one genuinely needs another at runtime —
  see scheduling below).

## Toggle map

| Subsystem | Config key | Default | Off means | Hot-reload? |
|---|---|---|---|---|
| Background tasks (`vexis-bg`) | `background_tasks.enabled` | `true` | `bg_spawn` refuses with a `Disabled` error; existing tasks still status/tail/cancel | yes (per spawn) |
| Watcher (codemux orchestration) | `watcher.enabled` | `true` | controller not constructed; `watch_*` ops return `CodemuxNotConfigured`; Telegram watcher verbs hidden | no (startup) |
| Relationship / user-fact learning | `relationships.enabled` | `true` | silent extractor + restart-recovery + promote passes + per-turn hook all skip | yes (per tick / turn) |
| Skill / memory curator (lessons) | `learning.enabled` | `true` | lesson reviewer skips; controller still runs if relationships on | yes (per tick) |
| Goals (`/goal`) | `goals.enabled` | `true` | slash command + post-turn hook reply "disabled" | yes |
| Scheduling (`/schedule`) | `schedules.enabled` | `true` | manager thread not started; slash command replies disabled | no (startup) |
| Archive curator | `curator.enabled` | `true` | controller not started | yes |
| Kanban | `kanban.enabled` | `true` | store + dispatcher not constructed | no (startup) |
| Telegram transport | `transports.telegram.enabled` | `true` | no long-poll; bot token no longer required | no (startup) |
| Web dashboard + chat | `transports.web.enabled` | `true` | FastAPI dashboard not started | no (startup) |

`brain.file_mutation_footer`, `compression.enabled`, and `voice.enabled`
are pre-existing per-feature switches documented with their own
features.

## The two learning systems are distinct switches

The learning controller (`core/learning_curator.py`) hosts two
independent jobs that used to ride a single flag:

- **Skill / memory curator** (`learning.enabled`) — promotes
  generalized lessons into skills / `MEMORY.md` / `USER.md`. Makes the
  agent faster over time. **Keepable** for a headless backend.
- **Relationship / user-fact learning** (`relationships.enabled`) — the
  v3c silent extractor that builds a social graph in
  `RELATIONSHIPS.md`. **Droppable** — personal, useless to a backend
  with no human owner.

The controller daemon starts if **either** flag is on; each per-tick
pass is gated again on its own flag. So:

| `learning.enabled` | `relationships.enabled` | Result |
|---|---|---|
| true | true | both run (default — unchanged) |
| true | false | lessons only (typical headless: keep the speed-up, drop the social graph) |
| false | true | relationship extraction only |
| false | false | controller daemon does not start |

> Note: `USER.md` identity promotion is part of the lesson classifier
> and rides `learning.enabled`. Only the v3c third-party-fact
> extraction (`RELATIONSHIPS.md`) is on the `relationships` switch.

## Transport selection

`transports.telegram` and `transports.web` choose the active
transport(s). Both accept a bare bool or a nested `enabled:` key:

```yaml
transports:
  telegram: false          # bare bool …
  web:
    enabled: true          # … or nested, both honoured
```

- **Telegram off** drops the main long-poll loop *and* the bot-token
  requirement: `core.config.load_config(require_telegram=False)` lets
  the daemon boot without `TELEGRAM_BOT_TOKEN` / `TELEGRAM_ALLOWED_USER_ID`.
  The daemon then blocks in `main._run_headless_until_signal`, serving
  the dashboard + control socket until SIGTERM (same shutdown path a
  signal already takes for the long-poll).
- **Web off** skips starting the dashboard — a Telegram-only deployment
  with no HTTP surface.

### Scheduling needs a draining transport

`/schedule` fires are drained by the transport's `claim() ? drain :
enqueue` loop. With Telegram disabled there is no loop to drain them, so
fires would enqueue and never run. The daemon logs a `WARNING` at boot
if `schedules.enabled: true` while `transports.telegram: false`. Set
`schedules.enabled: false` for a headless deployment.

In-place `/restart` (the dashboard/Telegram affordance) is a no-op in
headless mode for now — issue #39 scope is *booting* headless, not
in-place restart. `systemctl restart` still works.

## Example: headless web backend

```yaml
transports:
  telegram: false        # no phone in the loop
  web:
    enabled: true        # dashboard + chat API only

relationships:
  enabled: false         # no social graph
watcher:
  enabled: false         # no codemux desktop orchestration
schedules:
  enabled: false         # no transport to drain fires
goals:
  enabled: false         # optional — drop if unused
background_tasks:
  enabled: false         # optional — drop if unused

# learning.enabled left default-on: keep the skill/memory speed-up.
```

Everything not listed stays on its default.

## Provisioning headlessly (non-interactive setup, issue #40)

Issue #39 made the daemon *boot* headless; issue #40 makes it *set up*
headless — no interactive TTY, no Telegram. `vexis-agent setup` grew an
unattended path that writes the headless config for you:

```sh
# Disable the Telegram transport + provision a Telegram-free .env,
# with no prompts. Equivalent: VEXIS_WEB_ONLY=1 vexis-agent setup --non-interactive
vexis-agent setup --web-only --non-interactive
```

What it does, all without reading stdin:

- writes an active `transports:` block with `telegram: false`, `web: true`
  (the same toggle the daemon reads — there's still no shipped preset,
  setup just composes it for you),
- comments out the Telegram placeholders the `.env` template ships so
  there are no active Telegram values, and
- skips the Telegram prompts entirely.

`--web-only` implies `--non-interactive`; `VEXIS_WEB_ONLY=1` implies
both. `--non-interactive` on its own provisions a normal Telegram
install from `$TELEGRAM_BOT_TOKEN` / `$TELEGRAM_ALLOWED_USER_ID` in the
environment instead of prompting. Pick the brain with `$VEXIS_BRAIN_KIND`
(default `claude-code`). Under the hood it feeds `run_setup()` the
existing prompt/confirm/choice seam via `env_backed_prompt` +
`noninteractive_*` providers — no second wizard.

`vexis-agent doctor` agrees: with the Telegram transport disabled it
treats absent Telegram secrets as a clean pass, so a headless container
passes the readiness check.

### Dockerfile

`Dockerfile.web-only` (repo root) provisions at build time and launches
unattended:

```dockerfile
FROM python:3.11-slim
RUN useradd --create-home vexis
USER vexis
WORKDIR /home/vexis
ENV PATH=/home/vexis/.local/bin:$PATH
RUN pip install --user --no-cache-dir vexis-agent   # or -e /src from a checkout
ENV VEXIS_WEB_ONLY=1
RUN vexis-agent setup --non-interactive              # web-only, no TTY, exit 0
EXPOSE 8766
CMD ["vexis-agent", "run"]
```

The brain CLI (e.g. `claude`) must be installed + authenticated in the
image for real replies — mount `~/.claude` or set `ANTHROPIC_API_KEY` at
run time. The chat API is bearer-token gated; read the token from
`~/.vexis/dashboard_token` inside the container and POST
`/api/v1/chat/send`.

## Where each gate is read

All gates live in `core/yaml_config.py` (kanban's in `core/kanban/lanes.py`),
read disk per call via `_section(...)`, and share the `_bool_or_default`
parser. Startup-bound gates (transport selection, watcher construction,
kanban/schedule/curator daemon start) are evaluated once in `main._run`
and need a daemon restart, the same as `brain.kind`. Per-call gates
(`background_tasks`, `relationships`, `goals`, `learning`,
`file_mutation_footer`) hot-reload at the next read boundary.

Tests: `tests/test_subsystem_toggles.py` (gates + transport parsing +
`load_config` + the `bg_spawn` dispatch gate) and the
`test_*_still_starts` cases in `tests/test_learning_curator.py` (the
learning/relationships split).

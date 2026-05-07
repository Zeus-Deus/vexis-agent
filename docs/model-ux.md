# Model UX

Vexis exposes the per-subsystem model-tier system through two
mutation surfaces (`/model` slash command on Telegram and the
Models tab on the dashboard) plus the `~/.vexis/config.yaml`
edit-and-restart path. This doc covers what each knob does,
what's hot-reloadable vs needs a restart, and the validator
+ spawn-site backstop that catch mistakes before they ship.

For the per-brain reference (auth, session storage, MCP config,
tool naming) see [`docs/brains.md`](brains.md). For the
opt-in / opt-out flow between claude-code and opencode see
[`docs/migration.md`](migration.md) (which now points back here
for the recommended path).

## Config knobs

| Knob | Type | What it does | Hot-reload? |
|---|---|---|---|
| `brain.kind` | str | Selects which agent CLI vexis spawns under (`claude-code` / `opencode` / `null`). Read once at startup. | **Restart required** |
| `models.subsystems.<name>` | str | NEW (Phase B+) per-subsystem tier override. Value is one of `tiny` / `small` / `medium` / `large`, OR a raw model id for power users. Wins over the legacy `models.<name>` key when both are set. | Hot |
| `models.tiers.<brain-kind>.<tier>` | str | Per-brain tier→native-id override. Example: `models.tiers.opencode.large: openai/gpt-4o`. | Hot |
| `models.<subsystem-name>` | str | LEGACY raw-string passthrough (pre-Phase-B). Works on claude-code via passthrough; breaks on opencode (which requires `provider/model` shape). The slash + dashboard surface a rule-4 warning when this combo would crash. See [`docs/migration.md`](migration.md). | Hot (when valid) |
| `models.brain` | str | Foreground-display only. The dashboard renders this in the resolution table; the foreground turn never passes `--model` to the brain CLI. | Hot for display |
| `model_ux.enabled` | bool | Gates the `/model` slash command and the dashboard's edit affordances. Default `true` (Day 5 release flip). Set `false` to silence both surfaces without code changes. | Restart required |

### Why `brain.kind` needs a restart

`main.py` reads `brain.kind` once at daemon startup and instantiates
the corresponding `Brain` subclass (claude-code: `ClaudeCodeBrain`;
opencode: `OpenCodeBrain`; null: `BrainNull`). The instance is bound
to `MessageHandler` for the daemon's lifetime. Changing the disk
value without restarting leaves the daemon running on the old brain.

The dashboard's 5-second polling and the slash command's `/model
status` both surface a **canary warning** when the on-disk value
differs from the running brain — so you'll see "edited brain.kind
without restarting" the next time you look at either surface, with
the literal restart command in the suggested-fix text.

### Why everything else hot-reloads

`subsystem_tier()` and `model_for_tier()` re-read
`~/.vexis/config.yaml` on every call (verified at
`core/yaml_config.py:_read_raw` — no module-level cache). Every aux
spawn (curator, judges, extractors, classifier) goes through
`brain.spawn_aux(prompt, model_tier=subsystem_tier(name))`, so the
next aux call after your edit sees the new value. Foreground turns
don't pass `--model` (use the brain's account default), so they're
unaffected by tier changes.

## Why model name is the primary input now

Pre-picker-UX, both the slash command and the dashboard
dropdown made tier (`small`/`medium`/`large`) the primary
knob: users were nudged toward "pick a tier; the brain picks
the model". The picker UX research (`.plans/model-picker-ux-research.md`
§1) surfaced this as an inversion bug — users routinely think
in model names ("I want claude-opus-4-1 on the curator") and
were forced to translate that intent into a tier or remember
the legacy `models.<sub>: sonnet` raw-string shape. Worse:
tiers are unstable across brains (claude-code's `large` →
`sonnet`; opencode's `large` → `anthropic/claude-sonnet-4`),
so the same tier picked yesterday on claude-code resolves to
a different family on opencode.

Day 1–4 of the picker UX inverted this:
- **Picker buttons surface model names directly** (provider-grouped).
  Tiers move into a "Tier fallbacks (advanced)" section at the
  bottom of the dashboard dropdown.
- **Aliases (`haiku`/`sonnet`/`opus`) are omitted from picker
  buttons** to enforce explicit version pinning — yesterday's
  `sonnet` is a different model than tomorrow's `sonnet` once
  Anthropic ships a new generation. The typed-arg path keeps
  aliases for power-user convenience.
- **Tiers stay as the per-subsystem default floor** under the
  hood — `subsystem_tier()` and `model_for_tier()` resolution
  is unchanged. The picker writes to `models.subsystems.<name>`;
  the validator and resolution layer disambiguate by inspecting
  the value's shape, not by the key location.

## /model slash command

```
/model                                 show current resolution
/model status                          alias for bare /model
/model list                            enumerate subsystems + brains
/model list <brain>                    per-brain model hints
/model set brain <name>                change brain.kind (restart req)
/model set <subsystem>                 picker: tap provider, then model
/model set <subsystem> ?               picker (explicit alias of above)
/model set <subsystem> <tier-or-name>  typed-arg: set per-subsystem
/model refresh                         refresh opencode discovery cache
/model reset                           reset all subsystems to defaults
/model reset <subsystem>               reset one subsystem
```

### Examples

Open the picker for the curator, tap a provider, then a model:

```
/model set curator
# Reply: Pick a model for curator (currently: small).
#        Tap a provider, then a model.
#        [anthropic] [openai] [✗ Cancel]
# (Tap anthropic →)
#        curator → anthropic. Tap a model.
#        [claude-haiku-4-5] [claude-opus-4-1] [claude-sonnet-4-6]
#        [← Back] [✗ Cancel]
# (Tap claude-opus-4-1 →)
#        ✓ curator → claude-opus-4-1 (resolves to claude-opus-4-1
#        on claude-code). Takes effect on the next curator call.
```

Switch the goal judge to `large` (typed-arg, resolves to `sonnet`
on claude-code, `anthropic/claude-sonnet-4` on opencode):

```
/model set goal_judge large
```

Switch to opencode (next restart picks it up):

```
/model set brain opencode
# Reply: ✓ brain.kind → opencode
#        ⚠ Restart vexis to take effect (e.g. systemctl --user
#          restart vexis-agent). brain.kind is read once at startup.
```

Reset just the curator (drops both legacy and new-schema entries
for that subsystem):

```
/model reset curator
```

### Picker walkthrough

The picker uses Telegram inline keyboards. The flow:

1. **Provider step.** Bot replies with one button per discovered
   provider (anthropic first, then alphabetical) plus a Cancel
   row. The picker prompt copy mentions that aliases are omitted
   from the buttons — power users who want one keep using the
   typed-arg path.
2. **Model step.** Tapping a provider edits the existing message
   to show that provider's models (full names only — aliases
   filtered) plus `← Back` and `✗ Cancel`. Edit-in-place
   preserves the user's scrollback position; no new reply.
3. **Confirmation.** Tapping a model edits the message to the
   same `✓ <subsystem> → <model>` confirmation the typed-arg
   path emits. Validator runs pre-write; on error severity the
   message edits to the refusal copy and no write happens.

Cancel deletes the picker reply entirely via `bot.delete_message`
so the chat doesn't accumulate dead UI elements (the user's
slash message persists). If the picker reply is older than 48
hours (Telegram bot delete-message limit), the handler logs +
falls back to editing the message to `(cancelled)` rather than
crashing or leaving live picker buttons in chat.

### Pagination

Providers with more than 20 models (opencode's `openrouter` is
the typical case) paginate. Top row carries `← Prev` /
`page n/m` / `Next →` based on the current page; the page
indicator is a no-op button (Telegram requires every button
to have callback_data, so a deliberately-ignored callback is
the cleanest way to render an inert label).

### `/model refresh`

```
/model refresh
# On opencode: invalidates the in-process discovery cache,
#              re-runs `opencode models --refresh` so models.dev
#              picks up newly-added providers, replies with
#              per-provider counts.
# On claude-code: informational no-op — the curated list is
#                 in-process and has no live cache to refresh.
```

`/model refresh` and the dashboard's refresh button share the
same in-process helper (`core.model_discovery.refresh_opencode_models`).
Single backend primitive, two surfaces — there's no HTTP
round-trip between the daemon and itself.

### Validator refusal

The validator runs **pre-write** on every `set` (typed-arg
path AND picker callback). Error-severity findings refuse
the write with the suggested-fix copy inline. Example:
opencode + bare alias:

```
/model set learning_review sonnet     # on brain.kind: opencode
# Reply: Won't write — validator rejected the proposed config:
#   • [learning_review] resolves to bare alias 'sonnet' on opencode;
#     the spawn would fail with 'Model not found: sonnet/.'.
#     → 'sonnet' is a bare alias; opencode requires provider/model
#       shape. Switch to abstract tier 'small' (resolves to
#       anthropic/claude-haiku-3-5) or pick an explicit
#       provider/model from /model list opencode.
#       Run: /model set learning_review small
```

Day 4 of the picker UX promoted **rule 6** (available-models
membership) from warning → error on opencode: a configured id
that isn't in `opencode models` output now refuses the write
rather than warning-and-letting-the-spawn-fail. claude-code
stays at warning because (a) its discovery is a curated
in-process list that goes stale between Anthropic releases and
(b) the spawn itself errors gracefully on truly unknown names.

Same vocabulary the dashboard surfaces and the spawn-site
`BrainModelNotFoundError` carries — single source of truth across
all three surfaces (validator + dashboard + spawn-site backstop).

### `brain.kind` typo policy

The validator's rule 1 only **warns** on unknown `brain.kind`
because the daemon falls back to `claude-code` at startup with a
warning rather than crashing. The slash command **refuses** to
write the typo anyway — typos here are user-hostile to recover
from (you think you switched but didn't). This is a policy
decision documented at the call site, not a severity-driven
behaviour.

## Models dashboard tab

Reachable at `/#models` once the daemon is up. Same data source
as `/model status` (`build_resolution_table` is the shared helper).

### Layout

- **Brain banner** — current `brain.kind` + clickable "switch to:"
  affordances for the other two kinds (when `model_ux.enabled` is
  `true`). Switching opens a confirm modal.
- **Validator (whole config)** — error/warning-level findings
  scoped to the whole config (e.g. typo'd `brain.kind`, unknown
  legacy keys). Hidden when no findings present.
- **Subsystem resolution table** — one row per known subsystem.
  Editable `<select>` per row sourced from
  `available_models_by_provider` (Day 1 of picker UX added the
  provider-grouped API field; Day 2 migrated the dropdown). Each
  provider becomes its own `<optgroup>` (anthropic first, then
  alphabetical); a `Tier fallbacks (advanced)` `<optgroup>`
  always sits at the bottom. Aliases (`haiku`/`sonnet`/`opus`)
  are filtered out of the picker per the version-pinning
  decision in `.plans/model-picker-ux-research.md` §5 — the
  typed-arg path on the slash still accepts them. When a row's
  configured value isn't otherwise visible (e.g. legacy alias),
  it surfaces under a dedicated `Current` `<optgroup>` at the
  top so the dropdown's value stays valid.
  Status column shows ✓/⚠/✗/ⓘ glyph based on the highest-severity
  finding for that row, with a hover tooltip carrying the
  suggested-fix.
- **Per-row search filter** — when a row's option count exceeds
  ~30 (opencode trips this; claude-code stays simple), an
  `<input type="text">` renders above the `<select>` and
  filters visible options as the user types (debounced ~150ms).
  Filter matches against full provider/model id; provider
  buckets with no matches collapse out entirely. Search input
  is NOT auto-focused — default focus stays wherever the
  browser put it so navigation keystrokes work as before.
- **Tier overrides** — collapsible per-tier table showing the
  configured override (if any) and the default for the active
  brain. Day 4 added the read display; the editor lands in a
  later workstream.
- **Available models** — per-brain hint + a "refresh" button that
  shares `core.model_discovery.refresh_opencode_models()` with
  the slash command's `/model refresh` (single backend primitive,
  two surfaces). claude-code's curated list is in-process and
  has no live cache, so the button is a no-op there.

### Brain switcher modal

Clicking a "switch to:" affordance opens a confirm modal that:
- shows the **restart-required** reminder prominently,
- detects the **legacy-keys → opencode trap** inline (when
  switching to opencode, surfaces `docs/migration.md` pointer +
  a "legacy raw-string keys will surface as errors after the
  switch" note),
- pulls current-brain validator findings so you see what's
  already broken before confirming,
- dispatches `POST /api/v1/models/brain` on Confirm; Cancel /
  Esc / click-outside dismisses without write.

Refusal posture matches the slash: only typos refuse (server-side
policy). Pre-existing rule-4 errors surface in the warnings list
as awareness, not a hard block — you opted into the brain change
knowing you'll fix tiers post-restart.

### Optimistic update + race guard

Dropdown changes optimistically swap the visible value before
the POST returns. The 5-second polling loop guards against
clobbering the in-flight optimistic state via a
`pendingMutationCount` ref — refresh skips state-replace when
count > 0. The mutation handler itself decrements the count
**before** its own converge/revert refresh, so the recovery
refetch always runs even after a 400 response.

On 400 (validator refusal), the optimistic update reverts to the
canonical server state and a toast surfaces the validator's
suggested-fix copy verbatim.

## Edit-during-active-turn race

**Scenario.** You have `/goal write a haiku then improve it
twice` in flight. The brain just emitted turn 1; the goal judge
spawn is `await`ing. You run `/model set goal_judge tiny`.

**What happens.** The goal_judge spawn calls
`subsystem_tier()` once at the start of its
`await brain.spawn_aux(...)`. So:

- If your YAML write completes **before** that read, the
  in-flight judge call uses the new tier.
- If the read happens first (more likely — the spawn is already
  mid-`await`), the old tier wins for this call; the new tier
  kicks in on the next continuation.

**Surfaced honestly in the slash reply** ("Takes effect on the
next *subsystem* call") so you can predict the timing. The
documented race is acceptable in practice — model changes
mid-goal are a power-user move, and the worst case is "one more
turn on the old model."

A stronger gate ("apply after this chat is idle for N seconds")
was rejected for v1 — adds complexity and surfaces a state
machine you'd have to reason about. The race documentation is
the right level of honesty.

## Comment-preservation backup

Both the slash command and the dashboard run a
**comment-presence-gated backup** before mutating
`~/.vexis/config.yaml`: if the current file has any line that
(after whitespace-strip) starts with `#`, the file is copied
verbatim to `~/.vexis/config.yaml.bak` before the write
proceeds. The toast / reply text mentions the backup when fired.

**Self-managing across daemon restarts.** After the first
mutation, comments are gone (PyYAML's `safe_dump` doesn't
preserve them), so the next mutation sees no comments and
**skips** the backup — the original `.bak` from the first edit
is preserved indefinitely. Trigger condition is on-disk state,
not in-memory flag, so a daemon restart between edits doesn't
clobber the original.

If you manually re-add comments to `config.yaml` after a
mutation, the next slash/dashboard write will re-back-up to the
same `.bak` (overwriting the previous one). That's correct — the
new commented state is what you just curated and want preserved.

The dashboard also surfaces a **confirm modal** before the
first save in a session if comments are detected, with a
"close-and-edit-directly" escape for users who'd rather edit
the YAML by hand than lose comments to PyYAML. The slash
auto-backs-up without prompting because it has no UI surface
for the modal.

## Validator + spawn-site backstop

Two layers catch model-config mistakes:

1. **Validator** — runs pre-write on every `/model set` (typed
   AND picker callback), every dashboard save, AND on daemon
   startup (logs findings without crashing). 7 rules, scoped to:
   - brain.kind validity (warning, fall-back posture)
   - subsystem name validity (warning per unknown key)
   - tier resolution to non-empty (defense in depth)
   - opencode + bare alias = error (would crash spawn)
   - claude-code + slashy id = warning (advisory)
   - **available-models membership: error on opencode (Day 4 of
     picker UX promotion), warning on claude-code** (curated list
     goes stale; spawn errors gracefully on truly unknown names).
     Skips abstract tiers (`tiny`/`small`/`medium`/`large`)
     because tiers are validated by the resolution layer, not by
     membership in the discovered set.
   - dead-knob hygiene (info, currently surfaces
     `migration_classifier`)

2. **Spawn-site backstop** — `Brain.spawn_aux` raises
   `BrainModelNotFoundError` when the underlying CLI rejects
   the model id at spawn time. Catches what the validator
   missed (stale claude-code discovery list, opencode discovery
   cache empty, edge cases).

The two layers share `suggested_fix` copy via constants in
`core.model_validator` so the user sees the same actionable
text regardless of which gate caught the mistake. Pinned by
`tests/test_brain_model_not_found.py::test_validator_and_backstop_share_suggested_fix_constants`.

## Switching from claude-code to opencode

Pre-Day-5 the recommended path was YAML edit + restart. Day 5
ships `/model` slash and the Models tab; the recommended path
is now:

1. Install opencode and authenticate:
   ```bash
   curl -fsSL https://opencode.ai/install | bash
   opencode providers login
   ```

2. From Telegram (or the dashboard's brain switcher modal):
   ```
   /model set brain opencode
   ```
   Reply confirms the write + reminds you to restart.

3. Migrate any legacy raw-string subsystem keys. The slash
   command's reply (or the dashboard's preview-mode warnings)
   tells you which subsystems would crash on opencode. For each
   one:
   ```
   /model set learning_review small
   /model set coherence_judge small
   /model set learning_triage tiny
   ```
   These set abstract tiers under `models.subsystems.<name>`;
   opencode's `DEFAULT_TIER_MAP_OPENCODE` resolves them to the
   correct provider/model id.

4. Restart vexis (`systemctl --user restart vexis-agent` or
   however you run it). The daemon comes up on opencode.

5. The dashboard's resolution table now shows opencode native
   ids (`anthropic/claude-haiku-3-5` etc.) and the canary
   silences itself.

The `~/.vexis/config.yaml` edit-and-restart workflow still works
— it's just no longer required. See [`docs/migration.md`](migration.md)
for the both-brains-in-one-config recipe if you want to flip
back and forth without re-migrating each time.

## Advanced configuration — tiers as fallback

Tiers (`tiny`/`small`/`medium`/`large`) are no longer the
primary input — they're the per-subsystem default floor under
the hood. The picker writes raw model ids to
`models.subsystems.<name>` directly; the resolution layer
treats any value not in `ABSTRACT_TIERS` as a raw passthrough.

You can still set tiers explicitly when you want
brain-portable defaults:

```yaml
models:
  subsystems:
    curator: small        # resolves per brain via DEFAULT_TIER_MAP_<brain>
  tiers:
    claude-code:
      large: opus         # override what `large` maps to on claude-code
```

The picker surfaces tiers under a `Tier fallbacks (advanced)`
`<optgroup>` at the bottom of the dropdown — selectable, just
visually demoted. The slash typed-arg path still accepts them:
`/model set curator small`.

### Aliases — picker omits, typed-arg keeps

The bare aliases `haiku`/`sonnet`/`opus` are accepted by the
typed-arg slash path:

```
/model set goal_judge sonnet     # power-user shorthand
```

They are **omitted from the picker buttons** by deliberate
design. Reasons: (a) aliases drift over time as Anthropic
ships new models behind the same name — yesterday's `sonnet`
is a different model than tomorrow's `sonnet`, so picking via
button (which implies "I clicked exactly this thing") would
silently update on you; (b) version pinning matters for
reproducibility — when you pick `claude-sonnet-4-6` from the
button, that selection is stable until you change it. The
picker enforces version pinning by surfacing only full names;
the typed-arg path keeps aliases for users who want
auto-tracking-latest.

## Default-flip posture

`model_ux.enabled` defaulted to `false` Days 1-4 of the rollout
(slash + dashboard wired but flag-gated). Day 5 flipped the
default to `true`. Existing users who hadn't explicitly set the
knob get the new behaviour on next restart; users who set
`model_ux.enabled: false` explicitly stay opted out.

The spawn-site `BrainModelNotFoundError` backstop fires
**regardless** of the flag — it's catching real spawn errors
that should always have actionable messaging. Flag-gating
that would let the production claude-code path silently fail
with the raw CLI wording.

## Reference

- `core/model_validator.py` — validator engine + 7 rules + the
  shared suggested-fix template constants + the brain.kind
  consistency canary.
- `core/yaml_config_writer.py` — atomic-write helper +
  `has_comments` + `backup_if_commented`.
- `core/model_discovery.py` — claude-code curated list + opencode
  subprocess wrapper + 5-min cache.
- `core/web_server.py` — `_models_payload` + 4 POST endpoints
  (`/set`, `/reset`, `/brain`, `/discovery/refresh`).
- `transports/telegram.py` — `_on_model` slash handler.
- `web/src/pages/ModelsPage.tsx` — dashboard tab.

For the design rationale and per-day audit history see
`.plans/model-management-ux-research.md` (gitignored;
mirror-of-record at `docs/brains.md` "Phase C close" notes the
post-rollout posture).

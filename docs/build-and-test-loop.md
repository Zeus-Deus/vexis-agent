# Build-and-test agent loop

Vexis can now compile, run, observe, iterate, and verify — end to end —
inside a per-task Docker sandbox, on a laptop or a headless VPS. The
six components are sketched in the research at
`/home/deus/vexis-workspace/agent-research/index.html`. This doc is the
operational reference.

## TL;DR

```bash
# 1. Start a sandbox for a task. Lazy-started by `exec`, so this is
#    only needed when you want a non-default image or extra mounts.
vexis-sandbox start build-feature --image rust:alpine

# 2. The agent runs build/test commands through the sandbox:
vexis-sandbox exec build-feature -- cargo test

# 3. Once it claims done, the verify hook checks acceptance criteria:
vexis-verify run build-feature --checks /workspace/checks.yaml

# 4. Tear down (also happens automatically when vexis-bg finishes):
vexis-sandbox stop build-feature
```

`vexis-bg spawn` opts in to this loop automatically when the prompt
contains build/test keywords (`compile`, `cargo`, `pytest`, `make`, …).
Override per task with `--sandbox` / `--no-sandbox`. Pass `--verify
<path>` to wire the post-claim check; it implies `--sandbox`.

## The six components

| # | CLI               | Purpose                                                 |
|---|-------------------|---------------------------------------------------------|
| 1 | `vexis-sandbox`   | Per-task Docker container, lazy start, persistent state |
| 2 | `vexis-bg`        | Routes background tasks through the sandbox (heuristic) |
| 3 | `vexis-display`   | Headless Xvfb/Wayland display *inside* the sandbox      |
| 4 | `vexis-ui`        | AT-SPI snapshot/click/type/press for native apps        |
| 5 | `vexis-ui vision-snapshot` | Screenshot fallback when AT-SPI is empty       |
| 6 | `vexis-verify`    | Post-claim YAML check spec runner                       |

## `vexis-sandbox` reference

```
vexis-sandbox start <task-id> [--image IMG] [--mount HOST:CONT]
vexis-sandbox exec  [--cwd PATH] [--timeout N] [--json] [--no-start] <task-id> -- CMD...
vexis-sandbox cp    <task-id> SRC DST          # 'container:/abs/path' on one side
vexis-sandbox stop  <task-id>
vexis-sandbox list
```

* `exec` flags go BEFORE the task-id (same convention as `docker exec`).
  Anything after `--` is the command to run inside the sandbox.
* `--json` makes `exec` always exit 0 and emit a JSON envelope
  containing `{exit_code, stdout, stderr}`. Without it, the wrapper
  passes through stdout/stderr/exit verbatim.
* Each task-id maps to a container named `vexis-sb-<task-id>`. Workspace
  is mounted at `/workspace` from `$VEXIS_WORKSPACE` (default
  `~/vexis-workspace`); `/scratch` is mounted from
  `/tmp/vexis-sandbox/<task-id>/scratch`.
* Default image is `debian:bookworm-slim`. Override per task with
  `--image`, or set `VEXIS_SANDBOX_DEFAULT_IMAGE` for a host-wide
  default.

## `vexis-bg` routing

The daemon constructs `BackgroundTasks` with a `SandboxRunner` whenever
the host has both `docker` and `vexis-sandbox` on PATH. The runner is
used opportunistically — `vexis-bg spawn` calls it only when the task's
sandbox decision (heuristic or explicit) resolves to True.

Three flags on `vexis-bg spawn`:

* `--sandbox` / `--no-sandbox` — force the decision.
* `--verify <path>` — point at a YAML check spec. Implies `--sandbox`.

The agent's system prompt is automatically augmented with a short
"## Build-and-test sandbox" section telling it to run filesystem
mutations through `vexis-sandbox exec <task-id> -- …`. The env var
`VEXIS_SANDBOX_TASK_ID` is also exported into the agent's process so
future tools (`vexis-display`, `vexis-ui`) can discover the task-id
without parsing the prompt.

When the agent process exits with code 0 and a `--verify` path is set,
the watcher runs `vexis-verify run <name> --checks <path>` and flips
the task to `FAILED` if any check fails — the verify summary is shown
to the user in the completion notification. The sandbox container is
stopped after verify runs (or after a cancel / failure / shutdown), so
we never leak containers across daemon restarts.

## `vexis-verify` check spec

```yaml
checks:
  - name: tests-pass
    cmd: ["sh", "-c", "cd /workspace && cargo test"]
    expect_exit: 0          # default; null disables the exit check

  - name: greeting-prints-5
    cmd: ["/workspace/target/debug/myapp"]
    expect_stdout_contains: "5"
    expect_stdout_regex: '^\d+$'
    expect_stderr_contains: ""        # optional
    expect_stderr_regex: ""           # optional
```

* `vexis-verify template --path checks.yaml` writes a starter file.
* Exit codes: `0` = all checks passed, `1` = at least one failed, `2` =
  the spec couldn't be loaded (missing file / invalid YAML).
* The CLI is generic over any sandbox shape — pass a different runtime
  by setting up `Sandbox.exec` to point elsewhere; useful for the tests
  in `tests/test_verify.py`.

## `vexis-display` + `vexis-ui`

The display runs *inside the sandbox*, so the host's `:0` / `wayland-1`
is never touched.

```bash
vexis-display start build-feature                     # Xvfb at :99 by default
vexis-display env   build-feature --shell             # → export DISPLAY=:99
vexis-sandbox exec build-feature -- sh -c 'DISPLAY=:99 mygui &'
vexis-ui snapshot build-feature                       # AT-SPI tree DSL
vexis-ui click   build-feature 3
vexis-ui press   build-feature ctrl+s
vexis-ui vision-snapshot build-feature --out /scratch/before.png
```

Backends: `auto` (default) → `xvfb`. Pass `--backend wayland-headless`
for Cage / `Hyprland --headless` (the image must ship the binary).

`vexis-ui` ships a self-contained AT-SPI walker as an embedded Python
string (`vexis_agent/tools/ui/runner_src.py`); it's piped into the
sandbox via `python3 -c <source>` so the agent doesn't have to install
anything extra. Required inside the sandbox: `python3`, `pyatspi` (or
the d-bus session for the walker), `xdotool` (for `press` under X11)
or `ydotool` (Wayland), and a screenshot tool (`grim` / `import`) for
the vision fallback.

## Test gates

| Marker            | When to run                              |
|-------------------|------------------------------------------|
| `sandbox_docker`  | Real Docker; ~2s. Run locally pre-merge. |
| `display_real`    | Needs `vexis-test-xvfb:latest` (see       `tests/fixtures/Dockerfile.test-xvfb`). |
| `ui_real`         | Needs a live AT-SPI bus + GUI app.        |

Unit tests for all six components are unmarked and run in <1s
collectively. The full suite (`pytest`) excludes the three opt-in
markers above by default — see `pyproject.toml`'s `addopts` line.

## First milestone

The first acceptance test from the plan — Rust CLI that adds two
numbers, with a failing test that the agent fixes — runs cleanly with
just components 1, 2, 6 in place. A reproducible shell transcript is
embedded as comments in
`tests/test_sandbox_integration.py::test_state_persists_across_exec`
and was manually verified during implementation:

1. `vexis-sandbox start milestone-rust --image rust:alpine`
2. `vexis-sandbox exec milestone-rust -- cargo new addtwo --name addtwo`
3. Write buggy `add(a, b) → a - b`; first `cargo test` fails with exit 101.
4. Write fix `add(a, b) → a + b`; second `cargo test` passes.
5. `vexis-verify run milestone-rust --checks checks.yaml` → `all_passed: true`.
6. `vexis-sandbox stop milestone-rust`.

## Constraints (re-stated from the plan)

* Tailnet-only for any web surface. No `tailscale funnel`.
* No autostart/systemd for one-off sandboxes; they die with the task.
* Greenfield: no backward-compat shims. Either route through sandbox
  or use `--no-sandbox` explicitly.
* No GPU passthrough; software rendering (Xvfb) handles the 99% case.
* Don't touch the host's `:0` from sandbox tasks. The whole point is
  isolation. Targeting the host session must be an explicit per-task
  flag, never a default.

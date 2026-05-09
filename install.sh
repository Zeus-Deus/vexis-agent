#!/usr/bin/env bash
# vexis-agent installer — Linux only (Hyprland-targeted, Wayland-only).
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Zeus-Deus/vexis-agent/main/install.sh | bash
#
# Or with options (when piping into bash, pass them after `bash -s --`):
#   curl -fsSL ... | bash -s -- --dry-run
#   curl -fsSL ... | bash -s -- --skip-setup
#
# Env knobs:
#   VEXIS_VERSION=<tag-or-sha> pin to a specific git tag or commit
#                              (e.g. v0.2.0). Default empty = latest main.
#   VEXIS_REPO=git+...         override the source URL (forks, mirrors, ...).
#   NO_COLOR=1                 disable ANSI colors (per https://no-color.org/).
#
# What this script does (roughly):
#   1. Detects platform + privilege; refuses macOS/Windows + root.
#   2. Installs pipx (via pacman / apt / dnf / zypper, or pip --user fallback).
#   3. pipx install --force git+https://github.com/Zeus-Deus/vexis-agent.git@<branch>
#   4. Surfaces the soft dependencies vexis-agent needs (brain CLI,
#      Hyprland-Wayland actuator tools, Tailscale, systemd).
#   5. Auto-runs `vexis-agent setup` unless --skip-setup or piped
#      stdin can't reach a TTY.
#
# Plan §7.2 (.plans/packaging-implementation-plan.md), revised in
# Phase 5e to mirror the hermes/openclaw banner-and-auto-setup UX.

set -euo pipefail

# ── colors (only when stdout is a tty + NO_COLOR not set) ───────────
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
    BOLD=$'\033[1m'
    DIM=$'\033[2m'
    CYAN=$'\033[36m'
    GREEN=$'\033[32m'
    YELLOW=$'\033[33m'
    RED=$'\033[31m'
    MAGENTA=$'\033[35m'
    RESET=$'\033[0m'
else
    BOLD="" DIM="" CYAN="" GREEN="" YELLOW="" RED="" MAGENTA="" RESET=""
fi

print_banner() {
    cat <<EOF

${MAGENTA}${BOLD}┌────────────────────────────────────────────────────────┐
│            ⌬  vexis-agent installer                    │
├────────────────────────────────────────────────────────┤
│  Telegram-bridged agent for Linux (Hyprland).          │
│  Single-user. Hyprland/Wayland. Tailscale-friendly.    │
└────────────────────────────────────────────────────────┘${RESET}
EOF
}

section() { printf '\n%s%s◆ %s%s\n' "$BOLD" "$CYAN" "$1" "$RESET"; }
err()  { printf '  %s✗%s %s\n' "$RED" "$RESET" "$*" >&2; }
warn() { printf '  %s!%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
info() { printf '  %s→%s %s\n' "$DIM" "$RESET" "$*"; }
ok()   { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$*"; }

# ── arg parsing ─────────────────────────────────────────────────────
DRY_RUN=0
SHOW_HELP=0
SKIP_SETUP=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        --skip-setup)
            SKIP_SETUP=1
            shift
            ;;
        -h|--help)
            SHOW_HELP=1
            shift
            ;;
        *)
            err "Unknown argument: $1"
            exit 64
            ;;
    esac
done

if [[ "$SHOW_HELP" -eq 1 ]]; then
    cat <<'EOF'
vexis-agent installer

Usage:
  install.sh [--dry-run] [--skip-setup]

Flags:
  --dry-run     Print what would happen and exit without installing.
  --skip-setup  Don't auto-run 'vexis-agent setup' after installing.
                Default behaviour is to launch the wizard so users
                end up with a configured daemon in one shot.
  -h, --help    Show this help.

Environment:
  VEXIS_VERSION  Pin to a git tag or commit (e.g. v0.2.0).
                 Default empty = latest commit on main.
  VEXIS_REPO     Override the install source (default: GitHub main).
  NO_COLOR       Disable ANSI colors.
EOF
    exit 0
fi

print_banner

# ── platform + privilege checks ─────────────────────────────────────
section "Platform"
if [[ "$(uname -s)" != "Linux" ]]; then
    err "Linux only for now (detected: $(uname -s))."
    info "macOS / Windows support is on the roadmap; until then, see the manual install path in README.md."
    exit 1
fi
ok "Linux detected ($(uname -m))"

if [[ "$EUID" -eq 0 ]]; then
    err "Refusing to run as root. vexis-agent is single-user by design."
    info "Re-run as your normal user account; pipx and ~/.config/systemd/user need to live there."
    exit 1
fi
ok "Running as $(whoami) (non-root)"

# Hyprland is a soft hint at install time — actual enforcement happens
# at daemon start. Print a heads-up so non-Hyprland users know early.
if [[ "${XDG_SESSION_TYPE:-}" != "wayland" ]]; then
    warn "Wayland session not detected (XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-unset})."
    info "vexis-agent is Hyprland/Wayland-targeted; X11 won't work."
fi

# ── version resolution ──────────────────────────────────────────────
# Default install picks the latest semver tag (e.g. v0.2.0) so
# end-users only ever land on code the maintainer has explicitly
# released. If the repo has no tags yet (early-development), fall
# back to the main branch tip. VEXIS_VERSION pins to a specific tag
# or commit; VEXIS_REPO overrides the whole git URL.
section "Source"
GH_REPO="https://github.com/Zeus-Deus/vexis-agent.git"

resolve_default_version() {
    # If git's missing, we can't probe remote tags — fall back to
    # main. pipx install will pull git in transitively but that's
    # too late to discover the latest tag here.
    if ! command -v git >/dev/null 2>&1; then
        echo "main"
        return
    fi
    # Pull the latest semver tag from the remote without cloning.
    # `git ls-remote --tags --refs --sort=-v:refname` lists tags
    # newest-first; head -1 picks the freshest. Strip the refs/tags/
    # prefix and the trailing ^{} that some annotated tags emit.
    local latest
    latest="$(
        git ls-remote --tags --refs --sort=-v:refname "$GH_REPO" 2>/dev/null \
            | awk '{ print $2 }' \
            | sed 's|^refs/tags/||; s|\^{}$||' \
            | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+' \
            | head -1
    )"
    if [[ -n "$latest" ]]; then
        echo "$latest"
    else
        echo "main"
    fi
}

VERSION="${VEXIS_VERSION:-}"
if [[ -n "$VERSION" ]]; then
    REPO_DEFAULT="git+${GH_REPO}@${VERSION}"
    SOURCE_LABEL="pinned to ${VERSION}"
else
    RESOLVED="$(resolve_default_version)"
    REPO_DEFAULT="git+${GH_REPO}@${RESOLVED}"
    if [[ "$RESOLVED" == "main" ]]; then
        SOURCE_LABEL="latest main (no release tags yet)"
    else
        SOURCE_LABEL="latest release: ${RESOLVED}"
    fi
fi
REPO="${VEXIS_REPO:-$REPO_DEFAULT}"

ok "${SOURCE_LABEL}"
info "source:  ${REPO}"
if [[ "$DRY_RUN" -eq 1 ]]; then
    warn "DRY-RUN mode — no changes will be made"
fi

# ── pipx detection / install ────────────────────────────────────────
section "pipx"
ensure_pipx() {
    if command -v pipx >/dev/null 2>&1; then
        ok "pipx already installed: $(command -v pipx)"
        return 0
    fi

    info "pipx not found — attempting install."
    if [[ "$DRY_RUN" -eq 1 ]]; then
        warn "[dry-run] would install pipx via the OS-native package manager."
        return 0
    fi

    if command -v pacman >/dev/null 2>&1; then
        info "Arch detected — installing python-pipx via pacman."
        sudo pacman -S --needed --noconfirm python-pipx
    elif command -v apt-get >/dev/null 2>&1; then
        info "Debian/Ubuntu detected — installing pipx via apt."
        sudo apt-get update
        sudo apt-get install -y pipx
    elif command -v dnf >/dev/null 2>&1; then
        info "Fedora detected — installing pipx via dnf."
        sudo dnf install -y pipx
    elif command -v zypper >/dev/null 2>&1; then
        info "openSUSE detected — installing python-pipx via zypper."
        sudo zypper install -y python-pipx
    else
        warn "No supported package manager found (pacman/apt/dnf/zypper)."
        warn "Falling back to 'python3 -m pip install --user pipx' — needs Python ≥ 3.11."
        python3 -m pip install --user pipx
    fi

    pipx_path="$HOME/.local/bin"
    case ":$PATH:" in
        *":$pipx_path:"*) : ;;
        *)
            export PATH="$pipx_path:$PATH"
            warn "Added ${pipx_path} to PATH for this shell only."
            warn "Add it to your shell rc to make pipx-installed binaries persistent:"
            info "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc"
            ;;
    esac

    if ! command -v pipx >/dev/null 2>&1; then
        err "pipx installation appeared to succeed but pipx is still not on PATH."
        info "Inspect 'python3 -m pipx ensurepath' and re-run."
        exit 1
    fi
    ok "pipx installed: $(command -v pipx)"
}

ensure_pipx

# ── install vexis-agent (or update if already present) ─────────────
section "vexis-agent"

# Detect existing pipx install. Re-running curl-bash is the common
# "I want the latest version" path — short-circuit to pipx upgrade
# when we can (fast, just refreshes the package), fall back to
# --force install otherwise (full venv rebuild, slower).
ALREADY_INSTALLED=0
if command -v pipx >/dev/null 2>&1; then
    if pipx list --short 2>/dev/null | awk '{print $1}' | grep -qx 'vexis-agent'; then
        ALREADY_INSTALLED=1
    fi
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
    if [[ "$ALREADY_INSTALLED" -eq 1 ]]; then
        warn "[dry-run] vexis-agent is already installed — would update via:"
        warn "[dry-run]   pipx install --force '$REPO'  (rebuilds venv at the new ref)"
        info "[dry-run] would skip the setup wizard since it already ran on the previous install"
    else
        warn "[dry-run] would run: pipx install --force '$REPO'"
        warn "[dry-run] would run: vexis-agent setup (unless --skip-setup)"
    fi
    # Fall through to the soft-deps section so users see what their
    # current setup is missing without having to actually install.
fi

if [[ "$DRY_RUN" -eq 1 ]]; then
    # Skip the actual install in dry-run; soft-deps advice still
    # renders below so the user can see what's missing.
    if [[ "$ALREADY_INSTALLED" -eq 1 ]]; then
        SKIP_SETUP=1
    fi
elif [[ "$ALREADY_INSTALLED" -eq 1 ]]; then
    info "vexis-agent is already installed — updating to ${SOURCE_LABEL}."
    info "(Skipping the setup wizard; your config + workspace are preserved.)"
    # --force here both refreshes the venv at the new git ref AND
    # works regardless of whether the old install was from a tag,
    # main, or a custom VEXIS_REPO. pipx upgrade only works when the
    # package was originally installed from a registry, which doesn't
    # apply for our git+ source — so this is the right verb.
    pipx install --force "$REPO"
    ok "vexis-agent updated."
    SKIP_SETUP=1
else
    info "Installing vexis-agent from ${REPO}"
    pipx install --force "$REPO"
    ok "vexis-agent installed."
fi

# ── soft-dependency advice ──────────────────────────────────────────
section "Soft dependencies"
info "The daemon only requires a brain CLI to run. Other tools enable"
info "specific features — install only what you'll use."
info "Run 'vexis-agent doctor' anytime for the full readiness check."

CHECK_HEAD=" • "

# Brain CLI: required for daemon start. claude-code is the default;
# opencode is opt-in via brain.kind in config.yaml.
if command -v claude >/dev/null 2>&1; then
    ok "${CHECK_HEAD}claude (claude-code) on PATH"
elif command -v opencode >/dev/null 2>&1; then
    ok "${CHECK_HEAD}opencode on PATH"
else
    warn "${CHECK_HEAD}no brain CLI detected — install one of:"
    info "    claude-code: https://docs.anthropic.com/claude/claude-code"
    info "    opencode:    curl -fsSL https://opencode.ai/install | bash"
fi

# Compositor detection — scopes the desktop-control advice to what
# the user can actually use. Hyprland users get Hyprland-specific
# tools; non-Hyprland users get a clear "those tools won't help here"
# instead of a wrong-distro install command.
COMPOSITOR="other"
if [[ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]] || \
   [[ "${XDG_CURRENT_DESKTOP:-}" == *Hyprland* ]] || \
   [[ "${XDG_CURRENT_DESKTOP:-}" == *hyprland* ]]; then
    COMPOSITOR="hyprland"
elif [[ "${XDG_SESSION_TYPE:-}" != "wayland" ]]; then
    COMPOSITOR="non-wayland"
fi

# Desktop-control feature group (per-tool optional). Daemon starts
# without these; tools that need them (vexis-click etc.) just return
# a clear "tool unavailable" error when invoked. We only nudge users
# to install on Hyprland — telling a GNOME-X11 user to install
# 'hyprctl' is wrong.
if [[ "$COMPOSITOR" == "hyprland" ]]; then
    DC_MISSING=()
    for cmd in hyprctl wtype ydotool grim; do
        if ! command -v "$cmd" >/dev/null 2>&1; then
            DC_MISSING+=("$cmd")
        fi
    done
    if [[ ${#DC_MISSING[@]} -eq 0 ]]; then
        ok "${CHECK_HEAD}desktop control: Hyprland tools all present"
    else
        warn "${CHECK_HEAD}desktop control degraded: missing ${DC_MISSING[*]}"
        info "    Arch:   sudo pacman -S ${DC_MISSING[*]}"
        info "    Debian: sudo apt install ${DC_MISSING[*]}"
        info "    Fedora: sudo dnf install ${DC_MISSING[*]}"
    fi
elif [[ "$COMPOSITOR" == "non-wayland" ]]; then
    info "${CHECK_HEAD}desktop control: unavailable (needs Wayland)"
    info "    The daemon still runs for Telegram chat + brain dispatch."
    info "    Switch to Wayland (ideally Hyprland) to enable screenshots,"
    info "    typing, clicking, and window control."
else
    info "${CHECK_HEAD}desktop control: partial on non-Hyprland Wayland"
    info "    wtype + grim work on most Wayland compositors; hyprctl is"
    info "    Hyprland-only (workspace/window dispatches will no-op)."
fi

# Voice feature group (optional — only matters if you'll send voice
# notes via Telegram).
VOICE_MISSING=()
for cmd in voxtype ffmpeg; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        VOICE_MISSING+=("$cmd")
    fi
done
if [[ ${#VOICE_MISSING[@]} -eq 0 ]]; then
    ok "${CHECK_HEAD}voice notes: voxtype + ffmpeg present"
else
    info "${CHECK_HEAD}voice notes: optional (missing ${VOICE_MISSING[*]})"
    info "    Voice transcription stays disabled until both are installed."
fi

# Shell helpers (jq) — minor, but used by the dispatch wrappers.
if ! command -v jq >/dev/null 2>&1; then
    info "${CHECK_HEAD}shell helpers: install jq for the dispatch wrappers (optional)"
fi

# Tailscale (SOFT — dashboard works on localhost without it; the
# remote-from-phone story needs it).
if command -v tailscale >/dev/null 2>&1; then
    if tailscale status >/dev/null 2>&1; then
        ok "${CHECK_HEAD}tailscale up + logged in"
    else
        warn "${CHECK_HEAD}tailscale installed but not logged in"
        info "    Run: ${BOLD}tailscale up${RESET}"
    fi
else
    info "${CHECK_HEAD}tailscale: optional (dashboard will be localhost-only)"
    info "    Install: https://tailscale.com/download — then ${BOLD}tailscale up${RESET}"
fi

# ── auto-run setup wizard ───────────────────────────────────────────
section "Setup"
if [[ "$DRY_RUN" -eq 1 ]]; then
    info "[dry-run] would launch 'vexis-agent setup' here (skipped if --skip-setup)."
elif [[ "$SKIP_SETUP" -eq 1 ]]; then
    info "Skipping wizard (--skip-setup). Run 'vexis-agent setup' when you're ready."
elif ! { : </dev/tty; } 2>/dev/null; then
    # Piped curl-bash with no TTY — stdin's the install script. Skip
    # rather than blow up with the wizard's TTY guard.
    info "No TTY available (curl|bash with no terminal); skipping the wizard."
    info "Run '${BOLD}vexis-agent setup${RESET}' from a terminal to finish."
else
    info "Launching 'vexis-agent setup' (Ctrl+C to skip)..."
    # Run the wizard with stdin re-attached to the controlling tty.
    if ! vexis-agent setup </dev/tty; then
        warn "Setup wizard exited non-zero. You can re-run it any time:"
        info "  vexis-agent setup"
    fi
fi

# ── final next-steps ────────────────────────────────────────────────
section "Done"
cat <<EOF

  ${BOLD}vexis-agent${RESET} is installed at $(command -v vexis-agent)

  Useful commands:
    ${BOLD}vexis-agent doctor${RESET}             — readiness check
    ${BOLD}vexis-agent service install${RESET}    — install systemd user unit
    ${BOLD}systemctl --user enable --now vexis-agent.service${RESET}
                                   — start daemon at login
    ${BOLD}vexis-agent service logs -f${RESET}    — tail journald
    ${BOLD}vexis-agent backup${RESET}             — pack \$VEXIS_HOME + \$VEXIS_WORKSPACE
    ${BOLD}vexis-agent update${RESET}             — pipx-aware self-upgrade

EOF

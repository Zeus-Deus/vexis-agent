#!/usr/bin/env bash
# vexis-agent installer — Linux only.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/Zeus-Deus/vexis-agent/main/install.sh | bash
#
# Or with options (when piping into bash, pass them after `bash -s --`):
#   curl -fsSL ... | bash -s -- --dry-run
#
# Env knobs:
#   VEXIS_CHANNEL=stable|dev   default stable (main branch).
#   VEXIS_REPO=git+...         override the source URL (forks, mirrors, …).
#
# Doesn't run `vexis-agent setup` for you — that's a separate explicit
# step (matches the AUR convention; lets the user re-read the install
# output before answering interactive prompts).
#
# Plan §7.2 (.plans/packaging-implementation-plan.md).

set -euo pipefail

# ── colors (only when stdout is a tty) ──────────────────────────────
if [[ -t 1 ]]; then
    BOLD=$'\033[1m'
    GREEN=$'\033[32m'
    YELLOW=$'\033[33m'
    RED=$'\033[31m'
    DIM=$'\033[2m'
    RESET=$'\033[0m'
else
    BOLD=""
    GREEN=""
    YELLOW=""
    RED=""
    DIM=""
    RESET=""
fi

err()  { printf '%svexis-agent installer: %s%s\n' "$RED" "$*" "$RESET" >&2; }
warn() { printf '%svexis-agent installer: %s%s\n' "$YELLOW" "$*" "$RESET" >&2; }
info() { printf '%svexis-agent installer: %s%s\n' "$BOLD" "$*" "$RESET"; }
ok()   { printf '%s✓%s %s\n' "$GREEN" "$RESET" "$*"; }

# ── arg parsing ─────────────────────────────────────────────────────
DRY_RUN=0
SHOW_HELP=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)
            DRY_RUN=1
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
  install.sh [--dry-run]

Flags:
  --dry-run   Print what would happen and exit without installing.
  -h, --help  Show this help.

Environment:
  VEXIS_CHANNEL  stable (default, main branch) or dev.
  VEXIS_REPO     Override the install source (default: GitHub main).
EOF
    exit 0
fi

# ── platform + privilege checks ─────────────────────────────────────
if [[ "$(uname -s)" != "Linux" ]]; then
    err "Linux only for now (detected: $(uname -s))."
    err "macOS / Windows support is on the roadmap; until then, see the manual install path in README.md."
    exit 1
fi

if [[ "$EUID" -eq 0 ]]; then
    err "Refusing to run as root. vexis-agent is single-user by design."
    err "Re-run as your normal user account."
    exit 1
fi

# ── version + channel resolution ────────────────────────────────────
CHANNEL="${VEXIS_CHANNEL:-stable}"
case "$CHANNEL" in
    stable) BRANCH="main" ;;
    dev)    BRANCH="develop" ;;
    *)
        err "Unknown VEXIS_CHANNEL='$CHANNEL' (valid: stable, dev)."
        exit 64
        ;;
esac

REPO_DEFAULT="git+https://github.com/Zeus-Deus/vexis-agent.git@${BRANCH}"
REPO="${VEXIS_REPO:-$REPO_DEFAULT}"

info "Channel:    $CHANNEL (branch=$BRANCH)"
info "Source:     $REPO"
info "Mode:       $([[ $DRY_RUN -eq 1 ]] && echo 'DRY-RUN — no changes will be made' || echo 'install')"

# ── pipx detection / install ────────────────────────────────────────
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
        sudo pacman -S --needed --noconfirm python-pipx
    elif command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update
        sudo apt-get install -y pipx
    elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y pipx
    elif command -v zypper >/dev/null 2>&1; then
        sudo zypper install -y python-pipx
    else
        warn "No supported package manager found (pacman/apt/dnf/zypper)."
        warn "Falling back to 'python3 -m pip install --user pipx' — this needs Python ≥ 3.11."
        python3 -m pip install --user pipx
    fi

    # pipx installs to ~/.local/bin; make sure it's on PATH for *this*
    # shell so the install command immediately below succeeds.
    pipx_path="$HOME/.local/bin"
    case ":$PATH:" in
        *":$pipx_path:"*) : ;;
        *)
            export PATH="$pipx_path:$PATH"
            warn "Added $pipx_path to PATH for this shell."
            warn "Add it to your shell rc to make pipx-installed binaries persistent."
            ;;
    esac

    if ! command -v pipx >/dev/null 2>&1; then
        err "pipx installation appeared to succeed but pipx is still not on PATH."
        err "Inspect 'python3 -m pipx ensurepath' and re-run."
        exit 1
    fi
    ok "pipx installed: $(command -v pipx)"
}

ensure_pipx

# ── install vexis-agent ─────────────────────────────────────────────
if [[ "$DRY_RUN" -eq 1 ]]; then
    warn "[dry-run] would run: pipx install --force '$REPO'"
    warn "[dry-run] would print the post-install hints."
    exit 0
fi

info "Installing vexis-agent from $REPO"
# --force so a re-run upgrades cleanly without 'package already installed'.
pipx install --force "$REPO"

# ── post-install ────────────────────────────────────────────────────
ok "vexis-agent installed."
echo
cat <<EOF
Next steps:

  1. ${BOLD}vexis-agent setup${RESET}                 — interactive: writes
     ~/.vexis/config.yaml and ~/.vexis/.env, prompts for Telegram bot
     token and your numeric Telegram user ID, optionally installs
     the systemd user unit.

  2. ${BOLD}systemctl --user enable --now vexis-agent.service${RESET}
                                       — start the daemon (skip if
                                         you ran 'vexis-agent setup'
                                         and answered 'y' to the
                                         service-install prompt and
                                         followed its hint).

  3. ${BOLD}vexis-agent doctor${RESET}                — sanity-check the install.
EOF

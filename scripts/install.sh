#!/usr/bin/env bash
# vexis-agent installer — bash wrapper around scripts/install.py.
#
# Activates the conda env, then delegates to the Python installer
# which holds all the actual logic (so tests/test_install_script.py
# can exercise it without spawning subprocesses).
#
# Usage:
#   ./scripts/install.sh [--dry-run] [--workspace PATH] [--quiet]
#
# Idempotent — re-running mints no churn on existing symlinks or
# config files. See scripts/install.py docstring for the full
# contract.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Activate the conda env if it's not already active. Skip if
# CONDA_DEFAULT_ENV is already vexis-agent_env (re-running inside
# a shell that's already activated).
if [[ "${CONDA_DEFAULT_ENV:-}" != "vexis-agent_env" ]]; then
    if [[ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]]; then
        # shellcheck disable=SC1091
        source "$HOME/miniconda3/etc/profile.d/conda.sh"
        conda activate vexis-agent_env
    else
        echo "WARN: miniconda profile not found at \$HOME/miniconda3/etc/profile.d/conda.sh" >&2
        echo "WARN: continuing with the current Python — make sure 'vexis-agent_env' is active." >&2
    fi
fi

exec python "$REPO_ROOT/scripts/install.py" "$@"

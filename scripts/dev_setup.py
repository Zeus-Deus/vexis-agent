"""Vexis-Agent installer.

One-shot setup of the workspace + per-brain config files. Idempotent
— re-running mints no churn on existing symlinks or config files.

What it does:

1. **Repo AGENTS.md symlink.** Creates ``<repo>/AGENTS.md`` as a
   symlink to ``CLAUDE.md`` so opencode (and any future
   AGENTS.md-reading brain) finds the same content claude-code
   reads from ``CLAUDE.md``. Refuses to overwrite a real
   (non-symlink) ``AGENTS.md`` — if you maintain one by hand,
   delete it first or rename it. AGENTS.md is gitignored so the
   symlink is per-clone.

2. **Brain on PATH.** Reads ``brain.kind`` from
   ``~/.vexis/config.yaml`` (default ``claude-code``) and
   verifies the binary is reachable. If missing, prints the
   one-liner install command for that brain and exits non-zero.

3. **Workspace.** Reads workspace path from
   ``VEXIS_WORKSPACE`` (default ``~/vexis-workspace``), creates
   if missing, and symlinks ``<workspace>/AGENTS.md`` ↔
   ``<workspace>/CLAUDE.md``.

4. **MCP config.** Calls ``brain.write_mcp_config`` so each brain
   writes its native shape:
     - claude-code → ``<workspace>/.mcp.json``
     - opencode → ``<workspace>/opencode.json`` with the
       ``vexis-`` namespace prefix per Phase C Day 3's merge
       strategy (preserves user-owned non-prefixed entries).

The canonical MCP server set is read from the repo's
``.mcp.json`` (vexis ships exactly the entries it needs and uses
this file as the source of truth for both the developer-mode
``claude`` invocation in the repo dir AND the daemon's
production workspace).

Flags:

  --dry-run   Print what would happen, don't touch the filesystem.
              Used by ``tests/test_install_script.py`` to verify
              the planning logic without side effects.
  --workspace PATH
              Override ``VEXIS_WORKSPACE`` for this run only.
              Useful for tests + multi-workspace setups.
  --quiet     Suppress informational lines; only errors print.

Exit codes:

  0  success (or dry-run completed without surfacing a fatal
     planning failure).
  1  configuration / planning failure (brain not on PATH; the
     repo's CLAUDE.md is missing; etc.).

Design citation: ``.plans/brain-abstraction-research.md`` §5 Day 6
"AGENTS.md ↔ CLAUDE.md install hook".
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
from pathlib import Path

# Add the repo root to sys.path so we can import core.* even when
# the script is invoked directly as ``python scripts/dev_setup.py``.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from vexis_agent.core.brain.base import McpServerSpec  # noqa: E402

log = logging.getLogger("vexis.install")


# ──────────────────────────────────────────────────────────────────
# Helpers (testable)
# ──────────────────────────────────────────────────────────────────


def repo_root() -> Path:
    """Path to the vexis-agent repo this script ships from."""
    return _REPO_ROOT


def read_canonical_mcp_servers(repo: Path) -> list[McpServerSpec]:
    """Parse the repo's ``.mcp.json`` into a list of
    ``McpServerSpec`` so per-brain writers can serialise to their
    native shape. Empty file or missing file returns []."""
    path = repo / ".mcp.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    servers_block = data.get("mcpServers")
    if not isinstance(servers_block, dict):
        return []
    out: list[McpServerSpec] = []
    for name, entry in servers_block.items():
        if not isinstance(entry, dict):
            continue
        command = entry.get("command")
        if not isinstance(command, str) or not command:
            continue
        args = entry.get("args") or []
        if not isinstance(args, list):
            args = []
        env = entry.get("env") or {}
        if not isinstance(env, dict):
            env = {}
        out.append(
            McpServerSpec(
                name=str(name),
                command=command,
                args=[str(a) for a in args],
                env={str(k): str(v) for k, v in env.items()},
            )
        )
    return out


def resolve_brain_kind() -> str:
    """Read ``brain.kind`` from ``~/.vexis/config.yaml`` via the
    canonical helper. Defaults to ``"claude-code"``."""
    from vexis_agent.core.yaml_config import brain_kind
    return brain_kind()


def resolve_workspace(override: str | None) -> Path:
    """Resolve the workspace path. Override > ``VEXIS_WORKSPACE`` >
    ``~/vexis-workspace``."""
    if override:
        return Path(override).expanduser().resolve()
    raw = os.environ.get("VEXIS_WORKSPACE", "~/vexis-workspace").strip()
    return Path(raw).expanduser().resolve()


def brain_binary_for_kind(kind: str) -> str | None:
    """Map brain.kind to the binary name expected on PATH. ``null``
    has no binary; returns None."""
    return {"claude-code": "claude", "opencode": "opencode"}.get(kind)


def brain_install_hint(kind: str) -> str:
    """One-liner the user can run to install the missing binary."""
    if kind == "claude-code":
        return (
            "Install Claude Code: "
            "https://docs.anthropic.com/claude/claude-code"
        )
    if kind == "opencode":
        return (
            "Install OpenCode: "
            "curl -fsSL https://opencode.ai/install | bash"
        )
    return f"(no install hint registered for brain.kind={kind!r})"


# ──────────────────────────────────────────────────────────────────
# Symlink action — pure function returning a planned action
# ──────────────────────────────────────────────────────────────────


class SymlinkAction:
    """One ``ln -s <target> <link>`` planned action.

    Distinct states:
      - ``"create"`` — link doesn't exist; will be created.
      - ``"already_correct"`` — link exists and points at the
        right target. No-op.
      - ``"refuse_real_file"`` — a real (non-symlink) file or
        directory exists at the link path. We don't overwrite —
        the user's hand-maintained file wins.
      - ``"replace_wrong_symlink"`` — link exists but points
        elsewhere. Plan: unlink + recreate.
    """

    def __init__(self, link: Path, target: Path) -> None:
        self.link = link
        self.target = target
        self.state, self.detail = self._classify()

    def _classify(self) -> tuple[str, str]:
        if self.link.is_symlink():
            current = os.readlink(self.link)
            if Path(current) == self.target or self.link.resolve() == self.target.resolve():
                return ("already_correct", f"→ {current}")
            return ("replace_wrong_symlink", f"currently → {current}")
        if self.link.exists():
            return ("refuse_real_file", "real file exists, not overwriting")
        return ("create", f"will create → {self.target}")

    def apply(self) -> None:
        """Execute the action. No-op for ``already_correct`` and
        ``refuse_real_file`` (the latter logs a warning at the
        caller's level)."""
        if self.state in ("already_correct", "refuse_real_file"):
            return
        if self.state == "replace_wrong_symlink":
            self.link.unlink()
        # symlink_to writes target as the literal link content;
        # use a relative target when both files are siblings so
        # the symlink survives directory moves.
        if self.link.parent == self.target.parent:
            rel_target = self.target.name
        else:
            rel_target = str(self.target)
        self.link.symlink_to(rel_target)

    def describe(self) -> str:
        return (
            f"symlink {self.link.name} ({self.state}): {self.detail}"
        )


# ──────────────────────────────────────────────────────────────────
# Plan + apply
# ──────────────────────────────────────────────────────────────────


class InstallPlan:
    """Aggregated install plan. ``apply()`` executes side effects;
    ``describe()`` is what ``--dry-run`` prints."""

    def __init__(
        self,
        *,
        brain_kind: str,
        binary_present: bool,
        binary_hint: str,
        repo: Path,
        workspace: Path,
        repo_symlink: SymlinkAction | None,
        workspace_symlink: SymlinkAction | None,
        servers: list[McpServerSpec],
        brain_factory,  # callable -> Brain
    ) -> None:
        self.brain_kind = brain_kind
        self.binary_present = binary_present
        self.binary_hint = binary_hint
        self.repo = repo
        self.workspace = workspace
        self.repo_symlink = repo_symlink
        self.workspace_symlink = workspace_symlink
        self.servers = servers
        self._brain_factory = brain_factory

    def fatal_errors(self) -> list[str]:
        """Conditions that mean ``apply()`` shouldn't proceed."""
        errors: list[str] = []
        if not self.binary_present:
            binary = brain_binary_for_kind(self.brain_kind) or "<none>"
            errors.append(
                f"brain binary {binary!r} not on PATH for "
                f"brain.kind={self.brain_kind!r}. {self.binary_hint}"
            )
        return errors

    def describe(self) -> list[str]:
        out: list[str] = []
        out.append(f"brain.kind = {self.brain_kind}")
        out.append(
            f"binary on PATH = {self.binary_present} "
            f"({brain_binary_for_kind(self.brain_kind)})"
        )
        out.append(f"repo = {self.repo}")
        out.append(f"workspace = {self.workspace}")
        if self.repo_symlink:
            out.append(f"repo {self.repo_symlink.describe()}")
        if self.workspace_symlink:
            out.append(f"workspace {self.workspace_symlink.describe()}")
        out.append(
            f"MCP servers from .mcp.json: "
            f"{[s.name for s in self.servers] or '(none)'}"
        )
        out.append(f"  → will write via {self.brain_kind}.write_mcp_config")
        return out

    def apply(self) -> Path | None:
        """Execute the plan. Returns the path of the written MCP
        config, or None if no MCP write was attempted."""
        errs = self.fatal_errors()
        if errs:
            for e in errs:
                log.error(e)
            raise SystemExit(1)
        if self.repo_symlink:
            self.repo_symlink.apply()
        # Ensure the workspace exists before symlinking into it.
        self.workspace.mkdir(parents=True, exist_ok=True)
        # If the workspace doesn't have CLAUDE.md yet, copy the
        # repo's so opencode/claude-code have something to read.
        ws_claude = self.workspace / "CLAUDE.md"
        if not ws_claude.exists():
            repo_claude = self.repo / "CLAUDE.md"
            if repo_claude.is_file():
                shutil.copy2(repo_claude, ws_claude)
        if self.workspace_symlink:
            # Re-classify in case the workspace was just created.
            ws_action = SymlinkAction(
                self.workspace_symlink.link, self.workspace_symlink.target
            )
            ws_action.apply()
        # Pre-commit hook for dashboard builds (added 2026-05-08).
        # Idempotent: silently skips if the source script is missing
        # (older checkouts) or the .git/ dir doesn't exist (rare —
        # not in a git checkout).
        _install_dashboard_precommit_hook(self.repo)
        # Brain-side MCP config write.
        brain = self._brain_factory()
        try:
            return brain.write_mcp_config(self.servers)
        except NotImplementedError:
            log.warning(
                "%s.write_mcp_config not implemented; skipping MCP write",
                type(brain).__name__,
            )
            return None


def _install_dashboard_precommit_hook(repo: Path) -> None:
    """Install ``scripts/pre-commit-dashboard-build`` as the git
    pre-commit hook. Idempotent + chained — preserves any existing
    hook by chaining the dashboard build behind it.

    Why chained: if the user already has a pre-commit hook (e.g.
    a linter), overwriting it would silently lose that behavior.
    Chained pattern: existing hook becomes ``pre-commit.local``
    if present, and our installed hook calls it first.

    Silent fail-fast cases:
      - ``.git/hooks/`` doesn't exist (not a git checkout, e.g.
        running install in a tarball extraction)
      - source script is missing (older checkout, partial repo)
    """
    src = repo / "scripts" / "pre-commit-dashboard-build"
    hooks_dir = repo / ".git" / "hooks"
    if not src.is_file() or not hooks_dir.is_dir():
        return

    hook_path = hooks_dir / "pre-commit"
    desired_marker = "# vexis-dashboard-build hook"

    # If our hook is already the active one (matches our marker),
    # no-op — keeps re-runs of install.py from churning.
    if hook_path.is_file():
        try:
            content = hook_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            content = ""
        if desired_marker in content:
            return  # already installed, nothing to do

        # Existing non-vexis hook present — preserve it as
        # pre-commit.local so the chain can call it first.
        local_path = hooks_dir / "pre-commit.local"
        if not local_path.exists():
            try:
                shutil.move(str(hook_path), str(local_path))
                local_path.chmod(0o755)
            except OSError as exc:
                log.warning(
                    "could not preserve existing pre-commit hook: %s", exc,
                )
                return

    # Write the chained wrapper. Calls the local hook first (if it
    # exists) so existing user hooks keep firing, then runs the
    # dashboard rebuild.
    wrapper = (
        "#!/usr/bin/env bash\n"
        f"{desired_marker}\n"
        "# Chains the original pre-commit (preserved as\n"
        "# pre-commit.local during install) before the dashboard\n"
        "# rebuild. Edit scripts/pre-commit-dashboard-build to\n"
        "# change the rebuild behavior; re-run scripts/dev_setup.py\n"
        "# to refresh this wrapper.\n"
        "set -euo pipefail\n"
        'HOOKS_DIR="$(dirname "$0")"\n'
        'REPO_ROOT="$(git rev-parse --show-toplevel)"\n'
        'if [[ -x "$HOOKS_DIR/pre-commit.local" ]]; then\n'
        '    "$HOOKS_DIR/pre-commit.local" "$@"\n'
        "fi\n"
        f'exec "$REPO_ROOT/scripts/pre-commit-dashboard-build" "$@"\n'
    )
    try:
        hook_path.write_text(wrapper, encoding="utf-8")
        hook_path.chmod(0o755)
        log.info("installed git pre-commit hook for dashboard rebuild")
    except OSError as exc:
        log.warning("could not install pre-commit hook: %s", exc)


# ──────────────────────────────────────────────────────────────────
# Plan builder
# ──────────────────────────────────────────────────────────────────


def build_plan(
    *,
    repo: Path,
    workspace: Path,
    brain_kind: str,
    servers: list[McpServerSpec] | None = None,
) -> InstallPlan:
    """Construct the install plan without touching the filesystem
    beyond reading the repo's ``.mcp.json``. Side effects only fire
    when ``plan.apply()`` is called."""
    binary = brain_binary_for_kind(brain_kind)
    binary_present = binary is None or shutil.which(binary) is not None
    binary_hint = brain_install_hint(brain_kind)

    repo_claude = repo / "CLAUDE.md"
    repo_symlink = None
    if repo_claude.is_file():
        repo_symlink = SymlinkAction(repo / "AGENTS.md", repo_claude)

    workspace_claude = workspace / "CLAUDE.md"
    # The workspace symlink target is the workspace's own CLAUDE.md
    # (which apply() copies from the repo if missing). We use the
    # path even though it may not exist yet at plan-build time —
    # apply() re-classifies after the copy.
    workspace_symlink = SymlinkAction(
        workspace / "AGENTS.md", workspace_claude
    )

    if servers is None:
        servers = read_canonical_mcp_servers(repo)

    def _make_brain():
        # Defer brain construction until apply() so we don't
        # spawn workspace/state side effects during dry-run.
        from vexis_agent.core.brain.claude_code import ClaudeCodeBrain
        from vexis_agent.core.brain.opencode import OpenCodeBrain
        from vexis_agent.core.running_tasks import RunningTasks
        from vexis_agent.core.sessions import SessionStore

        sess = SessionStore(workspace / ".vexis-install-sessions.json")
        running = RunningTasks()
        if brain_kind == "opencode":
            return OpenCodeBrain(
                workspace=workspace, session=sess, running_tasks=running,
            )
        # claude-code is the default and the only other supported
        # kind for write_mcp_config (null has no MCP layer).
        return ClaudeCodeBrain(
            workspace=workspace, session=sess, running_tasks=running,
        )

    return InstallPlan(
        brain_kind=brain_kind,
        binary_present=binary_present,
        binary_hint=binary_hint,
        repo=repo,
        workspace=workspace,
        repo_symlink=repo_symlink,
        workspace_symlink=workspace_symlink,
        servers=servers,
        brain_factory=_make_brain,
    )


# ──────────────────────────────────────────────────────────────────
# CLI entry
# ──────────────────────────────────────────────────────────────────


def _setup_logging(quiet: bool) -> None:
    level = logging.WARNING if quiet else logging.INFO
    logging.basicConfig(
        level=level, format="%(levelname)s: %(message)s",
        stream=sys.stderr,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="vexis-install",
        description="Install vexis-agent: symlinks + brain config",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print plan without touching the filesystem",
    )
    p.add_argument(
        "--workspace", default=None,
        help="Override VEXIS_WORKSPACE for this run",
    )
    p.add_argument("--quiet", action="store_true", help="Suppress info logs")
    args = p.parse_args(argv)

    _setup_logging(args.quiet)

    plan = build_plan(
        repo=repo_root(),
        workspace=resolve_workspace(args.workspace),
        brain_kind=resolve_brain_kind(),
    )

    if args.dry_run:
        log.info("DRY RUN — no changes will be made:")
        for line in plan.describe():
            log.info("  %s", line)
        for err in plan.fatal_errors():
            log.error(err)
        return 1 if plan.fatal_errors() else 0

    log.info("Installing vexis-agent (brain.kind=%s)", plan.brain_kind)
    written = plan.apply()
    if written is not None:
        log.info("MCP config written: %s", written)
    log.info("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

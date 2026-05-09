# Security policy

## Reporting a vulnerability

Open a **private** report via GitHub's security advisory feature
(`https://github.com/Zeus-Deus/vexis-agent/security/advisories/new`)
or email the maintainer directly. Please don't open a public
issue for security findings.

The maintainer is single-user, so response time is best-effort.
A reasonable target is a first response within 7 days.

## What's in scope

- Anything that lets an unauthenticated party run code on the
  daemon's machine. The Telegram allowlist
  (`TELEGRAM_ALLOWED_USER_ID`) is the trust boundary; bypasses
  are critical.
- Dashboard token leakage via the web server.
- Path-traversal in MCP tool dispatch or workspace file ops.
- Privilege escalation via the systemd user unit's `ExecStart`.

## What's out of scope

- Supply-chain compromise of upstream pip dependencies. We pin
  ranges in `pyproject.toml` and rebuild on `vexis-agent update`,
  but we don't audit every transitive dep on every release.
- Issues that require an attacker who already has shell access as
  the user running the daemon (vexis is single-user; a local
  attacker has won regardless).
- Issues in the brain CLI (claude-code or opencode) itself —
  report those upstream.

## Hardening guidance for users

- Keep `~/.vexis/.env` at mode 0600. The setup wizard sets this
  automatically; `vexis-agent doctor` warns if it drifts.
- Don't share `~/.vexis/dashboard_token`. Rotation is manual:
  delete the file, restart the daemon, the dashboard mints a new
  one.
- Don't expose the dashboard port (default 8766) directly to the
  internet. Tailscale is the canonical fronting; the dashboard
  trusts whoever can reach the local socket.
- Keep the systemd user unit instead of running `vexis-agent run`
  manually — the unit's `Restart=on-failure` policy prevents
  silent crashes from masking issues.

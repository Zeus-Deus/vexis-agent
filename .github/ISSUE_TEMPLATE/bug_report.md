---
name: Bug report
about: Something broke. Help me reproduce it.
title: "[bug] "
labels: bug
---

**What broke**
A short, specific description.

**To reproduce**
1.
2.
3.

**Expected vs actual**
What you expected to happen, and what actually happened.

**Environment** (run `vexis-agent doctor` and paste the relevant lines)
- vexis-agent version: `vexis-agent --version`
- Distro:
- Wayland session: `echo $XDG_SESSION_TYPE` (must be `wayland`)
- Brain: claude-code / opencode / null

**Logs**
If the daemon was running, paste the relevant tail:

```
journalctl --user-unit vexis-agent.service -n 100 --no-pager
```

(Redact `TELEGRAM_BOT_TOKEN` and any user-message content you don't
want public.)

**Workaround / what you tried**
Optional but helpful.

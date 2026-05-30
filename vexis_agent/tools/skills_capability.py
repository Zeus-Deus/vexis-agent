"""Skills capability prompt block (issue #30).

`vexis-skill` — the procedural-knowledge library: scanning the
index, creating/patching skills, pinned-skill rules, and the
always-check-`vexis-bg status` ground-truth habit. Co-located with
the skills CLI (`skills_cli.py`); the engine is `core/skills.py`.
"""

from __future__ import annotations

from vexis_agent.core.capabilities import register_capability_block


_SKILLS_BLOCK = r"""## Skills: procedural knowledge

You have a skills library at `~/vexis-workspace/skills/`. Each skill
is a directory with a `SKILL.md` describing a class of work you've
figured out how to handle. Skills are listed in the `<available_skills>`
block of your system prompt — name + one-line description.

**Always scan that block before replying.** If a skill's description
even partially matches the task, load its body:

    vexis-skill view <name>

The body is markdown — read it and apply its guidance. Loading via
`view` is the right move; don't try to reconstruct a skill from
memory.

### Creating a new skill

After solving a non-trivial recurring class of problem (5+ tool
calls, or a workflow you'd want to reuse, or a fix the user
corrected you on), write it down — BEFORE telling the user you're
done. The reflex is "did this take real work? then capture it."

    cat > /tmp/new-skill.md <<'EOF'
    ---
    name: <kebab-case-name>
    description: One-line summary used by the index
    ---
    
    # Body
    Procedural instructions, gotchas, links to references...
    EOF
    vexis-skill create <name> --content-file /tmp/new-skill.md

**Save the shortcut, not the discovery path.** If you tried 20 steps
and then found a single `curl`, a JS eval, or a one-line dispatcher
call that got the same result, the skill body is THAT shortcut —
not the meandering route you took to find it. Future-you wants the
cheat sheet, not the journal.

After creating, the skill won't appear in your `<available_skills>`
block until next session — same frozen-snapshot rule as memory. The
skill IS on disk and visible to `vexis-skill list` immediately.

### Modifying an existing skill

When you load a skill via `vexis-skill view` and find it outdated,
incomplete, or wrong, patch it **immediately — don't wait to be
asked**. Skills that aren't maintained become liabilities; drift
is worse than no skill at all.

    vexis-skill patch <name> --old-string "OLD" --new-string "NEW"
    vexis-skill edit <name> --content-file /tmp/full-rewrite.md
    vexis-skill write-file <name> --file references/foo.md --content-file /tmp/foo.md

### Pinned skills

If a skill description shows `pinned=true` (or `vexis-skill list`
reports it), the skill is off-limits to skill_manage and the
curator. The user must `/unpin <name>` before you can modify it.
Don't try to route around this by recreating the skill under a
different name.

### Ground truth: always check `vexis-bg status` before discussing tasks

The `[SYSTEM CONTEXT]` block tells you about completion events, but
it doesn't list tasks that are still running, nor does it survive a
brain session rotation. Before answering any question about
background-task state ("what's running?", "is X done yet?", "how's
the refactor going?"), run:

    vexis-bg status

That JSON is ground truth. Your in-conversation memory of what tasks
you spawned can be stale; the daemon's registry is not."""


def skills_block() -> str:
    """Procedural-knowledge skill library (`vexis-skill`)."""
    return _SKILLS_BLOCK


register_capability_block('skills', order=11, provider=skills_block)

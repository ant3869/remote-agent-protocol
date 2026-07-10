---
name: claude-collaboration
description: Best practices for using Claude Code in team environments. Covers skill management, knowledge capture, version control, and collaborative workflows. Use when adding/editing project skills, deciding where a piece of knowledge should live, or multiple people/agents are working in the same repo.
---

# Claude Collaboration

## Instructions

### Step 1: Never duplicate skill content across tool directories

Projects often expose the same skill to several agent tools: `.claude/skills/`,
`.agents/skills/`, `.cline/skills/`, `.roo/skills/`, and sometimes a top-level
`skills/`. Pick **one** of these as the real, tracked source of truth (default:
`.claude/skills/`) and make the others directory-level symlinks to it:

```bash
ln -s "$(pwd)/.claude/skills" .agents/skills
ln -s "$(pwd)/.claude/skills" .cline/skills
ln -s "$(pwd)/.claude/skills" .roo/skills
```

Never symlink individual skill folders in both directions (e.g. `.claude/skills/x -> .agents/skills/x`
*and* `.agents/skills -> .claude/skills`) — that produces a circular reference
(ELOOP) that silently breaks every tool reading through it. If you inherit a
setup like this, `readlink -f` on the affected path returns nothing; that's
the tell. Fix it by deleting the per-item symlink and putting a real directory
back in the one location you chose as canonical.

### Step 2: Capture knowledge as you go, not at the end

When a session surfaces something non-obvious — a decision with a reason, a
gotcha, why a fix works the way it does — write it down immediately in
whichever of these fits:

- **Skill update**: if it changes how this kind of task should be done going
  forward, edit the relevant `SKILL.md` directly.
- **Memory** (if the assistant has a persistent memory system): user
  preferences, recurring feedback, or project facts that outlive this session.
- **A doc/comment in the repo**: implementation rationale that belongs next to
  the code it explains.

Don't let it live only in the conversation transcript — that's gone once the
session ends or gets summarized.

### Step 3: Treat skill edits like code

Skills are checked into git (when using the shared-directory pattern above,
edits to the canonical folder are visible through every symlink at once).
Commit skill changes with a message that explains *why* the skill changed, and
review skill diffs the same way you'd review a code change — a bad instruction
in a skill silently misguides every future session that triggers it.

### Step 4: Coordinate ownership on shared projects

When more than one person or agent edits the same project's skills or
`CLAUDE.md`:

- Treat `CLAUDE.md` edits as you would any shared config file — small, scoped
  changes, not wholesale rewrites, to avoid clobbering someone else's addition.
- If a skill is actively owned by one person's workflow (e.g. a per-workflow
  log, a personal shortcut), say so in the skill's description so others don't
  "fix" it into something that fits their own workflow instead.
- Prefer adding a new skill over overloading an existing one with an
  unrelated trigger condition — it's easier to review and easier to remove
  later if it turns out to be personal rather than shared.

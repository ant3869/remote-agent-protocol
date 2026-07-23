---
name: claude-skill-management
description: Expert guide for managing Claude Code global and project skills. Use when creating new skills, symlinking a skill into a project, updating existing skills, or organizing a centralized skill repository shared across projects.
---

# Claude Skill Management

## Instructions

### Step 1: Decide global vs. project-local

- **Global** (`~/.claude/skills/<name>/`): applies to every project, e.g. a
  personal debugging methodology or a communication-style preference. Create
  it once, reuse everywhere.
- **Project-local** (`<repo>/.claude/skills/<name>/`): specific to this
  codebase's conventions (a release process, a workflow log format tied to
  this repo's file types). Check it into the repo so teammates and other
  agents get it automatically.

Don't put project-specific instructions in a global skill (they won't apply
elsewhere and will confuse unrelated projects), and don't re-create a global
skill's content inside a project (it will drift out of sync with the original).

### Step 2: Share one skill across multiple agent tools without duplicating it

If a repo needs the same skill visible to Claude Code, Cline, Roo, and a
generic `.agents/` convention, keep exactly one real copy and symlink the
rest at the **directory level**:

```bash
# .claude/skills is the real, tracked directory
ln -s "$(pwd)/.claude/skills" .agents/skills
ln -s "$(pwd)/.claude/skills" .cline/skills
ln -s "$(pwd)/.claude/skills" .roo/skills
```

Verify there's no cycle after linking:

```bash
readlink -f .agents/skills/<any-skill-name>   # must print a real path, not empty
```

An empty result (or a "Permission denied"/"Function not implemented" surprise
on Windows) means two of these symlinks point at each other instead of at a
real directory — trace the chain with repeated `ls -la` on each hop and fix
the one that isn't pointing at the canonical real folder.

### Step 3: Centralizing skills across many personal projects

For skills reused across unrelated repos (not just multiple tool-dirs in one
repo), keep the real folders in one central location (e.g. `~/.agents/skills/`)
and symlink `~/.claude/skills/<name>` to each one individually. This is the
opposite direction from Step 2 (per-item symlinks are fine here because the
central location itself is never a symlink) — the danger is only when two
directories symlink to each other.

### Step 4: Creating a new skill

1. Pick a kebab-case name that describes the trigger, not the implementation
   (e.g. `systematic-debugging`, not `four-phase-method`).
2. Write `SKILL.md` with YAML frontmatter (`name`, `description`) where the
   description states both *what it does* and *when to use it* — the
   description is what a future session matches against, so vague
   descriptions mean the skill never triggers.
3. Put step-by-step instructions in the body. Keep it focused; move long
   reference material into a `references/` subfolder and point to it rather
   than inlining everything.
4. If the skill needs scripts or templates, use `scripts/` or `assets/`
   subfolders alongside `SKILL.md`.

### Step 5: Updating an existing skill

Edit the canonical copy directly (find it by resolving any symlink chain
first). Treat the edit like a code change: explain *why* in the commit
message, and re-read the description line afterward to confirm it still
matches what the skill actually does — a description that drifts from the
instructions means the skill stops triggering when it should, or triggers
when it shouldn't.

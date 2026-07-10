---
name: folder-organization
description: Best practices for organizing project folders, file naming conventions, and directory structure standards for research and development projects. Use when creating new top-level directories, naming new files, or a project's layout has become inconsistent.
---

# Folder Organization

## Instructions

### Step 1: Match structure to what already exists

Before creating a new directory, check whether an existing one already fits
(`data/`, `scripts/`, `tests/`, `docs/`). Adding a near-duplicate (`script/`
next to `scripts/`, `test/` next to `tests/`) fragments the project and makes
things harder to find. When in doubt, grep the repo for the convention already
in use rather than picking your own.

### Step 2: Naming conventions

- Use the casing convention the surrounding directory already uses
  (`snake_case` for Python packages, `kebab-case` for docs/skills, whichever
  the language/tool ecosystem expects) — don't mix conventions within one
  directory.
- Name files for what they contain, not for when they were made or who made
  them (avoid `notes_v2_final_FINAL.md`; prefer a stable name plus version
  control history).
- Prefix ordered artifacts (plans, migrations) with a zero-padded number
  (`001-`, `002-`) so they sort correctly and dependencies are visually
  obvious.

### Step 3: Where new files belong

- Runtime-generated state → a gitignored directory (e.g. `data/`), never
  mixed into source directories.
- One-off scratch output → a scratch/tmp location outside the tracked tree,
  or a clearly-named `output/` that's gitignored.
- Anything meant to be read by a future contributor → the existing docs/plans
  structure, not a loose file at the repo root.

### Step 4: Keep the root directory small

A repo root should be scannable at a glance — config files, a README, and a
handful of top-level source/doc directories. If the root accumulates loose
markdown files or scratch scripts, move them into a purpose-named directory
(`tasks/`, `notes/`) rather than letting them pile up ungrouped.

### Step 5: Watch for accidental duplication

Two directories with the same purpose (e.g. the same skill checked into both
`.claude/skills/` and `.agents/skills/` as separate real copies) will drift
out of sync. Prefer a single real location with symlinks for the others —
see [[claude-collaboration]] for the specific symlink pattern and its pitfalls.

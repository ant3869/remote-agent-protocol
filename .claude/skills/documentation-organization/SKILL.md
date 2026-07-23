---
name: documentation-organization
description: Organize research project documentation - structure working files, prepare sharing packages, maintain clean project layout. Use when a project's docs/notes are sprawling, before sharing a project externally, or when setting up a new project's documentation structure.
---

# Documentation Organization

## Instructions

### Step 1: Separate working files from finished documentation

Keep three tiers distinct:

- **Scratch/working** — in-progress notes, drafts, intermediate outputs.
  Fine to be messy; not meant to be read by anyone but the current work.
- **Project documentation** — README, architecture docs, changelogs. Kept
  current, checked into version control, meant for any future reader.
- **Archive** — completed plans, old audits, superseded designs. Kept for
  history but clearly marked as not-current (e.g. a dated subfolder), so
  no one mistakes it for active guidance.

Don't let scratch notes accumulate at the project root — move them to a
dedicated working directory (e.g. `tasks/`, `notes/`, or a scratchpad) so the
root stays navigable.

### Step 2: One canonical location per document type

Pick a single place for each kind of artifact and stick to it:

- Plans → one `plans/` directory with a status index (don't scatter loose
  plan files at the repo root).
- Architecture/design docs → `docs/`.
- Per-session or per-workflow logs → a consistent naming convention so they
  sort and grep predictably.

If a new doc doesn't fit an existing category, that's a signal to either
extend the convention deliberately or put it in scratch — not to invent a
one-off location.

### Step 3: Before sharing a project externally

1. Remove or redact anything containing credentials, internal URLs, or
   conversation excerpts with sensitive content.
2. Prune scratch/working files that only made sense mid-development.
3. Confirm the README and any setup docs reflect the *current* state, not an
   earlier iteration — stale setup instructions are worse than none.
4. Check that referenced paths and links still resolve after any restructuring
   done for the share.

### Step 4: Keep the structure honest over time

When a plan or doc is completed, update its status rather than leaving it to
look active indefinitely (e.g. a status table row, or moving it to an
archive folder). A documentation tree only stays trustworthy if "done" and
"still open" are easy to tell apart at a glance.

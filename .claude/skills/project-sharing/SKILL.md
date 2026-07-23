---
name: project-sharing
description: Prepare organized packages of project files for sharing at different levels - from summary PDFs to fully reproducible archives. Creates copies with cleaned notebooks, documentation, and appropriate file selection. After creating a sharing package, work continues in the main project directory. Use when the user asks to package, export, or share a project with someone else.
---

# Project Sharing

## Instructions

### Step 1: Establish the sharing level first

Ask (or infer from context) which of these the recipient needs — they have
very different scopes:

1. **Summary only** — a PDF/markdown write-up of results, no code.
2. **Read-through package** — cleaned code and docs, not meant to be re-run.
3. **Fully reproducible archive** — everything needed to install and re-run
   the work, including pinned dependencies and data (or instructions to
   fetch it).

Don't default to the largest scope "to be safe" — a reproducible archive
takes far more prep (dependency pinning, path scrubbing, data handling) than
a summary, and most sharing requests only need level 1 or 2.

### Step 2: Work in a copy, never the original

Create the sharing package in a separate directory (or a git worktree/branch)
and do all cleanup there. The main project directory keeps working normally
throughout — sharing prep should never leave the primary workspace in a
half-cleaned state.

### Step 3: Clean before packaging

- Strip notebook cell outputs that contain large binary blobs or sensitive
  data; keep outputs that are small and illustrative.
- Remove scratch/working files, credentials, `.env` files, and internal-only
  URLs.
- Update the README/setup doc to match what's actually included — a
  reproducible archive with a README describing the original (larger) repo
  will mislead the recipient.

### Step 4: File selection by scope

- Summary: the write-up plus key figures/tables only.
- Read-through: source code, README, docs — exclude `node_modules`,
  `.venv`, build artifacts, and other regenerable directories.
- Reproducible: the above plus a pinned dependency manifest
  (`requirements.txt`/`environment.yml`/lockfile), and either the data or
  clear instructions for obtaining it.

### Step 5: After packaging

State clearly that the shared package is a snapshot, not a live sync — future
changes to the main project won't propagate to it automatically. Continue all
further work in the main project directory, not inside the sharing copy.

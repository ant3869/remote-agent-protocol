---
name: documentation
description: Best practices for session documentation - incremental summaries, fix reports, and audit trails. Use when a task produces findings, a fix, or a decision that should survive past the current session.
---

# Documentation

## Instructions

### Step 1: Document incrementally, not at the end

Write down what changed and why as you go, not as a single retrospective
summary once everything is done. If a session gets interrupted or
context-compressed, incremental notes survive; a planned final write-up does
not.

### Step 2: Fix reports

When a bug fix or audit finding is resolved, capture:

- **What was broken** (symptom, one sentence).
- **Root cause** (not just the symptom — the actual mechanism).
- **The fix** (what changed, referencing file:line).
- **How it was verified** (test run, manual repro, etc.).

Skip fix reports for trivial one-line changes; write them for anything a
future reader would otherwise have to re-diagnose from the diff alone.

### Step 3: Audit trails

For multi-step or higher-risk work (data migrations, security fixes,
destructive operations), keep a running log of what was checked and what was
found at each step — not just the final state. This lets someone reconstruct
*how* a conclusion was reached, not just what the conclusion was, which
matters when the conclusion is later questioned.

### Step 4: Where documentation belongs

- Rationale that explains *why* code is the way it is → a comment next to the
  code, or the commit message.
- Decisions and facts that outlive this session but aren't code → the
  project's memory/notes system, not a throwaway file.
- Step-by-step audit trails for a specific piece of in-progress work →
  a scratch doc alongside the work, cleaned up or promoted once finished.

Don't let documentation only exist in the conversation transcript — anything
worth keeping should land in one of the above before the session ends.

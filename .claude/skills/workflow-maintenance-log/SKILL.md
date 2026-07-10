---
name: workflow-maintenance-log
description: Maintain per-workflow developer logs in Obsidian when working on Galaxy workflows. Use whenever creating, version-bumping, fixing, or updating an IWC/Galaxy `.ga` workflow or its `-tests.yml`. Logs detailed changes, test YAML edits, and `planemo` invocation history so the correct planemo command (full `planemo test` vs fast `workflow_test_on_invocation`) is obvious without guessing. Triggers on any session that edits `.ga`, edits `-tests.yml`, runs `planemo test`/`planemo workflow_test_on_invocation`, or prepares an IWC PR.
---

# Workflow Maintenance Log

## Instructions

### Step 1: One log per workflow

For each Galaxy `.ga` workflow under maintenance, keep a dedicated log note
in Obsidian (see [[obsidian]] for vault conventions) named after the
workflow. Create it the first time the workflow is touched; append to it on
every subsequent change rather than starting a new note.

### Step 2: What to log on every change

- **What changed** in the `.ga` file (tool version bump, step added/removed,
  parameter change) and why.
- **Test YAML edits** (`-tests.yml`) — what expectation changed and what
  triggered the need (new test data, tool output format change, etc).
- **The exact `planemo` command run** and its outcome (pass/fail, key error
  if it failed). This is the detail that matters most: a future session
  should never have to guess whether `planemo test` (full, slow) or
  `planemo workflow_test_on_invocation` (fast, invocation-only) is the right
  command for this workflow — the log should already say which one applies
  and why.

### Step 3: Choosing the planemo command

Default to `workflow_test_on_invocation` for a quick check during iterative
fixes (it re-runs against an existing Galaxy invocation rather than
re-executing the whole workflow). Use full `planemo test` before finalizing a
version bump or opening an IWC PR — it validates the workflow end-to-end
including tool resolution, which the fast path skips. If a past log entry for
this workflow already recorded which command was needed and why, follow that
precedent rather than re-deciding from scratch.

### Step 4: Before an IWC PR

Review the workflow's log for unresolved items (a fix attempted but not
confirmed passing, a test edit made without a corresponding `planemo test`
run) — don't open the PR with open threads still in the log. Summarize the
net change (version bump reason, what was fixed) in the PR description,
pulling from the log rather than reconstructing it from the diff.

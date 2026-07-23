---
name: token-efficiency
description: Token optimization best practices for cost-effective agent usage. Automatically applies efficient file reading, command execution, and output handling strategies. Includes model selection guidance. Use on every task by default -- especially large file reads, broad searches, or long-running sessions.
---

# Token Efficiency

## Instructions

### Step 1: Read narrowly

Don't read an entire large file when only a section is relevant — use
offset/limit on a Read call, or grep for the target first and read around
the matched line range. Re-reading a file you just edited is unnecessary
when the edit tool already confirms success.

### Step 2: Search narrowly

Prefer a targeted grep/search over a broad recursive read of a directory.
When a search tool supports filters (file glob, path scope), use them rather
than searching the whole repo and filtering mentally. For genuinely
open-ended exploration, batch a few searches instead of iterating one at a
time when they don't depend on each other's results.

### Step 3: Prefer commands over reading raw output

For structured data (counts, existence checks, diffs), a shell command that
returns just the answer (`wc -l`, `git diff --stat`, `test -f && echo yes`)
is cheaper than reading a full file to compute the same thing manually.
Pipe large command output through a filter (`head`, `grep`, `--oneline`)
instead of dumping everything into context.

### Step 4: Avoid redundant work

Don't re-derive facts already established earlier in the session, and don't
re-verify something a prior tool call already confirmed (e.g. a successful
Edit doesn't need a follow-up Read to check it worked). Delegate large,
self-contained research tasks to a subagent so only the summary — not the
raw exploration — lands in the main context.

### Step 5: Model selection

Match model to task weight where a choice is available: reserve the most
capable/expensive model for tasks that need deep reasoning or unfamiliar
problem-solving (learning a new codebase, architecture decisions); use a
lighter/faster model for routine development, mechanical edits, and
well-understood debugging. Don't default to the heaviest model for
every step of a long session.

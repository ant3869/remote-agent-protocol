---
name: command-discipline
description: Run shell commands bare -- no decorative echo headers ("=== X ==="), no echo-then-cmd chains, no trailing "echo done". Use the Bash tool's description field for any narration. Triggers any time a shell command is about to be issued.
---

# Command Discipline

## Instructions

### Step 1: Don't decorate commands with echo

Never wrap a command like this:

```bash
echo "=== Running tests ==="
npm test
echo "=== Done ==="
```

The Bash/PowerShell tool already has a `description` field for narration
("Run test suite") — use that instead of injecting extra `echo` lines into
the command itself. Decorative echoes add noise to the output, cost tokens,
and don't help the user any more than the description field already does.

### Step 2: Don't chain "echo intent, then act"

Avoid:

```bash
echo "Checking git status..." && git status
```

Just run `git status` with a description of "Check git status". The command
should be the smallest thing that accomplishes the goal — one command per
concern, not a narrated sequence.

### Step 3: Don't add a trailing confirmation echo

Avoid ending a command with `echo "done"` or `echo "Fixed!"` — the tool
result itself (exit code, stdout) is the confirmation. An extra echo is dead
weight that doesn't change what the user learns from the output.

### Step 4: When multiple real steps are genuinely needed

If a task truly requires several dependent shell steps, chain them with `&&`
(or `;` if later steps should run regardless) and let each command speak for
itself through its own output — don't narrate between them with echo. Put the
overall intent in the tool call's description field once, not per-step.

---
name: systematic-debugging
description: Structured 4-phase debugging methodology. Use when encountering any bug, test failure, unexpected behavior, or pipeline error -- before proposing fixes.
---

# Systematic Debugging

## Instructions

Work through these four phases in order. Do not jump to a fix before
completing Phase 1 and 2 — a fix based on a guessed cause is likely to either
miss the real bug or paper over a symptom while leaving the cause intact.

### Phase 1: Reproduce

Establish a reliable way to trigger the bug — a failing test, a specific
input, a specific sequence of actions. If it can't be reproduced
deterministically, narrow the conditions (logging, bisection, isolating
inputs) until it can. Don't attempt a fix for a bug you can't yet reliably
trigger; you won't be able to tell if the fix worked.

### Phase 2: Localize

Find exactly where the actual (not assumed) behavior diverges from the
expected behavior — the specific function, line, or state transition. Use
whatever gives ground truth: logs, a debugger, print statements, binary
search over commits (`git bisect`), or reading the code path the failing
input actually takes. Don't stop at "somewhere in module X" if you can
narrow it further; the fix quality depends on how precisely the cause is
located.

### Phase 3: Root-cause, not just symptom

Once localized, ask *why* the divergence happens — not just *what* line
produces the wrong value. A null check that avoids a crash is not the same
as understanding why the value was null in the first place. Fixing the
symptom without the cause tends to resurface the same bug elsewhere, or
mask a more serious underlying issue.

### Step 4: Fix and verify

Make the smallest change that addresses the root cause. Then:

1. Confirm the original reproduction case from Phase 1 now passes.
2. Check for related cases that would hit the same root cause (same
   function called from elsewhere, similar patterns nearby) — a root cause
   is often not unique to the one call site that surfaced it.
3. Run the broader test suite, not just the one test that was failing, to
   catch regressions the fix might introduce.

Do not report the bug as fixed until reproduction, verification, and a check
for related cases are all done.

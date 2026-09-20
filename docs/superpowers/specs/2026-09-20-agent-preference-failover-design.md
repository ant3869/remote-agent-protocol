# Design: Learned Agent Preference and Bounded Auto-Failover

**Product:** Remote Agent Protocol
**Milestone:** 4 - Learned selection history and safe automatic reassignment
**Status:** Proposed for user review
**Date:** 2026-09-20

## Product outcome

When Ant gives Butler a task without naming an agent, Butler already picks a qualified candidate from live control-plane evidence (Milestone 2, Task 5). This milestone adds memory to that choice: RAP remembers, per capability, which agents have actually succeeded or failed recently, and folds that durable history into the same ranking the selector already computes from live evidence. When the chosen agent fails in a way that does not change the requested outcome or its safety scope, RAP tries the next verified candidate on its own, without asking, exactly as Milestone 2's `classify_recovery` already decides is safe to do -- the only change is that RAP now *acts* on that decision automatically instead of stopping to narrate it, and only for work classified as safe to retry blind.

Failure never means silence. Whichever agent finally succeeds explains what happened in its own voice; if every verified candidate is exhausted, Butler explains the chain of attempts and asks rather than guessing further.

## Relationship to Milestone 2

Milestone 2 built every load-bearing piece this milestone needs: `AgentSelector` ranks eligible candidates from live control-plane evidence and recorded recent successes; `classify_recovery` already classifies a failure as reassignment-eligible only when "the outcome and safety scope remain unchanged," and refuses to invent a substitute -- a reassignment target must already be a verified candidate. Today, `service.py`'s failure path calls `classify_recovery`, gets back a `RecoveryDecision` naming a verified candidate, and only *announces* it (`BUTLER_INTERVENTION_STARTED`, with `recovery_kind`/`candidate_agent_id` on the event). This milestone does not replace any of that machinery -- it adds a durable, decaying outcome history that feeds the existing ranking, and it closes the gap between "Butler decided reassignment is safe" and "Butler actually did it."

This milestone does not depend on Milestone 3 (agent-to-agent collaboration) and can be sequenced before or after it.

## Goals

1. Persist a decaying, per-(agent, capability) outcome history -- not just the in-memory "recent successes" `AgentSelector` already tracks -- so ranking reflects real history across RAP restarts.
2. When a failure is reassignment-eligible **and** the originating task is classified safe to retry without side effects, dispatch the next verified candidate automatically, under the same task ID and a new attempt ID, instead of only narrating the decision.
3. Bound automatic cascading to a small fixed number of attempts per task before Butler stops and asks.
4. Carry forward, to the next attempt, whatever the failed attempt is known to have already completed, so a later agent does not blindly repeat a step that should not be repeated.
5. Record each failure's reason code against the (agent, capability) pair as durable, provenanced memory, so Ant can inspect why a particular agent was chosen or skipped.
6. Never let stale remembered access substitute for a live check -- remembered "agent X has access to Y" is a reason to prefer probing X first, never a fact used to skip verification.

## Non-goals

- Automatic silent cascading for destructive, externally-visible, or otherwise confirmation-gated work. That work still stops and asks, exactly as Milestone 2 already requires for anything outside "outcome and safety scope unchanged."
- A new, independent memory store. This extends the existing `conversation_hub.memory` scopes (`project`/`shared`) and `AgentSelector`'s existing ranking, not a parallel system.
- A hand-curated, static preference list per capability. Ranking stays evidence-computed and self-correcting; only the historical-outcome *input* to that ranking becomes durable.
- Agent-to-agent negotiation about who should take a task. Selection remains Butler's call, based on evidence, per Milestone 2.
- Solving the general "was partial work already done externally" problem. This milestone only carries forward what RAP itself observed the failed attempt complete, not out-of-band side effects it has no visibility into.

## Product principles

### Learn from outcomes, don't freeze on one

A single transient failure (a rate-limit blip) should not permanently demote an agent that is otherwise the best fit. Outcome history decays over time and is one input to ranking alongside live control-plane evidence, load, and access -- never the sole or overriding factor.

### Safety scope decides whether RAP acts alone

The dividing line is the same one Milestone 2 already drew: a failure is reassignment-eligible only when the outcome and safety scope are unchanged by picking a different, already-qualified agent. This milestone adds a second, task-level gate on top of that: even a reassignment-eligible failure only cascades automatically when the *task itself* was classified safe to retry without asking (idempotent, read-only, or otherwise side-effect-free). Anything that would normally require confirmation before dispatch still requires confirmation before a silent retry.

### Never repeat a step blindly

If the failed attempt reported completing part of a multi-step task, that fact travels with the next attempt so the next agent knows what not to redo. RAP does not guess at partial completion it did not observe.

### Remembered access is a hint to check first, not a fact

Consistent with Milestone 2's existing rule that stale access is "a reason to probe an agent, not a fresh claim," remembered capability/access history biases probe order and candidate ranking, but every dispatch still goes through the same live verification Milestone 1's control plane already performs.

### The chain is visible, not hidden

Every automatic reassignment is a recorded event (reusing `BUTLER_INTERVENTION_STARTED`/`RESOLVED`) with its reason code and candidate, exactly like Milestone 2's transcript already shows. Ant can always see why a task moved from one agent to another, and the same event log is what future selection reads back as history.

## Architecture

No new top-level subsystem. Three existing components change:

### AgentSelector (extended)

`SelectionCandidate` already carries `recent_successes: Mapping[str, Evidence]`, populated live for the current process lifetime. This milestone adds a durable counterpart sourced from `conversation_hub.memory` at selection time: a decaying outcome score per (agent_id, capability) pair, computed from promoted `project`/`shared`-scope memories of prior successes and reason-coded failures. `AgentSelector._rank` gains this as an additional, lower-priority ranking key, after live load and live recent-success evidence -- durable history breaks ties and informs a cold start, it does not override fresh live evidence.

### TaskRetrySafety (new, thin)

A single classification function, reusing whatever confirmation/risk gate `intent_router` already computes when it decides a task needs explicit confirmation before dispatch (Milestone 2, Task 8's ledger: `intent_router` owns "confirmation-need risk gating"). If a task was not flagged as needing confirmation to dispatch in the first place, it is eligible for silent auto-cascade on reassignment-eligible failure. If it was flagged, a reassignment-eligible failure still stops and asks -- this milestone adds no new judgment about what counts as destructive, it only reuses the existing one.

### Bounded auto-reassignment loop (service.py)

Where `_dispatch`'s failure path currently calls `classify_recovery` and stops at emitting `BUTLER_INTERVENTION_STARTED`, it gains: if `recovery.kind == RECOVERY_REASSIGN` and `TaskRetrySafety` allows silent retry for this task, and the attempt count for this `task_id` is below the configured maximum, dispatch `recovery.candidate_agent_id` under the same `task_id` with a new `attempt_id`, carrying forward any completed-subtask markers the failed attempt reported. If the attempt count is exhausted, or the task is not retry-safe, or no candidate exists, behavior is unchanged from Milestone 2: narrate and stop.

## Core data model additions

```yaml
capability_outcome:
  agent_id: hermes
  capability: calendar_read
  outcome: failure                 # success | failure
  reason_code: access_lost         # matches classify_recovery's failure_kind vocabulary
  task_id: task_123
  attempt_id: attempt_456
  observed_at: timestamp
  decay_half_life_days: 14         # configurable; older outcomes count for less

task_retry_state:
  task_id: task_123
  attempt_count: 2
  max_auto_attempts: 2
  retry_safe: true                 # from TaskRetrySafety at initial dispatch
  completed_subtasks: ["drafted_reply_1", "drafted_reply_2"]
```

`capability_outcome` records are promoted into existing `project`/`shared` memory scope with the same provenance/confidence fields Milestone 2 already defines (`source_turn_ids`, `confidence`, `observed_at`, `supersedes`) -- this is additive data on the existing model, not a new scope.

## Turn and task lifecycle addition

Extends Milestone 2's "Failure and recovery" lifecycle. After Butler would normally speak an intervention:

1. `classify_recovery` returns `RECOVERY_REASSIGN` with a verified candidate, as today.
2. `TaskRetrySafety` checks whether the *original* task was dispatched without a confirmation requirement.
3. If retry-safe and `attempt_count < max_auto_attempts`: RAP dispatches the candidate under the same task, carrying forward known completed-subtask markers; the `BUTLER_INTERVENTION_STARTED`/`RESOLVED` pair is still recorded, but Butler does not necessarily speak over the in-flight retry unless it also fails.
4. If not retry-safe, or attempts are exhausted, or no candidate exists: unchanged Milestone 2 behavior -- Butler explains and asks.
5. On the retry's eventual success or failure, a `capability_outcome` record is promoted for both the failed and (if applicable) succeeded agent.

## Privacy and safety

- Capability outcome history is a record of what RAP itself observed (a dispatch succeeded, or failed with a specific reason code) -- it never stores raw provider error bodies, tokens, or credentials, consistent with Milestone 2's existing privacy rules.
- The safety gate for auto-cascade is inherited from the existing confirmation/risk classification, not reimplemented, so this milestone cannot widen what counts as "safe to retry without asking" beyond what Milestone 2 already trusts to dispatch without confirmation.
- Stale capability-outcome history is never used as access evidence on its own; it only reorders which verified candidate is probed or preferred first.

## Testing strategy

- Unit: decaying outcome scoring folded into `AgentSelector._rank`, with fresh live evidence still dominant over older durable history.
- Unit: `TaskRetrySafety` correctly refuses silent cascade for any task that was flagged confirmation-required at dispatch.
- Unit: bounded attempt counting stops auto-cascade at `max_auto_attempts` and falls back to asking.
- Integration, mirroring the motivating examples: a coding task rate-limited on `claude-code` silently completes on `codex`; a calendar-read task with `hermes` losing access silently completes on `openclaw`, and both outcomes are durably remembered and change the next unnamed request's ranking; a destructive task (e.g., "send this email") that fails never silently cascades and instead asks.
- Integration: RAP restart preserves capability-outcome history and it measurably affects post-restart selection.

## Acceptance scenarios

1. An unnamed coding request goes to the historically-best agent for that capability; when it hits a rate limit, RAP silently retries the next verified candidate under the same task and the working agent reports the actual result.
2. An unnamed calendar-read request fails on an agent that has lost access; RAP records the access loss, silently retries a verified alternative, and a later unnamed calendar-read request prefers the agent that last succeeded.
3. A destructive or confirmation-gated task's reassignment-eligible failure does not auto-cascade; Butler explains and asks, exactly as it does today.
4. Automatic cascading stops after the configured attempt limit and asks, rather than looping through every configured agent.
5. A restart of RAP preserves capability-outcome history and it is visible in why a later selection was made.

## Delivery boundaries

This milestone requires no new adapter capability and touches no harness-specific code -- it is entirely within `conversation_hub` (`selection.py`, `results.py`/`service.py`, `memory.py`) plus a thin reuse of `intent_router`'s existing risk classification. It should not begin until Milestone 2 (Tasks 9-11) is complete, since it depends on the transcript/control surface and migration work to expose and persist this history correctly, but it does not depend on Milestone 3.

## Implementation guidance

Follow the same SDD process as Milestone 2: a brief per subsystem boundary (selector extension, retry-safety reuse, bounded auto-reassignment loop, memory schema addition), each independently reviewed before implementation, with the same standard of re-verifying reviewer findings against source rather than trusting reports, and re-running any "confirmed pre-existing failure" claim against an isolated worktree.

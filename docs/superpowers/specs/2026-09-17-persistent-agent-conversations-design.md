# Design: Persistent Agent Conversations and Butler Mediation

**Product:** Remote Agent Protocol  
**Milestone:** 2 — Persistent conversations, memory, and mediated handoffs  
**Status:** Proposed for user review  
**Date:** 2026-09-17

## Product outcome

Remote Agent Protocol becomes one continuous conversational room where Ant can speak naturally without remembering which harness has which access. Butler is the default front door and capability broker. When a request does not name an agent, Butler determines which available agent is best suited, hands off the work, and silently supervises it. When Ant names an agent, RAP opens that agent's durable conversation directly.

The agent that performs successful work reports the result in its own identity and voice. Butler does not compress or paraphrase a successful result. Butler intervenes when routing is ambiguous, an agent becomes unavailable, work fails, access is lost, or reassignment is needed.

From Ant's perspective this is one conversation. Internally, RAP maintains durable, scoped channels so each agent can remember the right context without leaking unrelated history or requiring Ant to manage context windows, session IDs, compaction, or restarts.

This milestone delivers one complete journey:

> Open RAP, ask naturally for an outcome, let Butler choose a capable agent, continue the conversation with that agent without repeating context, receive the complete result from the agent itself, then return to Butler or another agent by name—across application restarts and physical harness-session rotation.

## Relationship to Milestone 1

Milestone 1 introduced the live agent control plane: an evidence-backed registry, direct adapters for the five configured harnesses, concurrent probes, stale persistence, RAP-owned job controls, and status projections.

Milestone 2 consumes that control plane rather than replacing it. Agent selection, health claims, access claims, task ownership, failure recovery, and UI status must be based on structured control-plane evidence. The existing `AgentBridge` remains the execution boundary for RAP-launched work.

## Goals

1. Make Butler the default intelligent front door for unnamed work.
2. Let Ant address any configured agent directly at any time.
3. Preserve one durable logical conversation per configured agent, plus a coordinator context, across restarts.
4. Keep follow-up turns with the relevant agent without requiring its name on every message.
5. Hide physical harness-session creation, resumption, compaction, and rotation from Ant.
6. Give each turn bounded context assembled from recent conversation, summaries, active work, scoped memories, and live control-plane state.
7. Let the working agent deliver successful, partial, or blocked results in its own identity and configured harness voice.
8. Let Butler supervise failures, silence, invalid output, lost access, and reassignment without speaking over successful agents.
9. Keep responses natural, direct, brief, and complete, with full readable results available separately from voice-sized narration.
10. Provide transparent history, provenance, correction, forgetting, and session-reset controls.

## Non-goals

- Autonomous agent-to-agent conversations.
- Multi-agent collaboration rooms or roundtables.
- Heartbeat-triggered cross-agent consultation.
- Consensus, voting, or conflict resolution among agents.
- A redesign of the existing avatar or animation system.
- Replacing the Milestone 1 control plane, `AgentBridge`, Pipecat, STT, or TTS.
- A universal memory system shared indiscriminately across every agent.
- Resuming arbitrary terminal sessions that RAP did not create and bind.
- Exposing context-window sizes, compaction thresholds, or harness session IDs as normal user concerns.

The deferred collaboration behaviors belong to Milestone 3: bounded collaboration rooms and task-scoped autonomous consultation.

## Product principles

### One room, scoped minds

The primary UI remains one chronological conversation. Internal channels are context and ownership boundaries, not separate chat applications Ant must manage. The transcript may be filtered by speaker, agent, task, or channel, but it is unified by default.

### Butler brokers; agents perform

Butler interprets unnamed intent, chooses a qualified agent, announces a concise handoff when useful, and monitors the outcome. The assigned agent owns the work and its successful result.

### Direct address always works

Naming an agent transfers the conversational floor to that agent for the current subject. Direct address bypasses agent selection but not safety, availability checks, or Butler's silent supervision.

### Memory is scoped and sourced

RAP shares only durable information that is useful beyond one exchange. Every promoted memory has a scope, provenance, and timestamp. Raw tool chatter and temporary task details do not become global memory.

### Logical continuity, replaceable sessions

The stable object is the RAP conversation channel, not a particular subprocess or vendor session. RAP may rotate a physical session while preserving the same logical conversation through bounded rehydration.

### Complete does not mean long-winded

Spoken responses lead with the outcome and include every important result, decision, warning, failure, and next step. Routine tool chatter stays out of speech. The UI retains the full result.

## User experience

### Unnamed request

Ant says, “Check whether I have any important emails.” Butler derives the required capability, evaluates current candidates, and briefly says which agent is taking it and why when that information is useful. The selected agent does the work. On success, that agent speaks the result directly.

If the selected agent has lost email access, Butler explains the problem, chooses another verified candidate when allowed, and records the reassignment. If no capable candidate is known, Butler asks one short clarification or explains what access is missing.

### Direct request

Ant says, “OpenClaw, check my important emails.” RAP transfers the floor to Jax's durable channel and dispatches through OpenClaw. Jax answers in Jax's voice. Relevant follow-ups such as “What about school messages?” stay with Jax without repeating the name.

### Return to Butler

Ant says “Butler” or begins a new unrelated unnamed request. RAP returns the floor to Butler. A simple acknowledgment such as “thanks” remains addressed to the last speaker and does not create new work.

### Long result

The working agent gives a natural spoken answer with all materially important findings and displays the complete detailed result in the transcript. When the full result is too long for comfortable speech, the agent offers to continue reading rather than silently deleting details.

### Restart

After RAP restarts, the unified transcript, agent channels, summaries, promoted memories, task references, and last known floor are restored. Restored live status is stale until Milestone 1 re-verifies it. Ant can continue the subject naturally without managing a session.

## Architecture

Add an `AgentConversationHub` beside the existing `AgentControlPlane` and above harness-specific session mechanics.

### AgentConversationHub

The application service for conversational continuity. It owns logical channels, routes turns through the floor manager, requests bounded context, binds agent work to dedicated sessions, persists normalized conversation state, and publishes conversation events.

Both full voice mode and Brain mode use the same hub interface. Neither mode may implement separate routing or memory semantics.

### AgentChannel

A durable logical conversation for one configured agent. It contains:

- a stable channel ID and agent ID;
- normalized user and agent turns;
- a running compacted summary;
- references to active and recent tasks;
- agent-local memories;
- its current `SessionBinding`;
- chapter and archive metadata;
- communication-contract version;
- timestamps and schema version.

Butler has a coordinator context for intent, handoff, and supervision. Harness agents each have their own channel. These scopes project into one chronological transcript.

### FloorManager

Tracks three independent concepts:

- **front door:** Butler, the default interpreter for unnamed new work;
- **conversation floor:** the speaker expected to receive the next relevant turn;
- **task owner:** the agent responsible for a particular active task.

Keeping these separate prevents an asynchronous task completion from stealing the floor, and prevents Butler supervision from impersonating the working agent.

### SessionBinding

Maps a logical channel to a RAP-managed physical harness session. A binding records the adapter, native session identifier when supported, creation and last-use times, resume capability, rotation reason, and validation evidence.

An adapter may use one of two strategies:

1. **Native resume:** resume a dedicated session previously created for this RAP channel, after validating that the adapter and session identity still match.
2. **Rehydrated session:** start a clean physical session and inject context assembled from the logical channel.

RAP must never resume a terminal or harness session merely because it is recent. A resumable session must have an explicit persisted RAP binding to the same agent and channel.

### ContextAssembler

Builds a bounded, deterministic context package for a turn. It draws from:

1. the current user utterance;
2. recent channel turns, verbatim;
3. the channel's running summary;
4. active task state and relevant artifacts;
5. agent-local memory;
6. curated shared and project memory;
7. current control-plane observations relevant to the request;
8. the versioned communication contract.

The assembler applies per-section budgets and deterministic ordering. It excludes unrelated channels, superseded memories, raw hidden reasoning, secrets not required by the task, routine heartbeat events, and raw tool output unless explicitly requested.

### CommunicationContract

A small, versioned hidden instruction injected into RAP-managed turns:

> Speak to Ant like a trusted teammate: natural, direct, and brief, but complete. Lead with the outcome. Include every important result, decision, warning, failure, and next step. Omit internal tool chatter unless asked. Keep operational progress separate from the final answer. If blocked, ask one clear question. Never impersonate Butler.

The contract is application policy, not transcript content. It must not be persisted as a user message or promoted into memory. The contract version is recorded on each dispatched turn for reproducibility.

### ResultPresenter

Separates the canonical full result from its spoken presentation while preserving authorship.

The canonical full result is the agent's accepted, safety-normalized response—not raw stdout, hidden reasoning, secret material, or routine tool logs. It is stored without semantic rewriting, together with its agent, task, channel, and provenance.

The task-owning agent also authors `spoken_text` under the communication contract. If an adapter initially returns only machine-oriented or raw task output, RAP asks that same bound agent for one final presentation turn while retaining the detailed output as a referenced artifact. `ResultPresenter` may remove formatting and split the agent-authored speech at semantic boundaries, but it may not independently summarize, discard material findings, or use Butler or another model to impersonate the agent.

### Durable store

Persist channels, turns, summaries, memories, task references, bindings, and floor state using the project's existing atomic-write conventions. Storage must be schema-versioned and recover safely from a partially written update. Large artifacts remain external files referenced by stable metadata rather than copied into every context package.

## Core data model

Exact Python names may follow current project conventions, but the persisted model must express the following information.

```yaml
channel:
  channel_id: agent:openclaw
  agent_id: openclaw
  chapter_id: 3
  summary: "Ant is reviewing school-related email..."
  communication_contract_version: 1
  active_task_ids: [task_123]
  session_binding_id: binding_456
  created_at: timestamp
  updated_at: timestamp

turn:
  turn_id: turn_789
  channel_id: agent:openclaw
  task_id: task_123
  speaker_id: openclaw
  speaker_role: agent
  full_text: "..."
  spoken_text: "..."
  result_kind: success       # progress | success | partial | blocked | failure
  contract_version: 1
  created_at: timestamp

memory:
  memory_id: memory_321
  scope: project             # channel | shared | task | project
  subject: "email access"
  value: "OpenClaw successfully accessed primary inbox"
  source_turn_ids: [turn_789]
  confidence: verified       # verified | user_stated | inferred
  observed_at: timestamp
  supersedes: null
  status: active             # active | superseded | forgotten
```

### Memory scopes

- **Channel:** preferences, facts, and context useful primarily to one agent conversation.
- **Shared:** stable user preferences or cross-cutting facts useful to multiple agents.
- **Task:** decisions, constraints, and artifacts needed by one active or resumable task.
- **Project:** durable facts and decisions tied to a named project.

Promotion into shared or project memory requires a stable user statement, an explicit project decision, or verified task evidence likely to matter again. Model speculation cannot be promoted as fact. Inferred candidates remain labeled and are not used as access evidence.

Corrections create a new memory that supersedes the older value while retaining provenance. Selective forgetting marks the chosen memory unavailable to future context assembly; transcript deletion is a separate explicit action.

## Routing and floor rules

Routing is deterministic wherever the language supplies a clear agent name, task reference, acknowledgment, or Butler invocation. A model may interpret intent and capability needs, but it may not invent agent health, access, or task ownership.

1. A new unnamed request enters through Butler.
2. An explicit configured agent name transfers the floor to that agent for the relevant subject.
3. An explicit “Butler” transfers the floor to Butler immediately.
4. A relevant follow-up goes to the current task owner or floor holder without requiring the name again.
5. A simple acknowledgment goes to the last actual speaker.
6. An unrelated unnamed request returns to Butler.
7. When multiple active tasks make a reference ambiguous, Butler asks one short clarification.
8. A completion event does not automatically change the conversational floor; it is announced by the task owner and then the prior floor remains unless the user engages that result.
9. A named but unavailable agent triggers a Butler explanation and an offer of verified alternatives; RAP does not silently impersonate the named agent.
10. Switching to another agent transfers only the relevant task context, explicit user-selected context, and eligible shared/project memory—not the entire prior channel.

### Agent selection for unnamed work

Butler converts the request into structured capability and access requirements. Candidate selection then uses control-plane evidence in this order:

1. eliminate agents that lack a required capability or verified/last-known access;
2. eliminate agents known to be unavailable or unsafe for new work;
3. prefer fresh evidence over stale evidence;
4. consider current load and task conflicts;
5. consider recent success for the same capability;
6. apply the configured default only when candidates remain otherwise equivalent.

Stale access may be used as a reason to probe an agent, not as a fresh claim. If no qualified candidate can be verified, Butler says what is unknown or missing. Selection decisions store the evidence and policy reasons used.

## Turn and task lifecycle

### Butler-mediated task

1. Accept the user turn into the unified transcript.
2. Resolve it as unnamed new work.
3. Butler derives typed requirements.
4. The control plane refreshes evidence when required by freshness policy.
5. Butler selects a candidate and records the selection rationale.
6. Butler may speak one brief handoff: agent and reason.
7. The hub binds the task to the agent channel and physical session.
8. The context assembler creates the bounded package.
9. `AgentBridge` dispatches the task and the task owner emits meaningful progress only.
10. The agent emits a success, partial, or blocked result in its own identity.
11. On failure conditions, Butler takes the recovery path.

### Direct task

The same lifecycle applies except that explicit naming supplies the candidate. RAP still verifies availability and required safety constraints. Butler stays silent unless intervention is needed.

### Progress

Agents may speak meaningful milestones, decisions needed from Ant, and blockers. Routine liveness heartbeats remain visual status only. Progress events do not become long-term memory unless they contain a durable decision or artifact reference.

### Success

The task owner provides the actual result. Butler does not rewrite, shorten, repeat, or take credit for it. The complete result is retained in text even when the spoken delivery is segmented.

### Partial or blocked

The task owner explains what was completed, what remains, why it stopped, and the one next decision needed. Butler intervenes only if routing, recovery, or reassignment is required.

### Failure and recovery

Butler becomes the speaker when any of these occur:

- dispatch or launch failure;
- provider, authentication, permission, or quota failure;
- verified loss of required access;
- silence past the adapter-specific deadline;
- malformed or invalid final output;
- unsafe or unsupported requested control;
- agent disappearance during active work.

Butler states the failure plainly, preserves any partial result, and chooses among retry, fallback, reassignment, clarification, or manual action. Automatic reassignment is allowed only when the requested outcome and safety scope remain unchanged and another candidate is verified. Otherwise Butler asks before proceeding.

Each attempt retains its own task/attempt identifier and ordered lifecycle events. A reassignment never overwrites the failed attempt.

## Session continuity and rotation

### Dedicated sessions

Every adapter declares whether it supports validated native resume. When it does, RAP creates or adopts only a session explicitly dedicated to the matching logical channel. The binding is persisted before reuse.

### Rotation triggers

RAP may rotate a physical session when:

- the native session is missing, invalid, or no longer resumable;
- the adapter reports a context or session limit;
- the session is corrupted or repeatedly fails;
- a user requests “session reset”;
- policy requires a clean environment.

Rotation does not create a new logical channel or erase history. The new session receives the communication contract and a context package from the channel.

### Compaction

Compaction updates the running summary only after preserving the underlying normalized turns according to retention policy. Summaries must distinguish user statements, verified observations, decisions, open questions, and unresolved tasks. A summary cannot upgrade an inference into a fact.

Compaction and rotation are recorded as quiet operational events. They appear in diagnostics or history details, not as conversational interruptions unless recovery fails.

## Conversation controls

The UI and typed/voice control surface support:

- view channel history;
- search conversation history;
- filter the unified transcript by speaker, agent, task, or channel;
- archive an inactive channel;
- start a **new chapter**, preserving durable memory while closing the current conversational topic;
- perform a **session reset**, replacing only the physical harness session;
- selectively forget a memory;
- inspect why a memory was used and where it came from.

Archive, new chapter, and session reset are distinct operations. None silently deletes the transcript or long-term memory.

## Events and projections

Add normalized events for:

- floor changed;
- channel created, restored, archived, or reopened;
- session bound, resumed, rotated, or reset;
- context assembled;
- memory proposed, promoted, superseded, or forgotten;
- Butler handoff started and completed;
- task ownership assigned or reassigned;
- result available;
- Butler intervention started and resolved.

Events exposed to the UI must be allowlisted and omit hidden prompts, secrets, raw reasoning, and unnecessary context contents.

The transcript retains the true speaker, harness/persona voice, channel, task, attempt, result kind, timestamps, playback state, full text, and spoken text. Operational entries such as session rotation are visually distinct from speech.

The existing avatar and current `focused` working visual remain unchanged in this milestone.

## Voice behavior

- Butler uses the active Butler persona voice.
- A harness agent uses its configured harness identity and voice.
- TTS is fed `spoken_text`; the UI is fed both `full_text` and `spoken_text` metadata.
- Long spoken output is divided at semantic boundaries and remains interruptible.
- Interrupting speech does not cancel the underlying task unless Ant explicitly asks to stop the task.
- If playback cannot be confirmed, the transcript retains the existing unconfirmed state rather than claiming the response was heard.

## Privacy and safety

- Do not persist provider tokens, credentials, raw authentication material, or secret-bearing environment values in turns, summaries, or memories.
- Access claims require control-plane or successful-task evidence and include freshness.
- Hidden communication contracts and context packages never appear as user-authored transcript turns.
- Cross-agent context transfer follows least privilege and excludes unrelated channel history.
- Existing confirmation requirements for destructive or externally owned work remain in force.
- Forgetting removes a memory from future assembly and downstream indexes; any legally or operationally required event tombstone contains no forgotten value.

## Compatibility and migration

- Existing transcript and agent history remain readable.
- New persisted objects use explicit schema versions and additive migration where possible.
- The first Milestone 2 run creates stable channel IDs for all configured agents and Butler.
- Existing active RAP-owned jobs may be referenced from a channel only when ownership can be resolved unambiguously; otherwise they remain visible as unbound work.
- Existing short-term conversation may be imported as legacy coordinator context with provenance, but RAP must not manufacture agent-local memories from it.
- Full voice mode and Brain mode must produce the same routing, floor, ownership, memory, and result semantics.

## Testing strategy

### Unit tests

- floor transitions for unnamed, named, follow-up, acknowledgment, unrelated, and explicit Butler turns;
- separation of front door, floor, and task owner;
- candidate filtering and ranking from fresh, stale, degraded, busy, and missing evidence;
- deterministic context ordering and section budgets;
- channel isolation and least-privilege context transfer;
- memory promotion, provenance, supersession, and forgetting;
- summary generation rules and fact/inference preservation;
- native-resume validation and rejection of unrelated sessions;
- rotation with logical-channel continuity;
- full versus spoken result preservation;
- communication-contract version recording;
- recovery decisions for retry, reassignment, clarification, and manual action.

### Adapter contract tests

For each of Claude Code, Codex, Hermes, Code-Puppy, and OpenClaw:

- declare native-resume support accurately;
- create or resume only a channel-bound session;
- reject a mismatched or unverifiable session;
- dispatch with the context package and communication contract;
- normalize full, partial, blocked, and failure results;
- survive physical-session rotation without changing channel identity.

### Integration tests

- Butler-mediated request through selection, handoff, dispatch, and agent-owned result;
- direct-address request and unnamed follow-up continuity;
- simultaneous background task completion without unintended floor theft;
- application restart with restored channels, summaries, memories, task references, and stale live status;
- physical-session loss followed by transparent rehydration;
- access loss followed by Butler intervention and safe reassignment;
- Brain/full-mode behavior parity;
- event replay and transcript speaker fidelity;
- atomic persistence and recovery from interrupted writes.

### UI tests

- unified chronological transcript with correct identities and voices;
- channel/task filtering and search;
- full-result expansion independent of spoken text;
- history, archive, new chapter, session reset, and forgetting controls;
- operational events distinguished from speech;
- unchanged avatar behavior during working tasks.

## Acceptance scenarios

1. Ant asks an unnamed email question; Butler selects an agent using recorded capability, access, health, load, freshness, and recent-success evidence.
2. The selected agent—not Butler—speaks a useful, complete result and the UI retains the full result.
3. Ant names OpenClaw; RAP opens Jax's persistent direct channel and Jax answers in the configured voice.
4. Relevant follow-ups remain with Jax without repeating “OpenClaw.”
5. An unrelated unnamed request returns through Butler.
6. Saying “Butler” returns the floor immediately.
7. Restarting RAP preserves channel history, summaries, promoted memories, active-task references, and floor state while marking restored live status stale.
8. A physical harness-session rotation is invisible conversationally and does not lose relevant context.
9. An agent failure or verified access loss causes Butler to explain and safely retry, reassign, ask, or stop.
10. A long result remains complete in text and receives a detailed, voice-sized spoken delivery with an offer to continue.
11. RAP never resumes an unrelated terminal or harness session.
12. A user correction supersedes stale memory, preserves provenance, and changes future context assembly.
13. A background completion does not steal the floor from the current conversation.
14. Switching agents transfers only relevant scoped context, not the entire previous channel.
15. The same scripted session produces equivalent ownership and routing behavior in full voice mode and Brain mode.

## Delivery boundaries

Milestone 2 is complete only when all five configured harnesses participate in the same logical-channel contract, even if some adapters use rehydrated sessions rather than native resume. Unsupported native session behavior must degrade to clean rehydration, not an unsafe best-effort resume.

The milestone does not need agents to talk to one another. Requests such as “Code-Puppy, talk this through with Hermes” remain explicitly unsupported until Milestone 3 introduces bounded collaboration budgets, loop prevention, shared task rooms, and user-visible exchange rules.

## Implementation guidance

The implementation plan should begin by reconciling this design against the current local Milestone 1 tree. It should extend existing normalized conversation events, control-plane evidence, atomic persistence, and bridge lifecycles rather than building parallel substitutes.

Recommended subsystem boundaries are:

- conversation hub and domain models;
- floor and task-ownership resolver;
- session binding and adapter capability extensions;
- context assembly and compaction;
- scoped memory store and promotion policy;
- result presentation and speech segmentation;
- full/Brain integration;
- transcript and conversation controls;
- migration, observability, and end-to-end acceptance.

Each boundary should be test-driven and independently reviewable. Exact filenames should be chosen after inspecting the implemented Milestone 1 code because the local checkout is newer than the originally connected GitHub snapshot.

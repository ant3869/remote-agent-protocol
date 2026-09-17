# PRD: Live Agent Registry and Coordinator Control Plane

**Product:** Remote Agent Protocol  
**Milestone:** 1 — Verified agent awareness and control  
**Status:** Approved design  
**Date:** 2026-09-17

## Product outcome

Remote Agent Protocol becomes the authoritative front door for observing and controlling Anthony's agent harnesses. The active persona can report which agents are reachable, what they are doing, whether they are healthy, and which controls are safe. Status statements must come from current evidence rather than configuration, conversational inference, or another agent's opinion.

This milestone delivers one complete journey:

> Open RAP, ask for every agent's status, watch each harness get contacted, receive a verified spoken summary, then assign, inspect, stop, or redirect work.

## Background

RAP already has strong voice and application foundations: wake-word input, STT, TTS, selectable personas and harness voices, an animated avatar, deterministic delegation, asynchronous agent jobs, normalized job events, remote-host support, and agent consultation.

The missing layer is operational authority. The persona can delegate a task through `AgentBridge`, but it does not own a unified live view of agent presence, health, external activity, or safe controls. A request such as “give me a status check on all agents” can therefore be answered from configuration or delegated to a harness as ordinary work.

The supplied 2026-09-16 run demonstrates the failure mode:

- The initial status response described all configured agents as ready without contacting them.
- “Ping each one” was delegated to Code-Puppy rather than handled as a coordinator operation.
- The Code-Puppy job emitted generic heartbeats, exposed no meaningful progress, and failed after 303 seconds without output.
- Follow-up questions could not retrieve deeper status.
- Hermes records showed provider-limit information alongside empty or inconsistent completion results.

There is also a source-control prerequisite. The supplied local runtime and architecture describe version `1.14.0`, while the connected GitHub repository reports `1.11.0` and lacks several locally documented subsystems. The Windows checkout at `H:\Program Files (oss)\remote-agent-protocol` is the implementation source of truth until it is reconciled with GitHub.

## Goals

1. Maintain evidence-backed status for Claude Code, Codex, Hermes, Code-Puppy, and OpenClaw.
2. Track both RAP-owned jobs and detectable sessions started outside RAP.
3. Give the coordinator native status, launch, dispatch, progress, cancellation, and redirection operations.
4. Start stopped agents on demand when work is assigned.
5. Allow immediate control of RAP-owned jobs while protecting externally started work.
6. Expose probe and control activity through transcript, UI, avatar, and lifecycle events.
7. Isolate slow or failing harnesses so one cannot block the complete status response.
8. Preserve the existing voice stack, persona system, routing path, and `AgentBridge` execution behavior.

## Non-goals

- Replacing Pipecat or rewriting the voice pipeline.
- Migrating a running job between machines.
- Reattaching automatically after a remote host restarts.
- Fully autonomous multi-agent roundtables.
- General-purpose orchestration of arbitrary unconfigured executables.
- A major control-center redesign or new avatar artwork.
- Claiming detailed external-session activity when a harness exposes no reliable evidence.

## Product principles

### Evidence before narration

The LLM may turn structured observations into natural persona speech, but it may not create operational facts. Every current-state claim must be traceable to a timestamped observation.

### Unknown is a valid result

Failed inspection is not proof that an agent is offline. RAP distinguishes `unknown`, `unreachable`, and `stopped`.

### Coordination is not delegation

Inventory, health checks, job inspection, cancellation, and redirection are coordinator operations. RAP must not ask one harness to discover the state of the others.

### Capability-driven controls

Each adapter declares what it can prove and safely control. The UI and coordinator expose only supported operations.

## Architecture

Add an `AgentControlPlane` above the existing `AgentBridge`.

### AgentControlPlane

Owns the registry, adapter lifecycle, refresh policy, coordinator operations, state derivation, and normalized control-plane events. It is the single application service used by full voice mode and Brain mode.

### AgentRegistry

Stores the latest normalized observation for each configured agent, current RAP-owned job references, detected external work, capabilities, evidence, freshness, and health issues.

Persisted observations are last-known context only. All persisted observations begin a new application run as stale and cannot be presented as current until reverified.

### Agent adapters

One adapter per harness translates harness-specific evidence and controls into the normalized contract. The first milestone includes:

- Claude Code
- Codex
- Hermes
- Code-Puppy
- OpenClaw

### CoordinatorTools

Provides typed application operations to deterministic routing and the persona. These calls return structured results and emit lifecycle events; they are not free-form shell tools.

### AgentBridge

Remains responsible for executing and monitoring RAP-launched jobs. Its existing job lifecycle feeds the registry. The control plane does not duplicate subprocess management.

## Normalized status model

Status uses independent dimensions so “installed,” “running,” “working,” and “healthy” cannot collapse into one green indicator.

```yaml
agent_id: hermes
display_name: Mara
harness: hermes
machine: Main PC

presence: reachable       # reachable | unreachable | stopped | unknown
activity: working         # idle | working | waiting | blocked | unknown
health: degraded          # healthy | degraded | failed | unknown

current_work:
  summary: Cleaning up email
  project: Personal email
  ownership: rap           # rap | external | unknown
  started_at: timestamp
  progress:
    completed: 3
    total: 7
  last_activity_at: timestamp

capabilities:
  - accept_task
  - report_progress
  - cancel_rap_job
  - inspect_external_session

evidence:
  source: hermes_adapter
  observed_at: timestamp
  detail: Active session with recent tool activity
```

### Status rules

- Process existence alone never proves `idle`, `working`, or `healthy`.
- A successful harness response establishes `reachable`.
- A verified absence of a required local process or service establishes `stopped`.
- Inspection failure without verified absence establishes `unknown` or `unreachable`.
- Provider quota, authentication, configuration, or dependency trouble establishes `degraded` or `failed` according to whether useful work remains possible.
- `working`, `waiting`, and `blocked` require current job or session evidence.
- Project names, task summaries, progress counts, and percentages require a verifiable source.
- Generic process heartbeats establish liveness only and never count as task progress.
- Every observation includes its source, observation time, expiration time or freshness policy, and adapter capability set.

## Adapter contract

The exact Python types should follow current project conventions, but every adapter must provide behavior equivalent to:

```python
class AgentAdapter:
    async def discover(self) -> AgentObservation: ...
    async def probe(self) -> AgentObservation: ...
    async def launch(self) -> LaunchResult: ...
    async def inspect_jobs(self) -> list[ObservedJob]: ...
    async def dispatch(self, task: AgentTask) -> JobHandle: ...
    async def cancel(self, job_id: str) -> ControlResult: ...
```

Every first-milestone adapter must support:

- Basic installation/configuration discovery.
- Bounded active probing.
- On-demand launch when the configured harness is stopped.
- RAP-owned job tracking through `AgentBridge`.
- Explicit capability reporting.
- Normalized error classification.

External-session inspection and safe external interruption are optional per adapter. An unsupported capability must remain unavailable rather than being simulated with LLM inference or generic process termination.

## Coordinator operations

The application exposes the following typed operations:

```text
list_agents(refresh)
get_agent_status(agent, refresh)
get_agent_jobs(agent)
dispatch_task(agent, task)
get_job_progress(job_id)
cancel_job(job_id)
redirect_job(job_id, new_agent)
```

### Status request

1. Deterministic routing recognizes an all-agent or named-agent status request.
2. The coordinator emits a probe-started event for each target.
3. Adapter probes run concurrently under bounded per-adapter and overall timeouts.
4. Each completed probe updates the registry and emits a result event immediately.
5. Slow or failing probes do not discard successful results from other agents.
6. The persona receives structured results plus freshness and evidence metadata.
7. The persona speaks a concise summary that distinguishes verified status, degraded status, and unknown status.

Status operations are read-only and require no confirmation.

### Dispatch and on-demand launch

When work is assigned to a stopped agent, the coordinator invokes that adapter's launch operation, verifies readiness, and then dispatches through the existing execution path. Launch failure returns a classified error without silently selecting another harness.

### Progress request

Progress questions query the registry and live job state. RAP returns the latest verified milestone, tool activity, waiting reason, or last activity time. If no meaningful progress evidence exists, it says so directly.

### Cancellation and redirection

- RAP-owned jobs may be cancelled immediately.
- Redirection of a RAP-owned job is an ordered cancellation followed by a new dispatch; both actions are recorded.
- Externally started work requires confirmation unless its adapter explicitly verifies a safe interruption mechanism.
- Unsupported external interruption must remain unavailable even after confirmation; RAP explains the manual action needed.
- Cancellation or redirection targets a specific job or resolved active session, never merely “the newest process” across all agents.

## Events and user experience

The control plane emits normalized events for:

- probe started
- probe succeeded
- probe failed
- status changed
- launch started
- launch ready
- launch failed
- job dispatched
- job progress changed
- cancellation requested
- cancellation completed
- redirection completed

The conversation UI renders probe activity as operational transcript entries such as `Contacting Codex…` and `Hermes responded`. Results may appear while other probes continue.

The avatar receives a `consulting` activity state for the duration of a coordinator probe group. This milestone supplies the state/event hook and uses existing visual assets; it does not require new artwork.

The agent roster displays:

- presence
- activity
- health
- freshness or last contact
- current verified work
- machine
- available controls

The spoken layer remains concise. Detailed evidence and diagnostics stay visible in the UI.

## Routing requirements

- All-agent and named-agent status requests bypass the agent-task classifier.
- Phrases such as “ping every agent,” “who is working,” “what is Hermes doing,” and “is Codex available” resolve to coordinator operations.
- Follow-up questions about a known status or job resolve against registry/job identifiers stored in conversational control context.
- The persona cannot emit a delegation marker for a coordinator operation already executed deterministically.
- Existing direct-address routing for real harness work remains unchanged.

## Error handling

- Adapter failures are isolated and returned per agent.
- Empty output cannot complete a probe or task successfully unless the adapter contract explicitly defines an evidence-bearing empty success.
- Provider limits, authentication failures, missing executables, stopped services, timeouts, malformed output, and unsupported operations use distinct error categories.
- A timeout records the last meaningful activity and marks status according to available evidence; it does not invent progress.
- Concurrent refreshes for the same agent coalesce when safe to avoid duplicate probes.
- A late result may update the registry but cannot rewrite a completed spoken summary without a new visible status-change event.
- Brain mode and full voice mode use the same control-plane service and result shapes.

## Persistence and auditability

- Persist last-known normalized observations separately from persona memory.
- Mark persisted observations stale at application start.
- Record coordinator decisions and control outcomes in structured telemetry.
- Retain source and timestamp for every operational fact presented in the UI.
- Do not place raw credentials, unrestricted harness output, or secrets in registry persistence or lifecycle events.

## Testing requirements

### Unit tests

- State derivation from observation combinations.
- Freshness and stale-on-restart behavior.
- Error classification.
- Capability gating.
- Cancellation and redirection policy.
- Concurrent refresh coalescing and timeout isolation.

### Adapter contract tests

Use deterministic fake processes, session records, and responses to test each adapter without requiring live provider usage. Verify unsupported capabilities remain unavailable.

### Integration tests

- Control plane with `AgentBridge` lifecycle events.
- Full mode and `BrainSessionAdapter` parity.
- Transcript and avatar event emission.
- Persistence and restart behavior.

### Behavioral tests

Extend `voice_probe` with scenarios covering:

- status of all agents
- named-agent status
- one hung adapter among healthy adapters
- degraded provider/quota state
- progress with a meaningful milestone
- progress with liveness only
- cancel RAP-owned work
- redirect RAP-owned work
- confirmation before external interruption
- stale state immediately after restart

## Acceptance criteria

1. A status request contacts every configured target directly through its adapter.
2. One hung or failed harness does not block verified results from the others.
3. RAP never reports an agent ready solely because it is configured or installed.
4. Failed inspection is not mislabeled as offline.
5. Provider quota trouble is displayed and narrated as degraded or failed, never as an empty successful completion.
6. Progress answers contain verified activity or explicitly state that no meaningful progress is available.
7. RAP can stop and redirect its own job without delegating the control action to another harness.
8. Externally started work receives the approved confirmation protection.
9. Stopped agents launch on demand before receiving work.
10. Persisted state is stale until verified during the new run.
11. Probe activity is visible in the transcript and drives the avatar consulting-state hook.
12. Full voice mode and Brain mode produce equivalent coordinator behavior.
13. Existing voice conversation, direct agent delegation, job announcements, and remote-agent behavior continue passing their focused regression suites.

## Implementation sequence

1. Reconcile the Windows `1.14.0` checkout with GitHub without discarding local work; document the exact baseline commit before feature edits.
2. Add normalized control-plane models and the registry.
3. Add the adapter protocol and deterministic fake adapter.
4. Feed existing `AgentBridge` lifecycle events into the registry.
5. Implement the five harness adapters and their contract tests.
6. Add coordinator operations and control policies.
7. Add deterministic routing and conversational control context.
8. Add transcript, lifecycle, avatar, and roster projections.
9. Add persistence, telemetry, integration tests, and `voice_probe` scenarios.
10. Run focused tests, the application regression suite, lint, and an evidence-recorded Windows smoke test.

## Release requirement

Do not describe this milestone as complete from mocked adapters alone. Release evidence must include a Windows smoke-test matrix showing the observed result for every configured harness and the exact evidence source used for each status claim.

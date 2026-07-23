# Wargame: Mediator Universal Delegation

Source brief: `tasks/mediator-universal-delegation.md`

## Mission boundary

The current route already runs explicit-agent parsing, small-talk gating, capability confirmation, a keyword net, and a bounded semantic classifier through `IntentRouter._route`; `VoiceSession._resolve_delegation` then uses the existing confirmation and `AgentBridge` lifecycle paths. The viable mission is to improve measurable coverage and correction behavior. “Any conceivable CTA” is not a testable or safe acceptance criterion.

## Move 1 — Freeze the routing contract with adversarial utterances

**Move / Action:** Build one table-driven check covering direct commands, implied requests, non-request statements, compound requests, contextual corrections, ambiguous/destructive requests, and classifier timeout. Record expected action (`chat`, `dispatch`, `confirm`, `update_active`) and backend.

**Expected Observation:** Existing explicit commands and acknowledgements pass. At least implied wishes, compound workflows, and active-job corrections expose deterministic failures before production code changes. A failed case reports the exact utterance and decision provenance.

**Likely Failure & Counter Move:** Product language such as “implicit” is interpreted differently by each test author. Replace prose-only expectations with examples and require an explicit imperative or benefit-to-user signal before dispatch; keep mere future-tense self-statements in chat.

**Forks & Triggers:** If no labeled corpus is approved, stop at a conservative policy and confirmation for uncertain actions. If false-positive tolerance is zero, route all non-explicit actions to confirmation rather than dispatch.

## Move 2 — Extend the existing router, not create a second one

**Move / Action:** Add the smallest missing decision rules to `IntentRouter._route`, preserving the current tier order and `RoutingDecision` provenance. Represent compound work as one ordered task for one coordinating backend unless the request explicitly names different agents.

**Expected Observation:** High-confidence commands resolve before the classifier; casual statements return `ACTION_NONE`; ambiguous mutating work returns `ACTION_CONFIRM`; classifier timeout continues to degrade to chat instead of inventing work.

**Likely Failure & Counter Move:** A broad keyword rule catches “I’m going to write code today.” Require that heuristic matches include a request construction or user-benefit construction, then add the false positive to the permanent corpus.

**Forks & Triggers:** If a compound request can be completed by the configured default backend, send one ordered prompt. If it requires unavailable capabilities, explain the missing capability and do not partially launch unless the user approves the reduced scope.

## Move 3 — Snapshot only relevant context at dispatch time

**Move / Action:** Build the delegated task from the verbatim utterance plus a bounded snapshot of current conversation, active persona/backend, workspace, and only relevant retrieved memories. Label each section and treat remembered text as context, never as instructions.

**Expected Observation:** A command referring to a fact from five minutes earlier includes that fact; unrelated memories and GUI decoration do not enter the agent prompt; prompt size is capped and logged without sensitive values.

**Likely Failure & Counter Move:** Semantic retrieval returns stale or hostile text that changes the task. Quote memory as untrusted reference material, retain the user’s current utterance as authoritative, and ask a clarification when two candidate referents remain plausible.

**Forks & Triggers:** If no referent clears the relevance threshold, ask one concise question. If memory is unavailable, delegate only when the utterance is self-contained; otherwise ask rather than guess.

## Move 4 — Make selection explainable and fail closed

**Move / Action:** Select only among configured `AGENT_BACKENDS`, using existing aliases and capability facts. Store the chosen backend and reason in the routing event. Do not let model output construct a command or backend identifier directly.

**Expected Observation:** Coding work chooses the configured coding backend, general investigation chooses the default/general backend, and unsupported work produces a spoken limitation. No unknown backend reaches `AgentBridge.start`.

**Likely Failure & Counter Move:** Two agents score equally and the model picks nondeterministically. Prefer the current persona’s `tool_user`, then the configured default; require confirmation if that changes safety or output materially.

**Forks & Triggers:** If one backend can coordinate the whole workflow, launch one job. If distinct backends are mandatory, serialize jobs and pass the first job’s terminal artifact into the second only after success.

## Move 5 — Serialize start versus rapid correction

**Move / Action:** Give each proposed delegation a short-lived intent token and serialize dispatch/update decisions on the session loop. Before process launch, re-check that the token is still current. Route “wait/actually/instead” to the newest matching pending or active job.

**Expected Observation:** “Write tests—wait, use pytest” produces one job with the corrected task or one cancelled-and-replaced job, never two conflicting live jobs. Events identify the superseded token/job.

**Likely Failure & Counter Move:** The first subprocess starts between the correction and cancellation. Mark it cancelling, terminate through the existing bridge path, wait for terminal status, then launch the replacement; never overlap mutating jobs in the same workspace.

**Forks & Triggers:** If the agent supports live context input, append the correction and retain the job ID. Otherwise cancel-and-replace. If termination fails, block replacement and tell the user.

## Move 6 — Reuse confirmation and lifecycle feedback

**Move / Action:** Route destructive/privileged decisions through `_delegate_ack_ex` and existing pending confirmations. Announce starts through the current `agent_job` event path; use terminal/progress events for blocked, failed, and completed speech.

**Expected Observation:** The user hears a truthful start only after a job is accepted; destructive work remains pending until explicit approval; immediate spawn failures result in a failed lifecycle event and spoken diagnosis.

**Likely Failure & Counter Move:** The LLM promises work before `AgentBridge` has accepted it. Generate the acknowledgement from application state, not free-form model text, and preserve the existing markerless-promise guard as a fallback.

**Forks & Triggers:** If bridge start raises, emit failed immediately and clear pending state. If a job blocks, bind the spoken answer to that job’s confirmation token rather than the global newest confirmation.

## Move 7 — Run fault injection, then a live voice pass

**Move / Action:** Test classifier timeout/invalid JSON, missing memory, unavailable backend, immediate subprocess crash, delayed start plus correction, and destructive confirmation. Then run two end-to-end voice scenarios: contextual delegation and rapid correction.

**Expected Observation:** Every proposed task ends in exactly one of chat, pending confirmation, active job, or explicit failure; no request disappears silently. Each active job emits a terminal event or remains visibly cancellable under the configured timeout.

**Likely Failure & Counter Move:** Unit tests pass while STT punctuation changes routing. Add the observed transcription variants to the corpus and assert normalized decisions, not exact punctuation.

**Forks & Triggers:** If false positives exceed the agreed threshold, disable auto-dispatch for semantic-only decisions. If correction races remain reproducible, ship explicit commands and confirmation first; defer live active-job mutation.

## Abort Conditions

- Abort if the acceptance threshold and false-positive budget remain undefined; do not claim universal detection.
- Abort any launch when backend, workspace, objective, or destructive scope cannot be determined safely.
- Abort replacement work if the superseded mutating job cannot be confirmed stopped.
- Abort context injection if secrets or untrusted memory cannot be separated from executable instructions.
- Abort release if any tested utterance silently disappears or produces more than one live mutating job.
- Abort automatic delegation if classifier failure can dispatch rather than degrade to chat/confirmation.

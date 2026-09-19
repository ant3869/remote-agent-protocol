# Persistent Agent Conversations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Track every checkbox.

**Goal:** Turn RAP into one continuous conversational room where Butler brokers unnamed work, named agents can be addressed directly, each agent retains durable scoped context, and the working agent reports its own complete result.

**Architecture:** Add an `AgentConversationHub` beside the existing `AgentControlPlane`. The hub owns logical channels, floor and task ownership, scoped memory, bounded context assembly, RAP-managed harness-session bindings, and result presentation. The control plane remains the evidence source and `AgentBridge` remains the execution engine. Full voice mode and Brain mode consume the same hub and event contracts.

**Tech Stack:** Python 3.12, asyncio, dataclasses/enums, existing RAP/Pipecat application layer, local HTTP UI, vanilla JavaScript, pytest, Ruff, existing atomic JSON persistence.

**Spec:** `docs/superpowers/specs/2026-09-17-persistent-agent-conversations-design.md`

## Working context

Work in:

```text
H:\Program Files (oss)\remote-agent-protocol
```

Milestone 1 was reported as committed and passing with an evidence-backed registry, five direct adapters, concurrent probes, stale persistence, RAP-owned job controls, voice/Brain status routing, and roster/transcript projections. Do not assume filenames or interfaces from that report; verify them in the checkout before editing.

Read before editing:

1. `AGENTS.md`
2. The Milestone 1 PRD and this Milestone 2 design
3. `docs/agent-control-plane.md` and `docs/architecture.md`
4. `remote_agent_protocol/control_plane/`
5. `remote_agent_protocol/agent_bridge.py` and `conversation.py`
6. `session.py`, `brain.py`, and `brain_adapter.py`
7. `web_gui.py` and `web_app/conversation.js`
8. Relevant tests located during Task 0

If `AGENTS.md` requires Codebase Memory MCP, query it before implementation files. If unavailable, proceed only when `AGENTS.md` permits direct inspection.

## Global Constraints

- Preserve unknown user changes. Never reset, clean, force-checkout, force-push, stash, or discard them.
- Do not modify vendored `src/pipecat` unless a focused failing test proves the app layer cannot implement the behavior.
- Butler is the default front door for unnamed new work, not the universal speaker.
- Explicitly naming an agent bypasses selection but not availability and safety checks.
- Track front door, conversation floor, and task owner independently.
- The task-owning agent authors success, partial, and blocked results. Butler cannot rewrite or impersonate it.
- Butler owns routing ambiguity, dispatch failure, silence, lost access, invalid output, retry, and reassignment narration.
- Use live control-plane evidence for health, access, load, and capability claims. Stale evidence may trigger a probe but cannot be spoken as current.
- Never resume an unrelated terminal or harness session. Native resume requires a persisted RAP binding to the same agent and channel.
- Every adapter must support safe clean rehydration. Native resume is optional and must be proven per harness.
- Never persist credentials, tokens, hidden reasoning, system prompts, or raw secret-bearing output.
- Full voice mode and Brain mode use the same hub and behavior.
- Keep the existing avatar and `focused` visual unchanged.
- Agent-to-agent collaboration is Milestone 3 and is excluded here.
- Use TDD for every behavior change.
- Keep new internals out of already-large session, Brain, bridge, and web-server modules.
- Commit after each independently passing task. Do not push or open a PR without explicit approval.

---

### Task 0: Reconcile and protect the live baseline

**Files:**
- Read: `AGENTS.md`, `VERSION`, `pyproject.toml`, current implementation and tests
- Add if absent: the spec and this plan at their listed paths

**Produces:** Verified baseline SHA, dirty-tree report, current interface map, and passing baseline tests.

- [ ] Run read-only diagnostics:

```powershell
git status --short --branch
git remote -v
git log --oneline --decorate -20
git rev-parse HEAD
git diff --stat
Get-Content VERSION
```

- [ ] Map the actual Milestone 1 and conversation implementation:

```powershell
rg -n "class AgentControlPlane|class AgentRegistry|class AgentAdapter|class AgentBridge|class VoiceSession|class BrainSession|class BrainSessionAdapter|agent_job|conversation|speaker|memory|session" remote_agent_protocol tests docs
rg --files remote_agent_protocol\control_plane remote_agent_protocol\web_app tests | Sort-Object
```

- [ ] Record the baseline SHA, branch/worktree state, dirty-file ownership, test command, control-plane constructor, adapter protocol, bridge result events, full/Brain construction paths, conversation schema, and web projection paths.
- [ ] Stop and ask Ant if an overlapping dirty file has unknown ownership.
- [ ] Copy the approved spec and plan into place without changing approved product behavior.
- [ ] Run the focused baseline, adjusting filenames only when inspection proves they differ:

```powershell
.venv\Scripts\python -m pytest tests\test_agent_control_plane.py tests\test_session_delegation.py tests\test_agent_bridge.py tests\test_web_gui.py -q
.venv\Scripts\python -m ruff check remote_agent_protocol tests
```

- [ ] Stop before feature code if the baseline fails. Separate pre-existing failures from environment failures and report exact output.
- [ ] Commit only the documents when they are the sole intended changes:

```powershell
git add -- docs/superpowers/specs/2026-09-17-persistent-agent-conversations-design.md docs/superpowers/plans/2026-09-17-persistent-agent-conversations.md
git commit -m "docs: define persistent agent conversations"
```

---

### Task 1: Add conversation models and atomic persistence

**Files:**
- Create: `remote_agent_protocol/conversation_hub/__init__.py`
- Create: `remote_agent_protocol/conversation_hub/models.py`
- Create: `remote_agent_protocol/conversation_hub/store.py`
- Test: `tests/test_conversation_hub_models.py`
- Test: `tests/test_conversation_hub_store.py`

**Produces:** `AgentChannel`, `ConversationTurn`, `ScopedMemory`, `SessionBinding`, `FloorState`, `TaskReference`, and `ConversationStore`.

- [ ] Write failing tests for stable IDs, enum values, timezone-aware timestamps, round-trip serialization, schema rejection, atomic replacement, interrupted-write recovery, and restored bindings requiring validation.

```python
def test_channel_round_trip_preserves_identity():
    channel = AgentChannel.new(agent_id="openclaw", now=NOW)
    restored = AgentChannel.from_dict(channel.to_dict())
    assert restored.channel_id == "agent:openclaw"
    assert restored.chapter_id == 1


def test_restored_native_binding_requires_validation():
    binding = SessionBinding.native(
        binding_id="binding-1",
        channel_id="agent:openclaw",
        agent_id="openclaw",
        native_session_id="session-1",
        validated_at=NOW,
    )
    assert SessionBinding.from_dict(binding.to_dict(), restored=True).requires_validation
```

- [ ] Verify the tests fail on missing imports.
- [ ] Implement these exact enums:

```python
class MemoryScope(StrEnum):
    CHANNEL = "channel"
    SHARED = "shared"
    TASK = "task"
    PROJECT = "project"


class MemoryConfidence(StrEnum):
    VERIFIED = "verified"
    USER_STATED = "user_stated"
    INFERRED = "inferred"


class MemoryStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FORGOTTEN = "forgotten"


class ResultKind(StrEnum):
    PROGRESS = "progress"
    SUCCESS = "success"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILURE = "failure"


class SessionStrategy(StrEnum):
    NATIVE_RESUME = "native_resume"
    REHYDRATE = "rehydrate"
```

- [ ] Use stable `agent:<agent_id>` channel IDs; Butler uses `coordinator:butler`. Store normalized turns separately from summaries.
- [ ] Persist schema version, channels, turns, memories, bindings, task references, and floor state using the existing atomic temp-and-replace convention. Preserve an unreadable/newer file and return a classified load error.
- [ ] Redact keys containing `token`, `secret`, `password`, `api_key`, or `authorization` before persistence.
- [ ] Run focused tests and Ruff; commit:

```powershell
.venv\Scripts\python -m pytest tests\test_conversation_hub_models.py tests\test_conversation_hub_store.py -q
.venv\Scripts\python -m ruff check remote_agent_protocol\conversation_hub tests\test_conversation_hub_models.py tests\test_conversation_hub_store.py
git add -- remote_agent_protocol/conversation_hub tests/test_conversation_hub_models.py tests/test_conversation_hub_store.py
git commit -m "feat: add durable agent conversation models"
```

---

### Task 2: Implement floor and task ownership

**Files:**
- Create: `remote_agent_protocol/conversation_hub/floor.py`
- Test: `tests/test_conversation_floor.py`

**Produces:** `FloorManager.resolve(TurnRoutingInput) -> FloorDecision`.

- [ ] Write table-driven failing tests for unnamed work, explicit agent, explicit Butler, related follow-up, acknowledgment, unrelated work, ambiguous tasks, unavailable named agent, background completion, and agent switching.

```python
@pytest.mark.parametrize(
    ("text", "current", "kind", "target"),
    [
        ("OpenClaw, check my email", "butler", "direct", "openclaw"),
        ("What about school messages?", "openclaw", "follow_up", "openclaw"),
        ("Thanks", "openclaw", "acknowledgment", "openclaw"),
        ("Butler", "openclaw", "return_to_butler", "butler"),
        ("Plan a new dashboard", "openclaw", "butler_mediated", "butler"),
    ],
)
def test_floor_decision(text, current, kind, target):
    decision = manager.resolve(
        TurnRoutingInput(text=text, current_floor=current, now=NOW)
    )
    assert (decision.kind, decision.target_id) == (kind, target)
```

- [ ] Define immutable `TurnRoutingInput` and `FloorDecision` with target, task ID, clarification flag, Butler-selection flag, and reason code.
- [ ] Reuse the existing deterministic agent alias parser. Do not create a competing name parser.
- [ ] Completion is spoken by its task owner but does not automatically steal the active floor.
- [ ] When multiple active tasks make “it/that/this” ambiguous, return one short Butler clarification instead of guessing.
- [ ] A named unavailable agent transfers narration to Butler, which explains the evidence and offers only currently verified alternatives.
- [ ] Run tests and Ruff; commit `feat: add conversational floor resolution`.

---

### Task 3: Add scoped memory and bounded context

**Files:**
- Create: `remote_agent_protocol/conversation_hub/memory.py`
- Create: `remote_agent_protocol/conversation_hub/context.py`
- Test: `tests/test_conversation_memory.py`
- Test: `tests/test_context_assembler.py`

**Produces:** `MemoryRepository`, `MemoryPromotionPolicy`, `ContextBudget`, and `ContextAssembler`.

- [ ] Write failing tests for all four scopes, provenance, correction, forgetting, inferred-access exclusion, channel isolation, relevant project sharing, deterministic ordering, budgets, and secret exclusion.

```python
def test_correction_supersedes_without_erasing_provenance(memory_repo):
    old = memory_repo.add(user_memory("Picture day is October 2"))
    new = memory_repo.correct(old.memory_id, "Picture day is October 9", TURN_2, NOW)
    assert memory_repo.get(old.memory_id).status is MemoryStatus.SUPERSEDED
    assert new.supersedes == old.memory_id


def test_context_excludes_unrelated_channel_history(assembler):
    package = assembler.assemble(request_for("hermes", task_id="task-2"))
    assert "private OpenClaw-only turn" not in package.render()
```

- [ ] Implement promotion policy: reject secrets and raw tool output; inferred items remain channel-local; shared/project promotion requires stable user fact, explicit project decision, or verified result.
- [ ] Use this configurable default budget without adding a tokenizer dependency:

```python
@dataclass(frozen=True)
class ContextBudget:
    total_chars: int = 48_000
    recent_turns_chars: int = 20_000
    summary_chars: int = 8_000
    active_task_chars: int = 8_000
    memory_chars: int = 8_000
    live_state_chars: int = 4_000
```

- [ ] Assemble in this order: communication contract, current request, active task, recent channel turns, compacted summary, eligible memories, relevant live control-plane state. Never truncate the request or contract.
- [ ] Structure summaries under user statements, verified observations, decisions, open questions, and active tasks. Never upgrade inference to fact.
- [ ] A correction supersedes rather than erases. Forgetting excludes future assembly and leaves only a value-free tombstone.
- [ ] Switching agents transfers only the active task package, explicitly selected material, and eligible shared/project memory; never copy the previous channel wholesale.
- [ ] Keep current Brain-mode semantic-memory limitations explicit.
- [ ] Run tests and Ruff; commit `feat: add scoped conversation context`.

---

### Task 4: Add safe channel-bound harness sessions

**Files:**
- Create: `remote_agent_protocol/conversation_hub/sessions.py`
- Modify: `remote_agent_protocol/control_plane/adapters/base.py`
- Modify: all five concrete adapter modules
- Test: `tests/test_conversation_sessions.py`
- Test: `tests/test_harness_conversation_sessions.py`

**Produces:** `ConversationSessionAdapter` and `SessionBindingManager`.

- [ ] Write failing contract tests for clean creation, native resume, restored-binding validation, mismatch rejection, missing session, context-limit rotation, explicit reset, failure rotation, and clean rehydration.

```python
class ConversationSessionAdapter(Protocol):
    agent_id: str

    @property
    def conversation_session_strategy(self) -> SessionStrategy: ...

    async def validate_bound_session(self, binding: SessionBinding) -> bool: ...
    async def create_bound_session(self, channel_id: str) -> SessionBinding: ...
    async def dispatch_in_session(
        self, binding: SessionBinding, context: ContextPackage
    ) -> JobHandle: ...
```

- [ ] Native resume is allowed only when agent ID and channel ID match, a native ID exists, restored validation succeeded, and the adapter proves support.
- [ ] Mark native bindings unvalidated after process restart. Failed validation rotates to a clean session and never searches for a recent terminal session.
- [ ] Inspect each installed CLI's current help before claiming native resume. If dedicated resume cannot be proven, declare `REHYDRATE`.
- [ ] Preserve existing quoting, encoding, cancellation, quota detection, and remote-host behavior.
- [ ] Run new and existing adapter suites and Ruff; commit `feat: add channel-bound harness sessions`.

---

### Task 5: Add evidence-backed Butler selection

**Files:**
- Create: `remote_agent_protocol/conversation_hub/selection.py`
- Test: `tests/test_butler_agent_selection.py`

**Produces:** `CapabilityRequirement`, `SelectionCandidate`, `SelectionDecision`, and `AgentSelector`.

- [ ] Write failing tests for required capability/access, stale access, unreachable/degraded agents, load, recent success, default tie-break, no candidate, and rationale provenance.

```python
def test_fresh_access_beats_stale_history(selector):
    decision = selector.select(
        email_request(),
        (
            snapshot("openclaw", access="email", fresh=True, healthy=True),
            snapshot("hermes", access="email", fresh=False, healthy=True),
        ),
    )
    assert decision.agent_id == "openclaw"


def test_unknown_access_requests_probe_instead_of_inventing_candidate(selector):
    decision = selector.select(email_request(), snapshots_without_email_evidence())
    assert decision.agent_id is None
    assert decision.requires_probe is True
```

- [ ] Filter before ranking: required capability/access, availability/safety, freshness, load/conflicts, recent same-capability success, configured default.
- [ ] An old successful task is stale access evidence, not permanent access.
- [ ] Persist requirements, selected agent, eliminated candidates, reason codes, and evidence timestamps.
- [ ] Run tests and Ruff; commit `feat: add Butler capability selection`.

---

### Task 6: Build the AgentConversationHub

**Files:**
- Create: `remote_agent_protocol/conversation_hub/service.py`
- Create: `remote_agent_protocol/conversation_hub/events.py`
- Create: `remote_agent_protocol/conversation_hub/factory.py`
- Test: `tests/test_agent_conversation_hub.py`

**Produces:** `handle_turn`, `handle_job_event`, `restore`, `reset_session`, `new_chapter`, `archive_channel`, and `forget_memory`.

- [ ] Write failing async tests for Butler-mediated work, direct work, related follow-up, Butler return, handoff, ownership, progress, background completion, restart, and ambiguity.

```python
@dataclass(frozen=True)
class ConversationTurnRequest:
    text: str
    source: str
    explicit_agent_id: str | None
    correlation_id: str
    created_at: datetime


@dataclass(frozen=True)
class TurnDisposition:
    channel_id: str
    target_id: str
    task_id: str | None
    floor_decision: FloorDecision
    spoken_acknowledgment: str | None
```

- [ ] Implement one flow: resolve floor, clarify if needed, select/verify target, get channel, create task, bind session, assemble context, dispatch through `AgentBridge`, persist disposition.
- [ ] `AgentBridge` retains subprocess ownership. The hub owns conversational identity, channel context, and result attribution.
- [ ] Emit allowlisted events for floor changes; channel creation, restoration, archival, and reopening; session binding, resume, rotation, and reset; context assembly; memory proposal, promotion, supersession, and forgetting; Butler handoff/intervention; task assignment/reassignment; and result availability.
- [ ] Public events never contain hidden prompts, memory values, context contents, credentials, or raw harness output.
- [ ] Restore logical state at startup while leaving Milestone 1 live observations stale.
- [ ] Run hub, bridge, and control-plane tests and Ruff; commit `feat: add agent conversation hub`.

---

### Task 7: Enforce agent-authored results and Butler recovery

**Files:**
- Create: `remote_agent_protocol/conversation_hub/results.py`
- Modify: `remote_agent_protocol/agent_bridge.py`
- Test: `tests/test_conversation_results.py`
- Test: `tests/test_agent_bridge.py`

**Produces:** versioned communication contract, `AgentResultEnvelope`, `ResultPresenter`, and `RecoveryDecision`.

- [ ] Write failing tests proving the owner authors success/partial/blocked output, Butler never rewrites success, raw logs are not canonical results, full text survives segmentation, and failure transfers narration to Butler.
- [ ] Use this exact hidden contract:

```python
COMMUNICATION_CONTRACT_VERSION = 1
COMMUNICATION_CONTRACT = (
    "Speak to Ant like a trusted teammate: natural, direct, and brief, but complete. "
    "Lead with the outcome. Include every important result, decision, warning, failure, "
    "and next step. Omit internal tool chatter unless asked. Keep operational progress "
    "separate from the final answer. If blocked, ask one clear question. Never impersonate Butler."
)
```

- [ ] Define `AgentResultEnvelope` with task, attempt, channel, agent, result kind, full text, spoken text, artifact refs, contract version, and timestamp.
- [ ] Inject the contract as application policy, never as a transcript turn or memory.
- [ ] If an adapter returns machine-oriented output, ask the same bound agent for one final presentation turn. Never use Butler or a different model to impersonate it.
- [ ] `ResultPresenter` may remove speech-hostile formatting and segment at semantic boundaries; it cannot remove material findings.
- [ ] If a complete result exceeds the comfortable speech limit, speak a detailed first segment and have the same agent offer to continue reading. Keep the entire result visible.
- [ ] Speak meaningful milestones, questions, and blockers only. Routine liveness heartbeats remain visual events and do not become long-term memory.
- [ ] Butler recovery triggers only on launch/dispatch failure, provider/auth/quota failure, verified access loss, silence, invalid final output, unsupported control, or disappearance.
- [ ] Automatic reassignment requires unchanged outcome/safety scope and a fresh qualified second candidate. Preserve every attempt and partial result.
- [ ] Run tests and Ruff; commit `feat: preserve agent-owned results`.

---

### Task 8: Integrate full voice and Brain modes

**Files:**
- Modify: `remote_agent_protocol/session.py`
- Modify: `remote_agent_protocol/brain.py`
- Modify: `remote_agent_protocol/brain_adapter.py`
- Modify: current routing/processor modules found in Task 0
- Test: `tests/test_session_delegation.py`
- Test: `tests/test_brain_adapter_orchestration.py`
- Create: `tests/test_conversation_mode_parity.py`

- [ ] Write parity tests that feed identical scripts to full and Brain modes and compare target, channel, task owner, floor transition, handoff, result speaker, and recovery.
- [ ] Construct one hub per application session through the factory. Do not create separate stores or floor managers for full and Brain modes.
- [ ] Route voice transcripts and typed composer sends through the same `handle_turn` after existing confirmation/multimodal assembly.
- [ ] Preserve corrections, cancellation, consultation, wake routing, attachments, remote jobs, and confirmation.
- [ ] Brain mode degrades unavailable TTS/semantic writes explicitly.
- [ ] Run parity, delegation, Brain, bridge, and control-plane tests; Ruff; commit `feat: integrate persistent conversations`.

---

### Task 9: Project the unified transcript and controls

**Files:**
- Modify: `remote_agent_protocol/conversation.py`
- Modify: `remote_agent_protocol/lifecycle_ws.py`
- Modify: `remote_agent_protocol/web_gui.py`
- Modify: `remote_agent_protocol/web_app/app.js`
- Modify: `remote_agent_protocol/web_app/conversation.js`
- Modify: `remote_agent_protocol/web_app/styles.css`
- Test: current backend and JavaScript conversation/UI suites

- [ ] Write failing tests for allowlisted events and authenticated endpoints:

```text
GET  /api/conversation-channels
GET  /api/conversation-history?channel_id=<id>&query=<text>
GET  /api/conversation-memory?memory_id=<id>
POST /api/conversation-control
```

- [ ] Accept these control bodies:

```json
{"action":"archive","channel_id":"agent:openclaw"}
{"action":"new_chapter","channel_id":"agent:openclaw"}
{"action":"session_reset","channel_id":"agent:openclaw"}
{"action":"forget_memory","memory_id":"memory-123"}
```

- [ ] Reuse the existing per-launch CSRF boundary and validate ownership/state server-side.
- [ ] Render one chronological transcript by default. Agent channels are optional filters, not mandatory chat tabs.
- [ ] Preserve true speaker, voice, channel, task, attempt, result kind, timestamps, playback state, full text, and spoken text.
- [ ] The memory detail response shows scope, confidence, source turn IDs, observed time, supersession state, and why it was eligible for the current context; never expose hidden prompts or secret values.
- [ ] Make archive, new chapter, session reset, and forgetting visibly distinct; none silently deletes transcript or durable memory.
- [ ] Do not alter avatar assets, expressions, renderer, or current `focused` behavior.
- [ ] Run backend/JS tests, syntax checks, and Ruff; commit `feat: add persistent conversation controls`.

---

### Task 10: Add migration and restart recovery

**Files:**
- Create: `remote_agent_protocol/conversation_hub/migrations.py`
- Modify: `conversation_hub/store.py` and the current config module
- Test: `tests/test_conversation_migrations.py`
- Test: `tests/test_conversation_restart.py`

- [ ] Write failing tests for first-run channels, idempotent migration, legacy coordinator import, unbound jobs, stale status, corrupt-file preservation, retention, compaction, and session reset after restart.
- [ ] Create stable channels for Butler and every configured agent on first run.
- [ ] Import eligible short-term history only as `legacy_import` coordinator context. Do not manufacture agent-local/shared memories from legacy text.
- [ ] Bind an existing job only when owner/task mapping is unambiguous.
- [ ] Add these defaults:

```text
CONVERSATION_CONTEXT_CHAR_BUDGET=48000
CONVERSATION_RECENT_TURN_LIMIT=40
CONVERSATION_CHANNEL_TURN_RETENTION=5000
CONVERSATION_PROGRESS_RETENTION=500
CONVERSATION_SPEECH_SEGMENT_CHARS=1200
```

- [ ] Retention cannot remove promoted memory, active tasks, unresolved decisions, or canonical result artifact references.
- [ ] Run migration/restart tests twice against the same fixture, Ruff, and commit `feat: recover persistent conversations`.

---

### Task 11: Validate all acceptance behavior

**Files:**
- Create: `docs/agent-conversations.md`
- Modify: `docs/architecture.md`, `README.md`, `CHANGELOG.md`, and `VERSION` per release policy
- Create: `docs/validation/2026-09-17-persistent-agent-conversations.md`

- [ ] Convert all 15 design acceptance scenarios into deterministic automated tests.
- [ ] Run the complete project checks:

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m ruff check remote_agent_protocol tests voice_probe
.venv\Scripts\python -m compileall -q remote_agent_protocol
node --check remote_agent_protocol\web_app\app.js
node --check remote_agent_protocol\web_app\conversation.js
```

- [ ] Run the exact JavaScript and `voice_probe` commands discovered in Task 0 and record complete results.
- [ ] Harmlessly check all five harnesses. Record adapter, session strategy, native-resume evidence, binding, restart validation, rehydration, result speaker/voice, and degraded behavior.
- [ ] Run this live script:

```text
1. Ask an unnamed harmless question requiring one known capability.
2. Observe Butler's evidence-backed selection and concise handoff.
3. Confirm the selected agent delivers its own result.
4. Address OpenClaw/Jax directly with a harmless request.
5. Send a relevant unnamed follow-up and confirm it stays with Jax.
6. Send an unrelated unnamed request and confirm it returns through Butler.
7. Say “Butler” and confirm immediate floor transfer.
8. Complete a background task without stealing the active floor.
9. Restart RAP and continue a prior subject without repeating context.
10. Reset one physical session and confirm the logical channel survives.
11. Simulate one failure/access loss and confirm Butler intervenes with partial output preserved.
12. Correct one memory and confirm the old value is superseded.
```

- [ ] Inspect final state:

```powershell
git status --short
git diff --check
git diff --stat
git diff -- src/pipecat
git log --oneline --decorate -15
```

- [ ] Document verified behavior only; label unsupported or untested behavior.
- [ ] Commit docs/validation evidence with `docs: document persistent agent conversations`.

## Completion report

Return:

1. Baseline and final SHAs, branch/worktree, and dirty-tree disposition.
2. Created/modified files grouped by subsystem.
3. Exact Python, JavaScript, Ruff, compile, voice-probe, and live-check output.
4. Five-harness session-strategy matrix with evidence for native-resume claims.
5. Results for all 15 acceptance scenarios.
6. Transcript walkthrough: Butler selection, agent-authored success, direct address, follow-up, floor return, restart continuity, and Butler failure intervention.
7. Unsupported behavior, failed scenarios, risks, and plan deviations.
8. Commits created.

Do not claim completion from unit tests alone. Completion requires deterministic acceptance coverage plus a harmless live check of all five harnesses. If a harness is unavailable, mark that item unverified rather than inferring success. Do not push or open a PR without Ant's explicit approval.

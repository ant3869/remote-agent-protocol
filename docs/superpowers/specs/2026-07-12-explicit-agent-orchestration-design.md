# Explicit Agent Orchestration Design

## Goal

Treat the selected tool agent as the default for unnamed work, while allowing any single request to target another configured agent explicitly. Explicit targeting applies to one request only; it never changes the default unless the user asks to change the default.

## Voice Contract

The deterministic command parser recognizes natural one-turn forms:

- `Hermes, check my email.`
- `Use Hermes to check my email.`
- `Ask Code Puppy to inspect this repository.`
- `Have Codex diagnose Hermes.`
- `Tell Claude Code to run the tests.`

An unnamed tool request continues to use the selected default agent. A directly addressed conversational question such as `Hermes, what do you think about this?` also goes to Hermes for that turn. The next unnamed request returns to the default automatically.

Local orchestration controls do not launch an agent:

- `List agents` reports configured agents and availability.
- `What is my default agent?` reports the current default.
- `Make Hermes my default agent` changes and persists the default.

Existing aliases remain authoritative, including the explicit-only `hermes-yolo` alias. Unknown or unavailable names fail clearly and do not fall back silently.

## Architecture

Reuse the existing `AGENT_SPOKEN_ALIASES`, `parse_delegation`, `IntentRouter`, `VoiceSession`, and `AgentBridge` path. Extend deterministic parsing before heuristic or semantic routing; do not introduce an agent-planning layer or automatic model-based agent selection.

The parser returns the named backend and the user's task with only the addressing wrapper removed. `IntentRouter` records the decision as explicit. `VoiceSession` applies the existing confirmation, untrusted-context, lifecycle, result-relay, and persistence behavior. `AgentBridge` remains responsible for subprocess execution and per-backend session continuity.

Default-agent controls are handled by `VoiceSession` before delegation. The web status payload already exposes the default and configured backends; the Agents view should label the selected backend as `Default` and each job with its actual backend.

## Data Flow

1. A transcript or typed message enters the existing control/delegation processor.
2. Local orchestration controls are resolved first.
3. Explicit agent addressing is parsed deterministically.
4. If explicit, that backend receives only this request.
5. If no agent is named, normal routing uses the persisted default backend.
6. Existing destructive-action confirmation runs regardless of explicit or default routing.
7. Lifecycle and final results identify the actual backend used.

## Error Handling

- Unknown agent: report the name and list configured choices; do not dispatch.
- Configured but unavailable executable: preserve the bridge's fail-fast result and surface it plainly.
- Ambiguous alias: prefer the longest exact configured alias; never guess from partial names.
- Empty direct address such as `Hermes?`: keep it as chat unless a substantive request follows.
- Agent timeout or failure: report that backend's failure without changing the default or retrying through another agent automatically.

## Tests

Add focused regressions proving:

- unnamed work uses the runtime default;
- each supported explicit form overrides the default for one request;
- a following unnamed request returns to the default;
- directly addressed questions reach the named agent;
- longest aliases win (`hermes yolo` before `hermes`);
- unknown/empty addressing does not dispatch;
- destructive explicit work still requires confirmation;
- list/query/change-default controls stay local and persistence reflects a deliberate default change;
- job events and status payloads identify the actual and default agents separately.

## Deliberate Limits

There is no sticky direct-chat mode, agent-to-agent delegation, automatic agent selection, fallback agent, group chat, or workflow graph. Add those only after one-turn explicit routing is reliable and real usage demonstrates a need.

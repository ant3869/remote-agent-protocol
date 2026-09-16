# Conversation text and agent activity proposal

Status: accepted and implemented in RAP; external frontend acknowledgements remain
an integration boundary, described in [Conversation events](../../conversation-events.md).
Reviewed: 2026-09-13. Scope: the web conversation, local voice session, brain-mode
adapter, and agent event bridge.

## Recommendation

Make the conversation the reliable record of what the user, persona, and agents
say, with compact task cards explaining the work between those messages.
Keep every spoken utterance visible, identify its actual speaker and source,
and distinguish a full agent result from the persona's summary of it.

Use the existing graphite shell and agent inspector. The improvement should come
from better content, attribution, and grouping rather than another visual theme.

## Findings before implementation

Discovery used Codebase Memory `list_projects`, `get_architecture`, `search_graph`,
`trace_path`, and `get_code_snippet` against
`H-Program-Files-oss-remote-agent-protocol`. Some indexed source ranges were stale
even after `index_repository(mode="fast")`; the specific affected files were read
directly before drawing conclusions. This is a source review, not a live audio audit.

| Finding | Evidence | User-visible consequence |
| --- | --- | --- |
| Direct speech already has a text mirror. | `session_processors.py:413`, `TranscriptTap` handles `TTSSpeakFrame`. | Preserve this path; do not add a second independent copy. |
| Harness voice identity is lost before display. | `session.py:1422`, `_speak_agent_text` chooses a harness voice but queues a plain speech frame; `TranscriptTap` emits only role/text; `web_app/app.js:258` labels non-user transcripts with `currentPersona()`. | A harness can speak while its text is attributed to Jess or the selected butler. Persona changes can also affect the label of a delayed event. |
| Assistant text arrives late. | `TranscriptTap` buffers until `LLMFullResponseEndFrame`; `brain.py:199` yields speakable sentences but emits the transcript through `_finish_turn` after streaming. | Audio can begin before the matching text appears. Generation completion does not establish how much audio actually played. |
| Main-chat progress loses task identity. | `web_app/app.js:798`, `renderAgentEvent` retains only agent name and action/result text. | Concurrent jobs from the same agent look identical, and progress creates more chat rows. |
| Routing and consultations are not rendered explicitly. | `brain.py:404` emits `routing`; `agent_bridge.py` emits `agent_consult`; `web_app/app.js:247`, `handleEvent`, has no branches for either. | The reason for a handoff and who asked whom are hard to follow. Local consultation speech may still appear as generic assistant text. |
| Failure information is reduced in chat. | `renderAgentEvent` uses `result || summary || "No answer returned."`, without considering status or failure detail. | Failure, cancellation, and an empty successful result do not receive distinct main-chat presentations. |
| Reading position and history are fragile. | `renderChat` rebuilds the last 80 rows and always scrolls to the bottom; `web_gui.py` retains 800 mixed events and `_events_after` does not signal retention gaps. | New activity interrupts reading; older conversation text can disappear from view or be absent after reconnect/reload. |
| Useful structure already exists. | `AgentBridge._emit_job` includes job, agent, machine, state, action, tool, step, result, and failure fields; `renderAgentDetail` already exposes moves and output. | Reuse these facts and the existing inspector rather than inventing a parallel activity feed. |

A read-only Node reproduction executed the existing `renderAgentEvent` with two
Hermes jobs reporting “Checking sources” and a failed Codex job containing only
`failure_detail: "Connection refused"`. It produced two identical Hermes rows and
a Codex row saying “No answer returned.” No harness was launched.

Brain-mode completion relay is already partially implemented:
`BrainSessionAdapter._observe_event` publishes `agent_job_summary` to the external
frontend announcement queue. Do not treat the older continuity note about missing
relay as the complete current behavior. Actual external playback and any speech
generated independently by that frontend require separate validation.

## What belongs on screen

| Content | Default presentation | Details and rules |
| --- | --- | --- |
| User speech or typed message | Ordinary conversation row labeled **You**, with voice/text indicator. | Interim recognition is a draft; finalized input replaces it. Context held in the composer stays visibly unsent. |
| Persona speech | Full text in one growing row labeled **Jess · speaking** or the chosen persona. | Keep it after playback. Mark interrupted, queued, or text-only output accurately. |
| Harness speech | Full text labeled **Hermes · speaking**, plus the task reference. | Speaker identity comes from the event, not the currently selected persona or voice name. |
| Persona relaying an agent | **Jess · summarizing Hermes**, linked to the agent result. | The summary is its own actual utterance. Preserve the full result separately; do not present a paraphrase as a quotation. |
| Routing or handoff | Compact task row: **Assigned to Codex · you named Codex**. | Use an observed routing reason such as explicit request, configured default, or classifier decision. Avoid invented explanations. Distinguish pending assignment from started work. |
| Current action | One updating line inside the task card: **Reading configuration** or **Running tests**. | Show tool and step count when reported. A heartbeat updates “last heard” rather than creating a message or claiming new progress. |
| Decisions and blockers | Brief, attributed activity entry: **Codex reports: retrying after connection failure**. | Show reported plan summaries, meaningful decisions, constraints, and what input is needed. These are public explanations, not inferred private thoughts. |
| Agent consultation | **Codex → Hermes · asks** followed by **Hermes → Codex · answers**, grouped under the task. | Preserve both texts and their relationship. If voiced, link the utterance to this exchange. |
| Result | Task card with explicit terminal state, readable result, and available artifact/source links. | Differentiate completed, failed, cancelled, timed out, and completed without an answer. Keep partial results when a job fails. |
| Approval request | Inline **Needs your approval** card with agent, proposed action, scope, and controls. | Resolve in place to approved/denied/expired; preserve the decision. Never hide it in collapsed activity. |
| Runtime details | Expandable **Technical output** in the existing inspector. | Keep raw CLI chatter, protocol markers, prompts, and stack traces out of the ordinary conversation and TTS. Preserve existing sanitization. |

Show brief plans and reasons when the agent supplies them; otherwise say what is
observable, such as “Waiting for agent output.” Do not fabricate a thought stream,
progress percentage, or a successful result from elapsed time or exit code alone.

## Visual example

Illustrative content only; this is not a transcript of a real run.

```text
CONVERSATION                                  ACTIVE TASKS

You · voice                                  Codex · Fix transcript
Have Codex fix the transcript and ask         Running tests
Hermes to check the speaker labels.           Hermes · Check labels
                                             Reviewing attribution
Jess · spoken
I've assigned the transcript fix to Codex.

Codex · Fix transcript · Running tests
Assigned because you named Codex
  Codex → Hermes · asks
  Can you check the speaker labels?
  Hermes → Codex · answers
  Harness speech is currently labeled Jess.
  Activity (4)    Technical output

Hermes · speaking · Check labels
Harness speech is currently labeled Jess.

Codex · Fix transcript · Completed
Speaker identity now follows each message.
View full result    Open changed files

Jess · summarizing Codex · speaking
The transcript fix is ready to review.

[ Message or attach context…                 Send ]
```

Speech stays in the chronological conversation, even when its linked task card's
activity is collapsed. An agent's full result and the actual spoken summary remain
distinct. The same speech event arriving twice updates one row; two legitimately
repeated utterances remain two rows. Do not deduplicate by matching text.

Use neutral user rows, a clear persona name, and a restrained purple agent accent;
reserve green/yellow/red for labeled states and orange for delegation or caution.
Use name, icon, and state text together so color is never the only signal. Render
prose, lists, code, and links readably using safe formatting. Long results can fold
with an explicit **Show full result** control, but must remain accessible and copyable.

Autoscroll only while the reader is already near the bottom. Otherwise preserve
position and show a **New messages** button. On narrow screens, task details open
below their card; approval and speech remain accessible. Announce completed
sentences and important state changes accessibly rather than every token.

## Event and playback contract

Introduce an additive application-level conversation contract shared by both
session modes. Keep the vendored Pipecat framework unchanged.

- Identity: stable session, event, turn, message/utterance, and optional job IDs;
  consultation IDs and reply relationships when applicable.
- Attribution: snapshot the speaker's ID, kind, and display name when creating
  the utterance; independently record the source agent, relaying persona, and
  target agent. The selected persona is not historical message metadata.
- Content: distinguish utterance, activity, routing, consultation, result,
  approval, and system notice. Record the origin of an action/decision as a
  structured agent report, parsed CLI fallback, or host observation.
- Timing: event occurrence time and sequence, plus explicit partial/final state.
  Do not timestamp every progress entry with the job's start time.
- Delivery: track text-only, queued, playing, played, interrupted, failed, or
  playback-unconfirmed separately from whether generation has finished.

Publish sanitized speech text before its audio is eligible to play, then update
that same row. For local TTS, correlate output events with the utterance; direct
speech and streamed LLM speech must use the same identity and attribution rules.
On interruption, retain the partial text and distinguish the unplayed remainder
where playback information supports it. Do not claim word-exact playback without
word-level acknowledgements.

Brain mode needs an explicit external frontend agreement: preserve utterance and
source/job IDs through announcement requests and report actual spoken text plus
playback start/end/interruption. Any externally generated speech must also enter
this contract. Until those acknowledgements exist, show the generated text with
**Playback unconfirmed** rather than claiming it was heard. Visible TTS failures
must not erase the text or agent result.

Use a normalized conversation store with cursor-based replay and snapshot
recovery. Retain full results independently of high-volume telemetry. On a cursor
gap, recover a snapshot or show a history-gap notice. Preserve existing clear-chat
and retention intentions; any durable transcript storage must honor explicit
clearing and use the existing local data conventions.

## Suggested implementation sequence

1. **Attribution and truthful outcomes.** Add shared identities/source metadata;
   preserve them through direct speech, narration, brain announcements, and the
   web bridge. Render explicit failure/cancellation states and routing/consultation
   events. Keep backward compatibility for events without new metadata, labeling
   unknown historical speakers honestly. This addresses the concrete defects first.
2. **Conversation structure.** Replace repeated progress rows with job-keyed cards,
   link results and spoken relays, preserve approvals, and fix reading position.
   Keep the existing Agents page as the detailed inspection surface.
3. **Speech synchronization and recovery.** Add partial text updates, playback
   correlation, interruption handling, external frontend acknowledgements, and
   normalized replay. This phase completes the “everything spoken is visible”
   requirement across modes; it is not satisfied by merely showing final LLM text.

Primary implementation areas: `session_processors.py`, `session.py`, `brain.py`,
`brain_adapter.py`, `agent_bridge.py`, `web_gui.py`, and `web_app/app.js` with its
HTML/CSS. Add a small shared event model and frontend state reducer if needed.
The external audio frontend is a separate integration boundary and has not been
audited in this proposal. No additional model calls should be required to render
existing events; use the exact narration text already produced for speech.

## Acceptance checks for implementation

- Run the same event scenarios through local voice and brain mode: persona speech,
  harness voice, direct TTS, streamed answer, agent consultation, result relay,
  confirmation/denial, failure, timeout, cancellation, and TTS failure.
- Switch persona during queued speech and finish two jobs from the same harness
  together: speaker, job, result, and reply relationships must stay correct.
- Interrupt mid-sentence: retain visible partial output, prevent buffer carryover
  into the next turn, and mark playback honestly.
- Send repeated heartbeats, duplicate events, and out-of-order updates: avoid
  duplicate utterances, progress spam, and terminal-state regression.
- Reload/reconnect after enough events to exceed retention: recover conversation
  state or explicitly report a gap. Verify cleared text does not reappear.
- Verify all actual speech remains accessible with activity collapsed and on
  mobile, without scroll stealing; test keyboard access and screen-reader output.
- Test safe formatting and existing prompt/protocol filtering. Check that full
  results remain available even when a spoken summary is shorter or fails.
- During implementation run focused Python and JavaScript tests, Ruff, syntax/
  build checks, and browser verification; then perform real playback checks with
  each supported local/external voice path.

## Implementation outcome

Implemented attributable streamed speech, task-grouped activity and consultations,
full results, inline approvals, explicit outcomes and playback states, and bounded
snapshot recovery with clear-chat protection. The conversation preserves reading
position, disclosure state, and keyboard focus on desktop and mobile.

RAP supplies utterance metadata and an authenticated acknowledgement endpoint for
external frontends. The separate speech-to-speech checkout has not adopted this
contract; its generated speech remains labeled Playback unconfirmed. Synthetic
audio checks exercise local TTS and output queues, but live device and external
audio verification remain outstanding. No runtime configuration was changed.

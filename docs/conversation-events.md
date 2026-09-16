# Conversation text and playback

The web conversation retains attributable utterances and task cards independently
of audio telemetry and raw CLI output. Text appears while replies stream, then
updates the same row when generation finishes or playback changes. A persona
switch does not rename an existing utterance. Harness speech carries its own
speaker and task; persona relays carry a separate `source_agent` and relationship.

Task cards group progress, tool/step information, agent consultations, and the
full result. Failed, cancelled, timed-out, and empty successful jobs receive
distinct labels. Raw output remains in the Agents inspector. Approval cards keep
the proposed task and resolve in place. Text supports safe links, lists, inline
code, emphasis, fenced code, and copying; it is never inserted as HTML.

Shared `web_app/identity.css` defines user ice blue, persona coral, and harness
purple across conversation, persona controls, agent rosters, settings, and search.
Names are bold and message borders reinforce identity; state labels retain their
separate success/warning/error meaning. `ui-icons.js` supplies local SVG icons for
navigation and menus using the existing graphite theme. Copy and inspection
actions show icons with tooltips and accessible names; approval choices remain
explicitly labeled.

## Local speech

`ConversationEvents` creates session, message, turn, and event identities plus
speaker snapshots and occurrence timestamps. `TranscriptTap` emits partial/final
text with one `message_id`, after delegation-marker filtering. Direct
`TTSSpeakFrame` narration carries attribution in its `conversation` metadata.

Application `SpeechBoundaryFrame` markers pass through the TTS serialization
queue and local output transport. `SpeechPlaybackTap`, after output, uses these
ordered boundaries to associate audio with an utterance. Delivery states are:

| State | Meaning |
| --- | --- |
| `text_only` | The caller requested text without a playback path. |
| `queued` | Text is available and local speech is queued. |
| `playing` | Audio frames for the utterance passed the output transport. |
| `played` | Its ending boundary passed output after audio. |
| `interrupted` | Generation or output was interrupted; retain the available text. |
| `failed` | An identified playback failure was reported. |
| `playback_unconfirmed` | Playback was not acknowledged or no audio was observed. |

`played` reports delivery through the output device API, not acoustic verification
that a person heard it. Text is the generated utterance, not a word-exact account
of playback before an interruption. When a frontend reports `spoken_text`, the UI
preserves it separately from the generated text.

Transport audio is chunked and may retain a short trailing buffer across a
boundary. Delivery labels describe utterances at frame granularity, not sample-
or word-exact playback. An utterance with no observed output stays unconfirmed.

## Replay and clearing

`GET /api/events?after=<cursor>` continues to return `events`, `latest`, and
`status`. Conversation events additionally include `conversation_row` and
`conversation_epoch`; `conversation_processed` lets the browser avoid rendering
the legacy event a second time. Raw output and metrics have no conversation row.

An initial cursor, an expired cursor, or a cursor from a restarted server returns
a `conversation` snapshot containing `epoch`, `rows`, and `truncated`. The browser
restores that snapshot and uses the same response's `latest` cursor. Snapshot
rows already incorporate the accompanying events, which must not be replayed
into the conversation again. Non-conversation state can still consume them.

The store retains 1,200 rows per running web process, with up to 60 activity entries
per task, separately from the 800-event transport buffer. An explicit notice marks
conversation truncation. This snapshot survives browser reloads and reconnects;
it does not persist the full conversation UI across application restarts. Existing
LLM memory and job-history persistence remain separate.

New chat and clearing short-term memory clear the snapshot and replay buffer,
advance the conversation epoch, and retire source session IDs. Late output from
an earlier turn cannot restore cleared text. Existing task inspection/history is
still available in the Agents view. Scrolling follows the active scroll container
on desktop and mobile; reading older messages exposes a New messages button.

## External frontend contract

Brain streaming publishes visible text before yielding the corresponding text
chunk. OpenAI-compatible completion responses and content chunks carry an
additive top-level `rap` object:

```json
{
  "message_id": "opaque-utterance-id",
  "session_id": "opaque-source-session-id",
  "turn_id": "opaque-turn-id",
  "speaker_id": "Jess",
  "speaker_name": "Jess",
  "source_agent": "hermes",
  "job_id": "opaque-job-id",
  "relation": "summary"
}
```

The external frontend must preserve this object through its LLM, TTS, and output
queues. Send acknowledgements to the **GUI brain server** at
`POST /api/speech-events`, using the same bearer authentication as the existing
S2S telemetry routes:

```json
{
  "message_id": "opaque-utterance-id",
  "session_id": "opaque-source-session-id",
  "sequence": 1,
  "delivery": "playing"
}
```

Increment `sequence` for each update to that utterance. Report `played` only after
buffered audio finishes, or `interrupted`/`failed` when appropriate. Optional
`spoken_text` records the frontend's actual transcript. Unknown identities,
wrong source sessions, repeated/out-of-order sequences, and terminal-state
regressions are rejected with HTTP 409; authentication failures return 401.
Accepted requests return `{"ok": true}`. Retry a transient failure using the same
sequence; a repeated accepted acknowledgement may return 409 and must not be
reissued under a new utterance identity.

Speech generated independently by the frontend can create a row by providing
`source: "external"`, a unique `message_id`, nonempty `text`, `speaker_name`, and
the current `conversation_epoch` from `status.conversationEpoch`, together with
its initial sequence and delivery state. Subsequent updates use that epoch as
their `session_id`. Never reuse an ID or replay old speech into a new epoch.

Agent announcement queue entries include source/job metadata and an opaque
announcement reference in the `[[announce]]` request. The brain resolves the
reference to its stored metadata, strips it before generation, and marks the
reply as a summary. It does not infer attribution from the summary's words.

The existing separate speech-to-speech checkout has not been modified to pass
through and acknowledge this extension. Until a frontend implements it, generated
brain text remains visible as **Playback unconfirmed**; RAP does not claim it was
spoken. Independent external speech requires the explicit reporting contract
above. The headless bridge carries `rap` metadata but has no GUI conversation or
speech-event endpoint.

## Verification

Use the existing repo environment for the integration suite:

```powershell
.venv/Scripts/python -m pytest tests/test_conversation.py tests/test_conversation_speech.py tests/test_brain_streaming.py tests/test_s2s_voice_control.py
node --test tests/js/conversation.test.mjs
```

`Dockerfile.conversation` supplies isolated pure-contract and JavaScript checks;
these targets do not need a microphone, models, API keys, or the app's optional
audio dependencies. Full local/external audio validation additionally requires
the configured services and the external acknowledgement integration.

Browser checks used simulated events at desktop and mobile widths and covered
safe formatting, task grouping, reading position, and disclosure/focus retention.
Synthetic speech exercised consecutive harness utterances through the real TTS
serialization and base output queues without sending audio to a physical device.

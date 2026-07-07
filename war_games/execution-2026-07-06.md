# Wargame execution report — 2026-07-06

Execution used the safe defaults approved in `ledger.md`. No abort condition
triggered.

## Mediator universal delegation

1. Added an adversarial explicit-command/false-positive corpus; direct commands
   exceed the approved 95% target and status chatter remains chat.
2. Extended the existing tiered router only: directly addressed aliases route
   deterministically, classifier failures still fail closed, and concrete
   coding tasks prefer Code Puppy when configured.
3. Delegated execution prompts now include at most six recent user/assistant
   messages, capped at 1,600 characters and labeled as untrusted reference
   context; obvious credential-shaped messages are excluded.
4. Backend choice remains allowlisted: explicit aliases win, coding work can
   select Code Puppy, and all other work keeps the persona/default backend.
5. Pending corrections revise the held confirmation; active corrections
   cancel-and-replace the newest job. A start/cancel race check proves a
   superseded subprocess cannot launch alongside its replacement.
6. Existing confirmation, lifecycle, direct-TTS status, and markerless-promise
   paths remain the single handoff mechanism.
7. Timeout, invalid classifier response, unavailable backend, start failure,
   destructive confirmation, correction, and lifecycle regressions are covered
   by the focused application suite.

## Multi-wake-word persona routing

1. Added `WAKE_WORD_PERSONAS_JSON`, per-model targets, validation against known
   personas, and backward-compatible single-model fallback.
2. One openwakeword `Model` loads every resolved local target. This machine has
   only `hey_jarvis`; absent secondary models are logged and skipped per ledger.
3. Eligible scores use per-target thresholds; highest confidence wins and
   configuration order breaks ties.
4. The async persona callback is awaited with a 500 ms ceiling before the wake
   window opens or later command audio can pass downstream.
5. The trigger chunk stays dropped; pipeline ordering buffers later frames
   during the bounded switch. Switch failure keeps the gate armed.
6. Detection continues during an open window so another installed wake model
   can switch personas; repeated selection of the current persona avoids
   redundant LLM/TTS updates.
7. Synthetic multi-model ordering/failure/re-wake checks pass. The installed
   Jarvis ONNX model loaded in 0.520 s and processed a warm 80 ms chunk in
   1.189 ms. A real room-audio phrase matrix still requires a second local wake
   model and operator speech samples; v1 therefore exposes only locally found
   targets rather than claiming unavailable Jess-model coverage.

## WebSocket lifecycle events

1. Added a v1 allowlisted JSON envelope with stable lifecycle names, UTC time,
   per-session sequence, and no raw output.
2. Each client has a bounded independent queue; overflow closes only that client
   with code 1013.
3. The existing `websockets` dependency starts on the voice-session loop at
   `ws://127.0.0.1:8765/events`.
4. Clients receive future events only; wrong paths close with code 1008 and
   disconnect cleanup is isolated.
5. One synchronous ingress point assigns sequence and fans out in callback
   order.
6. Session shutdown closes clients and awaits server closure before bridge
   cleanup; a replacement server can immediately reuse the port.
7. Real local socket checks covered two clients, abrupt disconnect, slow-client
   overflow, invalid path, bind collision/degraded status, and port reuse.

## Verification and existing boundary

- The complete application-focused suite passed: 294 tests, with one existing
  Pipecat deprecation warning. Ruff lint/format and compileall are clean.
- A wheel build succeeds, but the existing upstream packaging configuration
  still includes only `pipecat`, not `remote_agent_protocol`; application-wheel
  packaging remains the separate work item already recorded under `plans/`.

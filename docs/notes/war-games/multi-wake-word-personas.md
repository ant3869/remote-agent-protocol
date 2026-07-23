# Wargame: Multi-Wake-Word Persona Routing

Source brief: `tasks/multi-wake-word-personas.md`

## Mission boundary

`WakeWordGate` currently uses one `WakeWordSettings`, opens the microphone when any detector score crosses one threshold, and emits only gate state plus the configured model. Persona changes already flow through `VoiceSession.set_persona`/`_apply_persona`, which queues LLM and TTS setting frames. The critical ordering requirement is therefore: identify the winning model, apply its persona settings, then release command audio to STT.

## Move 1 — Define and validate the mapping

**Move / Action:** Parse one mapping from wake model name to existing persona name, with optional per-model threshold. Reject duplicates, unknown personas, missing model files, and an empty mapping when wake mode is enabled.

**Expected Observation:** Startup reports every armed wake model and persona; invalid rows produce one actionable preflight error before audio starts. Existing single-model configuration remains a one-entry mapping.

**Likely Failure & Counter Move:** Human phrases (“Hey Jess”) do not match openwakeword score keys/model identifiers. Store an explicit model ID and display label separately; compare only normalized model IDs returned by the detector.

**Forks & Triggers:** If backward compatibility is required, translate the current `WAKE_WORD_MODEL` into one mapping entry. If no valid entry remains, use the existing safe preflight behavior rather than partially arming.

## Move 2 — Prove one detector can load all models

**Move / Action:** Pass the configured model list to one openwakeword detector and add a fake-detector check that returns independent scores for two models in the same chunk.

**Expected Observation:** Both score keys are visible for every prediction chunk; memory/CPU remain within the agreed budget; a missing second model fails preflight rather than degrading to the first model silently.

**Likely Failure & Counter Move:** The installed openwakeword version or custom model format cannot load the full list. Confirm the installed API locally; if multi-model construction is unsupported, create one detector per model only after measuring that real-time processing still meets the audio deadline.

**Forks & Triggers:** If one-detector loading works, keep it. If separate detectors overrun an 80 ms chunk budget, abort multi-model mode rather than dropping audio.

## Move 3 — Select exactly one wake winner

**Move / Action:** Replace `any(score >= threshold)` with deterministic winner selection: eligible scores only, highest normalized margin above that model’s threshold, then stable configuration order as the tie-breaker. Emit `model`, `persona`, and score metadata.

**Expected Observation:** A fake chunk with Jess above threshold selects Jess; Jarvis selects Jarvis; simultaneous hits always choose the same winner. Below-threshold chunks keep the gate armed.

**Likely Failure & Counter Move:** Crosstalk causes both models to trigger on one phrase. Add a configurable minimum margin between first and second scores; when the margin is not met, remain armed and log an ambiguous detection without switching.

**Forks & Triggers:** If false activations cluster around one model, tune its threshold independently. If ambiguity remains common, require another wake phrase rather than guessing a persona.

## Move 4 — Apply persona before releasing speech

**Move / Action:** Make wake detection invoke an async persona-switch callback and await `VoiceSession._apply_persona` before marking the gate awake. Buffer command audio arriving during the switch, then release it downstream in order once LLM/TTS settings have been queued.

**Expected Observation:** The first transcription after “Hey Jarvis” sees Jarvis as the session persona and the next response uses Jarvis model instructions and voice. No command frames reach STT while the switch is pending.

**Likely Failure & Counter Move:** The callback is currently synchronous and frame setting updates are asynchronous. Change only the wake callback boundary to async; do not switch through the Tk GUI. On apply failure, discard buffered command audio, remain armed, and emit a spoken/GUI failure.

**Forks & Triggers:** If setting frames have an acknowledgement mechanism, wait for it. If they do not, queue them before releasing buffered audio and verify ordering with a pipeline test.

## Move 5 — Preserve utterance boundaries

**Move / Action:** Decide whether the wake phrase audio itself is discarded and retain only audio after the detected chunk. Bound the switch buffer by time/bytes and keep VAD state consistent while switching.

**Expected Observation:** STT receives “what time is it,” not “hey Jarvis what time is it”; the first word after the phrase is not clipped; a long or stalled switch cannot grow memory without bound.

**Likely Failure & Counter Move:** Detection occurs after command speech has already entered the same 80 ms chunk. Retain a small post-trigger tail and test real recordings; tune overlap based on observed clipping rather than adding an unbounded preroll.

**Forks & Triggers:** If the STT reliably strips wake phrases, passing the trigger chunk is acceptable. Otherwise discard it and document the short pause required after the wake phrase.

## Move 6 — Handle re-wake, collision, and active conversations

**Move / Action:** Permit a different configured wake word to switch persona even while the conversation window is awake, while preventing ordinary audio from feeding the detector twice. Define the newest completed wake detection as authoritative.

**Expected Observation:** “Hey Jess” during a Jarvis session changes the next response to Jess without GUI input; repeated “Hey Jess” refreshes the window without redundant TTS/model churn.

**Likely Failure & Counter Move:** The current awake path bypasses `_listen`, so re-wake cannot be detected. Feed awake audio to detection without withholding it, and suppress the detected wake phrase at the transcript/turn boundary if needed.

**Forks & Triggers:** If continuous awake detection causes CPU or false-positive regressions, only allow persona changes after re-arm. If a persona switch is already pending, cancel/replace it before releasing any command audio.

## Move 7 — Validate with synthetic and real audio

**Move / Action:** Add one focused detector/gate test for each persona, ties, ambiguity, apply failure, buffering, and re-wake. Run live recordings for both phrases and inspect emitted wake, transcript, persona, LLM setting, and TTS setting order.

**Expected Observation:** Each success path produces `wake detected -> persona applied -> audio released -> transcript -> matching voice`; disconnect/rebuild restores the configured default and rearms all models.

**Likely Failure & Counter Move:** Synthetic scores pass but room audio cross-triggers. Capture score distributions from real samples without storing raw audio by default, tune thresholds, and rerun the same phrase/noise matrix.

**Forks & Triggers:** If one model cannot meet the false-activation target, remove that mapping from release. If switch latency clips commands, require a short chime/pause or preload persona voices before enabling the feature.

## Abort Conditions

- Abort startup of multi-wake mode if any configured persona or model identity is unresolved.
- Abort a detected turn when persona application fails; do not answer with the previous persona.
- Abort release if command audio can reach STT before the chosen persona settings are queued.
- Abort multi-model mode if detector work cannot consistently finish within the incoming audio chunk budget.
- Abort automatic winner selection when simultaneous-model ambiguity exceeds the agreed margin.
- Abort release if real-audio tests show material phrase clipping or cross-persona false activation.

# Wake word research for Jess

Goal: add a local wake word gate before Jess starts treating speech as a user turn.

## Shortlist

| Option | Repo | Fit | Notes |
|---|---|---|---|
| openWakeWord | https://github.com/dscripka/openWakeWord | Best OSS fit | Apache-2.0, local, Python, ONNXRuntime-based, PyPI package available. Dry-run install on this venv is modest: openwakeword, scikit-learn, narwhals, threadpoolctl. |
| Picovoice Porcupine | https://github.com/Picovoice/porcupine | Best commercial/mature fit | Very mature and cross-platform, but typically needs an access key/account. Good fallback if openWakeWord accuracy disappoints. |
| wyoming-openwakeword | https://github.com/rhasspy/wyoming-openwakeword | Good process-isolated option | Wraps openWakeWord as a Wyoming server. Useful if we want wake detection as a separate service later. |
| Mycroft Precise | https://github.com/MycroftAI/mycroft-precise | Not preferred | Older RNN listener. Less attractive than openWakeWord now. |
| Snowboy | https://github.com/Kitt-AI/snowboy | Avoid | Old/unmaintained ecosystem, awkward Windows story. |

## Recommendation

Use `openwakeword` first.

Reasons:

- Fully local/offline.
- Apache-2.0.
- Python package exists.
- Uses ONNXRuntime, which this environment already has.
- No cloud key / account dependency.
- Can be integrated either directly as a `FrameProcessor` or as a sidecar later.

## Proposed architecture

Do not replace STT or VAD. Add a gate before the expensive/meaningful path:

```text
mic -> WakeWordGate -> MicGate -> Whisper STT -> delegation/chat
```

Initial behavior:

- Default disabled until tested.
- When enabled, ignore audio until wake phrase fires.
- After wake, allow one user turn through.
- Re-arm after Jess finishes speaking or after a timeout.
- Keep GUI mute as a hard override above wake word. Mute always wins.

## Config sketch

```python
WAKE_WORD_ENABLED = False
WAKE_WORD_ENGINE = "openwakeword"
WAKE_WORD_MODEL = "hey_jarvis"  # or a custom model later
WAKE_WORD_THRESHOLD = 0.5
WAKE_WORD_ACTIVE_WINDOW_SECS = 12
```

## Current prep status

`openwakeword==0.6.0` is installed in this venv and preflight reports:

```text
openwakeword ready (hey_jarvis, threshold 0.5)
```

Wake-word mode is still disabled by default so the existing always-listening mode remains available.

To enable later, add this to `.env`:

```text
WAKE_WORD_ENABLED=true
WAKE_WORD_ENGINE=openwakeword
WAKE_WORD_MODEL=hey_jarvis
WAKE_WORD_THRESHOLD=0.5
WAKE_WORD_ACTIVE_WINDOW_SECS=12
```

The GUI now shows a Wake status card. With the default config it says `wake word disabled`; if enabled and the dependency is present, it reports readiness.

## Next implementation step

Write a small microphone/file probe to measure false positives before wiring it into the live Pipecat pipeline. Do not shove it directly into production audio like a raccoon with a soldering iron.

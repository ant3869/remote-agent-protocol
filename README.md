# remote-agent-protocol

**Remote Agent Protocol** is a local-first desktop voice switchboard for
conversations and background agent work. It combines live speech, Ollama,
selectable personas (the default assistant is Jess), persistent memory, and
deterministic routing to Hermes, Code Puppy, OpenClaw, or any command-line
agent configured by the operator.

The application lives in the [`remote_agent_protocol/`](remote_agent_protocol/)
package and uses the vendored Pipecat framework under `src/pipecat` for its
real-time audio pipeline.

## Features

- Voice and typed input share one brain: STT → delegation routing → memory →
  Ollama → TTS, with live persona/voice/model switching from the local web UI.
- The composer can bundle held voice, typed notes, links, images, and files into
  one reviewed prompt before the assistant or a delegated agent sees it.
- Voice modes cover Free Talk, Wake Word, and Push To Talk, with the selected
  mode persisted across restarts.
- Agent jobs run as background subprocesses, stream output without blocking
  voice, follow a normalized lifecycle (started / in progress / tool running /
  waiting / blocked / completed / failed), and are announced out loud.
- Destructive or elevated delegations are held for spoken or clicked
  confirmation before they run. If a one-shot backend asks for permission and
  exits, approval safely relaunches the same task instead of reporting success.
- Rapid corrections such as “wait, actually use httpx” cancel and replace the
  newest agent job; delegated prompts carry a bounded, explicitly untrusted
  conversation snapshot so contextual references survive the handoff.
- Completed jobs speak the agent's substantive result, while live tool, step,
  and last-completed fields continue advancing in the Agents panel.
- The selected tool user is the default for unnamed work, not the only agent:
  address Hermes, Code Puppy, Codex, or Claude Code explicitly for one request,
  then unnamed work returns to the persisted default automatically.
- The companion avatar is a locally rendered holographic butler with expressive
  gaze, phrase-shaped lip sync, state-aware scanlines/glitches, quality tiers,
  reduced-motion behavior, and no CDN dependency.
- Optional multi-wake persona routing (`WAKE_WORD_ENABLED=true` in `.env` or
  Wake Word mode in the UI): locally installed openwakeword models are matched
  to personas, repo-local models under `remote_agent_protocol/wake_word/wake_models` are discovered,
  the highest-confidence trigger wins, and persona/model/voice settings are
  queued before command audio reaches STT. Missing secondary models are skipped.
- External dashboards can subscribe to future agent lifecycle events at
  `ws://127.0.0.1:8765/events`. The v1 stream is loopback-only and excludes raw
  agent output; see [the lifecycle API](docs/lifecycle-websocket.md).
- Transcript memory plus semantic (mem0 + Qdrant) memory, both 100% local.
- `Ctrl+L` focuses typed input, `Ctrl+M` toggles the microphone, and `Ctrl+K`
  opens the agent task console. The EXPORT button writes a diagnostics bundle.
- Devices, models, voices, and agent backends are configurable from `.env`
  without editing Python -- see the "Remote Agent Protocol" section of
  `env.example`.
- Conversation memory, vector state, and job history live under ignored
  `data/`; voice-stack subprocess output lives under ignored `logs/`.
- A low-VRAM **brain mode** (`RAP_MODE=brain`) drops the local audio graph and
  exposes routing, delegation, confirmations, personas, and short-term
  transcript context over an OpenAI-compatible endpoint, so an external
  realtime speech-to-speech frontend can own the microphone and speakers.
  Free Talk/Wake Word state, mute, voice, latency, avatar envelopes, and queued
  agent-result narration synchronize across the process boundary.
- The command-frame web UI replaces generic dashboard grids with focused
  Control Center, Agents, Personas, Memory, Settings, Status, and Setup views,
  compact navigation rails, a live runtime line, and accessible input-state
  feedback. See "Brain mode" below for the external-audio topology.

## Requirements

- Windows 11 (the GUI and audio path are developed and tested there), Python 3.12.
- [Ollama](https://ollama.com) running locally with at least one chat model
  (see [docs/MODELS.md](docs/MODELS.md) for the model map).
- A microphone and speakers. A CUDA GPU is optional but makes Whisper STT fast.

## Setup

```bat
:: 1. Create the virtual environment (once)
python -m venv .venv
.venv\Scripts\pip install -e ".[local,silero,whisper,moonshine,kokoro,openai]"
.venv\Scripts\pip install mem0ai ollama openwakeword

:: 2. Configure (optional -- sensible defaults work out of the box)
copy env.example .env

:: 3. Make sure Ollama is serving and the configured model is registered
ollama list
```

The editable install (`pip install -e .`) now includes the `remote_agent_protocol`
package itself, not just the vendored `pipecat` framework -- a built wheel
contains both. `kokoro` is one of the extras selected above, since
`persona_tts.py` imports it at module load; it is not something you can skip
by omitting it from the extras list. Note that neither the wheel nor the
editable install bundles Ollama models or agent CLIs (`hermes`, `code-puppy`,
etc.) -- those remain external tools you install and configure separately.

Optional Coqui TTS: select `coqui` in the web Settings page or set
`TTS_BACKEND=coqui`. The app can use either an installed `TTS` package or the
repo-local `TTS/` checkout via `COQUI_TTS_SOURCE_DIR`; Coqui upstream currently
targets Python `<3.12`, so the status panel is the source of truth for whether
the local environment can import and run it.

Brain mode additionally needs the paired `speech-to-speech` checkout, its
`.venv`, Kokoro support, spaCy's `en_core_web_sm`, and the RAP-aware server/client
launchers. Put that checkout beside this repository or set `S2S_HOME`; follow the
[external speech integration guide](docs/notes/speech-to-speech-integration.md)
for exact installation and compatibility requirements.

## Run

| What | Command |
| --- | --- |
| Web control center | `scripts\start_gui.bat` or `.venv\Scripts\python -m remote_agent_protocol` |
| Terminal mode (no GUI) | `scripts\start_terminal.bat` or `.venv\Scripts\python -m remote_agent_protocol.terminal` |
| Brain mode, whole stack | `scripts\start_voice.bat` or `.venv\Scripts\python -m remote_agent_protocol.voice_stack` |
| Brain endpoint only (headless) | `scripts\start_brain_bridge.bat` or `.venv\Scripts\python -m remote_agent_protocol.openai_bridge` |
| Startup doctor (read-only checks) | `.venv\Scripts\python -m remote_agent_protocol.doctor` |
| List audio devices | `.venv\Scripts\python scripts\list_audio_devices.py` |
| App tests | `.venv\Scripts\python -m pytest tests\test_agent_bridge.py tests\test_session_controls.py ...` |
| Avatar/web UI tests | `node --test "tests\js\*.test.mjs"` |

The startup doctor checks Python version, Ollama reachability and whether the
configured chat/intent models are actually registered, the selected TTS
backend, the STT/TTS/wake-word Python packages, any explicitly configured
audio device indices, and each agent backend's executable -- all read-only.
It never installs a package, downloads a model, launches a service, or edits
configuration; it only reports what it finds. Exit code `0` means everything
configured is healthy, `1` means at least one check failed.

## Brain mode

Full mode runs the whole pipeline in one process. On a GPU that cannot hold
Whisper, Ollama, and a TTS model at once, `RAP_MODE=brain` drops the local audio
graph and keeps the brain -- routing, delegation, confirmations, memory, agent
lifecycle, personas -- behind an OpenAI-compatible endpoint that an external
realtime speech-to-speech frontend calls for every turn:

```text
frontend mic -> frontend VAD/STT -> RAP brain endpoint -> frontend TTS -> frontend speakers
```

`scripts\start_voice.bat` runs the whole arrangement. It allocates dynamic
loopback ports and a random bridge key, then starts the GUI brain, frontend
server, and frontend client as hidden logged subprocesses. It advances only
after the brain initializes, a temporary Realtime WebSocket handshake completes
and releases its pool slot, and the audio client opens its streams and receives
its own `session.created` event. Output is written to `logs/` for diagnosis.
It refuses to start while Remote Agent Protocol is already running. The
frontend checkout is found beside this repo automatically; set `S2S_HOME` if it
lives elsewhere.

`scripts\start_brain_bridge.bat` serves the same endpoint headlessly, with no
GUI and no lifecycle WebSocket, for pointing any other OpenAI-compatible client
at the brain. Run one or the other -- both bind `S2S_BRIDGE_PORT`.

Because the ears and mouth are out of process, brain mode uses a small local
control plane instead of lying about controls it cannot reach:

| Capability | Cross-process contract |
| --- | --- |
| Mute | RAP writes a generation-tagged command to `S2S_MIC_MUTE_FILE`; the client echoes it in `S2S_MIC_MUTE_STATUS_FILE` within `S2S_MIC_MUTE_ACK_TIMEOUT` or the control reports unconfirmed. |
| Output voice | RAP writes `S2S_VOICE_FILE`; the client sends a voice-only `session.update`. |
| Free Talk / Wake Word | Generation-tagged requests and acknowledgements use `S2S_VOICE_MODE_FILE` and `S2S_VOICE_MODE_STATUS_FILE`; failures roll back visibly. |
| Push To Talk | Reported unavailable because the external client owns microphone capture. |
| Avatar mouth | Playback envelopes POST to authenticated `/api/avatar-envelope`. |
| Input and latency | The client POSTs input phases and measured STT/response/audio timing. |
| Agent results | Immutable files under the `S2S_ANNOUNCE_FILE` queue are narrated once, in order. |

Brain mode supports conversation/transcript context but not semantic mem0 writes;
the Remember control reports that boundary. See the
[integration guide](docs/notes/speech-to-speech-integration.md) and `env.example`
for the full contract and every knob.

## Agents on another machine

Agent backends are normally commands on this PC. A second machine can offer its
own instead: run the host there, and its agents show up here as
`<host>:<agent>`.

On the machine with the agents (the laptop):

```bat
set AGENT_REMOTE_TOKEN=<shared secret>
python -m remote_agent_protocol.remote_host --host 0.0.0.0 --port 8790
```

On this machine, in `.env`:

```ini
AGENT_REMOTE_TOKEN=<the same secret>
AGENT_REMOTE_HOSTS_JSON={"laptop":{"url":"http://192.168.1.50:8790"}}
```

RAP then discovers what that machine offers, re-checks it every
`AGENT_REMOTE_HEARTBEAT_SECS`, and lets you delegate to `laptop:hermes` exactly
as you would to a local `hermes` -- same status protocol, same progress
heartbeats, same cancellation, with the Agents panel showing which machine ran
the job. A host that stops answering drops out of delegation until it is back,
and `python -m remote_agent_protocol.doctor` reports every configured host.

Three deliberate limits: every request carries the shared bearer token and a
host without one refuses to start; the host offers only the agent *names* it is
already configured with, never an arbitrary command; and it binds loopback
unless launched with an explicit `--host`.

## Repository layout

| Path | Purpose |
| --- | --- |
| `remote_agent_protocol/` | The application package: web UI (`web_gui.py` + `web_app/`, including the avatar), voice session, agent bridge, memory, personas, config |
| `remote_agent_protocol/wake_word/` | Optional repo-local wake models and training helpers for openwakeword |
| `remote_agent_protocol/models/` | Ollama Modelfiles for local GGUFs |
| `remote_agent_protocol/config_examples/` | `persona_overrides.example.json` -- template for `data/persona_overrides.json` |
| `src/pipecat/` | Vendored Pipecat framework (merge from the `upstream` remote; do not mix app code in) |
| `tests/test_*.py` | App unit tests live alongside the framework's tests |
| `tests/js/` | Avatar and web-UI tests, run with `node --test "tests\js\*.test.mjs"` (no npm dependencies; a bare directory argument makes Node try to *import* the folder) |
| `scripts/` | `start_gui.bat`, `start_terminal.bat`, `start_voice.bat`, `start_brain_bridge.bat`, `mock_agent.py`, `smoke_agent_bridge.py`, `list_audio_devices.py`, plus upstream tooling |
| `voice_probe/` | Text-driven harness for exercising the routing/delegation/confirmation mediator |
| `data/` | Runtime state (conversation memory, vector store, job history, frontend control files) -- gitignored |
| `logs/` | Voice-stack child-process logs -- gitignored |
| `docs/` | [Architecture](docs/architecture.md), [model map](docs/MODELS.md), [brain/S2S integration](docs/notes/speech-to-speech-integration.md), [wake-word research](docs/wake_word_research.md), [vendored framework README](docs/README.pipecat.md) and changelog, assets, `docs/notes/` |
| `VERSION`, `CHANGELOG.md` | Product version and changelog (`docs/CHANGELOG.pipecat.md` is the framework's) |

See the [architecture guide](docs/architecture.md) for the application
boundary, configuration, known limits, and product roadmap.

## Animated companion

The web Control Center includes an optional local frame-based butler companion.
The bundled butler uses a Canvas 2D renderer and individual
`runtime_512_v1/*.webp` frames for emotional state, gaze, blink, mouth shape,
materialization, and glitch effects. It reacts to wake detection, user speech,
transcription, thinking, agent jobs, errors, and assistant speech. Mouth movement
uses normalized local TTS or external-playback envelopes; raw audio never reaches
the browser.

Configure it under **Settings → Animated avatar**. Disabling the feature removes
the renderer and envelope connection. Motion can follow the operating-system
reduced-motion preference or be explicitly reduced. Generation guards and
visibility-aware disposal prevent stale or hidden scenes from replacing the
active avatar.

Avatar assets live under `remote_agent_protocol/web_app/assets/avatars/<avatar-id>/`.
The bundled `butler` uses the dedicated frame renderer; other safe local avatar
IDs may use the vendored Three.js/GLTF path. Missing or malformed assets fall
back without blocking the rest of the application.

## Pipecat foundation

Remote Agent Protocol preserves Pipecat's BSD-2-Clause license and upstream
framework history. The framework's own README lives at
[docs/README.pipecat.md](docs/README.pipecat.md) and its changelog at
[docs/CHANGELOG.pipecat.md](docs/CHANGELOG.pipecat.md); both describe
`src/pipecat`, not this application.

## Contributing

- **Found a bug?** Open an [issue](https://github.com/ant3869/remote-agent-protocol/issues)
- **Contributing to the vendored Pipecat framework** (`src/pipecat`)? See its own
  [CONTRIBUTING.md](src/pipecat/CONTRIBUTING.md) and merge upstream changes from
  the `upstream` remote (https://github.com/pipecat-ai/pipecat) rather than
  editing framework internals directly.

# External speech-to-speech integration

`speech-to-speech` exposes an OpenAI Realtime-compatible WebSocket voice server:

```text
client microphone/audio -> speech-to-speech VAD/STT -> OpenAI-compatible LLM -> speech-to-speech TTS -> client audio
```

Remote Agent Protocol already owns a complete local Pipecat voice stack:

```text
microphone -> wake gate -> STT -> intent router -> memory -> Ollama -> TTS -> speakers
```

Do not run both full audio stacks at once. That causes duplicate mic capture, duplicate VAD, duplicate TTS, and confusing transcript ownership.

## Recommended architecture

Use `speech-to-speech` as an optional external voice frontend, and make Remote Agent Protocol the OpenAI-compatible "brain" endpoint.

```text
OpenAI Realtime client
  -> speech-to-speech server
     -> STT/transcript
     -> RAP brain endpoint (/v1/chat/completions or /v1/responses)
        -> intent router
        -> memory
        -> agent bridge
        -> Ollama/persona response
     -> speech-to-speech TTS/audio
  -> client playback
```

This preserves Remote Agent Protocol's value: deterministic delegation, confirmations, agent lifecycle, personas, short-term transcript context, and GUI state. Semantic mem0 writes remain a full-mode capability and are reported unavailable in brain mode. `speech-to-speech` contributes its realtime audio transport/STT/TTS without replacing the app's brain.

## Implemented architecture

The low-VRAM bridge is implemented as app code, not vendored Pipecat code:

- `remote_agent_protocol/brain.py` -- text-only RAP coordinator: routing, short-term memory, confirmation gate, agent bridge, and Ollama response generation. It does **not** start local mic, STT, TTS, or speakers.
- `remote_agent_protocol/openai_bridge.py` -- loopback OpenAI-compatible HTTP server with:
  - `GET /health`
  - `GET /v1/models`
  - `POST /v1/chat/completions`
  - streaming SSE compatibility (`stream: true`) with sentence chunks forwarded as the brain produces them, followed by `[DONE]`.
- `scripts/start_brain_bridge.bat` -- Windows launcher.

Config knobs:

```dotenv
S2S_BRIDGE_PORT=8788
S2S_BRIDGE_API_KEY=local
S2S_BRIDGE_MODEL=remote-agent-protocol
S2S_BRIDGE_STREAMING=true
S2S_MIC_MUTE_FILE=data/s2s_mic_muted.flag
S2S_VOICE_FILE=data/s2s_voice.txt
S2S_VOICE_MODE_FILE=data/s2s_input_mode.json
S2S_VOICE_MODE_STATUS_FILE=data/s2s_input_mode_status.json
S2S_VOICE_MODE_ACK_TIMEOUT=8.0
S2S_ANNOUNCE_FILE=data/s2s_announce.json
```

## GUI controls in brain mode

Brain mode moves the ears and mouth out of process, so the frontend and GUI use
an explicit local control/telemetry contract:

| Capability | Mechanism |
| --- | --- |
| Mute | RAP writes `S2S_MIC_MUTE_FILE`; the client polls it and drops mic chunks. Startup health remains unavailable if the initial muted state cannot be published, and configured-file read errors fail closed. |
| Voice | RAP writes `S2S_VOICE_FILE`; the client polls it and sends a voice-only `session.update`. |
| Free Talk / Wake Word | RAP writes a generation-tagged request to `S2S_VOICE_MODE_FILE`; the client loads the detector and acknowledges through `S2S_VOICE_MODE_STATUS_FILE`. A timeout or error rolls the GUI back to its last confirmed mode. |
| Push To Talk | Explicitly unavailable because the external process owns microphone capture. |
| Avatar mouth | The client samples speaker playback and POSTs loudness to authenticated `/api/avatar-envelope`. |
| Input phase | The client POSTs wake/listening/transcribing/responding/error state to `/api/input-state`. |
| Turn timing | Measured STT, response-start, and first-audio timing is POSTed to `/api/turn-timing`. |
| Agent narration | Completed jobs publish immutable queue files beside `S2S_ANNOUNCE_FILE`; the client claims and narrates each result exactly once. |

Loudness is sampled in the playback callback rather than on chunk arrival: audio
is buffered and TTS often outruns realtime, so measuring on arrival would run the
mouth ahead of the voice. Barge-in needs no special case either -- clearing the
buffer makes the callback emit silence, which closes the mouth on its own.

Files keep normal controls restart-tolerant without maintaining another socket.
The mode status document adds acknowledgement only where model loading can fail.
The launcher briefly dials the frontend to prove a Realtime handshake and waits
for that probe's pool slot to return idle before starting the real audio client.
Session updates are deep-merged server-side, so a voice-only update leaves VAD
untouched. Announcement responses are out-of-band and carry their input directly;
they do not inherit a stale microphone turn or pollute conversation history.

The "test voice" button cannot work here -- the frontend exposes no verbatim-TTS
entry point -- so it reports that instead of queueing a phrase nothing will speak.

Wire the client to all three:

```cmd
run-realtime-client.cmd
```

Voice ids come from RAP's Kokoro catalog (`remote_agent_protocol/voices.py`).
For a dry English delivery use the British male group: `bm_george`, `bm_fable`,
`bm_daniel`, or `bm_lewis`. Launch Kokoro with `--kokoro_lang_code b` so its
phonemizer matches the accent rather than only the timbre.

## Frontend prerequisites

Kokoro is the TTS backend worth having -- it is the one with British voices and
the one that honours mid-session voice changes -- but it needs two pieces that do
not come with a default `speech-to-speech` checkout:

```cmd
cd /d "H:\Program Files (oss)\speech-to-speech"
uv sync --extra kokoro --inexact
uv pip install --python .venv\Scripts\python.exe ^
  "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
```

The second line is not optional and is easy to misdiagnose. Kokoro's G2P needs
spaCy's `en_core_web_sm`, and spaCy tries to install it by shelling out to `pip`
-- which a uv-managed venv does not have. The download reports success, nothing
lands, and it retries forever. Worse, the TTS handler wraps pipeline construction
in `except ImportError` and reports "kokoro is required ... install with pip",
which points at the wrong problem entirely. If you see that message with kokoro
already installed, the spaCy model is what is actually missing.

Match the model version to the installed spaCy major.minor (`spacy 3.8.x` ->
`en_core_web_sm-3.8.0`).

## Starting it

```cmd
scripts\start_voice.bat
```

That is the whole thing. It starts the brain/GUI, waits until the brain session
has actually initialized, starts the frontend's server, requires a real
`session.created` WebSocket handshake, then starts the frontend's client and
waits for its PID-tagged readiness file. The client publishes that file only
after microphone and speaker streams are open and its realtime session exists.
Each stage runs hidden with stdout/stderr redirected to a dedicated file under
`logs/`, so failures are inspectable without leaving console windows scattered
across the desktop.

Ordering is enforced rather than assumed; a listening port alone is not treated
as readiness. Stale client readiness is removed before spawn. If a stage dies or
cannot prove readiness, launch stops without starting downstream audio owners.
The launcher stops stages in reverse order: RAP receives an authenticated soft
shutdown so it can persist memory and reap agent work. If a wrapper cannot stop,
the Windows escalation targets its complete process tree with `taskkill /T /F`;
stale client readiness is also removed before the next spawn.

The launcher forces `RAP_MODE=brain` for its children, chooses dynamic loopback
bridge/realtime ports and a random per-launch bridge key, and passes those values
directly to every child. It also supplies mute, voice, mode request/status,
announcement, telemetry, envelope, timing, and readiness paths. The S2S listener
is forced to `127.0.0.1` rather than its upstream `0.0.0.0` default. It finds the
frontend beside this repo automatically; set `S2S_HOME` if it lives elsewhere.
Fixed `S2S_BRIDGE_PORT`, `S2S_WS_PORT`, and `S2S_BRIDGE_API_KEY` values mainly
apply when starting the GUI/headless bridge and frontend pieces manually.

Starting the pieces by hand still works and is documented below, which is what
you want when debugging one of them in isolation.

## Why the brain streams

A spoken turn's latency is dominated by when the first word can be *said*, not
when the reply is finished. The brain therefore streams: it asks Ollama for a
streaming completion and releases text at sentence boundaries, so the frontend
starts speaking the first sentence while the rest is still being generated.
Measured on a ~2.6s reply, the first speakable chunk lands at ~0.7s.

Two constraints shape the implementation:

- **Delegation markers must never be spoken.** Once `[[` appears the remainder is
  withheld, and the marker is dispatched when the reply ends. Text before it has
  already been released, so a delegating reply still sounds prompt.
- **The socket must not coalesce.** Each chunk is a sentence someone is waiting
  to hear, so the handler disables Nagle; otherwise the kernel batches the small
  writes and hands the frontend one late delivery.

Non-streaming callers still work. Both GUI brain mode and the headless bridge
forward sentence chunks incrementally; neither waits for `complete()` and then
wraps the finished answer in decorative SSE.

## Two topologies, one port

For manual launches the brain endpoint defaults to `S2S_BRIDGE_PORT` (8788),
and two different things can serve it. Pick one -- running both collides on the
port. The whole-stack launcher instead allocates an available loopback port:

| Topology | Serves the brain | Avatar + GUI voice control |
| --- | --- | --- |
| GUI brain mode (`RAP_MODE=brain` in `.env`, then `scripts\start_gui.bat`) | the GUI itself | yes |
| Headless (`scripts\start_brain_bridge.bat`) | `openai_bridge.py` | no |

The headless bridge has no GUI, so it serves no `/api/avatar-envelope` and reads
no voice file: the frontend keeps whatever voice its launcher set. Use GUI brain
mode for the full control surface, headless when you only want the endpoint.

Launch speech-to-speech against RAP:

```cmd
cd /d "H:\Program Files (oss)\speech-to-speech"
run-rap-brain.cmd
```

When using this mode, do not run RAP's full local voice pipeline at the same time. That avoids duplicate mic/STT/TTS and preserves the VRAM savings.

## Simpler but less integrated option

Point `speech-to-speech` directly at Ollama:

```cmd
run-realtime.cmd ^
  --llm_backend chat-completions ^
  --responses_api_base_url "http://127.0.0.1:11434/v1" ^
  --responses_api_api_key "ollama" ^
  --model_name "gemma-12b-huihui"
```

This gives a working realtime voice bot, but bypasses Remote Agent Protocol's router, memory, confirmation gate, GUI persona state, and agent bridge.

## Non-goals

- Do not import the `speech-to-speech` package into Remote Agent Protocol. Keep it an external process/server.
- Do not modify vendored `src/pipecat` for this.
- Do not expose the bridge beyond loopback until authentication and threat model are explicit.

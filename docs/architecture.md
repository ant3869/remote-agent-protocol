# Remote Agent Protocol architecture

Remote Agent Protocol (the desktop assistant persona is still called Jess)
is an application layer over Pipecat, not a renamed copy of the
`pipecat` Python package. Keeping that boundary preserves upstream compatibility
while giving the desktop product its own identity and release path.

The application lives in the `remote_agent_protocol/` package. Launch the desktop app with
`python -m remote_agent_protocol` (or `start_gui.bat`) and the terminal mode with
`python -m remote_agent_protocol.terminal` (or `start_terminal.bat`). Module references below
are relative to `remote_agent_protocol/`.

## Runtime flow

```text
microphone -> [wake gate] -> STT -> intent router -> memory -> Ollama -> TTS -> AvatarAudioTap -> speakers
                                      |                         |              |
                                      +-> AgentBridge ----------+              +-> latest normalized envelope
                                             |       |                                  |
                                             |       +-> main PC or configured remote launcher
                                             +-> loopback lifecycle WebSocket            +-> loopback SSE -> avatar renderer
```

- `web_gui.py` serves the default loopback web control center and bridges HTTP
  actions/events to `VoiceSession`; `gui.py` remains as the Tk fallback shell.
  The UI renders transcript, health, latency, session state, persona controls,
  shortcuts, and the shared prompt composer. In composer mode, voice
  transcripts, typed notes, links, images, and files stay in a local draft until
  the user sends one reviewed prompt.
- `session.py` owns the Pipecat pipeline and exposes thread-safe commands to the
  GUI. The audio loop never calls Tk directly. `send_multimodal_prompt()` adds
  one assembled user message to the LLM context and runs one LLM turn. The
  optional `AvatarAudioTap` observes TTS PCM after synthesis and before local
  output, publishing only normalized RMS/peak envelopes; it never mutates or
  delays the audio frame.
- `avatar_audio.py` defines the bounded latest-value envelope hub and SSE
  serialization. `WebVoiceApp` owns and closes one hub, while `VoiceSession`
  receives only its `publish` callback. Raw PCM never crosses the web boundary.
- `web_app/avatar/` is a zero-build ES-module runtime. It lazy-loads vendored
  Three.js only when enabled, renders the procedural butler or a safe local GLB,
  and owns expression, gaze, lip-sync, reduced-motion, fallback, and disposal
  behavior.
- `intent_router.py` routes explicit commands and high-confidence keyword
  matches without model latency, skips pure acknowledgments, and uses a small
  local classifier only for otherwise-ambiguous requests. Vague references to
  a named-but-forgotten package/skill/tool are caught deterministically and
  sent verbatim as identify-then-install tasks, held for confirmation.
- `session_processors.py` contains the microphone gate, manual composer STT
  draft tap, role-scoped transcript observers, delegation processor, and guard
  against replies that claim agent work without actually dispatching it.
- `multimodal_prompt.py` defines the prompt bundle, attachment references,
  agent-facing Markdown assembly, send/hold voice intent parsing, and simple
  durable-preference extraction across voice, text, and attachment notes.
- `wake_word.py` provides optional multi-model wake routing (openwakeword,
  fully local). Installed models are matched to personas or mapped with
  `WAKE_WORD_PERSONAS_JSON`; the highest score wins and its persona settings
  are applied before command audio reaches STT. The window remains VAD-aware.
  Missing secondary models are skipped, while engine failure preserves the
  existing always-listening fallback.
- `app_state.py` remembers the last persona and tool-user picks
  (`jess_app_state.json`) so a restart boots as the character you actually use.
- `agent_bridge.py` owns external agent subprocesses, bounded output capture,
  cancellation, provider-limit detection, model overrides, completion events,
  and concise spoken results. A backend that exits with its own confirmation
  gate is relaunched through the session's normal confirmation path instead of
  being treated as completed. See `model-recovery.md` for the exact CLI mappings.
  Jobs default to a neutral sandbox directory rather than this repository, every
  task carries a scope preamble, and the host repo's working tree is diffed
  before/after each run so an unexpected edit to Jess's own source is flagged
  and announced.
- `lifecycle_ws.py` projects the existing normalized `agent_job` events into a
  versioned, allowlisted JSON stream at `ws://127.0.0.1:8765/events`. Each
  client has a bounded queue; slow clients are disconnected instead of
  backpressuring the voice loop. Raw agent output is never exposed.
- `config.py`, `personas.py`, and `persona_config.py` hold operator settings and
  persona overrides.
- `memory.py`, `memory_manager.py`, and `mem0_setup.py` provide transcript and
  semantic memory.

## What is solid

- Voice and typed input use the same session and routing path.
- The desktop composer can hold voice, notes, links, images, and files as one
  reviewed prompt bundle; transcript completion and attachment changes update
  the draft instead of triggering the agent.
- Delegation happens in code before the LLM sees the request. Each routing
  decision is logged; capability-state audits bypass the classifier, and a
  markerless promise creates a real pending confirmation for the original
  request instead of relying on another LLM response.
- Directly addressed agents are deterministic and apply to one request only:
  forms such as `Hermes, ...`, `use Hermes to ...`, and `ask Code Puppy to ...`
  override the persisted default without changing it. Unnamed work returns to
  the default. `list agents`, `what is my default agent`, and `make Hermes my
  default agent` are local controls; a deliberate default change persists.
  Contextual handoffs include a bounded untrusted transcript snapshot, and
  corrections cancel the newest job before a revised job can launch.
- Agent work is asynchronous and streams to a dedicated console.
- STT, TTS, personas, model choice, wake word, memory, agent completion
  announcements, and audio devices are independently configurable (most of it
  from `.env`, no Python edits).
- The wake-word gate runs in the live audio path with graceful fallback to
  always-listening when the engine is unavailable; secondary persona models
  activate only when they are installed locally.
- Memory and job-history writes are atomic (temp file + swap), and every
  injected one-shot prompt is stripped before the transcript is persisted.
- Pure routing, memory, configuration, dashboard, wake-word, processor, and
  bridge behavior have focused unit coverage.

## Current boundaries and risks

- Agent backends are subprocess commands. A remote backend therefore requires a
  trusted launcher available on the main PC; the project does not yet define a
  network protocol or authenticate remote machines.
- The `hermes-yolo` backend remains available for explicit selection, but normal
  spoken Hermes routing and implicit delegation use the safer Hermes backend.
- Tk is appropriate for the local Windows control panel but is not a browser or
  mobile client. Pipecat's RTVI/UI worker path is the natural future boundary if
  remote control becomes a requirement.
- The lifecycle WebSocket is read-only, future-events-only, and loopback-only.
  It is not the authenticated remote-agent command protocol described above.
- The repository vendors the complete Pipecat framework. Upstream updates should
  be merged from the `upstream` Git remote without mixing custom code into
  `src/pipecat` unless the framework itself must change.

## Agent configuration

`AGENT_BACKENDS_JSON` adds or replaces command templates without editing Python.
Values must be JSON arrays of process arguments; shell command strings are
rejected. `AGENT_MACHINES_JSON` supplies the machine labels shown in the UI.

```dotenv
AGENT_BACKENDS_JSON={"openclaw":["trusted-launcher","openclaw","{task}"]}
AGENT_MACHINES_JSON={"openclaw":"Laptop"}
```

Agent tasks share one normalized lifecycle: started, in progress, tool running,
step completed, waiting, blocked, completed, or failed. The bridge asks capable
agents to emit `@@JESS_STATUS` JSON lines, derives basic tool activity from known
CLI output, and emits a heartbeat for otherwise-silent jobs. Structured terminal
markers finish and announce a task even when a one-shot CLI wrapper fails to exit.
Code Puppy headless turns use `--quick-resume`, preserving the latest session for
the job working directory's Git root and branch while starting fresh when absent.
Hermes uses progress-visible single-query mode; the bridge captures its exit
summary session ID and resumes that backend's session for later jobs in the same
run. `AGENT_JOB_TIMEOUT_SECS` limits consecutive output silence rather than total
runtime, allowing productive long jobs to continue while still reaping hangs.

The same events are available to local dashboards through the v1 lifecycle
WebSocket. Payloads have a monotonically increasing per-session sequence and
contain allowlisted metadata only. A port collision is shown as degraded health
without stopping voice. See `lifecycle-websocket.md` for the schema.

`AGENT_PROGRESS_INTERVAL_SECS` controls UI heartbeats;
`AGENT_VOICE_PROGRESS_MIN_SECS` and `AGENT_VOICE_PROGRESS_INTERVAL_SECS` keep
spoken updates useful but sparse. Terminal updates use TTS directly and therefore
do not depend on Ollama. Bounded runtime diagnostics are written to
`jess_runtime.log`.

`AGENT_CONFIRM_LOOP_LIMIT` defaults to `2` and stops a one-shot backend from
repeatedly relaunching when it keeps asking for confirmation instead of doing
the approved work.

The remote launcher is intentionally external until the laptop's available API,
authentication method, and exact Hermes/OpenClaw commands are known. Do not put
an unquoted voice transcript directly into an SSH shell command.

## Product roadmap

Shipped from earlier roadmaps: the confirmation gate for destructive/elevated
jobs, the diagnostics bundle (EXPORT button), the VAD-aware wake-word mic
gate, last-persona persistence, and clean shutdown of live agent subprocesses
(no more unclosed-transport crashes at exit).

1. Define one authenticated remote-agent protocol after confirming the laptop
   agents' real interfaces; add heartbeat and capability discovery with it.
2. Move to an RTVI web client only when access from other devices is required;
   the desktop UI already covers the local operator workflow.

# Remote Agent Protocol architecture

Remote Agent Protocol (the desktop assistant persona is still called Jess)
is an application layer over Pipecat, not a renamed copy of the
`pipecat` Python package. Keeping that boundary preserves upstream compatibility
while giving the desktop product its own identity and release path.

The application lives in the `remote_agent_protocol/` package. Launch the desktop app with
`python -m remote_agent_protocol` (or `scripts\start_gui.bat`) and the terminal mode with
`python -m remote_agent_protocol.terminal` (or `scripts\start_terminal.bat`). Module references below
are relative to `remote_agent_protocol/`.

## Runtime flow

The `voice_stack.py` supervisor starts the brain/GUI, speech server, and audio
client in order. Its child environment sets `TEMP`, `TMP`, and `TMPDIR` to
`DATA_DIR/stack-tmp` so large model-extraction files use the application data
drive. This is process-scoped and does not change the user's system environment;
the launcher reports a directory-creation failure before starting any children.

```text
Full mode:
  microphone -> [wake gate] -> STT -> intent router -> transcript/semantic memory
                                      |                    |
                                      +-> AgentBridge      +-> Ollama -> TTS -> speakers
                                             |                              |
                                             +-> lifecycle WebSocket        +-> avatar envelope

Brain mode:
  external mic -> external VAD/STT -> OpenAI-compatible RAP brain -> external TTS -> speakers
                                            |                       |
                                            +-> router/AgentBridge   +-> input/timing/avatar telemetry
```

- `web_gui.py` serves the loopback web control center (a hand-rolled
  `http.server` with a per-launch CSRF token) and bridges HTTP actions/events
  to `VoiceSession`; `web_app/` is the served HTML/CSS/JS, including the
  holographic avatar runtime under `web_app/avatar/`. The bundled Butler uses
  Canvas 2D frames; other avatar IDs can use the vendored Three.js path. The UI renders
  transcript, health, latency, session state, persona controls, shortcuts, and
  the shared prompt composer. In composer mode, voice transcripts, typed
  notes, links, images, and files stay in a local draft until the user sends
  one reviewed prompt.
- `session.py` owns the Pipecat pipeline and exposes thread-safe commands to the
  GUI. The audio loop never calls the UI layer directly. `send_multimodal_prompt()` adds
  one assembled user message to the LLM context and runs one LLM turn. The
  optional `AvatarAudioTap` observes TTS PCM after synthesis and before local
  output, publishing only normalized RMS/peak envelopes; it never mutates or
  delays the audio frame.
- `avatar_audio.py` defines the bounded latest-value envelope hub and SSE
  serialization. `WebVoiceApp` owns and closes one hub, while `VoiceSession`
  receives only its `publish` callback. Raw PCM never crosses the web boundary.
- `web_app/avatar/` is a zero-build ES-module runtime. The bundled butler uses
  `frame-avatar-scene.js`, a Canvas 2D renderer over individual
  `runtime_512_v1/*.webp` expression, mouth, gaze, materialization, and glitch
  frames. Other avatar IDs can use the vendored Three.js/GLTF path. A generation
  guard prevents stale asynchronous loads from replacing the current scene;
  both paths own reduced-motion, fallback, visibility, and disposal behavior.
- `intent_router.py` routes explicit commands and high-confidence keyword
  matches without model latency, skips pure acknowledgments, and uses a small
  local classifier only for otherwise-ambiguous requests. Vague references to
  a named-but-forgotten package/skill/tool are caught deterministically and
  sent verbatim as identify-then-install tasks, held for confirmation.
  Corrections and references to earlier conversation bypass the stateless
  classifier and reach the persona with history. Diagnosis routing distinguishes
  the named subject of a fault from an explicitly chosen executor, including
  delegation markers in both full and brain modes.
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
  Immediate provider-failure detection requires an error banner or a top-level
  JSON error; narrative and progress text mentioning quota failures do not abort
  an investigation.
- `lifecycle_ws.py` projects the existing normalized `agent_job` events into a
  versioned, allowlisted JSON stream at `ws://127.0.0.1:8765/events`. Each
  client has a bounded queue; slow clients are disconnected instead of
  backpressuring the voice loop. Raw agent output is never exposed.
- `config.py`, `personas.py`, and `persona_config.py` hold operator settings and
  persona overrides.
- `memory.py`, `memory_manager.py`, and `mem0_setup.py` provide transcript and
  semantic memory.

### Conversation events

`conversation.py` supplies source identity and a bounded normalized replay store.
`session_processors.TranscriptTap` emits partial/final utterances with speaker
snapshots; `speech_events.py` carries ordered application speech boundaries
through TTS/output and reports playback without changing the vendored framework.
`web_app/conversation.js` renders keyed utterances, task activity, consultations,
approvals, and outcomes while preserving reading position. The GUI brain's
authenticated `/api/speech-events` endpoint accepts external playback reports;
OpenAI-compatible responses carry additive `rap` identity metadata. External
clients that do not implement this contract show playback as unconfirmed.
See [conversation events](conversation-events.md) for replay/clear semantics,
retention limits, and the external integration boundary.

### Brain mode

`RAP_MODE=brain` keeps everything above except the local audio graph, so a GPU
too small for Whisper, Ollama, and TTS at once can hand the ears and mouth to an
external realtime speech-to-speech frontend:

```text
frontend mic -> frontend VAD/STT -> RAP brain endpoint -> frontend TTS -> frontend speakers
                                       |
                                       +-> intent router -> memory -> AgentBridge -> Ollama
```

- `brain.py` is the text-only coordinator: routing, short-term memory, the
  confirmation gate, the agent bridge, and Ollama generation, with no mic, STT,
  TTS, or speakers. Control turns retain original user wording alongside the
  application's dispatch/status context for future follow-ups.
- `brain_adapter.py` presents that coordinator through the `VoiceSession`
  control surface the GUI expects, so the same web control center drives both
  modes. Controls with no local audio path degrade explicitly rather than
  silently: spoken output is reported as text, not voiced.
- `agent_control.py` owns evidence-backed harness probes and RAP-owned task
  cancellation/redirection. It contacts each configured CLI independently,
  persists the latest observation, and marks it stale after a restart until a
  fresh probe succeeds; it does not infer external-session progress or perform
  unsafe external cancellation.
- `openai_bridge.py` is the loopback OpenAI-compatible HTTP server
  (`/health`, `/v1/models`, `/v1/chat/completions`, SSE streaming). `web_gui.py`
  serves the same routes when the GUI itself is in brain mode; run one or the
  other, since both bind `S2S_BRIDGE_PORT`.
- `voice_stack.py` is the ordered launcher for the three processes across two
  checkouts. Readiness means an initialized RAP brain, a Realtime WebSocket
  `session.created` handshake whose temporary pool claim has returned idle,
  then a PID-tagged client signal written only after audio streams open and its
  own Realtime session exists. RAP forces the S2S listener to loopback, removes
  stale readiness before spawn, allocates an ephemeral port per process, and
  refuses to start while the app's single-instance lock is already held.
  Shutdown first asks RAP to clean up gracefully, then uses process-tree
  escalation for wrapper descendants that cannot stop cleanly.
- The frontend control plane is explicit and loopback-only. Output voice crosses
  through `S2S_VOICE_FILE`. Mute, Free Talk, and Wake Word use generation-tagged
  request/acknowledgement documents -- `S2S_MIC_MUTE_FILE` with
  `S2S_MIC_MUTE_STATUS_FILE`, `S2S_VOICE_MODE_FILE` with
  `S2S_VOICE_MODE_STATUS_FILE` -- so a restart cannot read a previous run's
  acknowledgement as its own; an acknowledgement timeout leaves mute unconfirmed
  and rolls the GUI back to its last confirmed mode. Push To Talk remains
  unavailable because the external process owns microphone capture.
- The client POSTs input phases to `/api/input-state`, measured STT/response/audio
  latency to `/api/turn-timing`, and speaker playback loudness to
  `/api/avatar-envelope`. Completed agents publish immutable queue entries beside
  `S2S_ANNOUNCE_FILE`; the client claims and narrates each entry exactly once.
  `/api/stack-shutdown` is reserved for authenticated launcher teardown.
  Brain-mode health remains unavailable until the initial safe mute state is
  published, and configured mute-file read errors fail closed.
- `BrainSession` persists short-term transcript context. Semantic mem0 writes
  require the full local session and are explicitly unavailable in brain mode;
  the GUI does not silently claim otherwise.

## Persona orchestration (Local / Cloud / Hybrid)

`remote_agent_protocol/orchestration/` sits **on top of** the existing
`intent_router` -> `agent_bridge` path; it never replaces it. What it decides is
whether the *orchestration reasoning itself* should escalate to a cloud model --
not what the harness then goes and does.

| Module | Responsibility |
| --- | --- |
| `models.py` | `RiskFactors`, `StructuredDecision`, `Route` -- data only |
| `risk.py` | Per-turn signal extraction and weighted scoring; pure functions |
| `concurrency.py` | Global (2) and per-harness (1) job caps, plus duplicate admission |
| `quota.py` | Economy / Balanced / Performance / Cloud-preferred strategies |
| `telemetry.py` | JSONL records, kept separate from persona memory |
| `orchestrator.py` | `PersonaOrchestrator`: evaluate, admit, record outcome |
| `providers/` | `ModelProvider` ABC, `LocalProvider` (Ollama), `CopilotProvider` |

Both session types construct one: `VoiceSession` in full mode and `BrainSession`
in brain mode. Each calls `evaluate()` while resolving a delegation, passes every
real dispatch through `_gate_dispatch()`, and calls `record_outcome()` when the
job finishes. `web_gui.py` talks to whichever is active through one shared
surface, so `BrainSessionAdapter` has to keep pace with `VoiceSession` --
`tests/test_brain_adapter_orchestration.py` fails when it drifts.

Every risk factor is measured from something that varies per turn: how much of
the utterance depends on earlier ones, how many distinct constraints it carries,
how much of a choice the harness pick was, whether a previous result must be
interpreted, whether several ordered actions are needed, and how unsure the local
tiers themselves are. An earlier factor set held four constants and could not
reach the cloud band at all; the weights live in
`config.ORCHESTRATION_RISK_WEIGHTS` and sum to 1.0.

Scores map to bands (`ORCHESTRATION_LOCAL_THRESHOLD` /
`ORCHESTRATION_CLOUD_THRESHOLD`). Between them, hybrid stays local unless a
**hard trigger** fires -- a multimodal requirement, a previous routing failure,
or a harness pick under the confidence floor -- which also overrides the quota
strategy. Decisions land in `data/orchestration_telemetry.jsonl` and in the
Status view's "Persona orchestration" panel: mode and strategy controls, Copilot
auth state, provider health, and the recent routing decisions.

Cloud reasoning uses the official `github-copilot-sdk`; authentication is the
SDK's own (`copilot auth login`, or its `GH_TOKEN`-family env vars) and this repo
stores no token. Unauthenticated is a valid configuration: escalations fall back
to local and are recorded as fallbacks.

## Model endpoints (local and cloud)

`llm_endpoint.py` decides where each of the three model calls goes. The persona
(`BRAIN`), the intent classifier (`INTENT`), and the orchestrator's own reasoning
(`ORCHESTRATION`) each resolve their own chain: the configured cloud endpoint
first, then the local Ollama one, which is always last.

A cloud endpoint counts as configured only when base URL, key, and model are all
present -- a half-configured one would fall back on every turn, which is slower
than never trying. Nothing is provider-specific: any endpoint that speaks
`/chat/completions` works.

The request shapes are not interchangeable, so each caller rebuilds rather than
forwards. Ollama's `keep_alive`, `options`, `think`, and `format`-as-JSON-schema
are its own extensions and a hosted API rejects unknown fields; the classifier's
schema travels as `response_format` instead. The persona falls back only *before*
its first token -- once the user is hearing a reply, switching models mid-sentence
would talk over itself.

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

- Agent backends are subprocess commands on this machine, or agents offered by
  another machine over the authenticated remote-agent protocol (below). What the
  protocol does not do yet: schedule across hosts, move a job between them, or
  survive a host restart mid-job -- a job belongs to the host that accepted it.
- The `hermes-yolo` backend remains available for explicit selection, but normal
  spoken Hermes routing and implicit delegation use the safer Hermes backend.
- The web control center is loopback-only (bound to `127.0.0.1`, CSRF-gated)
  and is not reachable from another device on the network. Pipecat's RTVI/UI
  worker path is the natural future boundary if remote control becomes a
  requirement.
- The lifecycle WebSocket is read-only, future-events-only, and loopback-only.
  It is a dashboard feed, not the remote-agent protocol.
- Brain mode depends on a RAP-aware speech-to-speech frontend checkout that
  lives outside this repository and is not versioned with it. `voice_stack.py`
  checks for the launchers and virtualenv it needs and reports what is missing,
  but the app cannot install or update that side. Conversation/job state and
  control files live under ignored `data/`; launcher subprocess logs live under
  ignored `logs/`.
- The brain endpoint is loopback-only and gated by a shared `S2S_BRIDGE_API_KEY`.
  It is a local convenience boundary for the audio frontend, unrelated to the
  remote-agent protocol.
- The repository vendors the complete Pipecat framework. Upstream updates should
  be merged from the `upstream` Git remote without mixing custom code into
  `src/pipecat` unless the framework itself must change.
- Voice and TTS provider always follow the active persona at boot, the same
  way selecting a persona live does (`WebVoiceApp._use_persona_tts_defaults`).
  A voice picked directly in Settings -> Voice, independent of the persona
  picker, applies only for the rest of that run; it never survives a restart
  once a different (or the same) persona is loaded, so it cannot silently
  drift away from what the active persona is configured to sound like. Want a
  persona's own voice changed permanently? Edit that persona (built-in
  override in `data/persona_overrides.json`, or a custom persona), not the
  Voice dropdown alone. `model` and the Coqui detail fields (model/speaker/
  language/device) still restore independently of persona across restarts.
- Agent consultation's answer-file rendezvous has a narrow window: a consult
  slot is empty between being accepted and the answer landing, so another
  process that both knew the random mailbox token and won that timing could
  stage something there first (`collab.consult_slot_is_free` checks only at
  acceptance). Closing this fully needs a pending-marker handshake on the
  agent side; `AGENT_CONSULT_ENABLED=false` disables consulting entirely if
  that residual risk is unacceptable for a given deployment.

## Agent configuration

`AGENT_BACKENDS_JSON` adds or replaces command templates without editing Python.
Values must be JSON arrays of process arguments; shell command strings are
rejected. `AGENT_MACHINES_JSON` supplies the machine labels shown in the UI.

```dotenv
AGENT_BACKENDS_JSON={"openclaw":["trusted-launcher","openclaw","{task}"]}
AGENT_MACHINES_JSON={"openclaw":"Laptop"}
```

### Remote agent hosts

Another machine offers its own agents over one authenticated protocol
(`remote_protocol.py`), served by `remote_host.py` there and consumed by
`remote_client.py` here. Three routes, all bearer-token gated: capability
discovery, heartbeat, and a job whose output streams back as newline-delimited
JSON. Configure with `AGENT_REMOTE_HOSTS_JSON` and `AGENT_REMOTE_TOKEN`.

Discovered agents join delegation as `<host>:<agent>`, which keeps a remote
Hermes distinct from a local one and labels the job with the machine that ran
it. `RemoteRegistry` heartbeats each host on `AGENT_REMOTE_HEARTBEAT_SECS` and
withdraws its agents the moment one stops answering, so a sleeping laptop stops
being a delegation target instead of accepting a job it cannot run.

The integration point is deliberately narrow: `RemoteProcess` presents a running
remote job through the same members the bridge uses on a local subprocess
(`stdout.readline()`, `wait()`, `returncode`, `terminate()`), so status-marker
parsing, progress heartbeats, silence timeouts, and cancellation are the same
code for both. The host applies the scope preamble and resolves the working
directory itself, because only it knows the real paths on that machine, and it
will only run backend *names* it is already configured with -- never a command
supplied by the peer.

Agent tasks share one normalized lifecycle: started, in progress, tool running,
step completed, waiting, blocked, completed, or failed. The bridge asks capable
agents to emit `@@JESS_STATUS` JSON lines, derives basic tool activity from known
CLI output, and emits a heartbeat for otherwise-silent jobs. Structured terminal
markers finish and announce a task even when a one-shot CLI wrapper fails to exit.
Code Puppy headless turns are stateless: its `--quick-resume` reads the newest
session for the working directory's Git root and branch, which is the same pool
the human's own interactive runs write to, so a delegated task would land
mid-conversation in unrelated context. Set `AGENT_BACKENDS_JSON` to opt back in.
Hermes uses progress-visible single-query mode; the bridge captures its exit
summary session ID and resumes that backend's session for later jobs in the same
run. `AGENT_JOB_TIMEOUT_SECS` limits consecutive output silence rather than total
runtime, allowing productive long jobs to continue while still reaping hangs.

The same events are available to local dashboards through the v1 lifecycle
WebSocket. Payloads have a monotonically increasing per-session sequence and
contain allowlisted metadata only. A port collision is shown as degraded health
without stopping voice. See `lifecycle-websocket.md` for the schema.

`AGENT_PROGRESS_INTERVAL_SECS` controls UI heartbeats. Only a job that is
WAITING or BLOCKED on the user is spoken as progress; routine "still working"/
"completed a step" updates stay GUI/log-only. Terminal updates use TTS
directly and therefore do not depend on Ollama. Bounded runtime diagnostics
are written to `jess_runtime.log`.

Spoken lines about agent work are generated, not canned: `narration.py`
writes each one fresh from what the job is actually doing, in the active
persona's voice, so no status sentence is heard twice. It runs on the small
model `intent_router` already keeps resident (`NARRATION_MODEL`, defaulting
to `INTENT_MODEL`), never the large chat model answering the user -- so it
adds no VRAM and never queues behind a reply. Progress narration prefetches
during a job's dead time; missing the `NARRATION_TIMEOUT_SECS` deadline
speaks a rotating stock line instead, so audio never waits on generation.
Throttling is on *facts*, not phrasing (`_agent_last_spoken` holds the last
detail, not the last sentence): a job whose situation hasn't changed stays
quiet however differently it would be worded. Text that must survive
verbatim -- an agent's actual answer, its question, the words that recover a
failure (`agent_bridge.recovery_hint`) -- is passed through as the `body`
the narrator writes around, so substance never drifts.

Agents share a commons (`collab.py`): a `_commons` folder inside
`AGENT_WORKSPACE_DIR` holding `findings.jsonl` (what a job learned),
`lessons.jsonl` (what an agent got wrong, fed back to its own next run), and
`notes/` for anything longer. Every dispatch carries a short briefing naming
the folder, so separate CLI processes stop rediscovering the same facts.
Notes are written by agents, so one agent's output becomes another's prompt --
the commons is an indirect prompt-injection path by construction, and is
built accordingly. Going in, `collab.sanitize` fails closed: a note carrying
anything credential-shaped (AWS keys, PEM blocks, JWTs, connection strings,
vendor token prefixes) or shaped like an instruction to whoever reads it next
("ignore previous...", `rm -rf`, `curl | sh`, a request to re-run with
`--dangerously-skip-permissions`) is dropped entirely rather than cleaned up.
Coming out, borrowed notes are fenced with a per-process random token, so a
note cannot forge the marker that ends the section it is quoted inside.

The load-bearing mitigation is `AGENT_ELEVATED_BACKENDS`: the harnesses whose
command line disables tool approval (`hermes --yolo`, `codex --sandbox
danger-full-access`, `claude -p --dangerously-skip-permissions`) are never
handed another agent's text at all. They are told where the commons is and
may read it themselves -- an ordinary file read they can weigh -- rather than
receiving those words inside their own instructions. Their own past lessons
still travel, since those come from this app rather than from another agent.
A text label is not a security boundary for an LLM; keeping the text out of
the prompt of the processes that run unsupervised tools is.

Agents can also ask each other questions mid-task. A running job prints
`@@JESS_CONSULT {"id":...,"agent":...,"question":...}` and then polls
`_commons/consults/<id>.json`; the bridge vets the request
(`AgentBridge._handle_consult`), runs the named backend as a child job, and
writes the answer back to that file. Polling is what makes this work with
one-shot CLIs, which cannot be handed anything after launch -- the waiting
agent prints a `waiting` status each time it checks, which both keeps the
silence timeout from reaping it and gives the exchange something to narrate.
Both sides are spoken aloud in their own harness voices
(`session._announce_agent_consult`), so who is talking is audible without any
line having to say so.

The request comes from an agent's own stdout, so the limits are structural
rather than advisory -- `consult_depth` and `consult_chain` travel with the
job, not with the request, so no wording gets a job past them. A consulted
agent cannot consult in turn (`AGENT_CONSULT_MAX_DEPTH`), a job gets a fixed
number of questions (`AGENT_CONSULT_BUDGET`), an agent already in the chain
cannot be asked again, the target may never be an elevated backend, and a
question that reads as destructive is refused outright -- the user authorized
the original task, not that, and they are not in this loop. Every refusal
still writes an answer file, because an agent polling for a file that never
appears is an agent that hangs. Consult ids are validated as filenames
(`collab.consult_id_ok`), never escaped.

Each delegated backend has its own Kokoro voice (`HARNESS_VOICES`) so a
finished/failed job is recognizable by ear, independent of whichever persona
is currently front-of-house. `session._speak_agent_text` builds the
[switch voice, speak, restore front-of-house voice] frame list as one unit
under a lock, so two jobs finishing close together can't interleave one
job's voice switch with another's speech; a completion answer longer than
`AGENT_RESULT_SPEAK_MAX_CHARS` is trimmed with a spoken pointer to the full
answer (still staged into the LLM context). `AGENT_ANNOUNCE_START` gates
spoken job-start narration, which only fires when the starting job is the
sole active one.

In brain mode, a turn that already dispatched deterministically -- a routed
delegation, or an approved/denied confirmation -- marks itself a control turn
(`self._control_turn = True`) before generating the reply that narrates it.
Left unset, the model's own narration is free to invent a second
`[[delegate:]]` marker for the same task and run it again, since the LLM
delegate instruction is unconditionally live in its system prompt and has no
way to know a real dispatch already happened (confirmed live,
`jess_runtime.log` 2026-09-13 12:37:38-40: "ping each agent" ran twice, ~1.6s
apart, the second time from the acknowledgment reply itself).

`AGENT_MOCK_BACKEND_ENABLED` (off by default) gates whether the "mock"
backend -- which instantly "completes" any task with a canned response, for
smoke-testing dispatch/announce/consult without a real agent installed -- is
present in `AGENT_BACKENDS`/`AGENT_SPOKEN_ALIASES` at all. Confirmed live: a
session had it selected as the active default agent, so every delegation
"succeeded" with fabricated results and nothing real ever ran. Automated
tests build their own backend dicts and are unaffected by this flag.

`AGENT_CONFIRM_LOOP_LIMIT` defaults to `2` and stops a one-shot backend from
repeatedly relaunching when it keeps asking for confirmation instead of doing
the approved work.

The remote launcher is intentionally external until the laptop's available API,
authentication method, and exact Hermes/OpenClaw commands are known. Do not put
an unquoted voice transcript directly into an SSH shell command.

## Product roadmap

Shipped from earlier roadmaps: the authenticated remote-agent protocol with
heartbeat and capability discovery, the confirmation gate for destructive/
elevated jobs, the diagnostics bundle (EXPORT button), the VAD-aware wake-word
mic gate, last-persona persistence, and clean shutdown of live agent
subprocesses (no more unclosed-transport crashes at exit).

1. Cross-host scheduling on top of the remote-agent protocol: pick a machine by
   load or capability rather than by name, and reattach to a job whose host
   restarted. Both need the protocol to carry job identity across a reconnect,
   which today's one-job-per-connection stream does not.
2. Move to an RTVI web client only when access from other devices is required;
   the loopback web control center already covers the local operator workflow.

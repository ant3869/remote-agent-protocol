# Changelog

All notable changes to **Remote Agent Protocol** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Changes to the vendored Pipecat framework (`src/pipecat`) are tracked upstream;
see `docs/CHANGELOG.pipecat.md` and https://github.com/pipecat-ai/pipecat.

## [1.4.2] - 2026-07-07

### Fixed

- A single request no longer triggers the agent twice or loops the
  confirmation prompt. When the deterministic router had already dispatched or
  held a task for the turn, the assistant's own acknowledgement could carry a
  delegation marker that fired a second job -- or, for a held task, re-asked
  for confirmation on every reply in an endless loop. Delegation markers are
  now ignored on turns that are merely acknowledging or confirming an
  already-handled task, so one request maps to at most one delegation.

## [1.4.1] - 2026-07-07

### Changed

- Default voice model is now `gemma-12b-huihui` -- a 6.9GB abliterated 12B that
  replies in ~710ms and, unlike the 12GB Q8 build, stays resident alongside the
  intent classifier instead of evicting it. `gemma-e4b-max` remains the
  low-latency "snappy" option (see the preset block in `env.example`).
- Default intent classifier is now `gemma-e4b-aggressive` (was `llama3.2:1b`),
  with the per-turn budget raised to 3s. Benchmarked at 92% routing accuracy
  versus ~49% for the old default, which invented tasks from ordinary chat.
- `env.example` documents the Snappy and Quality model presets with their
  measured latency and VRAM footprint.

## [1.4.0] - 2026-07-06

### Added

- A text-driven test harness (`voice_probe`) for the routing / delegation /
  confirmation mediator. It feeds a deliberate corpus of ~130 prompts through
  the exact brain the voice path uses, scores each decision (chat / dispatch /
  confirm) against a grounded expectation, classifies every failure, and writes
  JSON, Markdown, and HTML reports. Run it with
  `python -m voice_probe run --classifier {stub|live|off}`.
- The harness can benchmark any local Ollama model as the intent classifier
  (`--model <tag> --timeout <secs>`), reporting pass rate and latency so a
  classifier can be chosen on evidence rather than guesswork.

### Changed

- The intent classifier now disables the model's hidden "thinking" pass. Some
  local models spent their whole output budget reasoning and returned nothing,
  which silently degraded every routed turn to plain chat; thinking-capable
  models now work as the classifier.

### Fixed

- The confirmation gate for destructive actions now matches whole words (with
  common inflections) instead of loose substrings. Data-loss and system
  commands that previously slipped through unconfirmed -- "empty the recycle
  bin", "kill the process", "disable the firewall", `rm` -- now correctly ask
  first, while lookalikes ("installer", "dropbox", "a new skill") no longer
  trigger a spurious confirmation.
- Asking how to do something destructive ("search the web for how to delete an
  account") is now treated as the read-only lookup it is, instead of being held
  for confirmation.

## [1.3.1] - 2026-07-06

### Fixed

- The Agents panel's "Last completed" milestone now advances on every step
  instead of freezing after the first one, and falls back to the final summary
  when a backend never reports a milestone at all.
- Delegated agents that exit by asking for permission now create a real
  confirmation. Approval relaunches the task with the original working
  directory, and repeated confirmation loops stop after two retries by default.
- Spoken completion announcements now relay the agent's actual answer instead
  of its short internal label. Delegated agents are told to keep spoken results
  brief so the useful answer is heard in one pass.

## [1.3.0] - 2026-07-06

### Added

- Multi-model wake-word persona routing discovers installed openwakeword
  models, selects the highest-confidence trigger, and applies persona/voice
  settings before releasing command audio to STT.
- A loopback-only, versioned lifecycle WebSocket at
  `ws://127.0.0.1:8765/events` broadcasts allowlisted agent metadata to local
  dashboards without exposing raw output or backpressuring voice.

### Changed

- Directly addressed agent commands now route deterministically. Delegated
  prompts include bounded untrusted conversation context, and rapid corrections
  cancel-and-replace the newest pending or active task without overlapping
  subprocess launches. Concrete coding tasks prefer Code Puppy when configured.
- Long-term memory now captures facts stated casually -- phrasings with a
  leading filler word ("well, my...") or a contraction ("I've got...") that a
  stricter prefix filter previously dropped.
- Spoken agent status updates (started, still working, finished, cancelled,
  failed) now lead with a short result summary or task label instead of reading
  the full, often long, task sentence back verbatim on every update.

### Fixed

- Delegated conversation context remains private to the agent execution prompt
  instead of leaking into lifecycle events, history, UI labels, or spoken progress.
- Completed delegated tasks now relay their actual result -- both spoken and into
  the assistant's context -- so a follow-up like "what were they?" is answered
  from the result instead of restating the original request.
- Requests that merely contain the word "code" (for example "find my validation
  code in an email") no longer get misrouted to the coding agent.
- The Agents panel live-output pane refreshes while a job runs and surfaces the
  final result on completion, so a long-running task no longer appears frozen.
- The Agents panel no longer crashes when it renders legacy task-history rows
  that predate status tracking (their null state/action previously killed the
  GUI event loop).
- Injected agent status speech (task started, still working, finished, handoff)
  now appears in the on-screen transcript. It previously bypassed the LLM text
  path, so later-stage updates were spoken aloud but never shown.
- Whisper's stock silence-hallucinations ("thank you", "thanks for watching",
  a bare "you", etc.) are dropped when they are the entire utterance, so they no
  longer trigger a spurious reply or get parsed as a command.
- An ungrounded read-only lookup the classifier invents with no connection to
  what was said (the small local model regurgitating its weather few-shot
  example is the classic case) is now discarded silently instead of prompting
  you to confirm a task you never asked for. Ungrounded *mutating* tasks still
  ask first, since a state change is worth one question.
## [1.2.0] - 2026-07-05

### Added

- Tiered intent routing keeps explicit commands, acknowledgments, and keyword
  matches on a zero-model-call path, using the resident `llama3.2:1b` model
  only for ambiguous requests with a 1.5-second fallback budget.
- Markerless agent-promise detection prevents claimed-but-unsent work from
  disappearing as an ordinary chat reply.
- Vague capability references ("there's a package that does X, I forgot the
  name, make sure we have it") are detected deterministically and shipped to
  the agent verbatim as an identify-then-install task held for spoken
  confirmation, instead of being rewritten into a generic action prompt.
- Agent scope guardrails: delegated jobs run in a neutral sandbox directory
  (`data/agent_workspace/`) instead of inheriting this repository as their
  working directory, every task carries a scope preamble telling the agent its
  cwd is not the subject of the task, and each job is checked afterwards for
  silent modifications to this application's own working tree -- flagged in
  the job history and announced out loud.

### Changed

- `env.example` now documents only Remote Agent Protocol settings, with every
  optional override disabled by default; `OLLAMA_HOST` consistently configures
  chat, memory, intent routing, model discovery, and health checks.

### Fixed

- Capability audits such as checking for missing skills now route
  deterministically in command and question form; classifier warmup gets a
  cold-start budget without increasing live-turn latency.
- Markerless promises now create a real pending confirmation instead of asking
  the LLM to correct itself, and background-task failures are logged rather
  than silently discarded.
- Agent CLIs that echo status-protocol examples or print terminal rate-limit
  errors no longer report false successful completion.
- Runtime logs and persisted agent jobs now carry full ISO timestamps, and
  silent agent subprocesses fail after five minutes instead of running forever.
- Provider quota/rate/capacity failures are classified from streaming output;
  fatal quota exhaustion is announced immediately and supports spoken OpenAI
  model switching plus an explicit one-shot retry for CodePuppy and Hermes.
- "Install" (not just "uninstall") is treated as a destructive task word, so a
  spoken request that ends up running `pip install` from a third-party source
  now holds for confirmation like any other system-mutating action.

## [1.1.0] - 2026-07-04

Project renamed to **remote-agent-protocol** (the desktop assistant persona is
still Jess) and restructured into a production-style layout.

### Changed

- Application package renamed `jess/` → `remote_agent_protocol/`; entry points
  are now `python -m remote_agent_protocol` (GUI) and
  `python -m remote_agent_protocol.terminal`.
- All runtime state moved into a gitignored `data/` directory
  (`jess_memory.json`, `jess_app_state.json`, `jess_agent_history.json`,
  `jess_qdrant/`, `persona_overrides.json`, `jess_runtime.log`, diagnostics
  bundles); existing files were migrated in place.
- Root decluttered: framework changelog → `docs/CHANGELOG.pipecat.md`,
  community-integrations guide → `docs/`, `pipecat.png` → `docs/assets/`,
  `persona_overrides.example.json` → `config/`,
  `docs/jess-voice-hub.md` → `docs/architecture.md`,
  `tests/test_jess_dashboard.py` → `tests/test_dashboard.py`.
- In-app product branding ("Remote Agent Protocol" window title and
  diagnostics header) follows the new name via `APP_NAME`.

## [1.0.0] - 2026-07-04

First versioned release as a standalone repository.

### Added

- `jess/` application package: the desktop control panel (`python -m jess`),
  terminal mode (`python -m jess.terminal`), voice session, agent bridge,
  wake-word gate, personas, transcript + semantic memory, diagnostics, and
  configuration, all previously loose top-level modules.
- `AgentBridge.shutdown()`: stops and reaps every live agent subprocess before
  the event loop closes. `VoiceSession.run()` now calls it on exit, fixing the
  "Exception ignored in BaseSubprocessTransport.__del__ / I/O operation on
  closed pipe" crash printed at exit on Windows when an agent job was still
  running.
- Project staples: `VERSION`, this changelog, `jess.__version__`.

### Changed

- Entry points: `gui.py` → `python -m jess` (or `start_gui.bat`);
  `demo_local.py` → `python -m jess.terminal` (or `start_terminal.bat`, which
  replaces `start_demo.bat`); `list_audio_devices.py` →
  `scripts/list_audio_devices.py`; `MODELS.md` → `docs/MODELS.md`.
- Configuration, state files (`jess_memory.json`, `jess_app_state.json`,
  `jess_agent_history.json`, `jess_qdrant/`), the runtime log, `.env`, and
  `persona_overrides.json` are now resolved relative to the repository root,
  so the app works from any working directory.
- Pipecat's framework changelog moved to `CHANGELOG.pipecat.md` (towncrier and
  release tooling updated accordingly).

### Removed

- Unused `AGENT_QUESTION_PROMPT` template (agent follow-up questions are spoken
  directly by the bridge; the memory-strip prefix remains so old transcripts
  still clean up).

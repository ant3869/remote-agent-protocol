# Changelog

All notable changes to **Remote Agent Protocol** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and the project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Changes to the vendored Pipecat framework (`src/pipecat`) are tracked upstream;
see `docs/CHANGELOG.pipecat.md` and https://github.com/pipecat-ai/pipecat.

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

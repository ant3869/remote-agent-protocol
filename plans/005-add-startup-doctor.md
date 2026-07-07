# Plan 005: Add a side-effect-free startup doctor

> **Executor instructions**: Execute Plan 004 first. The doctor diagnoses only;
> it must never install packages, download models, launch services, or edit
> configuration. Update `plans/README.md` when done.
>
> **Drift check (run first)**: `git diff --stat a9cd12a61..HEAD -- remote_agent_protocol/dashboard.py remote_agent_protocol/ollama_models.py remote_agent_protocol/stt_factory.py remote_agent_protocol/tts_factory.py remote_agent_protocol/config.py README.md`

## Status

- **Priority**: P2
- **Effort**: M
- **Risk**: LOW
- **Depends on**: `plans/004-package-and-test-desktop-app.md`
- **Category**: direction / DX
- **Planned at**: commit `a9cd12a61`, 2026-07-05

## Why this matters

Startup spans Python extras, audio devices, Ollama reachability and model names,
the configured TTS/STT backend, and external agent executables. Today those
checks are scattered and most failures appear only after opening the GUI. A
small read-only doctor gives operators and support bundles one deterministic
answer before they start a voice session.

## Current state

- `dashboard.py` already provides `ollama_health`, `tts_health`, audio/process path helpers.
- `ollama_models.py` lists registered models but substitutes fallbacks on failure, so the doctor must not treat that fallback as live evidence.
- `stt_factory.py` lazily selects Whisper or Moonshine; constructing either service is too heavy for a doctor.
- `config.py` exposes the selected engines, model, audio indices, agent argument arrays, and Ollama host.
- README setup is Windows-first and already has a requirements list.
- Project style favors pure helpers plus `unittest`; follow `tests/test_dashboard.py`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Doctor tests | `.venv\Scripts\python -m pytest tests/test_doctor.py` | all pass |
| Healthy/help smoke | `.venv\Scripts\python -m remote_agent_protocol.doctor --help` | exit 0, usage text |
| App tests | `$files = rg -l "remote_agent_protocol" tests -g "test_*.py"; .venv\Scripts\python -m pytest @files` | all pass |
| Lint/format | `.venv\Scripts\python -m ruff check remote_agent_protocol/doctor.py tests/test_doctor.py` | exit 0 |

## Scope

**In scope**:
- `remote_agent_protocol/doctor.py` (new)
- `tests/test_doctor.py` (new)
- `README.md`
- Reuse-only calls into `dashboard.py`, `config.py`, and stdlib helpers

**Out of scope**:
- GUI changes, automatic repair, package installation, model pulls, server launches.
- Importing/constructing CUDA, Whisper, Moonshine, Kokoro, or wake-word models.
- Network protocols or remote authentication.
- A plugin/check framework; one module and one list of checks are enough.

## Git workflow

- Branch: `advisor/005-add-startup-doctor`
- Commit: `feat(diagnostics): add read-only startup doctor`
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Define a tiny result model and pure formatter

In `doctor.py`, add a frozen dataclass with check name, status (`ok`, `warn`,
`fail`), and message. Add one formatter that prints one line per result and a
final summary. No framework or JSON schema is needed.

Exit policy: `0` when there are no failures, `1` when any required configured
component fails. Warnings do not fail the command.

**Verify**: formatter and exit-policy unit tests pass.

### Step 2: Implement bounded read-only checks

Use stdlib and existing helpers to check:

1. Python meets the supported version.
2. Ollama responds within a short timeout and the configured chat model and intent model are registered. Query `/api/tags` directly; do not use fallback names.
3. Configured TTS backend is locally reachable or has required configuration, using `dashboard.tts_health`.
4. Selected STT/TTS/wake-word Python modules are discoverable with `importlib.util.find_spec`, without importing model runtimes.
5. Configured mic/speaker indices exist in the enumerated device list when explicit indices are set.
6. Each agent command's executable token is an existing absolute path, `{python}`, or resolvable through `shutil.which`; do not execute it.

Catch each check's expected I/O/config errors and return a failed result; one
broken check must not abort the rest.

**Verify**: tests mock network, module discovery, devices, paths, and config;
no test accesses real hardware or network.

### Step 3: Add the module entry point and documentation

Use `argparse` with no required arguments so
`python -m remote_agent_protocol.doctor` runs checks and `--help` is standard.
Document it beside Run/diagnostics commands in README, including the read-only
guarantee and exit codes.

**Verify**: help smoke and full app tests pass.

## Test plan

- Fully healthy configuration -> exit 0.
- Unreachable Ollama and missing configured model -> failure lines, exit 1.
- Optional disabled wake word -> no missing-package failure.
- Explicit bad device index and missing backend executable -> failures.
- One check raising an expected exception -> remaining checks still render.
- Assert mocks for subprocess launch/install/download are never called.

## Done criteria

- [ ] The doctor runs without starting the GUI or loading AI/audio models.
- [ ] Every network operation has a short timeout.
- [ ] Output names the failing configured component and an actionable setting/package, never secret values.
- [ ] Exit code is machine-usable and tests cover healthy and failed states.
- [ ] Full app tests and Ruff checks pass.

## STOP conditions

- A check requires importing a model runtime or opening an audio stream.
- The implementation starts, installs, downloads, mutates, or prompts interactively.
- Correctness requires a generic plugin/check registry.
- Plan 004 has not made the new module part of the application artifact.

## Maintenance notes

Keep checks aligned with configuration choices, not every optional Pipecat
provider. A future authenticated remote-agent protocol can add one capability
check here after its API is real.

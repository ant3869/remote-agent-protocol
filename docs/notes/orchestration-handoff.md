# Local / Cloud / Hybrid orchestration — handoff note

Status as of 2026-09-14. Written for whoever (human or agent) picks this up
next. **Do not redesign; continue focused hardening.** The architecture below
is settled and has been through several hardening passes already — the
remaining work is closing specific, named gaps, not rethinking the approach.

## Operator quick reference

Everything needed to run real-world sessions, in one place. Details and
rationale are in the sections below.

**Architecture.** `remote_agent_protocol/orchestration/` sits *on top of* the
existing `intent_router` -> `agent_bridge` dispatch pipeline; it decides whether
the *orchestration reasoning* escalates to cloud, never what the harness itself
runs. Two session types wire it identically -- `VoiceSession` (`session.py`,
full mode) and `BrainSession` (`brain.py`, brain mode, what this repo's `.env`
actually runs): both call `PersonaOrchestrator.evaluate()` in
`_resolve_delegation`, pass every real dispatch through `_gate_dispatch()`, and
call `record_outcome()` when a job finishes. `web_gui.py` talks to whichever is
active through one shared method surface; `BrainSessionAdapter`
(`brain_adapter.py`) is the brain-mode facade, now surface-complete and
drift-locked by `tests/test_brain_adapter_orchestration.py`.

**Active defaults: Hybrid + Balanced.** `ORCHESTRATION_MODE=hybrid`,
`ORCHESTRATION_QUOTA_STRATEGY=balanced` -- both config defaults, neither set in
`.env`. Per-persona overrides come from `ORCHESTRATION_PERSONA_MODES_JSON` or
the UI; unlisted personas use the global mode.

**UI.** Web GUI -> **Status** view -> **"Persona orchestration"** panel: mode
buttons (local/cloud/hybrid), strategy dropdown, Copilot auth state with a
"Check now" button, cloud model, local provider health, four telemetry rates
(local resolution, cloud escalation, routing failures, fallback), and an open
"Recent routing decisions" list. Backed by `GET /api/orchestration`, refetched
every 5s while Status is open. A non-200 renders an explicit error, never a
silent "Loading...".

**Copilot auth.** Cloud reasoning uses the official `github-copilot-sdk`
(`.venv\Scripts\python -m pip install github-copilot-sdk`). Authentication is
the SDK's own: `copilot auth login` via the bundled CLI (credentials in the OS
keychain), or `COPILOT_GITHUB_TOKEN` / `GH_TOKEN` / `GITHUB_TOKEN`. RAP stores
and forwards no token itself. **Unauthenticated is a valid test configuration**:
hybrid still runs, escalations find the provider unavailable, fall back to
local, and are recorded as fallbacks -- so the panel's fallback rate is the
quickest way to notice auth has lapsed mid-session.

**Routing thresholds (current, untuned).**

| Setting | Default | Effect |
| --- | --- | --- |
| `ORCHESTRATION_LOCAL_THRESHOLD` | `0.39` | risk score at or below -> local |
| `ORCHESTRATION_CLOUD_THRESHOLD` | `0.65` | risk score at or above -> cloud |
| `ORCHESTRATION_HARNESS_CONFIDENCE_FLOOR` | `0.75` | routing confidence below this is a hard trigger |
| `ORCHESTRATION_GLOBAL_JOB_CAP` | `2` | concurrent jobs across all harnesses |
| `ORCHESTRATION_HARNESS_JOB_CAP` | `1` | concurrent jobs per harness |

Between the two thresholds, hybrid stays local unless a **hard trigger** fires:
multimodal capability need >= 0.5, a previous routing failure, or a real harness
pick landing under the confidence floor. A hard trigger also overrides the quota
strategy.

Risk weights (sum 1.0), every one measured per turn in `orchestration/risk.py`:
context dependency .22, constraint complexity .20, harness-selection uncertainty
.18, result-interpretation need .15, sequential coordination .12, routing
disagreement .08, multimodal need .03, previous routing failure .02.

The earlier factor set was corrected on 2026-09-15 because four of its eight
factors could not move: `job_result_interpretation` and
`cancel_correct_resume_risk` were hardcoded constants (the latter unreachable by
design, since `evaluate()` is never called for cancel/correct turns),
`context_dependency` was binary and pinned to its floor because ungrounded turns
are held upstream, and `constraint_complexity` read the safety tier rather than
counting constraints. With 0.40 of the weight budget dead or constant, the
reachable score topped out at 0.28 against a 0.65 floor. Thresholds were left
alone -- see the follow-ups below.

**Telemetry.** `data/orchestration_telemetry.jsonl`
(`ORCHESTRATION_TELEMETRY_FILE`), JSONL, one record per routed turn, kept
separate from persona memory. It is the raw material for threshold tuning --
see `docs/orchestration-eval-checklist.md`.

**Test baseline (2026-09-15).** Full sweep: `2865 passed, 149 skipped, 323
deselected, 17 errors` -- the 17 are pre-existing vendored-pipecat collection
errors from missing optional extras, unrelated to this work. Focused suite
(orchestration + adapter surface + web GUI + brain streaming + S2S voice +
diagnostics): `227 passed`. Exact commands are under "Commands" below.

**Known non-blocking limitations.**

- Thresholds above are untuned heuristics; no real-usage data exists yet.
- `CopilotProvider.quota()` returns `None` by design -- the SDK GA documents no
  usage/cost endpoint, so quota strategies act on health, not remaining budget.
- Brain mode has no verbatim TTS: `speak_text` logs the phrase as a system note
  instead of speaking it, and the TTS panel's provider/model/options apply only
  to the local stack (the voice id itself does reach the frontend).
- Brain-mode semantic memory add/delete are logged no-ops until a mem0 facade
  exists; short-term transcript memory works.
- The wake-word / post-wake transcription report is still open and most likely
  lives in the external frontend (see below).
- One pre-existing unrelated failure in `tests/test_session_controls.py` and two
  flaky vendored timing tests are deselected by the sweep command below.

## What was implemented

A Local / Cloud / Hybrid persona-orchestration layer sits **on top of** (never
replacing) the existing `intent_router` / `agent_bridge` dispatch pipeline:

- Risk-scored routing (`orchestration/risk.py`) decides whether *orchestration
  reasoning itself* — not the harness's own task execution — should escalate
  to a cloud provider. Weights/thresholds are explicitly tunable heuristics,
  not authoritative math.
- A `ModelProvider` abstraction with `LocalProvider` (Ollama) and
  `CopilotProvider` (the **official** `github-copilot-sdk` package, GA
  2026-06-02 — not a reverse-engineered API; verified live against a real
  authenticated Copilot account).
- Centralized concurrency/duplicate-task admission (`_gate_dispatch` in both
  `session.py` and `brain.py`) immediately before every real dispatch, layered
  *beneath* the existing `_agent_ack_turn` / dedup / destructive-confirmation
  safeguards — not replacing them.
- Structured telemetry (JSONL, separate from persona memory) answering: why
  local vs cloud, risk factor breakdown, whether Copilot changed the harness
  pick, which harness ran, success/fail/cancel, duplicate/unnecessary
  delegation, and local vs cloud reasoning latency.
- A "Persona orchestration" panel in the web GUI's Status view: mode/strategy
  controls, Copilot connect status, cloud model info, local provider status,
  and a live "Recent routing decisions" list.
- Default config: **`ORCHESTRATION_MODE=hybrid`**, **`ORCHESTRATION_QUOTA_STRATEGY=balanced`**
  (confirmed this doesn't override any existing `.env` setting — there wasn't one).

## Current architecture

```
remote_agent_protocol/orchestration/
  models.py         StructuredDecision, RiskFactors, Route (data only)
  risk.py           score_risk() / classify_route() -- pure, tunable heuristic
  concurrency.py    ConcurrencyGuard -- global(2)/per-harness(1) caps + dedup
  quota.py          Economy/Balanced/Performance/Cloud-Preferred strategy
  telemetry.py      TelemetryRecorder -- JSONL, summary(), recent(n)
  orchestrator.py   PersonaOrchestrator -- evaluate()/admit()/admit_by_task()/
                     record_outcome() -- the thing session.py and brain.py both
                     construct and call into
  providers/
    base.py         ModelProvider ABC, ModelCapability, ProviderHealth
    local.py        LocalProvider (Ollama)
    copilot.py      CopilotProvider (official github-copilot-sdk)
```

**Two call sites construct and wire `PersonaOrchestrator`**, because there are
two session types depending on `RAP_MODE`:

- `session.py` (`VoiceSession`, full mode) — original wiring.
- `brain.py` (`BrainSession`, brain mode — **this is what actually runs** per
  this repo's `.env`, `RAP_MODE=brain`) — wired in this pass, after discovering
  the orchestrator had never been connected here at all (see "what was fixed"
  in the previous session's report). Both call `PersonaOrchestrator.evaluate()`
  in their own `_resolve_delegation`, gate every real dispatch through
  `_gate_dispatch()`, and call `record_outcome()` in their job-finished handler.

`web_gui.py` talks to **whichever session type is active** through a shared
surface (`orchestration_status()`, `check_copilot_auth()`,
`set_orchestration_mode()`, `set_orchestration_quota_strategy()`,
`set_persona_orchestration_override()`). `BrainSessionAdapter` (`brain_adapter.py`)
is a compatibility facade around `BrainSession` — **this facade is the thing
most likely to drift out of sync with `VoiceSession` again**; see "remaining
tasks" below.

## Files changed this effort (48 total in git status; orchestration-relevant subset)

New:
- `remote_agent_protocol/orchestration/` (whole package: `__init__.py`,
  `models.py`, `risk.py`, `concurrency.py`, `quota.py`, `telemetry.py`,
  `orchestrator.py`, `providers/__init__.py`, `providers/base.py`,
  `providers/local.py`, `providers/copilot.py`)
- `tests/test_orchestration_risk.py`
- `tests/test_orchestration_providers.py`
- `tests/test_orchestration_concurrency.py`
- `tests/test_orchestration_telemetry.py`
- `tests/test_orchestration_orchestrator.py`
- `tests/test_brain_adapter_orchestration.py` (locks the adapter-drift bug
  class that caused the panel's "Loading..." bug)
- `docs/orchestration-eval-checklist.md` (18-turn real-world eval script)
- `docs/notes/orchestration-handoff.md` (this file)

Modified (orchestration-relevant):
- `remote_agent_protocol/config.py` — new `ORCHESTRATION_*` / `COPILOT_*`
  settings block; defaults changed to hybrid/balanced.
- `remote_agent_protocol/session.py` — orchestrator construction,
  `_resolve_delegation`/`_delegate_ack_ex` wiring, `_gate_dispatch`,
  `orchestration_status()` + control methods, startup provider probe.
- `remote_agent_protocol/brain.py` — same wiring as `session.py`, adapted to
  `BrainSession`'s existing delegation methods.
- `remote_agent_protocol/brain_adapter.py` — delegates the five orchestration
  methods to `self._brain`.
- `remote_agent_protocol/web_gui.py` — `/api/orchestration` GET route,
  `_orchestration_payload()`, four `/api/action` handlers
  (`set_orchestration_mode`, `set_quota_strategy`, `check_copilot_auth`,
  `set_persona_orchestration_override`).
- `remote_agent_protocol/web_app/app.js` — orchestration panel rendering,
  poll-driven refresh (`maybeRefreshOrchestration`), explicit
  unavailable/error states (never silently swallows a non-200 response).
- `remote_agent_protocol/web_app/styles.css` — panel styling only, reusing
  existing theme primitives (`.status-pill`, `.link-button`, `.chip-toggle`).

The other ~26 modified/untracked files in `git status` (`conversation.py`,
`speech_events.py`, `Dockerfile.conversation`, wake-word/VAD test files, etc.)
predate this orchestration effort and are unrelated — do not attribute them
to this work or "clean them up" as part of it.

## Exact current test status

Last full run (2026-09-14, after the log-audit fixes):

```
2865 passed, 149 skipped, 323 deselected, 806 warnings, 17 errors in 386.09s
```

The 17 errors are **pre-existing, unrelated** vendored-pipecat collection
errors from missing optional extras (`fastapi`, `anthropic`, `piper`,
`aws_sdk_sagemaker_runtime_http2`, a `LiveKitTransportClient` NameError, etc.)
— present before this work started, not caused by it. The excluded tests via
`-k` below are two flaky vendored-framework timing tests
(`test_user_bot_latency_observer`) that fail only under full-suite load and
pass in isolation, plus one pre-existing unrelated failure
(`test_session_controls.py::AgentVoiceControlTests::test_default_controls_are_local_and_emit_persistable_change`
— an event-envelope shape mismatch in `conversation.py`, unrelated to
orchestration; do not attribute it to this feature).

Focused suite (orchestration + brain-adapter surface + web GUI + brain
streaming + S2S voice + diagnostics): **227 passed**.

## Follow-ups

### The three `BrainSessionAdapter` compatibility fixes (done 2026-09-14)

`export_snapshot`, `set_agent_scope_preamble`, and `set_tts` are now on the
adapter, so the diagnostics export, the agent-prompt save, and the TTS settings
panel no longer raise `AttributeError` -> HTTP 500 in brain mode:

- **`export_snapshot()`** returns the same keys as `VoiceSession.export_snapshot`
  so one bundle format reads across both modes, built from brain state
  (persona, model override, routing history, `_messages`). The reported voice is
  the one actually published to the realtime frontend, not the persona's own --
  `set_voice` refuses a non-Kokoro id, and that divergence is usually the thing
  a diagnostics export is chasing.
- **`set_agent_scope_preamble()`** delegates to
  `self._brain._bridge.set_scope_preamble()`.
- **`set_tts()`** publishes the voice through the existing `set_voice` ->
  `S2S_VOICE_FILE` handoff (the frontend's real voice channel, passed to it as
  `--external-voice-file` by `voice_stack.py`) and logs the provider/model/
  options, which describe a local TTS stack brain mode never starts.

The allowlist in `tests/test_brain_adapter_orchestration.py` is gone; that file
now asserts the whole `_session` surface in both directions (adapter gaps,
`VoiceSession` gaps, argument compatibility) and exercises all three call paths
through the real `web_gui` handlers. `complete_text`/`stream_text` are
brain-only and stay guarded by `hasattr` at their call sites -- a test pins that
guard in place.

One further mismatch surfaced while auditing and was fixed: the adapter's
`set_manual_prompt_mode` named its parameter `value` where `VoiceSession` names
it `enabled`. Harmless today (web_gui calls it positionally) but it would break
the first keyword call. `set_startup_defaults` intentionally differs -- the
adapter takes `**kwargs` and ignores what brain mode cannot use.

### Voice-stack and agent-bridge fixes (2026-09-14, from a log audit)

A pass over `data/jess_runtime.log`, `jess_agent_history.json`, and
`orchestration_telemetry.jsonl` found failures worth knowing about here:

- **Seven frontend instances were alive at once**, one per voice-stack start
  since the previous day, each still polling the shared S2S mode files and
  holding the microphone. `_spawn` now reaps the process named in the previous
  run's handshake file before starting a new one. This is the most likely
  explanation for the open "not transcribing after the wake word" item below,
  so re-test that before digging into the frontend checkout.
- Agent jobs were being killed with a false "quota exhausted" whenever an agent
  printed content containing provider-error wording, and inactivity timeouts
  recorded no reason at all. Both are classified now, along with auth failures
  and stalls on interactive approval menus (five jobs, ~33 minutes, sat on the
  same menu nothing could answer).
- `test_session_controls.py` and `voice_probe`'s `deleg-unknown-agent` case were
  both stale fixtures, not product bugs, and no longer need deselecting.

**Routing observation, no change made:** across 13 routed turns every decision
was local, with risk scores between 0.065 and 0.28 against a 0.39 local ceiling.
The multi-constraint eval turn scored 0.175. On this evidence the weights, not
just the thresholds, are what keep the 0.65 cloud floor out of reach -- worth
weighing when the tuning data in the checklist is collected.

### Other open items (not urgent, not blocking normal use)

- **Wake-word / post-wake transcription (RESOLVED 2026-09-15)**: root cause was
  the orphaned speech-to-speech frontends, not the frontend's own logic. Seven
  frontend trees (49 processes) were alive at once, each running Parakeet STT on
  CPU and competing for the same microphone. Transcription then took longer than
  the 8-second post-wake `active_window_secs`, so the sentence after the wake
  word was lost. Measured either side of the cleanup, same machine, same model:

  | | before cleanup | after cleanup |
  | --- | --- | --- |
  | `stt_s` (data/s2s_turn_timings.jsonl) | 3.29 s, 9.04 s | 0.74 s, 1.19 s |

  With one frontend running, CPU STT finishes comfortably inside the window and
  the whole chain works: wake detected (scores 0.977-0.989 against a 0.5
  threshold), VAD opened and closed the segment, Parakeet transcribed, the text
  reached RAP over `/v1/chat/completions`, and RAP routed and answered it. The
  CPU-only torch build in the frontend's venv (`2.11.0+cpu`) is therefore not
  the cause and was left alone.

  One edge worth knowing: a wake word spoken *while the assistant is still
  speaking* is detected but captures nothing, because the server only logs
  "listening re-enabled" when the response completes. That is a barge-in
  question, not the bug that was being chased.
- **Threshold tuning**: explicitly deferred. Do not touch
  `ORCHESTRATION_LOCAL_THRESHOLD` / `ORCHESTRATION_CLOUD_THRESHOLD` /
  `ORCHESTRATION_HARNESS_CONFIDENCE_FLOOR` until real usage data exists — see
  `docs/orchestration-eval-checklist.md` for the recommended collection
  process (3-5 sessions, ~50-100 routed turns, a few real cloud escalations)
  before revisiting.
- **Future cleanup -- a shared session protocol** (deliberately NOT done):
  `VoiceSession` and `BrainSessionAdapter` keep their contract in sync by test
  rather than by type. A `typing.Protocol` both must satisfy (or an ABC) would
  move that check from a source-scanning test to the type checker and cover
  dynamic `getattr` dispatch, which the current test cannot see. Recorded as
  technical debt only -- the user deferred it on 2026-09-14 in favour of
  real-world testing. Do not start it without asking.
- **`CopilotProvider.quota()`** returns `None` by design — the SDK documents
  no usage/cost endpoint as of GA. Do not add an undocumented-endpoint
  workaround; re-check the SDK's own changelog before assuming this is fixable.

## Commands

Install the real Copilot SDK once (already done in this environment, but
needed on a fresh machine/venv):
```
.venv\Scripts\python -m pip install github-copilot-sdk
```
(Real API calls require `copilot auth login` via the bundled CLI; tests never
touch the real SDK — everything is mocked.)

Focused orchestration + brain-adapter suite:
```
.venv\Scripts\python -m pytest tests/test_orchestration_risk.py tests/test_orchestration_providers.py tests/test_orchestration_concurrency.py tests/test_orchestration_telemetry.py tests/test_orchestration_orchestrator.py tests/test_brain_adapter_orchestration.py tests/test_web_gui.py tests/test_web_layout_v3.py tests/test_brain_streaming.py tests/test_s2s_voice_control.py tests/test_diagnostics.py -q
```

Full regression sweep (excludes known pre-existing flaky/unrelated failures):
```
.venv\Scripts\python -m pytest tests/ -q --continue-on-collection-errors -k "not test_aws_credentials and not test_bridge_processor and not test_context_aggregators_universal and not test_deprecation_markers and not test_evals_suite and not test_service_init and not test_startup_timing_observer and not test_voice_probe and not test_websocket_proxy and not test_user_bot_latency_observer"
```

Lint/format (run on whatever you touch):
```
.venv\Scripts\python -m ruff check <paths>
.venv\Scripts\python -m ruff format --check <paths>
```

Run the app and check the panel live (brain mode, per this repo's `.env`):
```
.venv\Scripts\python -m remote_agent_protocol
```
Then open the printed URL → **Status** view → "Persona orchestration" panel.
`GET /api/orchestration` should return HTTP 200 with `mode`, `local_status`,
`cloud_status` (including `models`/`reasoning_model`), and `recent_events`
populated once at least one delegation has been routed.

## Do not redesign; continue focused hardening

The architecture (provider abstraction, risk scoring, centralized admission
gate, JSONL telemetry, panel) is settled across multiple hardening passes and
a live smoke test against a real Copilot account. The three adapter gaps
above are the same one-line-delegation fix repeated three times — resist the
urge to refactor `brain_adapter.py`'s pattern while fixing them. If you find
a *fourth* method gap beyond these three, that's a sign to look for a
structural fix (e.g. a shared protocol/interface check in CI), but that's a
decision for the user to weigh in on, not something to do unilaterally.

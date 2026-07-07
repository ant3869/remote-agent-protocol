# voice_probe — text-driven test harness for the voice mediator

A repeatable way to probe the voice assistant's **decision brain** — the
routing / delegation / confirmation logic every request passes through — using
**text prompts as a stand-in for speech**. Typed input hits the exact same
brain as voice (`VoiceSession.send_text` → `_resolve_delegation` →
`IntentRouter.route`), so a text corpus is a faithful, deterministic, batchable
substitute for you inventing prompts by hand.

It systematically surfaces: routing/delegation mistakes, confirmation failures
(too little **and** too much caution), hallucinated tasks, dropped requests,
grounding leaks, over-eager dispatch, and latency problems — then ranks them
worst-first with concrete fix suggestions.

## What it tests (and what it deliberately doesn't)

The harness drives `IntentRouter.route()` plus the confirmation gate (replayed
from `session._delegate_ack_ex` in `schema.effective_outcome`). It does **not**
boot the audio pipeline (mic/STT/TTS/speakers) or spawn real agent
subprocesses — those are slow, non-deterministic, and orthogonal to the
routing/confirmation behavior under test. Every user-visible decision collapses
to one of three **outcomes**:

| outcome    | meaning                                                        |
|------------|----------------------------------------------------------------|
| `chat`     | nothing dispatched; the persona just replies                   |
| `dispatch` | a task handed to an agent with no confirmation                 |
| `confirm`  | held for a spoken/GUI yes-or-no first (destructive/uncertain)  |

For a real end-to-end run against live agents + audio, see
[Live end-to-end](#live-end-to-end) below.

## Quick start

```bash
# Offline: exercises the deterministic tiers + all plumbing, runs anywhere.
.venv\Scripts\python -m voice_probe run --classifier stub

# The real evaluation: uses the actual Ollama intent classifier (tier 6).
# Needs Ollama up with INTENT_MODEL resident.
.venv\Scripts\python -m voice_probe run --classifier live

# Benchmark a SPECIFIC classifier model without touching config. Large models
# need a wider budget (--timeout) or every case times out and degrades to chat.
.venv\Scripts\python -m voice_probe run --classifier live --model gemma-e4b-aggressive --timeout 15
.venv\Scripts\python -m voice_probe run --classifier live --model hermes-20b --timeout 30

# Isolate what the keyword net alone can do (semantic tier disabled).
.venv\Scripts\python -m voice_probe run --classifier off

# List / filter the corpus without running.
.venv\Scripts\python -m voice_probe list --category delete --difficulty brutal

# Re-render reports from an existing run.
.venv\Scripts\python -m voice_probe report data\voice_probe\run-live-*.jsonl
```

Each `run` writes three files under `data/voice_probe/` (gitignored):
`run-<mode>-<ts>.jsonl` (raw results), `.md` (report), `.html` (visual report).
Exit code is non-zero when any case **fails**, so it drops into CI cleanly.

### Classifier modes

| mode   | tier-6 classifier      | classifier-dependent cases | use for                          |
|--------|------------------------|----------------------------|----------------------------------|
| `stub` | deterministic fake     | recorded as `info`         | CI, offline, validating tiers 1–5 |
| `live` | **real Ollama call**   | **graded for real**        | the actual evaluation            |
| `off`  | disabled               | fall through to chat       | isolating the keyword net        |

Cases whose correct outcome only the semantic tier can decide are marked
`classifier_dependent`; outside `--classifier live` their mismatches are
downgraded to `info` rather than blamed on a synthetic stub.

### Choosing a classifier model

The tier-6 model is set by `INTENT_MODEL` (`.env`). To pick one, benchmark
candidates against the corpus and compare the report's **pass rate** and
**latency p95** — the classifier runs on every routed turn, so it must be both
accurate *and* fast enough for the live turn budget (`INTENT_TIMEOUT_SECS`):

```bash
for m in llama3.2:1b gemma-e4b-aggressive gemma-12b; do
  .venv\Scripts\python -m voice_probe run --classifier live --model "$m" --timeout 15
done
```

Each writes a `run-live-<model>-<ts>.{jsonl,md,html}` so results don't collide.
A model whose p95 exceeds `INTENT_TIMEOUT_SECS` will time out in production and
silently degrade turns to chat — the report flags those as `latency`. Two
resident models (voice + classifier) also share VRAM, so favor a small, fast,
accurate classifier over the biggest one.

## The test corpus (`corpus.py`)

~130 prompts, easy → brutal, across every category the system must handle.
Expectations are grounded in the *actual* tier-by-tier routing logic, so a fail
is real signal, not a mis-specified test. Several cases deliberately encode a
**known/suspected weakness** (with a `note` explaining the trap) — e.g. a
read-only web search whose text contains "delete", an unconfigured named agent,
unicode-styled destructive verbs, mid-sentence prompt injection.

Categories: `conversational`, `about-self`, `info-lookup`, `tool-use`,
`delegation`, `stay-chat`, `github`, `install-app`, `winget`, `edit-file`,
`create-file`, `organize`, `delete`, `move-rename`, `capability` (skills/
packages/tools), `multi-step`, `ambiguous`, `under-specified`, `risky`,
`needs-confirm`, `no-confirm`, `noise`, `routing-weakness`, `adversarial`.

Add a case by appending a `ProbeCase` to `CORPUS`. Only `expect_outcome` is
asserted hard; `expect_source` / `expect_intent` add quality checks; set
`expect_outcome=None` for an exploratory prompt you only want logged and timed.

## Result schema (`schema.py`)

Every prompt produces a `ProbeResult` row with: the prompt + metadata, the full
flattened routing decision (`outcome`, `action`, `intent`, `category_label`,
`source` tier, `agent`, rewritten `task`, `confidence`, `risk`, `grounded`,
`requirement`, `fallback`, `reason`), `latency_ms`, the expectation, and the
`verdict` + `failure_kind` + `failure_detail`.

Verdicts: `pass` (exact match) · `partial` (right outcome, wrong tier/agent/
grounding) · `fail` (wrong user-visible outcome — a real defect) · `info`
(exploratory or classifier-dependent-offline).

## Failure taxonomy → the user's diagnostic list

| `failure_kind`            | maps to                                            | severity |
|---------------------------|----------------------------------------------------|:--------:|
| `missing_confirmation`    | confirmation failure / unsafe behavior             | 0 (worst)|
| `dropped_risky_request`   | confirmation failure (risky request dropped)       | 1        |
| `grounding_leak`          | stale context / ungrounded task                    | 2        |
| `dropped_request`         | intent misunderstanding / routing failure          | 3        |
| `hallucinated_dispatch`   | hallucinated task/action                           | 4        |
| `hallucinated_confirm`    | hallucinated task/action                           | 5        |
| `over_confirmation`       | unnecessary caution / unnecessary refusal          | 6        |
| `routing_tier_mismatch`   | routing quality (wrong tier or agent)              | 7        |
| `latency`                 | latency/performance problem                        | 8        |
| `routing_crash`           | tool/execution failure (route() raised)            | fail     |

## Metrics tracked

- **Pass rate (graded)** — the headline number, excludes `info`.
- **Failure-mode histogram** — which defect classes recur.
- **Per-category breakdown** — where the system is weak by request type.
- **Latency** — p50 / p95 / max / mean, plus the slowest decisions.
- **Prioritized issue list** — every fail, ranked by the severity above.

## How to review a run

1. Read the **Scoreboard** and **Common failure modes** — is any `severity 0/1`
   (unsafe dispatch, dropped risky request) present? Those come first, always.
2. Walk the **Highest-priority issues** section top-down; each entry shows the
   routed decision, the task text, and the probe's `note` (why it exists).
3. Check **partials** for quality drift (right call, wrong tier/agent).
4. Apply the **Recommended improvements** — they translate the recurring
   failure kinds into concrete code pointers (`AGENT_DESTRUCTIVE_WORDS`,
   `voice_commands` keyword nets, the grounding guard, classifier warmup).
5. Re-run `--classifier live` and diff the pass rate to confirm the fix.

## Live end-to-end

The router harness intentionally stops at the decision. To watch a real request
run through the *entire* stack (LLM reply, agent subprocess, spoken status
updates), drive the running app directly — `VoiceSession.send_text(prompt)` from
the GUI/terminal, or the typed-input box — with `AGENT_BACKENDS` pointed at the
`mock` backend for a safe, deterministic agent. The event bus (`on_event`)
carries every `routing`, `agent_confirm`, and `agent_job` event you'd want to
capture; those are the same events this harness scores offline.

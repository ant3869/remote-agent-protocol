# Roadmap gap closure (Phases A, B, C0) -- progress log

**Date:** 2026-09-26 · **Branch:** `claude/zen-allen-eynk4c`, based on `feat/c0-model-providers` @ `476a918`
**Roadmap:** `docs/notes/grok-style-orchestration-roadmap.md`

## Branch state

`main` (2026-07-11) is on the old pipecat-fork history. All current work lives on the
standalone history that starts at `f5fba48` ("Restructure project layout and start standalone
project history"): `release/v1.13.0` -> ... -> `fix/brain-stack-readiness-and-audio-devices`
-> `feat/c0-model-providers` -> this branch. They share no merge base with `main`, so `main`
cannot be fast-forwarded. It has to be replaced (or the default branch switched) deliberately.
`integration/agent-improvements-with-animated-butler` and `feature/animated-butler-avatar`
are already folded into the release history.

## What was still open, and what was done

| Roadmap item | State before | Done here |
|---|---|---|
| A2 RAP-owned model pinning with per-harness ordered lists and failover | One `openai` target per harness plus a static default | `AGENT_MODEL_CHAINS_JSON` failover on quota/auth/model-not-found, `AGENT_MODEL_TARGETS_JSON` for any provider, the winning model sticks, spoken switch for every configured provider |
| A2 "Switch Hermes to OpenRouter" in brain mode | Not wired at all | Handled locally, with a fixed reply built from the bridge result |
| A3 Truthful health | Self-check only; real work never counted | Finished jobs count as proof of life (with latency and model), real quota/auth failures mark the agent down, busy agents are reported as working, confirmed responses are cached for `AGENT_HEALTH_FRESH_SECS` |
| B4 Pin the persona name | Not done | Each system prompt pins the character's own name |
| C0 Intent/Orchestration role assignment | Saved but ignored by both callers | Both walk their chain, then fall back to local |
| C0 deferred: chain reorder, "what answered" view | Deferred | Earlier/later controls; `lastAnswers` per role in the editor |

## Bugs found on the way (all with regression tests)

- **Tests touched real app data.** Provider keys went to the real Credential Manager. The
  agent registry, job history, UI and s2s state, and the endpoint file were read from and
  written to `data/`. Everything is now sandboxed in `tests/conftest.py`.
- **Providers panel.** The "Import from .env" banner could never show (`.hidden` class vs the
  `hidden` attribute), and the manual model-ID field was wiped on every re-render.
- **Lost cancels.** A cancel during process spawn, or during quota termination, was
  overwritten, so the job was recorded as failed. A status line drained after a cancel could
  revive the job.
- **Leaked processes on shutdown.** `shutdown()` skipped jobs that hadn't spawned yet. This
  was the intermittent `test_shutdown_reaps_live_jobs_before_loop_close` failure.

## Tests

Linux container run of every app-layer test file: 1671 passed and 9 failed. All 9 failures
are platform-only and not caused by this work:
- 8 in `test_process_guard.py`, which needs `ctypes.windll`
- 1 in `test_voice_stack.py`, which takes the POSIX `os.kill` path, where the test's mocks
  assume `taskkill`

`ruff check remote_agent_protocol tests` is clean. The UI changes were syntax-checked with
`node --check`, not clicked through.

## What Ant must do

1. Add model chains to `.env` for the harnesses that keep hitting quota. The provider args
   must be flags that harness's CLI really accepts. See `env.example`.
2. Try "check all the agents" twice in a row. The second answer should come from cache in
   seconds.
3. Assign the Intent role in Models & providers, speak a turn, and confirm the role row shows
   "Last answered by ...".
4. Decide what to do about `main` (see Branch state).

## Still open

- Phase A exit: all 5 harnesses completing a real task. This needs your credentials and
  quota, as listed in `docs/notes/2026-09-25-harness-audit.md`.
- `/api/providers` still uses snake_case while `/api/status` uses camelCase. This is cosmetic.
- Phase C (tool-calling Butler) is next on the roadmap.

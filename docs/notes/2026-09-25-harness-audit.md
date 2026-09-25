# Harness audit — Phase A, item 1 (read-only)

**Date:** 2026-09-25 · **Branch:** `fix/brain-stack-readiness-and-audio-devices` @ `f0a755df4` (+ uncommitted WIP)
**Method:** for each of the 5 entries in `config.AGENT_BACKENDS`, built the command with `agent_bridge.build_command` (no `extra_args` — `_model_overrides` is empty on a fresh process, so this is each harness's own default model, exactly as a live RAP job would launch it today), ran it with a 120s timeout and `cwd=AGENT_WORKSPACE_DIR`, task text `"Reply with exactly: RAP_OK"`. No config, credential, account, or install was changed. One follow-up probe (openclaw, with the corrected binary name only) was run to get past a launch-layer bug and see the real gateway error underneath — see §OpenClaw.

Cross-referenced against `data/jess_agent_history.json` (100 jobs, filtered to `started_at >= 2026-09-01`).

---

## Headline finding: RAP can't even launch 2 of the 5 harnesses on this machine

`config.AGENT_BACKENDS` templates use bare executable names (`"openclaw"`, `"codex"`, …), and RAP launches them with `asyncio.create_subprocess_exec` (`agent_bridge.py:1519`, `:2037`) — i.e. no shell. On Windows, `CreateProcess` with no shell **only auto-appends `.exe`** when a bare name has no extension; it does not consult `PATHEXT` the way `cmd.exe` or `shutil.which` do. Both `codex` and `openclaw` are installed as npm global shims, which are `.cmd` files, not `.exe`. Result:

- **codex**: no `codex.exe` exists anywhere on PATH → RAP's launch fails outright with `WinError 2` before codex ever runs, every time, regardless of account/quota state.
- **openclaw**: there *is* a same-named `.exe` on PATH — `C:\Users\SuperHands\.local\bin\openclaw.exe`, an unrelated third-party installer for a product called **cmdop** (`cmdop.com`, installed 2026-08-13). RAP's bare `"openclaw"` silently launches *that* binary instead of the real gateway CLI, which rejects `agent --agent jax --message-file …` with an argparse usage error. `job-7`'s own summary text ("OpenClaw is installed... npm and .local\bin") shows this collision has been visible in history without being identified as the cause.

Verified fix: passing the extension explicitly (`"openclaw.cmd"`, `"codex.cmd"`) resolves to the correct binary every time, independent of PATH ordering. This is a one-line change per template in `config.py`.

Historical `jess_agent_history.json` entries for openclaw (09-20/09-22/09-23) show genuine *gateway*-level errors (auth profile unavailable, 9router 404, DB schema v19, codex-plugin degraded) — meaning the correct CLI **was** reached at other times. So the `.exe` shadowing looks intermittent/environment-dependent rather than total, which makes it worse, not better: it's a silent, unpredictable failure mode layered on top of the harness's own real problems.

---

## Hermes

| | |
|---|---|
| **Effective model/provider** | Active profile is `mera` (`%LOCALAPPDATA%\hermes\active_profile`). `profiles\mera\config.yaml`: `provider: openrouter`, `default: openai/gpt-6-luna-pro`, `base_url: https://openrouter.ai/api/v1`. `profiles\mera\auth.json.active_provider` currently reads `openai-codex` (a different value — see root cause). The **top-level, non-active** `hermes\config.yaml` still points at `provider: lmstudio` with `base_url: https://generativelanguage.googleapis.com/v1beta` — a stale/inconsistent default that isn't in play while `mera` is active. |
| **Probe result** | **PASS.** Exit 0, 34.1s, replied `RAP_OK` correctly via `hermes chat --query-file …`. |
| **History since 09-01** | 24 jobs: 10 done, 13 failed, 1 cancelled. Dominant `failure_kind: quota` (11×) — `(RESOURCE_EXHAUSTED): You exceeded your current quota, please check your plan`, all Gemini. `credential_pool.gemini` in `auth.json` has 2 credential entries. |
| **Root cause** | Gemini-credential quota exhaustion drove repeated failures; hermes appears to rotate `active_provider` across its credential pool on failure (currently parked on `openai-codex`, config default says `openrouter`) without RAP ever knowing which model actually answered. The top-level default profile's config is also internally inconsistent (`lmstudio` provider + Google API `base_url`), a landmine if `active_profile` ever reverts to it. |
| **Fix — Ant must do** | Check Gemini plan/quota in the hermes credential pool, or drop it from rotation entirely. Clean up the stale top-level `config.yaml` (mismatched provider/base_url) so it's safe if `active_profile` ever changes. |
| **Fix — RAP should do** | Phase A3's truthful-health probe should record *which model actually answered* (hermes tells you in its own session banner) instead of trusting `AGENT_MODEL_TARGETS`, since the harness silently rotates providers underneath RAP. |

## OpenClaw

| | |
|---|---|
| **Effective model/provider** | `openclaw.json` → `.agents.entries.jax.model`: primary `9router/ds/deepseek-v4-pro`, fallbacks `9router/cx/gpt-5.5-review`, `google/gemini-3.6-flash`. `9router` and `local-proxy` are both custom OpenAI-compatible providers pointed at `http://127.0.0.1:20128/v1` — a **local proxy shared with hermes and code-puppy** (see cross-harness note below). |
| **Probe result (configured template)** | **FAIL at the launch layer.** Exit 2 in 0.19s: `openclaw: error: unrecognized arguments: agent --agent jax --message-file …` — this is the cmdop installer, not OpenClaw (see headline finding). |
| **Probe result (corrected binary, `openclaw.cmd`)** | **FAIL, but a real one.** Exit 1, 40.4s: `Preflight compaction required but failed: Summarization failed: …; Retry-After: 120 seconds` — the jax session's context-compaction step can't reach its summarization model. |
| **History since 09-01** | 18 jobs: 2 done, 14 failed, 2 cancelled. Recent `failure_detail`: `All models failed (3): openai/gpt-5.6-luna: Auth profile "openai:anthon3869@gmail.com" is temporarily unavailable… \| 9router/cx/gpt-5.5-review: 404 No active credentials…`; `Agent harness runtime "codex" is unavailable (reason=owner-plugin-degraded)`; `OpenClaw agent database … uses schema version 19; stop active agents and run openclaw doctor --fix`. |
| **Root cause** | Stacked: (1) launch-layer PATH collision above; (2) jax's model chain depends on the shared local proxy, which has its own credential problems (see cross-harness note); (3) OpenAI auth profile `openai:anthon3869@gmail.com` intermittently unavailable; (4) the codex plugin inside OpenClaw is degraded; (5) the agent's own sqlite DB is on schema v19 and wants a doctor pass. |
| **Fix — Ant must do** | Run `openclaw doctor --fix` (the tool names this itself for the schema-v19 problem). Re-check the `openai:anthon3869@gmail.com` auth profile. Investigate/repair the degraded codex plugin (`openclaw plugins inspect codex --runtime --json`, per the harness's own error text). Decide whether to keep or remove the cmdop installer at `~/.local/bin/openclaw.exe` — even after the RAP-side fix below, it will keep shadowing any *other* tool on this machine that launches `openclaw` without a shell. |
| **Fix — RAP should do** | Change the `"openclaw"` template entry to `"openclaw.cmd"` in `config.AGENT_BACKENDS` (§ config.py:501-508) — closes the launch-layer bug unconditionally, independent of PATH order or what else is installed. |

## Code Puppy

| | |
|---|---|
| **Effective model/provider** | `puppy.cfg`: `model = local-proxy-claude-haiku`. `extra_models.json` resolves that to a `custom_openai` entry: `name: cc/claude-haiku-4-5-20251001`, `custom_endpoint.url: http://localhost:20128/v1` — the **same local proxy** OpenClaw's `9router`/`local-proxy` providers use, authenticated via `$NEXUS_LLM_API_KEY`. |
| **Probe result** | Exit 0 (process launched fine) but the task itself failed: `Unexpected error: status_code: 404, model_name: cc/claude-haiku-4-5-20251001, body: {'message': 'No active credentials for provider: claude', 'type': 'invalid_request_error', 'code': 'model_not_found'}`. |
| **History since 09-01** | 48 jobs: 22 done, 20 failed, 6 cancelled — the busiest and least reliable harness. Repeated `Error executing prompt: status_code: 404, model_name: …` (09-20, 09-21, 09-23), plus `failure_kind` of `rate_limit` (3×), `quota` (3×), `timeout` (1×). Roadmap's original phrasing ("empty model") is superseded — the model name is now populated (`cc/claude-haiku-4-5-20251001`) but still 404s, so the defect moved from "unset model" to "unreachable credential" without the failure clearing. |
| **Root cause** | The local proxy on port `127.0.0.1:20128` (confirmed listening — `node.exe`, currently PID 66480) has no active Claude credential for the `claude-haiku` route it's asked to serve. `~/.code_puppy/claude_code_oauth.json` is 8 days stale (last modified 2026-09-17, today is 2026-09-25) — a plausible expired/rotted token behind that proxy. |
| **Fix — Ant must do** | Refresh/re-auth whatever feeds the local proxy's Claude credential (the stale `claude_code_oauth.json` is the first thing to check). Separately, code-puppy's own `chatgpt_oauth.json`/`claude_models.json`/`copilot_*` credentials should be checked for the `rate_limit`/`quota` failures unrelated to the proxy route. |
| **Fix — RAP should do** | `AGENT_MODEL_TARGETS["code-puppy"]["openai"]` (`--model chatgpt-gpt-5.5`) already exists and doesn't depend on the broken local proxy. Until the proxy credential is fixed, RAP should default code-puppy jobs onto that target instead of code-puppy's own configured default. |

## Codex

| | |
|---|---|
| **Effective model/provider** | `~/.codex/config.toml`: `model = "gpt-5.6-terra"`, `model_provider = "openai"` — the real OpenAI API, **not** the local proxy (alternate `[model_providers.local-proxy]` / `[model_providers.9router]` blocks exist in the file but aren't active). |
| **Probe result** | **Never launches.** `executable not found: [WinError 2] The system cannot find the file specified` — see headline finding; there is no `codex.exe` on PATH at all, only `codex.cmd` (confirmed via `shutil.which`). A follow-up live probe with the corrected `codex.cmd` binary was blocked by this session's own safety classifier (`--sandbox danger-full-access` read as agent creation), so the OpenAI-side auth/quota state beyond the launch bug is not independently re-verified here — config.toml plus history is the evidence. |
| **History since 09-01** | 5 jobs: 1 done, 1 failed (`failure_kind: quota`), 3 cancelled. The one "done" job confirms codex *has* launched successfully at some point recently, so the PATH bug is either new or intermittent the same way OpenClaw's is — not a permanent block historically, but it is a permanent block **today**, reproduced twice (once via the scripted probe, once via direct `asyncio.create_subprocess_exec`). |
| **Root cause** | Same class of bug as OpenClaw: bare `"codex"` in the template, no `.exe` on PATH, `asyncio.create_subprocess_exec` won't find `codex.cmd` without the extension. Separately, real OpenAI quota/credit exhaustion shows up in history once launch works. |
| **Fix — Ant must do** | Check OpenAI account credits/quota behind the `openai` provider in `config.toml`. |
| **Fix — RAP should do** | Change the `"codex"` template entry to `"codex.cmd"` in `config.AGENT_BACKENDS` (§ config.py:531) — without this, codex cannot run at all regardless of account state. |

## Claude Code

| | |
|---|---|
| **Effective model/provider** | `~/.claude/settings.json`: `"model": "sonnet"` (Claude Sonnet 5, via the authenticated CLI plan — no API key, no `ANTHROPIC_MODEL`/`CLAUDE_CODE_MODEL` env override present). Project-level `.claude/settings.json` is empty and doesn't override it. |
| **Probe result** | **PASS.** Exit 0, 8.2s, replied `RAP_OK` correctly via `claude -p --dangerously-skip-permissions …`. |
| **History since 09-01** | 5 jobs: 2 done, 3 failed. `2026-09-23T09:16` — `You've hit your weekly limit — resets Sep 25, 8am (America/Chicago)`. That reset is **today**, which is consistent with the probe passing cleanly just now. Two other failures (09-17, both) — `Agent failed with exit code 1 without reporting details`, i.e. RAP captured no diagnostic content at all. |
| **Root cause** | The weekly-limit failures are plan-usage limits, not a config problem — already resolved by the reset. The two silent exit-1 failures are a RAP-side observability gap: something failed and RAP has no stderr/detail to show for it. |
| **Fix — Ant must do** | Nothing right now — the weekly-limit window has reset. Watch usage if this recurs before next Thursday's reset. |
| **Fix — RAP should do** | Capture and surface stderr/exit detail on a non-zero `claude` exit instead of recording "failed with exit code 1 without reporting details" — this is exactly the kind of truthful-health gap Phase A3 is meant to close. |

---

## Cross-harness note: one shared local proxy is a single point of failure for three harnesses

Hermes (top-level default profile's `lmstudio`/Gemini slot), Code Puppy (`local-proxy-claude-haiku`), and OpenClaw (`9router`/`local-proxy` providers) **all** route through the same local process — a Node service (`node.exe`, currently PID 66480) listening on `127.0.0.1:20128`. Confirmed live: this proxy is up (port is listening), but the specific route code-puppy hit for a Claude model came back `No active credentials for provider: claude`, and the stale `claude_code_oauth.json` (8 days old) sitting in code-puppy's own directory is a strong candidate for what feeds that route's credential. Fixing that one credential is a good first move before chasing per-harness symptoms individually — it's plausibly the single highest-leverage fix in this audit.

One local API key for this proxy was visible in plaintext while reading `openclaw.json`'s `models.providers` block during this audit (it authorizes only loopback requests to `127.0.0.1:20128`, not any external account) — it was not copied into this report; flagging its existence here in case Ant wants to rotate it regardless.

---

## Recommended default model list for Phase A2 (`AGENT_MODELS_JSON`)

Ordered by what's actually verified working today, cheapest/most-reliable first:

| Harness | Priority 1 | Priority 2 | Notes |
|---|---|---|---|
| **hermes** | `openrouter` / `openai/gpt-6-luna-pro` (verified PASS just now) | `openai-codex` (currently the harness's own rotated-to credential) | Keep Gemini out of rotation until quota is confirmed clear. |
| **code-puppy** | `openai` / `chatgpt-gpt-5.5` (existing `AGENT_MODEL_TARGETS` entry, doesn't touch the broken local proxy) | — | Don't default to `local-proxy-claude-haiku` until the proxy's Claude credential is fixed. |
| **openclaw** | `google/gemini-3.6-flash` (jax's own 3rd fallback, avoids both the local-proxy credential issue and the `openai` auth-profile issue) | `9router/cx/gpt-5.5-review` once 404s clear | Needs the launch-layer fix (`openclaw.cmd`) and `openclaw doctor --fix` before any model choice matters. |
| **codex** | `openai` / `gpt-5.6-terra` (current `config.toml` default; real OpenAI, not the local proxy) | `local-proxy` / `9router` blocks already defined in `config.toml` as a fallback | Needs the launch-layer fix (`codex.cmd`) before it can run at all. |
| **claude-code** | `sonnet` (current default, plan-based) | — | Single account/plan — no meaningful second target; the fix here is observability, not model choice. |

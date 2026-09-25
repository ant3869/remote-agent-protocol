# RAP — Grok-Bot-Style Orchestration Roadmap (revised)

**Revised:** 2026-09-25 · **Baseline:** branch `fix/brain-stack-readiness-and-audio-devices` @ `f0a755df4` (+ uncommitted WIP)
**Evidence:** `data/conversations.json` (171 turns, 09-19 → 09-25), `data/jess_agent_history.json` (100 jobs), `data/jess_runtime.log` (383 routing decisions), `data/agent_registry.json`
**Supersedes:** the ChatGPT draft, which was written against GitHub `main` @ `f63531a` (2026-07-11) and assumed most of the machinery didn't exist yet.

---

## 1. What the logs show

The architecture pieces exist: control plane, conversation hub, floor manager, narration, and consult. In daily use it still fails at the simplest things.

### 1.1 The workers are down most of the time

Jobs since 2026-09-01:

| Agent | Done | Failed | Cancelled | Dominant failure |
|---|---|---|---|---|
| code-puppy | 22 | 20 | 6 | `404 model_name:` (empty model), quota, chit-chat routed to it |
| hermes | 10 | 13 | 1 | Gemini 429 quota (11×) |
| openclaw | 2 | 14 | 2 | model chain auth: OpenAI profile, 9router 404, Anthropic workspace header, embeddings credits, DB schema v19, codex plugin degraded |
| claude-code | 2 | 3 | 0 | weekly limit |
| codex | 1 | 1 | 3 | quota |

By 09-23 all five were unhealthy in `agent_registry.json`. Ant diagnosed it himself: *"none of them work because none of them are set to the right model right now."* RAP has no way to set or fix a harness's model beyond one `openai` target for code-puppy and hermes.

**Grok's bots run on one provider under one account and are always up. No orchestrator looks good when every worker it dispatches to fails.**

### 1.2 The brain is a regex-plus-3B-classifier stack, not a reasoning model

- 65 of 383 routing decisions fell through every tier (`no routing tier matched`, 11 of them classifier timeouts).
- `INTENT_MODEL=qwen2.5:3b` decides whether something is a task, which agent gets it, and what the task text is.
- Real failures:
  - "Check for any active agents" was dispatched to **code-puppy**, when it should have been a local status read. The hub then refused with "I don't currently have a verified agent for that." (09-25 07:15)
  - "Are you checking on that or what was the status?" got a verdict that echoed the classifier prompt's own example, which was discarded, so the turn fell back to chat (09-25 07:18).
  - "Have Codex do it", "Open claw", and "Okay, try claud code." were dispatched with those words as the literal task text. The referent ("the email search", "check on Code Puppy") was lost.
  - "Instruct Codex to check on Code Puppy" was sent **to** Code Puppy.
  - Small talk ("snacks", "kitchen produce") became Code Puppy jobs.
  - Unrelated questions got "Which task do you mean?"

### 1.3 The persona narrates instructions, not facts

Application context is injected as fake user turns (`User request: … Application context (not a new request): [Voice delegation dispatched … Tell the user in ONE short sentence …]`). The persona model then improvises around it:

- "I've dispatched Hermes" when OpenClaw ran. Ant: *"None of that is true. You didn't even talk to Hermes."*
- Telling Ant OpenClaw couldn't write the token file before its result arrived, then apologizing when it had. Ant: *"Stop answering for people."*
- "Tony", "Jarvis", "Bartholomew": identity drift.

Grok works differently. The model calls tools, and it speaks from the tool results it actually got back.

### 1.4 Persistence and transcript bugs

- One OpenClaw success turn was written to the store **20 times** at 09-20 04:04:24.
- After a restart on 09-21 11:59:17, ~30 old turns (including injected `[Agent job update …]` / `User request: … Application context` wrappers) were re-appended as new turns.
- The communication contract and untrusted-context blobs are stored inside `full_text` of user turns.

### 1.5 Why Grok's demo works

One frontier model holds the whole conversation and has a small set of tools: list bots, send to bot, get status, calendar, and so on. The bots are always-on services with working credentials. Reference resolution, "try another one", "how's the landing page", and status questions all fall out of one model reasoning over tool results. Nothing is regex-matched.

---

## 2. The plan

Keep the parts of RAP that work: `AgentBridge`, the control plane, the conversation hub store, narration, harness voices, the confirmation gate, and the consult limits. Replace the decision layer, and make the workers dependable first.

| Phase | Outcome | Why this order |
|---|---|---|
| **A. Workers that work** | Every harness completes a real task on demand, with its model set by RAP | Nothing else is testable while workers fail |
| **B. Transcript/dispatch bug fixes** | No duplicate turns, no replay dump, no wrapper text stored as user speech | Small, isolated, and it corrupts the context that phase C depends on |
| **C. Tool-calling Butler** | One capable model with tools makes every decision. Speech comes only from tool results | This is the Grok architecture, and it replaces most of the router and floor heuristics |
| **D. Many tasks, one room** | Task subjects, per-harness caps, "what's running" | Small once C exists; mostly tool data |
| **E. Failover (existing M4 spec)** | "Try each agent until one succeeds" happens automatically | Ant literally asked for this on 09-19 |
| **F. Team tasks** | One objective across several agents | Only after single-agent work is solid |

### Phase A — Workers that work

1. **Harness audit** (Claude Code, read-only). For each of the 5 harnesses, run a trivial real task through the *exact* RAP command template from `config.AGENT_BACKENDS`, capture the failure, and locate where that harness gets its provider and model (its own config file, env vars, OpenClaw gateway profile). Output one table with the current model, provider, the failure, and the fix, split between what Ant must do (credits, keys, `openclaw doctor --fix`) and what RAP should do.
2. **RAP-owned model pinning.** Generalize `AGENT_MODEL_TARGETS` into a per-harness **ordered model list** in config/.env (for example `AGENT_MODELS_JSON={"hermes":[{"provider":"openai-api","model":"gpt-5.5"},{"provider":"openrouter","model":"…"}]}`). The bridge passes the first model explicitly. On a classified `quota`, `auth`, or `model_not_found` failure it relaunches the same task on the next model before calling the task failed. "Switch Hermes to OpenRouter" works for every configured provider, not just `openai`.
3. **Truthful health.** Replace the "fixed response check" verdict with a real tiny-task probe per harness. It records which model answered and how long it took, and the result is cached with a TTL. The status the user hears must match reality. *"I'm literally watching Hermes do a task right now"* must never happen again. Running RAP jobs count as proof of life.
4. **Exit:** "check all the agents" returns an accurate per-agent line in under 10 s, and all 5 complete a real task.

### Phase B — Transcript and dispatch bug fixes

1. Idempotent turn writes, keyed by `(task_id, attempt_id, result_kind)`, so one completion becomes one turn. Add a regression test that replays the 09-20 04:04 event burst.
2. Restart recovery restores turns without re-appending them.
3. Store what Ant actually said as the user turn. Application context, the communication contract, and untrusted-context blobs go in turn `metadata`, or nowhere, never in `full_text`.
4. Pin the front-door persona name in the system prompt, and never answer to other names by guessing.

### Phase C — Tool-calling Butler (the core change)

In brain mode (current `RAP_MODE=brain`), replace the per-turn pipeline (router tiers → classifier → floor → persona improvising around injected instructions) with a **single tool-calling loop**:

- **Model:** a cloud model with reliable function calling through the existing `llm_endpoint.py` chain (you already run `CLOUD_LLM_MODEL=google/gemini-2.5-flash` with local fallback off). Pick it by eval (C5), not by guess.
- **Tools** (thin wrappers over existing code; deterministic safety lives *inside* them):
  | Tool | Backed by |
  |---|---|
  | `list_agents()` → name, role, machine, health, current work | control plane registry |
  | `check_agents(names?)` → live probe results | control-plane probes (Phase A3) |
  | `start_task(agent, instructions, subject)` → task handle / "needs confirmation" | hub + `AgentBridge`, confirmation gate, `ConcurrencyGuard` |
  | `task_status(task?)` → state, subject, last progress, result/question | hub task references + bridge |
  | `list_tasks(scope=active\|recent)` | hub |
  | `redirect_task(task, instruction)` / `cancel_task(task)` | existing RAP-owned controls |
  | `answer_agent(task, reply)` → resumes on the bound session | hub channel/session binding |
  | `get_result(task)` → full stored result | hub result store |
  | `set_agent_model(agent, provider)` | Phase A2 |
  | `remember(fact)` / `recall(query)` | memory |
- **Rules in the system prompt:** Speak only from tool results. Never claim an agent was contacted without a `start_task` result. When a user refers to earlier work, call `list_tasks` or `task_status`; don't guess. When an agent fails and the user wants it done, try the next healthy agent. Keep spoken replies short.
- **Async events:** agent completion, question, and blocked events are injected as **tool-result-style system events**, not fake user turns. The model decides what to say. Agent-owned results still speak in the agent's voice (M2 rule), so Butler only frames them.
- **Keep deterministic:** a smalltalk/noise pre-gate (latency), the confirmation gate, concurrency caps, consult limits, and elevated-backend rules. The model can't bypass these because the tools enforce them.
- **Fallback:** if the cloud model is unavailable, fall back to the current router path. It's already built and tested.
- **C5 — replay eval.** Build a scripted corpus from Ant's real utterances (§3) and run it against the tool loop with mock agents. It gates the switch-over.

The existing `intent_router`, `FloorManager`, and `PersonaOrchestrator` stay as the fallback path. Don't delete them until the eval passes in daily use.

### Phase D — Many tasks, one room

Once C is in, this shrinks to data and limits:

- `subject` on `TaskReference` (the model supplies it via `start_task`), with a migration.
- Per-harness caps (`ORCHESTRATION_HARNESS_JOB_CAPS_JSON`) and a higher global default. The Hermes session lock stays.
- A `list_tasks` roll-up that includes the recently finished window.

### Phase E — Learned preference and bounded auto-failover

Execute `docs/superpowers/specs/2026-09-20-agent-preference-failover-design.md` as written, with the `start_task` tool as the dispatch point.

### Phase F — Team tasks

A parent task with child tasks across agents. `start_task` accepts a small plan, and a deterministic validator bounds it (max children, no elevated backend receiving another agent's text, the existing consult rules). The lead agent delivers the final result, and status rolls up.

### Later — Direct connectors

Calendar and email tools callable by Butler without a harness. Build them only if the phase C tool loop makes harness latency the bottleneck.

---

## 3. Acceptance corpus (Ant's own words)

These are the phrases from the logs. Each must produce the stated behavior with mock agents (C5), then live.

| # | Ant said | Must happen |
|---|---|---|
| 1 | "Check all the agents and tell me their status." | `check_agents` → one accurate line each, no dispatch |
| 2 | "So you're telling me no agents are available?" | Answered from the last check, naming which are down and why, in plain words |
| 3 | "Search my emails for upcoming events or news related to Miles' school." | `start_task` on a healthy email-capable agent |
| 4 | "Try one agent, see if it succeeds. If it doesn't, try another, one after the other." | Sequential failover across healthy agents, with one spoken line per switch |
| 5 | "Have Hermes do it." / "Have Codex do it." | `start_task(hermes, <the email search>)`: referent resolved, never the literal text |
| 6 | "No — the body of the email, more recent than August." | `redirect_task` on the email task, not a new unrelated task |
| 7 | "Uh here, have Codex try." | Same task, Codex, new attempt |
| 8 | "Instruct Codex to check on Code Puppy and get it working." | `start_task(codex, "diagnose and fix Code Puppy…")`, never to Code Puppy |
| 9 | "No, I need you to check, they can't check themselves." | Butler calls `check_agents`; no dispatch |
| 10 | "Check for any active agents." | `list_tasks(active)` + `list_agents`; no dispatch |
| 11 | "Are you checking on that or what was the status?" | `task_status` of the last started task |
| 12 | "Find my Hugging Face token… put it in a text file on my desktop." | Dispatch to a capable agent with the confirmation gate. Butler says nothing about the outcome until the result arrives |
| 13 | "Stop answering for people." | Structurally impossible after C: no outcome claims without a tool result |
| 14 | "Are you able to change the model for an agent like Hermes?" → "Switch Hermes to OpenRouter." | `set_agent_model`, confirmed from the tool result |
| 15 | "Clean up downloads." | Confirmation gate, then dispatch |
| 16 | "What's a good name for a golden retriever?" / "thanks" | Chat only, no tools |
| 17 | "What's running?" (3 tasks active) | Roll-up by subject |
| 18 | "How's the email thing?" | `task_status` resolved by subject |

## 4. Design rules

1. Butler speaks only from tool results. No tool result means no claim.
2. Butler is the front door. The agent that did the work delivers its result. Butler frames, hands off, and handles failure.
3. The model decides and the tools enforce: confirmation, caps, consult limits, and elevated-backend rules are code, not prompt.
4. What Ant said is stored as what Ant said. System context is never disguised as user speech.
5. Worker health is measured by real work, and the status Ant hears matches it.
6. RAP owns each harness's model choice and falls back across models before it gives up.
7. One-shot CLIs get no mid-run input. Redirect relaunches on the bound session.
8. Every utterance is interpreted against live and recent task state (via tools) before it's treated as new work.
9. The old router is the fallback, not the default, once the eval passes.
10. Full/brain parity where both modes exist.

## 5. Working agreement for Claude Code

- One branch per phase: `feat/a-worker-health`, `fix/b-transcript-integrity`, `feat/c-tool-butler`, …
- Workflow: spec in `docs/superpowers/specs/`, then plan in `docs/superpowers/plans/`, then task-by-task TDD with one commit per task.
- Tests: `.venv\Scripts\python -m pytest tests/<file>` · lint: `.venv\Scripts\python -m ruff check remote_agent_protocol tests`.
- Never edit `src/pipecat`. Never change credentials or harness accounts. Report what Ant must do instead.
- Update `docs/architecture.md` and `CHANGELOG.md` `[Unreleased]` per phase.
- STOP and report when the code doesn't match a plan's "current state", or when a change touches the confirmation gate, elevated-backend rules, or consult limits.

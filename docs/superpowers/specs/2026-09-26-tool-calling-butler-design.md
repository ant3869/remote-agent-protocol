# Tool-calling Butler (Phase C) -- design

**Roadmap:** `docs/notes/grok-style-orchestration-roadmap.md`, Phase C
**Mode:** brain mode (`RAP_MODE=brain`) only; full voice mode keeps the router path.

## Goal

One capable model holds the conversation and decides everything through a small set of
tools. It says only what those tools returned. The regex router, the classifier, and the
"Application context" wrappers become the fallback path. They stop being the default.

## Shape

```
user text ──► deterministic pre-gate ──► Butler loop ──► reply (spoken/streamed)
               (time, confirm/deny     │   ▲
                of a held task)        ▼   │ tool results
                                    ButlerToolbox ──► control plane / bridge / hub
```

- **Pre-gate (kept deterministic):** the local time query, and a yes/no reply to a held
  confirmation. Neither needs a model, and the confirmation must bind to the exact held task.
- **`ButlerLoop` (`butler/loop.py`):** an OpenAI-compatible `/chat/completions` call with
  `tools`, streamed. Text deltas are released as they arrive. When the model returns tool
  calls, they run, their results are appended as `tool` messages, and the model is called
  again. The loop is bounded to `BUTLER_MAX_TOOL_ROUNDS` rounds.
- **Endpoints:** the Butler role chain from Models & providers, through `llm_endpoint.chain`.
  An endpoint that fails before speaking hands off to the next one. If every endpoint fails
  before anything was spoken, the loop raises `ButlerUnavailable`, and the turn falls back
  to the existing router path unchanged.
- **Persistence:** only the user's words and the final spoken reply go into `_messages` and
  `jess_memory.json`. Tool calls and results are per-turn only (design rule 4).

## Tools (`butler/tools.py`)

| Tool | Returns | Backed by |
|---|---|---|
| `list_agents()` | name, machine, up/down/working line per agent, from recorded evidence | control plane registry |
| `check_agents(agents?)` | a fresh roll call: self-checks, reusing recent evidence | `agent_status_reporting.collect_rollcall_rows` |
| `start_task(agent, instructions, subject)` | `started` + task id, or `needs_confirmation`, or `refused` + reason | admission gate, confirmation gate, hub dispatch |
| `retry_task(task, agent)` | same as `start_task`, as a new attempt of an existing task | same |
| `task_status(task?)` | state, agent, subject, elapsed, last action, result preview | bridge job + ledger |
| `list_tasks(scope)` | active or recent tasks with subjects | ledger |
| `cancel_task(task)` | cancelled count | bridge cancel |
| `get_result(task)` | the full stored result or failure detail | bridge job |
| `set_agent_model(agent, provider)` | the new label, or unsupported | `AgentBridge.set_model_override` |

Safety stays inside the tools. The model cannot bypass any of the following:
- `start_task` and `retry_task` run the orchestrator admission gate (concurrency caps and
  duplicates) before any dispatch.
- A task matching the destructive-words rule is held for confirmation through the existing
  `_pending_confirmations` / `agent_confirm` mechanism, and is dispatched only on approval.
- Elevated-backend and consult rules live in the bridge and are untouched.

**Task ledger (`butler/ledger.py`):** Butler task ids (`t1`, `t2`, ...), each with a subject,
its instructions, and an ordered list of attempts `(job_id, agent)`. "Have Codex try" is a
new attempt on the same task, so the referent is never lost. The ledger is in-memory per
session and bounded.

## System prompt

The persona prompt, followed by the Butler rules:
- speak only from tool results
- never say an agent was contacted without a `started` result
- resolve references ("it", "the email thing") with `list_tasks`/`task_status`
- answer status questions with tools, never by starting a task
- when a task fails and the user wants it done, try the next healthy agent
- keep spoken replies to one or two sentences

A short live roster (agent names) and the current time are appended.

## Configuration

- `BUTLER_TOOLS_ENABLED` (default `false`): turns the loop on in brain mode.
- `BUTLER_MAX_TOOL_ROUNDS` (default `5`).

## Out of scope for this slice (next slices)

- Agent completion, question, and blocked events injected as tool-result-style events (C3).
  Until then they keep arriving as `[[announce]]` turns, which the loop narrates.
- `redirect_task` / `answer_agent` on bound sessions.
- `remember` / `recall`.
- C5 replay eval over the acceptance corpus. It gates turning the flag on by default.

## Acceptance (this slice)

These are scripted tests with a fake OpenAI-compatible model and fake agents:
- "check all the agents" calls `check_agents` and dispatches nothing
- "have Codex do it" after an email task calls `retry_task(<that task>, codex)`
- a destructive task returns `needs_confirmation`, and no job is started
- a status question calls `task_status`, and nothing is started
- a model outage falls back to the router path and nothing is lost

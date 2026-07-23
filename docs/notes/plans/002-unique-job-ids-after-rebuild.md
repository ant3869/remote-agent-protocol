# Plan 002: Keep job IDs unique across session rebuilds

> **Executor instructions**: Follow every step and verification gate. Update
> `plans/README.md` when done.
>
> **Drift check (run first)**: `git diff --stat a9cd12a61..HEAD -- remote_agent_protocol/agent_bridge.py tests/test_agent_bridge.py tests/test_gui_agents.py`

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `a9cd12a61`, 2026-07-05

## Why this matters

The GUI can rebuild `VoiceSession` in place. Every new `AgentBridge` restarts its
counter at one, while `AgentsPanel` retains rows from the old session and folds
events with `dict.setdefault`. A new `job-1` therefore updates the old row and
keeps its prior agent, task, and output.

## Current state

- `remote_agent_protocol/agent_bridge.py:408-409`: `self._counter = itertools.count(1)`.
- `remote_agent_protocol/agent_bridge.py:444-446`: `job_id=f"job-{next(self._counter)}"`.
- `remote_agent_protocol/gui.py:821-843` constructs a new session and rebinds the existing panels.
- `remote_agent_protocol/gui_agents.py:284-296` uses `self._jobs.setdefault(job_id, ...)`.
- Persisted rows already receive a `hist-{index}-` display prefix, so only live bridge instances in one process need a shared namespace.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.venv\Scripts\python -m pytest tests/test_agent_bridge.py tests/test_gui_agents.py` | all pass |
| Full app tests | `$files = rg -l "remote_agent_protocol" tests -g "test_*.py"; .venv\Scripts\python -m pytest @files` | 265+ pass |
| Lint/format | `.venv\Scripts\python -m ruff check remote_agent_protocol/agent_bridge.py tests/test_agent_bridge.py tests/test_gui_agents.py` | exit 0 |

## Scope

**In scope**:
- `remote_agent_protocol/agent_bridge.py`
- `tests/test_agent_bridge.py`
- `tests/test_gui_agents.py` only if a panel-level assertion is useful

**Out of scope**:
- UUIDs, timestamps, persistence-schema migrations, or renaming historical rows.
- Clearing the panel during rebuild; users should keep completed rows visible.
- Changing task lifecycle states.

## Git workflow

- Branch: `advisor/002-unique-job-ids-after-rebuild`
- Commit: `fix(agent-jobs): keep IDs unique across session rebuilds`
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Move the counter to process scope

Define one module-level `itertools.count(1)` and use it from every bridge.
Remove the per-instance counter. Keep the public `job-N` shape so existing UI,
tests, and persisted diagnostics remain readable.

**Verify**: focused tests pass.

### Step 2: Prove two bridges cannot collide

Add a regression that constructs two bridges in one event loop, starts one
fail-fast unknown-backend job on each, and asserts their returned IDs differ.
No subprocess or sleeps are needed.

Optionally add the panel assertion from the original failure: two `started`
events with distinct IDs retain distinct task text.

**Verify**: full app tests pass.

## Done criteria

- [ ] Two bridge instances in one process cannot issue the same ID.
- [ ] IDs retain the `job-N` format.
- [ ] Focused and full app tests pass; Ruff is clean.
- [ ] No GUI rebuild behavior or persistence schema changed.

## STOP conditions

- External consumers require counters to restart for each bridge.
- Tests reveal a documented contract requiring the first ID of every bridge to be exactly `job-1`.
- Fixing the collision would require clearing user-visible history.

## Maintenance notes

The guarantee is process-local, which is sufficient because persisted jobs are
renamed on display. A future network protocol must assign globally unique IDs
at that protocol boundary instead of stretching this counter across machines.

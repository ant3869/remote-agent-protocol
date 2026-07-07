# Plan 003: Make Clear finished delete persisted history

> **Executor instructions**: Execute Plan 001 first. Follow every verification
> gate and update `plans/README.md` when done.
>
> **Drift check (run first)**: `git diff --stat a9cd12a61..HEAD -- remote_agent_protocol/job_store.py remote_agent_protocol/session.py remote_agent_protocol/gui_agents.py tests/test_job_store.py tests/test_gui_agents.py`

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW
- **Depends on**: `plans/001-serialize-agent-history-writes.md`
- **Category**: bug / privacy
- **Planned at**: commit `a9cd12a61`, 2026-07-05

## Why this matters

The Agents panel promises “Clear finished,” but it only hides rows in memory.
Task descriptions and captured output remain on disk and return after restart.
For a local assistant that may handle sensitive task text, the button must
actually honor the user's deletion request and report a disk failure.

## Current state

- `remote_agent_protocol/gui_agents.py:253-259` removes terminal rows only from `_jobs` and `_order`.
- `remote_agent_protocol/session.py:789-801` exposes load and append but no clear operation.
- `remote_agent_protocol/job_store.py` uses best-effort filesystem operations and, after Plan 001, has one mutation lock.
- Use `tests/test_job_store.py` for filesystem behavior and `tests/test_gui_agents.py` for panel behavior.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Tests | `.venv\Scripts\python -m pytest tests/test_job_store.py tests/test_gui_agents.py tests/test_session_controls.py` | all pass |
| Lint | `.venv\Scripts\python -m ruff check remote_agent_protocol/job_store.py remote_agent_protocol/session.py remote_agent_protocol/gui_agents.py tests/test_job_store.py tests/test_gui_agents.py` | exit 0 |
| Format | `.venv\Scripts\python -m ruff format --check remote_agent_protocol/job_store.py remote_agent_protocol/session.py remote_agent_protocol/gui_agents.py tests/test_job_store.py tests/test_gui_agents.py` | already formatted |

## Scope

**In scope**:
- `remote_agent_protocol/job_store.py`
- `remote_agent_protocol/session.py`
- `remote_agent_protocol/gui_agents.py`
- `tests/test_job_store.py`
- `tests/test_gui_agents.py`

**Out of scope**:
- Clearing transcript or semantic memory.
- Deleting active jobs or suppressing future completed-job persistence.
- Confirmation dialogs, retention settings, archive/export formats.

## Git workflow

- Branch: `advisor/003-durable-clear-agent-history`
- Commit: `fix(agent-history): make clear finished durable`
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Add one locked delete operation

Add `clear_history(path) -> bool` in `job_store.py`. Use the Plan 001 mutation
lock, treat a missing file as success, and return `False` on `OSError` rather
than silently claiming deletion. Do not overwrite the file with `[]`; unlinking
is simpler and avoids an unnecessary write.

**Verify**: add tests for existing, missing, and failed deletion; focused job-store tests pass.

### Step 2: Expose the operation through VoiceSession

Add a synchronous `clear_agent_history() -> bool` next to `agent_history()`.
The GUI already runs this action on the Tk thread and the file is bounded to 100
rows, so do not create another coroutine or thread.

**Verify**: imports and session-control tests pass.

### Step 3: Wire the existing button

After removing terminal rows locally, call the session method. On failure,
append one clear warning to the system transcript saying history could not be
deleted; do not restore rows that the user already asked to hide.

Add a panel test with a fake session proving the method is called once and a
failure produces a warning.

**Verify**: all commands above pass.

## Done criteria

- [ ] Clear finished removes the on-disk history file.
- [ ] Missing history is a successful no-op; deletion failure is visible.
- [ ] Active rows remain and later completions can create a fresh history file.
- [ ] Clear and append share the Plan 001 lock.
- [ ] Tests and Ruff checks pass.

## STOP conditions

- Plan 001 is not complete or no shared mutation lock exists.
- The UI button has acquired a different documented meaning than deleting finished history.
- A fix requires touching transcript or semantic-memory stores.

## Maintenance notes

If history later moves to a database, preserve this button's durable-deletion
contract and test it at the storage boundary.

# Plan 001: Serialize agent-history writes

> **Executor instructions**: Follow every step and verification gate. Update
> `plans/README.md` when done. Do not broaden this into a storage rewrite.
>
> **Drift check (run first)**: `git diff --stat a9cd12a61..HEAD -- remote_agent_protocol/job_store.py tests/test_job_store.py`
> If either current-state excerpt no longer matches, stop and report.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: bug
- **Planned at**: commit `a9cd12a61`, 2026-07-05

## Why this matters

Each completed agent job persists from an independent asyncio task via
`asyncio.to_thread`. Two completions can therefore enter the synchronous
read/modify/write transaction together, both target the same `.tmp` path, and
lose one or both rows. Atomic replacement protects against a torn single write;
it does not serialize multiple writers.

## Current state

- `remote_agent_protocol/session.py:795-801` sends `job_store.append_job` to the thread pool.
- `remote_agent_protocol/job_store.py:66-75` currently does:

```python
history = load_history(path, 0)
history.append(row)
...
tmp.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
os.replace(tmp, p)
```

- The store is deliberately tiny and best-effort. Preserve that policy and the bounded JSON list format.
- Tests use `unittest` plus `tempfile`; follow `tests/test_job_store.py`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Focused tests | `.venv\Scripts\python -m pytest tests/test_job_store.py` | all tests pass |
| Lint | `.venv\Scripts\python -m ruff check remote_agent_protocol/job_store.py tests/test_job_store.py` | exit 0 |
| Format | `.venv\Scripts\python -m ruff format --check remote_agent_protocol/job_store.py tests/test_job_store.py` | files already formatted |

## Scope

**In scope**:
- `remote_agent_protocol/job_store.py`
- `tests/test_job_store.py`

**Out of scope**:
- Changing the JSON schema, retention limit, or best-effort error policy.
- Adding a database, file-lock dependency, async storage class, or per-job files.
- Changing `VoiceSession._persist_job`; its thread offload is appropriate for the voice loop.

## Git workflow

- Branch: `advisor/001-serialize-agent-history-writes`
- One conventional commit, e.g. `fix(agent-history): serialize concurrent writes`
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Lock the complete transaction

Add one module-level `threading.Lock`. Hold it around the entire operation in
`append_job`: load, append, trim, directory creation, temp write, replace, and
error cleanup. Do not lock `load_history` for ordinary readers; `os.replace`
already gives them either the old or new complete file.

**Verify**: `.venv\Scripts\python -m pytest tests/test_job_store.py` -> all existing tests pass.

### Step 2: Leave one concurrency regression

Add a test that releases many `append_job` calls together through
`ThreadPoolExecutor`, then asserts every unique job ID is present exactly once.
Use a temporary directory and repeat enough calls to exercise the former shared
`.tmp` collision without sleeps or external files.

**Verify**: run the focused test command 20 times in PowerShell:

```powershell
1..20 | ForEach-Object { .venv\Scripts\python -m pytest -q tests/test_job_store.py; if ($LASTEXITCODE) { exit $LASTEXITCODE } }
```

Expected: every run exits 0.

## Done criteria

- [ ] The whole read/modify/write transaction is guarded by one stdlib lock.
- [ ] Concurrent append regression passes repeatedly.
- [ ] Focused tests, Ruff check, and Ruff format check pass.
- [ ] Only the two in-scope files and the plan index changed.

## STOP conditions

- The persistence path is now called from multiple OS processes; a process-local lock would be insufficient.
- Correctness requires changing the history schema or adding a dependency.
- An in-scope file drifted and the excerpts no longer describe it.

## Maintenance notes

Any future mutating history operation must use the same lock. Plan 003 depends
on this so clear and append have a defined order.

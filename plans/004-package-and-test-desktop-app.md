# Plan 004: Package and continuously test the desktop app

> **Executor instructions**: Preserve the vendored Pipecat distribution name and
> extras. Follow every gate and update `plans/README.md` when done.
>
> **Drift check (run first)**: `git diff --stat a9cd12a61..HEAD -- pyproject.toml README.md .github/workflows/tests.yaml .github/workflows/coverage.yaml`

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none
- **Category**: packaging / tests / DX
- **Planned at**: commit `a9cd12a61`, 2026-07-05

## Why this matters

The repository's wheel omits the product package, so an installation contains
Pipecat but not Remote Agent Protocol. CI's selected extras omit
`kokoro-onnx`, which the app imports at module load, and coverage excludes the
application package entirely. The coverage upload also still identifies the
upstream Pipecat repository instead of this repository.

## Current state

- `pyproject.toml` keeps `name = "pipecat-ai"` intentionally and discovers packages only with `where = ["src"]`.
- A clean `python -m build --wheel` produced a wheel with `pipecat/` and no `remote_agent_protocol/`.
- `remote_agent_protocol/persona_tts.py:12` imports `Kokoro` from `kokoro_onnx` unconditionally.
- `.github/workflows/tests.yaml` and `coverage.yaml` do not install `--extra kokoro`.
- `[tool.coverage.run] source = ["src"]` excludes `remote_agent_protocol`.
- `.github/workflows/coverage.yaml` uses slug `pipecat-ai/pipecat`; `origin` is `ant3869/remote-agent-protocol` and `upstream` remains Pipecat.
- Preserve the application/framework boundary documented in `docs/architecture.md`; do not rename `src/pipecat` or mix app code into it.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Build | `.venv\Scripts\python -m build --wheel --outdir data/package-check` | one wheel, exit 0 |
| App tests | `$files = rg -l "remote_agent_protocol" tests -g "test_*.py"; .venv\Scripts\python -m pytest @files` | 265+ pass |
| Lint | `.venv\Scripts\python -m ruff check remote_agent_protocol $(rg -l "remote_agent_protocol" tests -g "test_*.py")` | exit 0 |
| Format | `.venv\Scripts\python -m ruff format --check remote_agent_protocol $(rg -l "remote_agent_protocol" tests -g "test_*.py")` | already formatted |

## Scope

**In scope**:
- `pyproject.toml`
- `.github/workflows/tests.yaml`
- `.github/workflows/coverage.yaml`
- `README.md`
- A small packaging regression under `tests/` only if it does not recursively build during normal unit tests

**Out of scope**:
- Renaming the `pipecat-ai` distribution or changing its version source.
- Moving `remote_agent_protocol/` under `src/`.
- Updating vendored Pipecat dependencies or its public project URLs.
- Adding a second build system, installer framework, or dependency manager.

## Git workflow

- Branch: `advisor/004-package-and-test-desktop-app`
- Commit: `fix(packaging): include and test the desktop app`
- Do not push or open a PR unless instructed.

## Steps

### Step 1: Include both package roots explicitly

Adjust setuptools discovery to search `src` and the repository root while
including only `pipecat*` and `remote_agent_protocol*`. Do not allow tests,
scripts, examples, or data to become namespace packages. Build the wheel and
inspect its ZIP member names with stdlib `zipfile`; both package prefixes must
be present.

**Verify**:

```powershell
.venv\Scripts\python -m build --wheel --outdir data/package-check
$wheel = (Get-ChildItem data/package-check/*.whl | Select-Object -First 1).FullName
.venv\Scripts\python -m zipfile -l $wheel | Select-String 'remote_agent_protocol/__init__.py|pipecat/__init__.py'
```

Expected: exactly one match for each package initializer.

### Step 2: Make CI capable of collecting app tests

Add the existing `kokoro` extra to both test and coverage sync commands. Do not
add every desktop runtime extra to Linux CI; the focused tests mock or lazily
load the remaining hardware-specific dependencies.

**Verify**: `uv tree --package kokoro-onnx --invert` shows it is supplied by the selected `kokoro` extra; YAML remains valid.

### Step 3: Measure the application and target the correct repository

Change coverage source roots so both vendored `src/pipecat` and
`remote_agent_protocol` are measured. Change the Codecov slug to
`ant3869/remote-agent-protocol`. Keep use of the repository secret; do not add
a token value.

**Verify**: run coverage on the focused app suite and confirm
`coverage report -m remote_agent_protocol/*` lists app modules.

### Step 4: Correct installation documentation

Update the product setup section to state that the editable installation now
includes the application package and that Kokoro is part of the selected
extras. Keep the manual `mem0ai`, `ollama`, and `openwakeword` line unless their
dependencies are explicitly moved into a tested product extra in this same
plan. Do not claim the wheel bundles models or external CLIs.

**Verify**: follow the README commands in a disposable virtual environment or
container and run `python -c "import remote_agent_protocol, pipecat"`.

## Test plan

- Build and inspect the artifact rather than asserting setuptools configuration text.
- Run every existing focused app test after package discovery changes.
- Run Ruff and one import smoke from outside the repository root using the built wheel.

## Done criteria

- [ ] Built wheel contains both application and framework packages, and no tests/data/examples.
- [ ] App tests can collect with the exact CI extras.
- [ ] Coverage includes `remote_agent_protocol` and uploads to this repository slug.
- [ ] Import smoke succeeds outside the checkout.
- [ ] The `pipecat-ai` name, versioning, extras, and upstream source package remain intact.

## STOP conditions

- Multiple-root discovery packages `src` as a namespace or includes tests/examples/data.
- Including the app requires renaming or republishing the upstream distribution.
- Linux CI requires GPU/audio hardware rather than mockable import dependencies.
- The Codecov owner/repository differs from the current `origin` URL.

## Maintenance notes

Every future product module must be covered by the explicit package include.
When upstream `pyproject.toml` changes are merged, preserve these two local
discovery/CI deltas deliberately.

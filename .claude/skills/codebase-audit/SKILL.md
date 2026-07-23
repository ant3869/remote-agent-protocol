---
name: codebase-audit
description: Audit the remote-agent-protocol app layer (remote_agent_protocol/) for dead code, code smells, refactor potential, outdated docs, and new-feature opportunities, with a verified fix pass. Use when asked to audit, review health, find improvements, look for dead code, or clean up this codebase -- not for one-off bug reports.
---

# Codebase Audit (remote-agent-protocol app layer)

Scope: `remote_agent_protocol/` (the app). Do **not** sweep `src/pipecat/` (vendored
framework -- see `AGENTS.md`) unless the user explicitly asks about it; it has ~1400
pre-existing lint findings that are not this app's problem to fix.

Two modes, pick based on the request:
- **Audit only** ("find issues", "what's wrong") -> produce the findings report (Step 5), stop there.
- **Audit and fix** ("fix what you find", "clean this up") -> do Steps 1-4, then apply fixes per
  the verification discipline in Step 6 before reporting.

## Step 1: Orient with the knowledge graph

Follow this project's `CLAUDE.md`/`AGENTS.md` mandate: `list_projects` -> `get_architecture`
(pass `aspects: ["hotspots", "clusters"]` -- the unfiltered call can exceed the tool's token cap
on this repo) -> `search_graph` scoped with `file_pattern: "remote_agent_protocol/*.py"`.

**Known unreliability, verify before reporting:** this codebase leans heavily on dependency
injection (bound methods and callables passed as constructor/callback args, e.g.
`DelegationTap(self._on_delegate, self._resolve, ...)`). The graph indexer does not record a
CALLS edge for "passed as a callback," only for "invoked directly." That makes `in_degree: 0`
on classes, `__init__` methods, and callback-shaped functions **look like dead code when they
are not**. Every dead-code candidate from the graph must be cross-checked with `Grep` for the
symbol name (constructor call, `python -m` entry point in `pyproject.toml`/`README.md`, or
`<script src=...>` in `web_app/index.html` for JS) before it goes in the report.

## Step 2: Complexity hotspots (refactor potential)

Run `scripts/complexity.py` against the largest app files (check with a quick `wc -l
remote_agent_protocol/*.py`; `session.py`, `agent_bridge.py`, and `web_gui.py` have
historically been the biggest) to rank functions by cyclomatic complexity, nested-def-excluded.

For each hotspot (cx > ~18), read the actual function before deciding it's a problem:
- **Genuine god-method** (a long `if name == X: ... elif name == Y: ...` dispatcher, or several
  unrelated command branches in one function): worth splitting. Use the dispatch-table pattern
  seen in `web_gui.py`'s `_action` (a `dict[str, Callable]` built in `__init__`, referencing
  either small `_action_<name>` methods or existing same-signature methods directly) or the
  named-handler pattern in `session.py`'s `_maybe_handle_model_control` (each branch becomes
  `_handle_<thing>`, called from a short top-level dispatch). **Before touching the fallthrough
  logic**, trace every branch by hand: does it always return, or can it fall through to the next
  check on a "matched but produced nothing" result? (`_maybe_handle_model_control`'s task-correction
  branch does exactly this -- a naive "return on first non-None" refactor would silently break it.)
  Preserve that exact shape; don't assume every branch is independent.
- **Naturally branchy but already readable** (a narration/throttle function with early-return
  guard clauses, a linear startup sequence with a few `if` checks): high cyclomatic count from
  guard clauses is not the same as bad code. Leave it. Splitting a clear top-to-bottom sequence
  into fragments for the sake of a lower number makes it worse, not better.
- **Stateful single-pass parsers** (e.g. a line-by-line output filter with a handful of "am I
  currently skipping a block" flags): extracting the flags into one small dataclass with a
  `consume()` method is usually a safe, mechanical win. Extracting a `pending`/buffering block
  from a read loop into a nested closure returning a tri-state (matched-and-done /
  matched-continue / not-matched) is also safe *if* every original branch already unconditionally
  `return`s or `continue`s -- confirm that before extracting.

A flat, comment-sectioned constants/settings module (e.g. `config.py`) is not a god object no
matter how many lines it has -- it has no behavior to extract. Don't propose splitting it; that's
churn across every `cfg.X` call site for zero behavioral change.

## Step 3: Dead code

1. `ruff check --select F,ERA remote_agent_protocol/` for unused imports/vars and commented-out
   code. The repo's root `pyproject.toml` only enables `D`/`I`/`UP` by default (F/ERA would
   surface ~1400 hits in vendored `src/pipecat`), so run this ad hoc with `--select`, or rely on
   `remote_agent_protocol/ruff.toml` if present (a nested config scoping F/ERA to the app layer --
   check it's still there and still passes before assuming it covers this).
2. Watch for eradicate (`ERA001`) false positives on comment blocks that read like code (model
   name lists, config examples in prose) -- read the surrounding comment before flagging.
3. Orphan-file scan: for each app module, grep for its name elsewhere in the app. Anything with
   zero hits needs a second check before calling it dead -- it may be a `python -m
   remote_agent_protocol.X` entry point (check `README.md`) or, on the JS side, a
   `<script type="module" src="...">` entry loaded directly by `index.html` rather than imported
   by another module.

## Step 4: Outdated docs

Grep `README.md` and `docs/architecture.md` (the two *live* reference docs) for stale references
to removed subsystems -- this repo's history includes at least one full UI rewrite (Tkinter ->
browser-based `web_gui.py` + `web_app/`), and live docs lag code changes.

**Do not edit as "fixes":** `CHANGELOG.md` (append-only historical record -- an old entry
describing something now-removed is correct as history), and anything under `docs/superpowers/plans/`,
`docs/superpowers/specs/`, `plans/`, or `war_games/` (dated planning artifacts describing a
plan-as-conceived; retroactively editing them to match current state destroys their value as a
record). Only `README.md` and `docs/architecture.md` describe present-tense state and should be
kept current.

## Step 5: New-feature ideas

Read `docs/architecture.md`'s "Product roadmap" / "Current boundaries and risks" sections first.
Prefer citing the project's own stated next-steps over inventing parallel feature ideas -- the
maintainer has already thought about this and written it down.

## Step 6: If fixing, verify before reporting done

- Trace fallthrough/edge-case behavior by hand for any dispatch-table or handler-extraction
  refactor (see Step 2) -- don't rely on tests alone to catch a subtly-changed control path.
- Run the narrowest test files that exercise each touched module (per `AGENTS.md`:
  `.venv\Scripts\python -m pytest tests/test_<name>.py`), not a bare `pytest` at repo root --
  that also collects vendored provider tests (Azure/Deepgram/LiveKit/etc.) that fail to import
  due to missing optional extras in most dev venvs, which is a pre-existing environment gap, not
  a regression signal.
- `ruff check` + `ruff format --check` on every touched file.
- For any doc edit, re-grep to confirm no other live doc has the same stale claim.
- If a memory file (see this project's auto-memory, if the agent has one) asserts something the
  audit just found to be false (e.g. describing a UI/module that no longer exists), update or
  supersede it -- don't leave a memory that will re-assert a false fact next session.

## Report format

Group findings under: **Dead Code**, **Docs Drift**, **Code Smells / Refactor Potential**,
**New Feature Ideas**, and **False Positives Ruled Out** (worth keeping -- it's the record of what
the graph/lint tools got wrong, so the next audit doesn't re-investigate the same non-issues).
Cite `file:line`. If fixes were applied, show a before/after complexity or LOC table for anything
extracted, and state what was verified (tests run, lint status).

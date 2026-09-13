---
name: update-repo
description: Full sweep for shipping a batch of app-layer work -- audit and fix code smells/dead code/bugs, apply UI/UX easy wins, bump the version and update docs, then commit and push. Use when asked to audit and clean up the codebase and ship it, or to "run the update repo skill" / "update_repo".
---

# Update Repo (audit, cleanup, version, ship)

Runs the full "wrap up a work session" routine for `remote_agent_protocol/` (the app layer --
see `AGENTS.md`; don't sweep vendored `src/pipecat` unless asked). Four phases, in order. Don't
skip a phase because it seems like nothing changed -- say so explicitly instead of silently
omitting it from the report.

## Phase 1: Audit and fix

Invoke the `codebase-audit` skill in its **audit-and-fix** mode (pass it a note naming anything
built or changed earlier in this conversation, so it's in scope alongside the rest of the app).
That skill's own steps cover: codegraph orientation, complexity hotspots, dead code (`ruff
--select F,ERA`), docs drift in `README.md`/`docs/architecture.md`, and a verified fix pass.

Two things worth checking here that a generic sweep can miss, since they come up often in this
codebase's shape:
- **Per-`job_id`-keyed state that's never released.** Job ids are monotonic and never repeat
  (`agent_bridge._JOB_ID_COUNTER`), so any `dict[job_id, ...]` populated during a job's life and
  never popped on completion is an unbounded leak over a long-running session (this app is a
  persistent desktop process, not a short CLI run). Grep for dicts keyed by `job_id`/`job.job_id`
  and confirm each has a cleanup path in the terminal-job handler (`session._announce_agent_job`
  is the natural place -- it already runs for every terminal status).
- **A capability that exists but is never surfaced.** Something computed or stored but never
  read anywhere (`grep -rn` the symbol name across the app) that isn't dead code per se -- it's
  wired on one end only. The app version (`remote_agent_protocol.__version__`, sourced from
  `VERSION`) is the canonical example: real plumbing, zero display surface, until this skill's
  Phase 3 wires it into `doctor.py` output and the web GUI's settings panel.

## Phase 2: UI/UX easy wins

Look specifically in `remote_agent_protocol/web_app/` (`index.html`, `app.js`) and `web_gui.py`'s
status/catalogs payloads for small, low-risk additions that fit an *existing* pattern rather than
introduce a new one -- e.g. the settings page's "Current state" diagnostics grid is a repeated
`<article><span>Label</span><strong id="settingsX">--</strong></article>` block; a new field there
is a three-line change (HTML row, payload key, one `$("settingsX").textContent = ...` line in
`app.js`), not a redesign. Prefer these mechanical, pattern-matching additions over inventing new
UI structure -- a genuinely new panel/flow is a "New Feature Idea" for the audit report, not an
easy win to just build.

## Phase 3: Version and docs

1. Read `VERSION` (single line, semver) and the latest dated heading in `CHANGELOG.md`. Decide
   the bump from what actually shipped this session: patch for fixes only, minor for new
   additive capability (the common case coming out of Phase 1/2), major only if something
   genuinely breaking happened (rare for this app; flag it explicitly if so, don't guess).
2. Update `VERSION` to the new number.
3. In `CHANGELOG.md`, cut the accumulated `## [Unreleased]` section into a dated release heading
   (`## [1.14.0] - YYYY-MM-DD`, matching Keep a Changelog format already in use) directly below a
   now-empty `## [Unreleased]`, then add fresh `### Added`/`### Fixed`/etc. bullets for anything
   from this session not already logged there. **Never edit the text of an existing dated
   entry** -- CHANGELOG is an append-only historical record; only add new content and the release
   boundary.
4. Grep for the *old* version string across `*.py`/`*.md`/`*.html`/`*.toml` (excluding `.venv`,
   `src/pipecat`, `CHANGELOG.md`/`docs/CHANGELOG.pipecat.md` which are correct history) to catch
   any place the number is hardcoded instead of read from `VERSION` -- fix forward to read
   `remote_agent_protocol.__version__` rather than hardcoding the new number too, unless the
   surrounding code has a specific reason not to import the package (see `doctor.py`'s deliberately
   narrow-import comment for an example of a legitimate exception, and import `__version__` there
   too since it's package-level metadata, not a heavy submodule).
5. Update `README.md`'s Features list for anything Phase 1 built that isn't mentioned yet --
   docs drift on a brand-new capability is not caught by grepping for stale references, only by
   checking new work made it in at all.

## Phase 4: Commit and push

1. `git status --short` and `git diff --stat` for the full picture; confirm nothing unexpected
   (unrelated files, anything under gitignored `data/`/`logs/`) is about to be staged.
2. Stage explicitly by path (never `git add -A`/`.`) -- list every file from the status output
   that's actually part of this work.
3. Run the narrowest test files touched (per `AGENTS.md`: `.venv\Scripts\python -m pytest
   tests\test_<name>.py ...`) plus `ruff check` on every touched `.py` file. Fix, don't skip, on
   failure.
4. Write one commit message in this repo's own established style -- plain descriptive sentences,
   no `feat:`/`fix:` type prefixes (check `git log --oneline -5` if unsure) -- covering what
   shipped and, briefly, why. A multi-paragraph body is fine for a multi-feature batch; keep the
   summary line under ~70 characters.
5. Confirm the current branch and its tracked remote (`git branch --show-current`, `git remote
   -v`) before pushing -- push to the branch already checked out and tracked, not `main`, unless
   the user has said otherwise.
6. `git push origin <branch>`. Report the resulting commit hash and what changed, plus a
   one-line note on anything from Phase 1 deliberately left unfixed and why.

## Report

After Phase 4, give a compact summary: what was audited and fixed (with file:line for the
non-trivial ones), what shipped in the version bump, and the pushed commit. Don't re-paste full
diffs the user can already see in the commit.

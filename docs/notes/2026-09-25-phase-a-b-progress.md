# Phase A/B reliability work -- unattended session progress log

**Date:** 2026-09-25 · **Branch:** `feat/a-b-reliability` (worktree `../rap-phase-ab`, base `f0a755df4`)
**Context:** Ant was away; ran Phase A (workers that work) and Phase B (transcript/dispatch bug fixes) from
`docs/notes/grok-style-orchestration-roadmap.md` end-to-end, unattended, per his own instructions to keep
going and make reasonable calls rather than stop and ask. This file is that record.

Not pushed, not merged, no branches deleted, as instructed. Every task has its own commit; nothing was
squashed. `git log --oneline f0a755df4..HEAD` (oldest first):

```
8a15270fd fix: centralize backend executable resolution and warn on shadow binaries
25ac41e38 fix: keep a redacted output tail in failure_detail for unclassified exits
289e6d667 feat: add AGENT_DEFAULT_MODEL_TARGETS_JSON to bypass a broken backend default
9b0c04cf5 feat: capture which model actually answered a delegated job (best effort)
c51a09079 fix: make agent-job completion turn writes idempotent (B1, B2)
62a0ae4ee fix: stop persisting injected scaffolding as brain-mode user turns (B3)
5b9a16c65 docs: document the Phase A/B reliability fixes
```

---

## Per-task status

### A1 -- Windows launch resolution: **done, but the premise needed correcting**

The 2026-09-25 harness audit's headline finding -- that a bare `"openclaw"`/`"codex"` in
`AGENT_BACKENDS` gets launched via Windows' `.exe`-only implicit search and can silently run the wrong
binary -- **does not reproduce against real `AgentBridge._launch`**. Reading `agent_bridge.py` closely
showed it already resolves via `shutil.which(command[0])` before spawning, a fix already made
2026-07-10 (the comment even cites `jess_runtime.log 2026-07-10 19:59:55`). The audit's probe script
called `subprocess.run` directly with the bare command, bypassing that resolution step -- a methodology
gap in the audit, not a live bug in RAP. I'm flagging this plainly rather than quietly building on a
premise I'd since found to be wrong.

What *was* real and worth fixing: the same `shutil.which(...)` resolve-then-substitute pattern was
duplicated verbatim in two places (`agent_bridge.py` and `remote_host.py`), and there genuinely is a
same-named `.exe` shadow (`~/.local/bin/openclaw.exe`, an unrelated third-party "cmdop" installer) sitting
on PATH that could bite some *other*, less careful caller. So:

- New `remote_agent_protocol/subprocess_resolution.py`: `resolve_executable()` (the one shared
  implementation) and `find_shadow_executable()` (walks PATH for a same-named `.exe` elsewhere when the
  resolved binary isn't one itself).
- `agent_bridge.py` and `remote_host.py` now both call `resolve_executable()`.
- `agent_bridge.executable_status()` (backs `doctor.check_agent_backends()`) now returns `"warn"`,
  naming both paths, when a shadow exists.
- Locked in a safety invariant with a test: hermes/openclaw/codex resolve to `.cmd` shims on this
  machine, and Windows hands a `.cmd` target to `cmd.exe /c`, which re-parses the trailing command line
  with its own metacharacter rules -- so those three backends' templates must never carry `{task}` as a
  literal argv token (only `{task_file}`/`{task_stdin}`). They already didn't; the test just makes sure
  nobody "simplifies" that back.

Tests: `tests/test_subprocess_resolution.py` (new, all mocked -- `shutil.which`/`os.name`/a real tmp
filesystem, no dependency on this machine's actual PATH), plus one new case in `tests/test_doctor.py`.

### A2 -- Failure detail on silent non-zero exits: **done**

`agent_bridge.py`'s existing fallback already tried a single last output line before giving up with
"exit code N without reporting details" -- that generic message only fired when there was *no* captured
output at all. Added `redact_secrets()` (strips key/token-shaped substrings -- vendor API-key prefixes,
JWT shapes, `key/token/secret: <value>`) and `_failure_tail()` (last 10 non-empty lines, redacted,
capped at 2000 chars), used as the fallback whenever no classified failure reason ever set
`failure_detail`. Multi-line diagnostic output that used to collapse into one 300-char line (or nothing)
is now kept in full, bounded and redacted.

Tests: 6 new cases in `tests/test_agent_bridge.py` -- pure-function coverage for both helpers, plus two
end-to-end scenarios (multi-line output survives; a `sk-...`-shaped string gets redacted). Verified RED
by temporarily reverting `agent_bridge.py` and re-running -- confirmed failing, then restored.

### A3 -- Default model target per backend: **done**

Added `AGENT_DEFAULT_MODEL_TARGETS_JSON` (agent -> provider key into the existing
`AGENT_MODEL_TARGETS`), applied via `AgentBridge`'s existing `set_model_override()` at construction.
Wired into both `session.py` and `brain.py` identically to preserve full/brain parity. Empty by default
-- confirmed via a test that behavior is unchanged when unset.

**Ant: nothing added to `.env` automatically, per your instruction. If you want code-puppy defaulting
off its broken local-proxy route (see the harness audit), add:**

```dotenv
AGENT_DEFAULT_MODEL_TARGETS_JSON={"code-puppy":"openai"}
```

### A4 -- Record which model actually answered: **done, deliberately conservative**

Added `AgentJob.answered_model`, parsed from harness output via a narrow, line-anchored
`Model:`/`Using model:` pattern, surfaced in `job_store.job_to_row()` (job history) and the web GUI's
Agents job-list row and detail panel (next to the existing, pre-run `model_label`).

**Honest limitation, not glossed over:** none of the 5 harnesses' output captured during the harness
audit contained a line matching this pattern (or any other unambiguous model-name marker I could verify
without guessing). So `answered_model` will read empty for every backend today. The plumbing is real and
tested; the extraction just has nothing to latch onto yet. Per the task's own instruction ("don't guess"),
I did not invent per-harness regexes for formats I haven't actually seen. If you watch a live session and
see where (if anywhere) a harness prints its model, tell me the exact text and I'll add a targeted
pattern for it.

Tests: pure-function tests for `extract_answered_model()` (including a "don't guess" negative case), two
end-to-end job tests, and `job_store` round-trip coverage including a job object that predates the field
(defensive `getattr`, matches the existing pattern for `result`/`host_modified`).

### B1/B2 -- Idempotent turns / restart recovery: **done, one fix covers both**

Traced both to the same root cause: `AgentConversationHub.handle_job_event()`'s
`_task_by_job_id()` matches purely on `attempt_id == job_id`, with **no check on the task's current
status**. A task's `attempt_id` only ever changes on a genuine retry (`_dispatch` assigns the new job's
id on reassignment), so once a given `job_id` has already driven its task to `done`/`failed`, any further
event carrying that same `job_id` is provably a duplicate delivery or a replay -- never a legitimate new
outcome. Added a `_TERMINAL_TASK_STATUSES` guard that returns immediately in that case. This is what
"idempotent per (task_id, attempt_id, result_kind)" cashes out to here: a task can only be terminal once
per attempt_id, so guarding on status is equivalent to keying on the full tuple without touching
individual turns.

One fix, two regression tests: a 20x-event burst yields exactly one turn (matches the 2026-09-20 04:04:24
OpenClaw incident), and a simulated restart (persist -> restore -> restore again -> replay) leaves the
turn count unchanged throughout (matches the 2026-09-21 11:59:17 incident). Both verified RED against the
pre-fix code via a temporary `git stash` of just `service.py`, then restored.

### B3 -- Store what Ant said, not wrappers: **done, but the target file needed correcting**

The task said "user turns in the hub store contain injected wrappers." I searched
`conversation_hub/*.py` exhaustively for the exact wrapper strings quoted in the roadmap
("User request:", "Application context", "[Agent job update") and found **none** -- every turn-creation
site in `conversation_hub/service.py` already stores clean `request.text`/agent-authored `full_text`.
The actual bug is one directory over, in `brain.py`: `_record_user_turn()` builds
`f"User request: {text}\n\nApplication context (not a new request):\n{content}"` and appends it straight
into `self._messages`, which is *also* what gets written to `cfg.MEMORY_FILE`
(`jess_memory.json`) on every turn and on shutdown. `session.py` (full voice mode) already runs its
outgoing messages through `memory.strip_ephemeral()` before saving; `brain.py` never did, for either the
wrapper or the synthetic `[[announce]] ... [Agent job update: ...]` relay text used to voice a finished
background job. I'm calling this out explicitly rather than silently fixing a different file than the
one named, since it changes where a reviewer should look.

Fix, three parts:
1. `_record_user_turn()` now remembers, per wrapped message, what Ant actually said -- keyed by the
   wrapped content string itself, in a new `self._persisted_text_for_wrapped` dict. `self._messages`
   (and therefore what the model receives -- `_ollama_payload` spreads it directly into the request
   body, and a hosted API rejects an unrecognized key on a message object) is **never mutated**.
2. New `_messages_for_persistence()` builds a transformed *copy* for `memory.save_memory()`: wrapped
   content is swapped back to the real utterance, then the result runs through the existing, tested
   `memory.strip_ephemeral()` with `config.EPHEMERAL_PROMPT_PREFIXES` -- to which I added one new entry,
   `"[[announce]]"`, covering the job-update relay the same way every other one-shot injected prompt
   already is. Both save call sites (`_finish_turn`, `stop()`) now use it.
3. Also strips on *load* (matching `session.py`), so old accumulated relay junk stops being resent to
   the model and repersisted every turn. Per "new writes only," this does not rewrite what's already on
   disk -- old wrapped/relay entries already in `jess_memory.json` stay there until the next natural
   save cycle passes through them (the relay ones get dropped then; already-wrapped old "User request:"
   entries can't be cleanly un-wrapped after the fact without fragile parsing, so I left them alone
   rather than risk losing real history -- **a real migration, if you want one, would need to regex-parse
   `"User request: (.*)\\n\\nApplication context"` out of old entries; I did not attempt that here**).

Tests: `tests/test_brain_memory_persistence.py` (new -- 5 cases covering wrapped/unwrapped/announce
content, `stop()`'s save path, and load-time stripping) and one new example added to
`tests/test_memory_strip.py`'s existing `_INJECTED_PROMPTS` coverage table. Verified RED by temporarily
stashing `brain.py`+`config.py` and re-running -- 4 of 5 new tests failed as expected (the fifth,
non-control-turn storage, was already correct), then restored.

---

## Final test results

Full scoped run (everything touched, plus every file named in the task instructions --
`test_agent_bridge`, `test_session_*`, `test_brain_*`, `test_conversation_*`,
`test_agent_control_plane`, `test_doctor`, `test_web_gui`, 28 files, 677 tests):

```
671 passed, 6 failed in 261.80s
```

`python -m ruff check remote_agent_protocol tests`: **all checks passed** (whole tree, not just touched
files).

### The 6 failures -- confirmed pre-existing, not caused by this session

I did not assume this -- for each cluster I built a throwaway `git worktree` at the original base commit
(`f0a755df4`, before any of my changes) and reran the exact failing tests there. All 6 reproduced
identically on stock `main`-adjacent code:

- `tests/test_web_gui.py::test_conversation_channels_payload_lists_the_hubs_channels` and
  `::test_conversation_routes_are_served_over_http` -- both expect the conversation-channels API to list
  only `coordinator:butler` after one turn, but get 6 extra channels (`agent:claude-code`, `agent:mock`,
  etc.). Looks like the hub test fixture is picking up real configured backends rather than a controlled
  set.
- `tests/test_brain_low_vram.py::test_a_group_ping_is_answered_from_rap_not_delegated`,
  `::test_a_roll_call_reports_an_unrunnable_backend_as_such`, and
  `tests/test_session_controls.py::AgentRollcallAndStatusControlTests::test_rollcall_question_answers_locally_without_delegating`,
  `::test_status_question_answers_locally_without_delegating` -- all four `monkeypatch`/`patch.object`
  `config.AGENT_BACKENDS` to a small fake set (e.g. `{"ghost": [...]}`), but the roll-call/status answer
  comes back describing the *real* 5 configured harnesses (claude-code, codex, hermes, code-puppy,
  openclaw) instead. Looks like the roll-call/status path reads live control-plane state that isn't
  reset by mocking `config.AGENT_BACKENDS` alone -- worth a look, since it suggests "are all the agents
  online" might not honor a from-cold-config backend set correctly, though I didn't dig further since
  it's well outside this session's scope.

I left all 6 untouched -- fixing them wasn't part of the assigned tasks, and both clusters touch files
(`web_gui.py`/`app.js`, `session.py`'s control-plane wiring) with enough surface area that a rushed fix
risked doing more harm than the two, pre-existing, narrowly-scoped test failures already do.

---

## Everything Ant must do by hand

From the original harness audit (docs/notes/2026-09-25-harness-audit.md), still outstanding -- none of
this is something code can fix:

- **9Router/local-proxy Claude credential**: refresh whatever feeds `127.0.0.1:20128`'s Claude route.
  `~/.code_puppy/claude_code_oauth.json` was 8 days stale at audit time and is the leading suspect;
  hermes and openclaw's `9router`/`local-proxy` providers share the same dependency.
- **`openclaw doctor --fix`**: the harness names this itself for its sqlite schema-v19 warning.
- **cmdop `openclaw.exe` at `~/.local/bin/`**: decide whether to remove it. It's not live-dangerous
  today (RAP already resolves around it), but it will keep shadowing `openclaw` for any *other*
  shell-less tool on this machine.
- **OpenAI credits/quota**: check the account behind codex's `model_provider = "openai"`.
- **If you want the `AGENT_DEFAULT_MODEL_TARGETS_JSON` override**, add the line under A3 above to
  `.env` yourself -- I did not touch `.env`.
- **Review before merging**: this branch is unmerged and unpushed on purpose. `git worktree remove
  ../rap-phase-ab` when you're done with it (or keep it and keep working there).
- **The two pre-existing test-failure clusters** above are worth a look when you have time, independent
  of anything in this session.

---

## Open questions

- Should `answered_model` extraction get harness-specific patterns once you've seen a real banner
  format, or is the generic `Model:`/`Using model:` pattern good enough as a baseline?
- Is a one-time migration to recover Ant's real words from *already-wrapped* `jess_memory.json` entries
  worth writing, or is leaving old history as-is (while new writes are clean) acceptable?
- The `test_web_gui.py`/`test_session_controls.py`/`test_brain_low_vram.py` pre-existing failures all
  smell like the same root cause (something not respecting a mocked/fresh `AGENT_BACKENDS` in tests) --
  worth a dedicated look, not attempted here since it's outside the assigned scope.

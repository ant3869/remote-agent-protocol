# Explicit Agent Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route unnamed work to the persisted default agent while natural, explicit agent addressing overrides it for exactly one request.

**Architecture:** Extend the existing deterministic parser and local session-control path; keep the current `IntentRouter` and `AgentBridge` execution flow. Emit one small default-change event so the web host persists voice-selected defaults, and label the existing roster without adding an orchestration service.

**Tech Stack:** Python 3.12, asyncio, Pipecat frames, vanilla JavaScript, unittest/pytest, Node test runner.

## Global Constraints

- Explicit targeting applies to one request only.
- Unnamed work uses the persisted default agent.
- Longest exact configured alias wins; unknown agents never fall back silently.
- Existing destructive-action confirmation applies to explicit and default routing.
- Do not add sticky chat, automatic agent selection, fallback agents, group chat, or dependencies.
- Preserve unrelated dirty avatar work.

---

### Task 1: Natural one-turn agent addressing

**Files:**
- Modify: `remote_agent_protocol/voice_commands.py:227-276`
- Modify: `tests/test_voice_commands.py:1-90`
- Modify: `tests/test_session_delegation.py:15-32`

**Interfaces:**
- Consumes: `parse_delegation(text: str, backends: dict, aliases: dict[str, str])`.
- Produces: the same `tuple[str, str] | None` interface with broader deterministic grammar.

- [ ] **Step 1: Write failing parser tests**

```python
def test_natural_explicit_forms_target_one_agent(self):
    for text in (
        "use hermes to check my email",
        "ask code puppy to inspect this repository",
        "have codex diagnose hermes",
        "tell claude code to run the tests",
    ):
        backend, task = voice_commands.parse_delegation(text, BACKENDS, ALIASES)
        self.assertIn(backend, BACKENDS)
        self.assertTrue(task)

def test_direct_question_reaches_named_agent(self):
    self.assertEqual(
        voice_commands.parse_delegation("Hermes, what do you think about this?", BACKENDS, ALIASES),
        ("hermes", "what do you think about this"),
    )

def test_empty_direct_address_does_not_dispatch(self):
    self.assertIsNone(voice_commands.parse_delegation("Hermes?", BACKENDS, ALIASES))
```

- [ ] **Step 2: Run tests and confirm the direct-question regression fails**

Run: `.venv\Scripts\python -m pytest tests/test_voice_commands.py -q`

Expected: the direct question returns `None`; empty addressing remains safe.

- [ ] **Step 3: Generalize only the existing parser**

Keep longest-alias ordering. Permit comma direct address regardless of question word, add `use` to the existing delegation verbs if absent, and strip only optional `to`; do not add fuzzy matching. The core direct-address branch should become:

```python
for alias in sorted(aliases, key=len, reverse=True):
    match = re.match(rf"^{re.escape(alias)}\s*[,,:]\s*(.+)$", lowered)
    if match:
        backend = aliases[alias]
        task = match.group(1).strip(_TRAILING_PUNCTUATION)
        return (backend, task) if backend in backends and task else None
```

- [ ] **Step 4: Prove explicit routing does not mutate the default**

Add to `tests/test_session_delegation.py`:

```python
async def test_explicit_turn_does_not_change_runtime_default(self):
    voice_session = session.VoiceSession(personas.DEFAULT_PERSONA)
    voice_session.set_default_agent_backend("mock")
    self.assertEqual(
        await voice_session._resolve_delegation("Hermes, check email"),
        ("hermes", "check email"),
    )
    self.assertEqual(voice_session.default_agent_backend(), "mock")
```

- [ ] **Step 5: Run focused routing tests**

Run: `.venv\Scripts\python -m pytest tests/test_voice_commands.py tests/test_intent_router.py tests/test_session_delegation.py -q`

Expected: all pass; explicit decisions report `source="explicit"` and the following unnamed request still selects the default.

- [ ] **Step 6: Commit**

```powershell
git add remote_agent_protocol/voice_commands.py tests/test_voice_commands.py tests/test_session_delegation.py
git commit -m "feat: support one-turn explicit agent addressing"
```

### Task 2: Local agent controls and persistence

**Files:**
- Modify: `remote_agent_protocol/voice_commands.py`
- Modify: `remote_agent_protocol/session.py:579-589,1084-1142`
- Modify: `remote_agent_protocol/web_gui.py:483-508,660-720`
- Modify: `tests/test_voice_commands.py`
- Modify: `tests/test_session_controls.py`
- Modify: `tests/test_web_gui.py`

**Interfaces:**
- Produces: `parse_agent_control(text, backends, aliases) -> tuple[str, str | None] | None` where action is `list`, `get_default`, or `set_default`.
- Produces: session event `{"type": "default_agent_changed", "agent": backend}` after a valid spoken change.

- [ ] **Step 1: Write failing pure parser tests**

```python
def test_agent_control_commands(self):
    self.assertEqual(parse_agent_control("list agents", BACKENDS, ALIASES), ("list", None))
    self.assertEqual(parse_agent_control("what is my default agent", BACKENDS, ALIASES), ("get_default", None))
    self.assertEqual(parse_agent_control("make hermes my default agent", BACKENDS, ALIASES), ("set_default", "hermes"))

def test_unknown_default_is_rejected(self):
    self.assertIsNone(parse_agent_control("make hal my default agent", BACKENDS, ALIASES))
```

- [ ] **Step 2: Run parser tests and confirm failure**

Run: `.venv\Scripts\python -m pytest tests/test_voice_commands.py -q`

Expected: FAIL because `parse_agent_control` does not exist.

- [ ] **Step 3: Implement exact local-control grammar**

```python
def parse_agent_control(text, backends, aliases):
    lowered = _strip_fillers(text.strip().lower().rstrip(_TRAILING_PUNCTUATION))
    if lowered in {"list agents", "list my agents", "what agents are available"}:
        return "list", None
    if lowered in {"what is my default agent", "what's my default agent", "which agent is default"}:
        return "get_default", None
    match = re.fullmatch(r"(?:make|set|use) (.+?) (?:as )?my default agent", lowered)
    if match:
        alias = match.group(1)
        backend = aliases.get(alias)
        if backend in backends:
            return "set_default", backend
    return None
```

- [ ] **Step 4: Add session control behavior and tests**

Handle this before cancel/correction/model controls in `_maybe_handle_model_control`:

```python
control = voice_commands.parse_agent_control(text, cfg.AGENT_BACKENDS, cfg.AGENT_SPOKEN_ALIASES)
if control:
    action, agent = control
    if action == "list":
        return f"[Agent control: available agents are {', '.join(self._bridge.backend_names())}; default is {self._default_agent_backend}.]"
    if action == "get_default":
        return f"[Agent control: the default agent is {self._default_agent_backend}.]"
    self.set_default_agent_backend(agent)
    self._emit({"type": "default_agent_changed", "agent": agent})
    return f"[Agent control: the default agent is now {agent}.]"
```

Tests assert no bridge job starts, the default changes only for `set_default`, and the event contains the backend.

- [ ] **Step 5: Persist the emitted change in the web host**

In `WebVoiceApp._fold_event`, handle `default_agent_changed` by calling the existing state writer:

```python
elif kind == "default_agent_changed":
    agent = str(evt.get("agent", ""))
    if agent in cfg.AGENT_BACKENDS:
        self._save_state(tool_user=agent)
```

Add a `test_web_gui.py` regression that folds the event, rebuilds the session, and observes the same default.

- [ ] **Step 6: Run control and persistence tests**

Run: `.venv\Scripts\python -m pytest tests/test_voice_commands.py tests/test_session_controls.py tests/test_session_delegation.py tests/test_web_gui.py -q`

Expected: all pass.

- [ ] **Step 7: Commit**

```powershell
git add remote_agent_protocol/voice_commands.py remote_agent_protocol/session.py remote_agent_protocol/web_gui.py tests/test_voice_commands.py tests/test_session_controls.py tests/test_web_gui.py
git commit -m "feat: add local default-agent voice controls"
```

### Task 3: Make orchestration visible and finish documentation

**Files:**
- Modify: `remote_agent_protocol/web_app/app.js:721-734`
- Create: `tests/js/agent-roster.test.mjs`
- Modify: `docs/architecture.md`
- Modify: `CHANGELOG.md`
- Modify: `.agent/CONTINUITY.md`

**Interfaces:**
- Consumes status fields `toolUser`, `agentBackends`, `agentStates`.
- Produces roster text that distinguishes the persisted default from the backend used by each job.

- [ ] **Step 1: Write the failing roster source-contract regression**

Read the shipped renderer and assert it compares each roster name with `state.status.toolUser` and emits the label. This keeps the check runnable without booting the browser-only application module.

```javascript
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

test('agent roster marks the persisted default backend', () => {
  const source = readFileSync('remote_agent_protocol/web_app/app.js', 'utf8');
  assert.match(source, /name === state\.status\.toolUser/);
  assert.match(source, />Default</);
});
```

- [ ] **Step 2: Run the JavaScript test and confirm failure**

Run: `node --test tests/js/*.test.mjs`

Expected: the new default-label assertion fails.

- [ ] **Step 3: Add the label to the existing roster renderer**

Inside `renderAgentRoster`, derive only one new string:

```javascript
const defaultLabel = name === state.status.toolUser ? '<span class="agent-default">Default</span>' : '';
```

Insert it beside the backend name. Reuse existing badge styles if one fits; add no new component.

- [ ] **Step 4: Document the final routing contract**

Add concise architecture and changelog entries covering unnamed-default routing, one-turn explicit forms, direct questions, local list/query/set controls, confirmation preservation, and deliberate non-features.

- [ ] **Step 5: Run full relevant verification**

```powershell
.venv\Scripts\python -m pytest tests/test_voice_commands.py tests/test_intent_router.py tests/test_session_delegation.py tests/test_session_controls.py tests/test_web_gui.py tests/test_agent_bridge.py -q
.venv\Scripts\python -m ruff check remote_agent_protocol tests
.venv\Scripts\python -m ruff format --check remote_agent_protocol tests
.venv\Scripts\python -m compileall -q remote_agent_protocol
node --test tests/js/*.test.mjs
node --check remote_agent_protocol/web_app/app.js
git diff --check
```

Expected: all checks pass; only the known Pipecat `AudioContextTTSService` deprecation warning may remain.

- [ ] **Step 6: Commit**

```powershell
git add remote_agent_protocol/web_app/app.js tests/js docs/architecture.md CHANGELOG.md .agent/CONTINUITY.md
git commit -m "feat: expose explicit agent orchestration"
```

# Operational Graphite UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the cyan-first web shell with the approved dense graphite Mission Control interface while preserving every existing workflow.

**Architecture:** Keep `WebVoiceApp` and its JSON bridge unchanged. Recompose the existing HTML into a compact sidebar/topbar/workspace hierarchy, replace the CSS token and component system, and make only small JavaScript rendering changes required by the new status treatments.

**Tech Stack:** Python stdlib HTTP bridge, HTML5, CSS, vanilla JavaScript, pytest, Playwright browser inspection.

## Global Constraints

- Use near-black and graphite as the dominant interface colors.
- Green means live, enabled, available, or successful; yellow means warning or attention; red means failed, offline, blocked, or destructive.
- Blue is limited to rare primary actions, purple to agent/research activity, and orange to delegation or exceptional caution.
- Preserve voice, chat, delegation, memory, setup, status, settings, and context workflows.
- Add no frontend dependency or framework.
- Keep keyboard navigation, visible focus, status labels, and responsive layouts.

---

### Task 1: Lock The Graphite Design Contract

**Files:**
- Modify: `tests/test_web_gui.py`
- Modify: `remote_agent_protocol/web_app/styles.css`

**Interfaces:**
- Consumes: existing static web assets under `remote_agent_protocol/web_app/`.
- Produces: CSS custom properties for graphite surfaces, semantic status colors, and sparse highlight colors.

- [ ] **Step 1: Write failing static design tests**

Add assertions for `--surface-app: #09090b`, `--status-success`, `--status-warning`, `--status-error`, `--accent-agent`, and the absence of cyan tokens, radial gradients, and the assistant orb.

- [ ] **Step 2: Run the focused test and confirm failure**

Run: `.venv\Scripts\python -m pytest tests\test_web_gui.py -q`

Expected: FAIL because the current stylesheet still defines cyan-first tokens and gradients.

- [ ] **Step 3: Replace the stylesheet**

Define the approved tokens, compact 6-10px radii, neutral borders, restrained shadows, semantic status variants, sparse blue/purple/orange actions, dense rows, three-pane memory, and responsive breakpoints. Remove cyan glow, blue page tint, large floating cards, and the orb.

- [ ] **Step 4: Run the focused test**

Run: `.venv\Scripts\python -m pytest tests\test_web_gui.py -q`

Expected: PASS.

### Task 2: Recompose The Application Shell

**Files:**
- Modify: `remote_agent_protocol/web_app/index.html`
- Modify: `remote_agent_protocol/web_app/app.js`
- Modify: `tests/test_web_gui.py`

**Interfaces:**
- Consumes: all existing DOM ids used by `app.js` and all `/api/action` names.
- Produces: a compact sidebar, global topbar, control-center split workspace, memory browser, setup workspace, status table, and grouped settings sections using the same ids and actions.

- [ ] **Step 1: Add failing structure tests**

Assert the presence of `control-grid`, `activity-panel`, `system-strip`, and compact assistant controls; assert the absence of `hero-panel` and `orb`.

- [ ] **Step 2: Run the focused test and confirm failure**

Run: `.venv\Scripts\python -m pytest tests\test_web_gui.py -q`

Expected: FAIL because the old hero/orb structure remains.

- [ ] **Step 3: Rebuild semantic HTML while preserving ids**

Move persona selectors into a compact toolbar, metrics into a system strip, chat into the primary column, agents into a right activity inspector, and keep the composer directly below chat. Convert secondary screens to dense operational sections without changing their navigation or action hooks.

- [ ] **Step 4: Update rendering classes**

Map live/success to `status-success`, warning/waiting to `status-warning`, failure/offline/blocked to `status-error`, active agent work to `accent-agent`, delegation to `accent-delegate`, and Send to the sole primary blue action.

- [ ] **Step 5: Run tests and JavaScript syntax checks**

Run: `.venv\Scripts\python -m pytest tests\test_web_gui.py tests\test_gui_theme.py tests\test_gui_agents.py -q`

Run: `node --check remote_agent_protocol\web_app\app.js`

Expected: all pass.

### Task 3: Browser Verification And Final Checks

**Files:**
- Modify only if browser inspection exposes a concrete defect: `remote_agent_protocol/web_app/styles.css`, `remote_agent_protocol/web_app/index.html`, `remote_agent_protocol/web_app/app.js`

**Interfaces:**
- Consumes: final static shell and mocked API status/events.
- Produces: verified layouts at 1536x960, 1280x800, 900x900, and 390x844.

- [ ] **Step 1: Serve the static shell locally and mock API responses in Playwright**

Use a local `python -m http.server` process rooted at `remote_agent_protocol/web_app`; intercept `/api/status`, `/api/events`, and `/api/action` with representative ready, active-agent, warning, and failure data.

- [ ] **Step 2: Capture and inspect screenshots**

Check for horizontal overflow, clipped controls, unreadable labels, overlapping panels, excessive blue, incorrect status colors, and layout shifts. Verify the chat remains the primary workspace and the context drawer stays subtle.

- [ ] **Step 3: Run final verification**

Run: `.venv\Scripts\python -m pytest tests\test_web_gui.py tests\test_gui_theme.py tests\test_gui_agents.py -q`

Run: `.venv\Scripts\python -m ruff check remote_agent_protocol\web_gui.py tests\test_web_gui.py`

Run: `.venv\Scripts\python -m compileall -q remote_agent_protocol\web_gui.py`

Run: `node --check remote_agent_protocol\web_app\app.js`

Expected: all commands exit zero; the only accepted warning is the existing Pipecat `AudioContextTTSService` deprecation warning.

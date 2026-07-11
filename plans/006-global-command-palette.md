# Plan 006: Add A Global Command And Search Palette

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report -- do not improvise. When done, update the status row for this plan in
> `plans/README.md`.
>
> **Drift check (run first)**: `git diff --stat f4cbc5785..HEAD -- remote_agent_protocol/web_app/index.html remote_agent_protocol/web_app/app.js remote_agent_protocol/web_app/styles.css tests/test_web_gui.py README.md`
> If any in-scope file changed since this plan was written, compare the
> "Current state" excerpts against the live code before proceeding; on a
> mismatch, treat it as a STOP condition.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none
- **Category**: direction / UX correctness
- **Planned at**: commit `f4cbc5785`, 2026-07-10

## Why this matters

The web control center is now dense enough that users must remember where each
control lives. The graphite UI design spec explicitly calls for a quiet global
search affordance, and README already promises `Ctrl+K` opens the agent task
console, but the browser UI only implements `Ctrl+L` and `Ctrl+M`. A small
client-side command palette makes the app feel more capable without adding a
backend route, dependency, or new state model.

## Current State

- `remote_agent_protocol/web_app/index.html` defines the topbar at lines 58-69
  with only the title stack and status pills:

```html
<header class="topbar">
  <div class="title-stack">
    <span class="section-label">Remote Agent Protocol</span>
    <h2 id="appTitle">Control Center</h2>
  </div>
  <div class="status-pills" aria-live="polite">
    ...
  </div>
</header>
```

- `README.md` lines 39-42 promise a shortcut that is not wired in the web app:

```markdown
- `Ctrl+L` focuses typed input, `Ctrl+M` toggles the microphone, and `Ctrl+K`
  opens the agent task console. The EXPORT button writes a diagnostics bundle.
```

- `remote_agent_protocol/web_app/app.js` lines 970-973 handle only two global
  shortcuts:

```js
document.addEventListener("keydown", (event) => {
  if (event.ctrlKey && event.key.toLowerCase() === "l") $("messageInput").focus();
  if (event.ctrlKey && event.key.toLowerCase() === "m") post("mute", { muted: !state.status?.muted });
});
```

- Existing client state already contains enough data for palette results:
  `state.messages`, `state.memories`, `state.agentJobs`, `state.agentHistory`,
  `state.status.personas`, settings controls, and existing action buttons.
- Existing actions already cover navigation and operations through `post()` and
  the `.nav-link` click handlers. Do not add a new Python API.
- Match the operational graphite constraints in
  `docs/superpowers/specs/2026-07-08-operational-graphite-ui-design.md`: compact
  black/graphite surfaces, semantic status colors, purple focus, no new
  framework, and no decorative gradients.

## Commands You Will Need

| Purpose | Command | Expected on success |
|---|---|---|
| JS syntax | `node --check remote_agent_protocol\web_app\app.js` | exit 0 |
| Focused tests | `.venv\Scripts\python -m pytest tests\test_web_gui.py -q` | all tests pass |
| Lint tests | `.venv\Scripts\python -m ruff check tests\test_web_gui.py` | exit 0 |
| Whitespace | `git diff --check -- remote_agent_protocol/web_app/index.html remote_agent_protocol/web_app/app.js remote_agent_protocol/web_app/styles.css tests/test_web_gui.py plans/README.md` | no output, exit 0 |

## Scope

**In scope:**
- `remote_agent_protocol/web_app/index.html`
- `remote_agent_protocol/web_app/app.js`
- `remote_agent_protocol/web_app/styles.css`
- `tests/test_web_gui.py`
- `plans/README.md` status row only after implementation

**Out of scope:**
- `remote_agent_protocol/web_gui.py` and other Python API code
- New frontend dependencies or a framework
- Server-side search, persistent palette history, fuzzy-search libraries
- Changing README unless the implemented behavior intentionally differs from
  the existing shortcut promise

## Git Workflow

- Branch: `advisor/006-global-command-palette`
- Commit message style follows current history, for example
  `feat(ui): add global command palette`.
- Do not push or open a PR unless the operator explicitly asks.

## Steps

### Step 1: Lock The Missing Shortcut And Palette Contract

Add focused static tests in `tests/test_web_gui.py` near the existing web shell
tests. Assert all of these:

- `index.html` contains a visible topbar control with `id="paletteOpenBtn"` and
  a palette/dialog container with `id="commandPalette"`.
- The sidebar shortcut footer includes `Ctrl K`.
- `app.js` contains `openCommandPalette`, `renderCommandPalette`, and a global
  `event.key.toLowerCase() === "k"` handler.
- `app.js` has palette commands for at least: Control Center, Agents, Personas,
  Memory, Settings, focus message, toggle mic, new chat, refresh memory, export
  diagnostics, start Ollama, and free VRAM.
- `styles.css` contains `.command-palette`, `.palette-result`, and a mobile rule
  that keeps the palette inside the viewport.

**Verify**: `.venv\Scripts\python -m pytest tests\test_web_gui.py -q` should
fail before implementation because the palette elements/functions do not exist.

### Step 2: Add Minimal Accessible Markup

In `remote_agent_protocol/web_app/index.html`, add a compact topbar button before
the status pills:

- `button#paletteOpenBtn` with text like `Search / Command` and a visible
  `Ctrl K` hint.
- `section#commandPalette` near the end of `<main>` or just before `</body>`,
  hidden by default, with `role="dialog"`, `aria-modal="true"`, a search input
  `id="paletteInput"`, and a result list `id="paletteResults"`.

Keep existing IDs intact. Do not move the status pills or current nav buttons.

**Verify**: `node --check remote_agent_protocol\web_app\app.js` still exits 0
because this step should not require JS changes yet.

### Step 3: Implement The Palette With Existing State And Actions

In `remote_agent_protocol/web_app/app.js`, add small helper functions:

- `openCommandPalette(initialQuery = "")`: unhide the palette, set the input
  value, render results, focus the input.
- `closeCommandPalette()`: hide it and return focus to `paletteOpenBtn`.
- `commandPaletteItems()`: return an array of result objects from existing
  state and actions.
- `renderCommandPalette()`: filter items by query and render buttons.
- `runPaletteItem(item)`: close the palette and perform the item action.

Use plain substring matching over a lowercased string. No fuzzy ranking, no
scoring, no new dependency.

Include these result groups:

- Navigation commands: Control Center, Agents, Personas, Memory, Setup, Status,
  Settings. Trigger the existing `.nav-link[data-view="..."]` click.
- Runtime commands: focus message, toggle mic, cycle voice mode, new chat,
  refresh memory, export diagnostics, start Ollama, free VRAM. Reuse existing
  buttons or `post()` calls.
- Dynamic objects: personas from `state.status.personas`, agent jobs from
  `allAgentJobs()`, and visible memory rows from `state.memories.short` and
  `state.memories.semantic`. For dynamic objects, selecting a result should
  navigate to the right panel and select/focus it when the current UI already
  supports that; otherwise navigate and prefill the local search input.

Wire keyboard behavior:

- `Ctrl+K` / `Meta+K`: open the palette.
- `Escape`: close the palette when open.
- `Enter`: run the highlighted/first result.
- Arrow up/down: move a selected result index.

Use a tiny state field such as `state.paletteIndex`; do not introduce a new
state manager.

**Verify**: `node --check remote_agent_protocol\web_app\app.js` exits 0.

### Step 4: Style It In The Existing Graphite System

In `remote_agent_protocol/web_app/styles.css`, style the palette as a restrained
overlay:

- Fixed inset overlay with a translucent black backdrop.
- Centered graphite panel with max width around 680px and max height around
  70vh.
- Dense result rows, 6-10px radii, neutral borders, purple active/focus state,
  semantic status chips only where real state is shown.
- Mobile breakpoint that uses `inset: 10px`, full available width, and no
  horizontal overflow.

Do not add gradients, blobs, card-in-card styling, or a blue-dominant palette.

**Verify**: `.venv\Scripts\python -m pytest tests\test_web_gui.py -q` passes the
static CSS/markup assertions.

### Step 5: Final Verification

Run the complete focused verification:

```bat
node --check remote_agent_protocol\web_app\app.js
.venv\Scripts\python -m pytest tests\test_web_gui.py -q
.venv\Scripts\python -m ruff check tests\test_web_gui.py
git diff --check -- remote_agent_protocol/web_app/index.html remote_agent_protocol/web_app/app.js remote_agent_protocol/web_app/styles.css tests/test_web_gui.py plans/README.md
```

Expected: all commands exit 0. The known Pipecat `AudioContextTTSService`
deprecation warning is acceptable if it appears during pytest.

## Test Plan

- Add static tests in `tests/test_web_gui.py` because existing web UI tests
  already assert DOM/CSS/JS contracts without launching the audio stack.
- Cover the missing shortcut contract, topbar palette affordance, palette DOM
  IDs, command categories, and CSS viewport containment.
- Do not add browser automation unless static tests pass and a visual defect is
  suspected. If browser verification is available, test desktop and mobile
  widths for overflow and focus visibility.

## Done Criteria

- [ ] `Ctrl+K` / `Meta+K` opens a command palette in the web UI.
- [ ] The topbar includes a visible search/command affordance.
- [ ] Palette results include navigation, runtime commands, personas, agent
      jobs, and memory rows from existing client state.
- [ ] Selecting results reuses existing buttons, nav links, and `post()` actions.
- [ ] No Python API route or frontend dependency was added.
- [ ] Focus and Escape behavior works for keyboard users.
- [ ] The four final verification commands in Step 5 pass.
- [ ] Only in-scope files changed, plus the status row in `plans/README.md`.

## STOP Conditions

Stop and report back if:

- The live code no longer has the topbar/status structure shown in the Current
  State excerpt.
- The implementation appears to require a new backend search route.
- The palette needs a fuzzy-search dependency to feel usable; ship substring
  search first.
- Tests fail twice after a reasonable fix attempt.
- Any secret, environment value, or raw diagnostic bundle content would be shown
  in palette results.

## Maintenance Notes

- Keep palette items explicit and boring. If the command list grows past a few
  dozen static items, group generation by view, not by a generic registry.
- Dynamic results should only use data already rendered in the UI. Do not query
  or persist anything just for the palette.
- Review keyboard focus carefully; this feature is most valuable when it feels
  faster than mousing through the dense sidebar.

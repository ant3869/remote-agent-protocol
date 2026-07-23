# Operational Graphite UI Design

## Objective

Rebuild the web control center to match the dense, understated operational character of the supplied Mission Control and Inventory references. The application must read as black and graphite, not blue, while preserving all voice, chat, delegation, memory, setup, and diagnostic workflows.

## Visual System

- Use near-black page and sidebar backgrounds with slightly lighter graphite panels.
- Use neutral one-pixel borders, restrained shadows, and 6-10px corner radii.
- Remove blue gradients, cyan glows, the assistant orb, oversized hero treatment, and decorative elevation.
- Use green for live, enabled, successful, and available states.
- Use yellow for warnings, attention, and approaching failure.
- Use red for failed, offline, destructive, and blocked states.
- Use blue only for a rare primary action, purple for agent/research activity, and orange for delegation or exceptional caution.
- Use compact, readable typography with white primary text and muted gray metadata. Monospace remains limited to logs, identifiers, and metrics.

## Application Shell

The shell uses a compact fixed sidebar, a thin global top bar, and a dense main workspace. Navigation rows use icons, labels, and a subtle neutral active background. The assistant identity and microphone state become compact operational rows rather than a promotional card.

The global top bar contains the current screen title, search affordance, session controls, semantic status indicators, and a small assistant presence marker. It must remain visually quiet until a state needs attention.

## Control Center

The main screen removes the persona hero. A compact header presents the active persona and summary, followed by a single settings toolbar for persona, tool user, model, and voice.

A narrow metric row shows voice mode, model, memory, server, and latency. The primary workspace uses:

- A central transcription chat with clear user, assistant, agent, and system treatments.
- A right-side agent activity inspector on wide screens, collapsing below chat at narrower widths.
- A compact composer fixed to the conversation flow.
- A subtle context drawer that remains closed until requested.
- Green live indicators, yellow attention states, red failures, purple agent activity, orange delegation, and a sparingly blue Send action.

## Memory

Memory uses a three-pane operational browser: category tree, dense memory list, and selected-memory details. Search and counters sit in a compact header. Selection uses a neutral elevated row with a small purple indicator; semantic status colors remain reserved for actual state.

## Setup, Status, And Settings

Setup becomes a structured step workspace with compact option groups, requirement rows, and a persistent action footer. Status uses table-like service rows with semantic indicators and concise metadata. Settings uses grouped form sections instead of isolated dashboard cards.

## Interaction And Accessibility

- Preserve keyboard navigation and visible focus rings using a restrained purple outline.
- Use color plus labels/icons for every status.
- Keep controls at least 36px high and body text readable at 13-15px.
- Avoid layout animation; use only brief opacity and transform transitions.
- At widths below 1100px, collapse the inspector beneath the main content. At mobile widths, convert the sidebar into a compact top navigation region without horizontal overflow.

## Implementation Boundaries

Keep the existing Python HTTP bridge and `VoiceSession` APIs. Refactor only the web shell HTML, CSS, and rendering code needed to support the new hierarchy. Do not introduce a frontend framework or dependency for styling that native HTML, CSS, and JavaScript already cover.

## Verification

- Update static GUI tests for the graphite and semantic token system.
- Assert obsolete cyan-first tokens and blue gradients are absent.
- Preserve tests for chat, agent state, setup, memory, and context controls.
- Run Python GUI tests, Ruff, compileall, and JavaScript syntax checks.
- Inspect Playwright screenshots at wide desktop, standard desktop, tablet, and mobile sizes for overflow, overlap, hierarchy, and readable status colors.

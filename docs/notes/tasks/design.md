````markdown
You are a senior frontend architect, product designer, and desktop-app UI engineer. The current GUI is not acceptable. Redesign and rebuild the frontend to a professional standard, matching the quality and layout polish of the provided Jarvis reference screenshots while using a darker blue/cyan theme instead of the orange/yellow motif.

This is a full frontend rebuild, not a minor restyle. Use a completely different GUI/frontend framework if needed.

## Goal

Create a professional, polished, production-quality AI assistant desktop interface that feels like a premium control center.

The app should look:
- modern,
- dark,
- spacious,
- polished,
- visually coherent,
- user-facing rather than developer/debug-facing,
- and clearly inspired by the provided Jarvis screenshots in layout quality, component structure, and presentation.

Use:
- deep dark-mode backgrounds,
- dark navy/charcoal elevated panels,
- blue/cyan highlights,
- green success states,
- amber only for real warnings,
- red only for destructive/error states,
- rounded cards,
- soft borders,
- subtle shadows/glow,
- clean typography,
- refined spacing,
- dashboard-style panels,
- professional buttons, tabs, pills, and controls.

## Important Direction

The current frontend looks too flat, boxy, sparse, and debug-console-like. Replace it completely.

If the existing frontend framework is limiting the design quality, migrate to a better-suited frontend approach. Acceptable options include, but are not limited to:
- React + Tailwind,
- React + CSS modules,
- Vue,
- Svelte,
- Tauri/Electron frontend shell,
- or another professional desktop-app UI stack that fits the existing project.

Choose the approach that gets the best professional result while preserving existing backend functionality.

## Visual Reference

Use the previously provided Jarvis screenshots as the benchmark for:
- layout density,
- card structure,
- setup wizard quality,
- memory viewer organization,
- rounded panels,
- header/status areas,
- sidebar layout,
- pill counters,
- tab styling,
- form controls,
- visual hierarchy,
- spacing,
- and professional polish.

However:
- Replace the orange/yellow/gold motif with blue/cyan.
- Make the app darker overall.
- Keep amber only for actual warning states.

## Required Screens to Redesign

Redesign the full app frontend, including:

1. Main assistant screen
2. Status dashboard
3. Voice controls
4. Mode selector
5. Chat/transcription area
6. Message composer
7. Context/attachment area
8. Memory viewer
9. Agent/persona settings
10. Setup wizard
11. Logs/debug views
12. Diagnostics/settings screens
13. Modal dialogs
14. Empty/loading/error states

## Main Assistant Screen Requirements

The main screen should become a polished AI control center.

Include:

### Header
- App name and subtitle.
- Right-aligned status pills.
- Status pills for session, model/server, TTS, agents, memory, or equivalent.
- Rounded/elevated pill styling with icons or dots.

### Sidebar
Create a visually rich sidebar with:
- assistant/avatar status card,
- mic state,
- current mode,
- push-to-talk or wake-word status,
- telemetry section,
- navigation links,
- action buttons,
- keyboard shortcuts footer.

The sidebar should look like part of a finished product, not a debug panel.

### Dashboard Area
Use large rounded cards for:
- active persona,
- selected model,
- selected voice,
- memory mode/status,
- server status,
- agent backends,
- current voice mode,
- recent activity.

### Chat/Transcript Area
Replace the huge empty log-style panel with:
- message bubbles or timeline cards,
- voice transcript cards,
- agent response cards,
- tool/event cards visually separated,
- intentional empty state,
- better scrolling,
- clear labels,
- readable typography.

### Composer
Create a polished composer with:
- rounded text input,
- Send button,
- Delegate button,
- Context button,
- attachment/context preview area,
- voice transcript preview when applicable,
- clear disabled/loading states.

## Memory Viewer Requirements

Rebuild the memory viewer to match the reference quality.

Include:
- top header with title,
- search bar,
- stat pills,
- category tabs,
- left memory tree/sidebar,
- central graph or memory content area,
- right details panel,
- topic tags,
- selected states using blue/cyan,
- readable long-form memory cards,
- polished dark scrollbars.

## Setup Wizard Requirements

Rebuild the setup wizard to match the reference layout quality.

Include:
- large icon and title,
- subtitle,
- rounded option cards,
- selected states,
- requirement/status cards,
- green installed indicators,
- amber missing indicators,
- large Back / Next / Start buttons,
- setup completion page,
- quick tips panel,
- professional spacing and typography.

## Design System

Create a shared design system instead of hardcoding styles everywhere.

Include reusable tokens for:

```ts
colors: {
  backgroundApp: "#030712",
  backgroundPanel: "#07111f",
  backgroundCard: "#0f172a",
  backgroundElevated: "#162033",

  borderSubtle: "#1e293b",
  borderStrong: "#334155",
  borderActive: "#22d3ee",

  textPrimary: "#f8fafc",
  textSecondary: "#cbd5e1",
  textMuted: "#7f8ea3",
  textFaint: "#526175",

  accentPrimary: "#3b82f6",
  accentCyan: "#22d3ee",
  accentGlow: "rgba(34, 211, 238, 0.25)",

  success: "#22c55e",
  warning: "#f59e0b",
  error: "#ef4444",
  info: "#38bdf8"
}
````

Also define tokens for:

* radius,
* spacing,
* typography,
* shadows,
* focus rings,
* transitions,
* z-index layers.

## Shared Components

Create or refactor reusable components for:

* AppShell
* Sidebar
* Header
* StatusPill
* DashboardCard
* SectionCard
* StatusRow
* Button
* IconButton
* Tabs
* SegmentedControl
* Badge/Pill
* SearchInput
* Select
* TextInput
* TextArea
* Modal
* Toast
* EmptyState
* LoadingState
* MessageBubble
* TimelineEvent
* MemoryCard
* MemoryNode
* SetupWizardCard

Every control should support:

* default,
* hover,
* active,
* selected,
* disabled,
* loading,
* focus-visible,
* warning,
* destructive states where appropriate.

## Framework Migration Permission

If the current GUI framework cannot realistically produce the target professional result, replace it.

When migrating:

* preserve existing backend APIs and app functionality,
* create a clean frontend architecture,
* keep integration points clear,
* avoid breaking voice, memory, agent, setup, and diagnostics workflows,
* remove obsolete frontend code once the new implementation works.

## Functionality That Must Not Break

Preserve:

* mic mute,
* Free Talk mode,
* Wake Word mode,
* Push To Talk mode,
* transcription,
* TTS,
* agent delegation,
* context bundling,
* memory viewer,
* persona settings,
* backend status checks,
* setup wizard,
* logs,
* diagnostics,
* Ollama controls,
* model selection,
* voice selection.

## Acceptance Criteria

The work is complete only when:

* The app no longer resembles the current “Remote Agent Protocol” debug-style interface.
* The interface clearly matches the professional quality of the Jarvis reference screenshots.
* The layout feels intentionally designed, not assembled from boxes.
* The whole app uses a cohesive dark blue/cyan design system.
* The main dashboard, setup wizard, and memory viewer look like one unified product.
* All major controls are visually consistent.
* Empty states, loading states, and errors look polished.
* Existing workflows still function.
* The frontend feels production-ready.

## Testing

Add or update tests for:

* main app shell rendering,
* sidebar rendering,
* status pills,
* mode selector states,
* chat/composer rendering,
* memory viewer rendering,
* setup wizard rendering,
* status dashboard rendering,
* theme token availability,
* absence of obsolete orange/yellow accent styling except warning states,
* preservation of key workflows.

Run the relevant test suite and fix failures.

## Deliverables

After implementing, provide:

* summary of the frontend rebuild,
* whether the framework was changed and why,
* files changed,
* new components added,
* design tokens added,
* screens redesigned,
* obsolete frontend code removed,
* functionality verified,
* tests added or updated,
* test results,
* remaining polish recommendations.

Do not provide only advice or mockups. Implement the professional frontend directly.

```
```

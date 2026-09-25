# Design: Avatar Stage Layout (Stage ↔ Console toggle)

**Product:** Remote Agent Protocol
**Status:** Proposed for user review
**Date:** 2026-09-25
**Constraint carried forward:** `docs/notes/2026-09-17-control-plane-avatar-handoff.md`. Don't alter the Butler art, frame sequence, or state mapping.

## Product outcome

The Control Center opens on the **Stage**: Butler, large and centered, as the main thing on screen, with the conversation in a slim panel on the right. One button (or `Ctrl+Shift+A`) switches to **Console**, which is today's chat-first layout with the avatar small in the left rail. The choice survives restarts. Both layouts are the same live session. Nothing reloads, and the avatar doesn't restart its animation when you switch.

## Current state

- `web_app/index.html`: the avatar lives in the left **context rail** (`#contextRail > .context-panel[data-context-view="control"] > #avatarPanel`), capped at `max-height: 244px` (`layout-v4.css` L111–112). The main area (`main.task-canvas > #controlView`) holds the confirm bar, the conversation workbench (`#chatLog`, composer), and the Live activity panel.
- The rail is `max-height: 240px` below one breakpoint, and the avatar panel is hidden at the narrowest (`layout-v4.css` L282, L308, L339).
- The renderer is `web_app/avatar/frame-avatar-scene.js` (Canvas 2D over `runtime_512_v1/*.webp`), with a generation guard, reduced-motion handling, a fallback, and disposal.
- UI shell helpers are in `ui-shell-state.js`. `app.js` handles `Ctrl+L`, `Ctrl+M`, and the `Ctrl+K` palette.

## Goals

1. Two layout modes, **Stage** (avatar-first) and **Console** (chat-first, today's layout).
2. One visible toggle in the command header, a keyboard shortcut, and a command-palette entry.
3. A single avatar renderer instance that's re-parented between slots, never a second renderer.
4. The chat in Stage stays fully usable: transcript, composer, mic/PTT, confirmations.
5. The mode persists across restarts and defaults to Stage.
6. Responsive at every existing breakpoint. Reduced-motion is respected.
7. Dark theme only, matching the graphite design spec.

## Non-goals

- New avatar art, frames, expressions, or animation behavior.
- A higher-resolution frame set. This design flags the need (below) but doesn't produce one.
- Changes to the Agents, Personas, Memory, Settings, or Status views. The toggle affects the Control view only.

## Layout

### Stage mode (default)

```
┌ command header ─────────────────────────────── [Stage|Console] ─┐
│ rail │                STAGE                     │  CHAT DRAWER  │
│ nav  │        ┌─────────────────────┐           │  transcript   │
│      │        │                     │           │  (compact)    │
│      │        │    BUTLER AVATAR    │           │               │
│      │        │   (large, centered) │           │               │
│      │        └─────────────────────┘           │               │
│      │   state · emotion · persona name         │               │
│      │   ── live caption (current speech) ──    │               │
│      │   [working-now harness chips]            │  composer     │
│      │   [confirm bar overlay when pending]     │  [mic] [send] │
└──────┴──────────────────────────────────────────┴───────────────┘
```

- **Stage column:** the avatar canvas centered, sized to `min(62vh, 60vw of the stage column, AVATAR_STAGE_MAX_PX)`. Below it sit the status row (existing `#avatarStateLabel` / `#avatarEmotionLabel`), the persona name, and a **live caption** line showing the text currently being spoken, fed from the existing conversation/speech events. When idle, the caption shows the last user utterance at low emphasis.
- **Working-now chips:** the existing `#workingNow` content, rendered as a compact row under the caption: harness name, subject/action, and elapsed time.
- **Confirm bar:** the existing `#confirmBar`, shown as a centered overlay card at the top of the stage column, so a pending approval is never hidden in either mode.
- **Chat drawer (right):** width `clamp(320px, 28vw, 420px)`. It holds the existing `#chatLog` and `.composer-card`, moved rather than duplicated, and has its own collapse button that shrinks it to a 44px edge tab showing an unread badge.
- The **context rail** (left) stays, minus the avatar panel. The persona selector and system strip remain there.
- The Live activity/telemetry panel is collapsed by default in Stage.

### Console mode (today)

This is exactly the current layout: the avatar in the context rail at its current size, and the chat as the main workbench.

## Mechanics

- **Mode state:** `data-layout="stage" | "console"` on `.app-shell`. CSS does all the placement, using grid areas keyed off that attribute. Existing selectors stay intact and new rules are scoped under `[data-layout="stage"]`, in a new `layout-stage.css` loaded after `layout-v4.css`.
- **Single renderer, moved:** there are two empty slots, `#avatarSlotRail` (existing location) and `#avatarSlotStage` (new, in the stage column). On mode change, `appendChild` moves the existing `#avatarPanelBody` node into the target slot. The renderer must **not** be recreated.
  - Verify first: `frame-avatar-scene.js` has to redraw correctly when its host resizes. If it only sizes at init, add a `ResizeObserver` on `#avatarCanvasHost` that resizes the canvas backing store (device-pixel-ratio aware) and redraws the current frame. This is a sizing change only, and the frames and state mapping stay untouched.
  - Moving a node doesn't reset the canvas, but confirm that the generation guard and preload state survive the move (add a test).
- **Chat elements:** `#chatLog` and `.composer-card` are also moved (same `appendChild` approach) between the Console workbench and the Stage drawer. Their event listeners are preserved because they're the same nodes. Reading-position preservation (`conversation.js`) must survive the move, so the scroll offset is stored before the move and restored after it.
- **Toggle control:** a segmented button `[Stage | Console]` in `.command-header`, with `aria-pressed` states, plus a `Ctrl+Shift+A` shortcut (add it to the footer shortcut list) and a palette command, "Switch to Stage/Console layout".
- **Persistence:** a new `ui_layout` field in `app_state.py` (`jess_app_state.json`), read at page load through the existing status/catalog payload and written through a new `/api/action` `set_ui_layout {mode}`. Mirror it to `localStorage` in a try/catch for instant first paint only. The server value wins.
- **Frame resolution:** `runtime_512_v1` frames are 512px. The stage cap defaults to `AVATAR_STAGE_MAX_PX = 560` CSS px so the avatar isn't blurry on a 1x display. A config constant lets Ant raise it once a higher-resolution frame set exists. Record the need for a `runtime_1024_v1` set in the handoff notes. **Don't generate or alter frames in this work.**

## Responsive behavior

| Width | Stage mode | Console mode |
|---|---|---|
| > 1100px | 3 columns: rail · stage · drawer | unchanged |
| 700–1100px | rail collapses (existing behavior). Stage and drawer share the width, and the drawer defaults to collapsed with its edge tab visible | unchanged |
| < 700px | avatar at the top at ~40vh, chat below it full-width (stacked). Toggle still available | unchanged (avatar hidden, as today) |

## Accessibility

- The toggle is a keyboard-operable segmented control with visible focus (purple focus ring per the graphite spec).
- The avatar keeps its `role="img"` label. The live caption region is `aria-live="polite"` and doesn't duplicate the existing `#conversationLive` announcement. Use one or the other: keep `#conversationLive` and make the caption `aria-hidden` visual text.
- The drawer collapse button has `aria-expanded` / `aria-controls`.
- Reduced motion: the existing avatar rule applies, and the mode switch itself doesn't animate when `prefers-reduced-motion`.

## Testing

- `node --test` for new pure helpers in `ui-shell-state.js`: `nextLayout(current)`, `layoutFromState(serverValue, localValue)`, and `slotFor(mode)`.
- A JS DOM test (existing `tests/js` pattern): moving the avatar body between slots keeps the same canvas element, which proves no re-instantiation, and doesn't call the renderer's dispose.
- A JS test: moving `#chatLog` preserves the scroll offset and the "New messages" state.
- `tests/test_web_gui.py`: markup has both slots, the toggle, and the stylesheet link. `set_ui_layout` validates the mode, persists it, and round-trips through status.
- `app_state` tests: the new field defaults to `"stage"`, loads old files that lack it, and rejects bad values.
- Manual check (record it in the PR): switch modes during speech and a running agent job. The animation keeps playing, the caption keeps updating, and a pending confirmation stays visible in both modes.

## Acceptance

1. A fresh launch opens in Stage, with Butler large and centered and the chat on the right.
2. The toggle, `Ctrl+Shift+A`, or the palette switches to Console and back instantly, with no avatar reload or animation restart.
3. You can type and send from the Stage drawer, and the mic and PTT work.
4. A confirmation request is visible and actionable in both modes.
5. The mode persists after an app restart.
6. It behaves correctly at >1100px, 900px, and 600px widths.
7. The Butler art, frames, and state mapping are unchanged (`git diff` shows no changes under `web_app/avatar/images/` or the frame lists).

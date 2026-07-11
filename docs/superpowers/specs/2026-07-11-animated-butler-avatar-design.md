# Animated Butler Avatar Design

**Date:** 2026-07-11  
**Repository:** `ant3869/remote-agent-protocol`  
**Branch:** `feature/animated-butler-avatar`  
**Status:** Approved design

## 1. Summary

Add an optional, local-first animated talking-head companion to the existing Remote Agent Protocol web control center. The initial avatar is a refined, slightly stylized procedural butler bust rendered with native Three.js. It reacts to the existing voice, wake-word, agent, TTS, and session lifecycle without replacing or weakening any current controls.

The avatar system must remain isolated from the core audio and agent logic, fail safely, and support a future GLB/GLTF model with blendshapes, skeleton animation, and visemes. The first implementation uses a procedural rig so the repository does not inherit an uncertain third-party model license.

## 2. Repository Constraints

The application currently uses:

- A Python `ThreadingHTTPServer` in `remote_agent_protocol/web_gui.py`.
- A zero-build browser client in `remote_agent_protocol/web_app/` using plain HTML, CSS, and JavaScript.
- A Pipecat voice pipeline owned by `remote_agent_protocol/session.py`.
- UI event delivery through `/api/events` and status snapshots.
- Atomic persisted runtime selections through `remote_agent_protocol/app_state.py`.
- A graphite/dark operational UI with semantic status colors.

The design therefore does not introduce React, Vite, npm, or a second frontend application. Native Three.js ES modules fit the current architecture with the least disruption.

## 3. Goals

The implementation must:

1. Add a polished butler-style head and upper bust to the Control Center.
2. React visibly to listening, thinking, speaking, agent activity, confirmations, success, and errors.
3. Use real TTS audio amplitude for mouth movement when available.
4. Provide natural gaze, blinking, breathing, and restrained micro-motion.
5. Persist avatar settings through the existing application state file.
6. Support a future local GLB/GLTF avatar without redesigning the UI or event system.
7. Preserve all current voice, persona, memory, TTS, wake-word, and delegation behavior.
8. Degrade to a static accessible companion card if WebGL or Three.js fails.
9. Remain optional and disposable at runtime.

## 4. Non-Goals

The first implementation will not:

- Create a photorealistic human face.
- Bundle a third-party avatar model.
- Perform camera-based eye tracking.
- Add cloud avatar services.
- Add neural facial animation.
- Require phoneme or viseme support from every TTS backend.
- Convert the existing frontend to React.
- Expose raw TTS audio outside the local application.
- Replace the current transcript, state labels, or controls with animation-only feedback.

## 5. Rendering Framework

Use a pinned, vendored Three.js browser module and GLTFLoader under the static web application directory.

Recommended layout:

```text
remote_agent_protocol/web_app/vendor/three/
  LICENSE
  three.module.min.js
  addons/loaders/GLTFLoader.js
```

The exact Three.js release must be pinned in a small `VERSION` or metadata file. The repository must retain the upstream license notice. No CDN is used so the local-first application remains functional offline.

The avatar entry point is loaded as an ES module:

```html
<script type="module" src="/avatar/avatar-entry.js"></script>
```

The module must not initialize a renderer until avatar settings are available and the avatar is enabled.

## 6. High-Level Architecture

```text
VoiceSession / agent lifecycle / wake-word events
                    |
                    v
            web_gui.py status + events
                    |
                    v
             app.js runtime adapter
                    |
                    v
          avatar/avatar-entry.js
                    |
        +-----------+------------+
        |                        |
        v                        v
AvatarStateController      AvatarPanel
        |                        |
        v                        v
Expression / gaze / idle   Three.js scene
        |                        |
        +-----------+------------+
                    |
                    v
        Procedural butler or GLB model

TTS PCM frames
      |
      v
AvatarAudioTap -> AvatarAudioEnvelopeHub -> /api/avatar-audio SSE
                                              |
                                              v
                                       browser lip-sync
```

### 6.1 Browser Module Boundary

The avatar module exposes a deliberately small interface:

```js
export interface AvatarRuntime {
  updateRuntime(runtimeState): void;
  updateSettings(settings): void;
  setPanelVisible(visible): void;
  dispose(): void;
}
```

`app.js` remains responsible for application state and event polling. It passes normalized runtime information to the avatar module rather than allowing the avatar to directly mutate shared UI state.

### 6.2 Planned Browser Files

```text
remote_agent_protocol/web_app/avatar/
  avatar-entry.js
  avatar-panel.js
  avatar-controller.js
  avatar-scene.js
  procedural-butler.js
  model-loader.js
  expressions.js
  gaze-controller.js
  lip-sync.js
  persona-profiles.js
  avatar-settings.js
  math.js
```

Each file has one clear concern:

- `avatar-entry.js`: bootstrap, feature detection, lazy initialization, public boundary.
- `avatar-panel.js`: DOM status labels, collapse behavior, fallback card.
- `avatar-controller.js`: state priority, transitions, emotion resolution.
- `avatar-scene.js`: renderer, camera, lights, animation loop, resize, disposal.
- `procedural-butler.js`: build the initial rig and expose named controls.
- `model-loader.js`: load and normalize a local GLB/GLTF model.
- `expressions.js`: expression target definitions and interpolation.
- `gaze-controller.js`: blinking, saccades, camera gaze, thinking offsets.
- `lip-sync.js`: audio-envelope smoothing and jaw/mouth targets.
- `persona-profiles.js`: persona-to-avatar behavior profiles.
- `avatar-settings.js`: normalize browser-facing settings and quality presets.
- `math.js`: reusable damp, clamp, seeded timing, and range helpers.

## 7. UI Placement

The companion appears at the top of the existing right-side activity area in the Control Center. The current agent activity and telemetry remain below it.

Desktop layout:

```text
+-----------------------------+-----------------------+
| Conversation                | Companion             |
|                             | [ animated butler ]   |
|                             | Listening - attentive |
|                             +-----------------------+
|                             | Agent activity        |
|                             | Telemetry             |
+-----------------------------+-----------------------+
```

Responsive behavior:

- Wide desktop: avatar card in the right activity column.
- Narrow desktop/tablet: avatar card above agent activity.
- Mobile: compact horizontal avatar card above the conversation or activity block.
- Collapsed: small status row with persona, state, and expand button.
- Disabled: panel and renderer are removed, not merely hidden.

Panel content:

- Canvas or static fallback visual.
- Current persona name.
- Resolved avatar state.
- Resolved emotion.
- Speaking/listening/thinking indicator.
- Optional short status text.
- Collapse/expand control.

No essential information is conveyed only through movement.

## 8. Procedural Butler Design

The initial avatar is a stylized head and upper bust made from native Three.js geometry and standard materials.

### 8.1 Visual Direction

- Composed, attentive, professional.
- Slightly stylized rather than uncanny pseudo-realism.
- Dark formal jacket, pale shirt, restrained bow tie.
- Soft neutral skin and subtle facial detail.
- Graphite studio environment with semantic highlights matching the existing UI.
- No bright cyan-first visual motif.

### 8.2 Rig Controls

The procedural model exposes stable named controls so the rest of the animation system does not depend on geometry implementation details.

```js
{
  root,
  bust,
  neck,
  head,
  jaw,
  mouthUpper,
  mouthLower,
  mouthCornerLeft,
  mouthCornerRight,
  cheekLeft,
  cheekRight,
  browLeft,
  browRight,
  eyeLeft,
  eyeRight,
  pupilLeft,
  pupilRight,
  lidLeft,
  lidRight
}
```

The bust includes shoulders, lapels, collar, and bow tie. A moustache or similarly restrained formal facial detail may be included when it improves the silhouette without making the character comic.

## 9. GLB/GLTF Upgrade Path

The system attempts to load a selected avatar from:

```text
remote_agent_protocol/web_app/assets/avatars/<avatar-id>/
  metadata.json
  <model-file>.glb
  textures/
  animations/
```

The default butler metadata file exists even while no GLB is bundled.

Example metadata:

```json
{
  "id": "butler",
  "label": "Butler",
  "model": "butler.glb",
  "fallback": "procedural-butler",
  "scale": 1.0,
  "cameraTarget": [0, 1.55, 0],
  "controls": {
    "jaw": ["jawOpen", "JawOpen"],
    "blinkLeft": ["eyeBlinkLeft", "Blink_L"],
    "blinkRight": ["eyeBlinkRight", "Blink_R"]
  }
}
```

`model-loader.js` must support:

- GLB/GLTF loading.
- Morph target discovery.
- Skeleton and AnimationMixer support.
- Metadata-provided aliases.
- Normalized scale and camera framing.
- Loading and error states.
- Complete disposal of geometry, materials, textures, animations, and object references.

Failure to load a model always returns to the procedural butler without taking down the application.

## 10. Avatar Runtime States

```ts
type AvatarState =
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "happy"
  | "concerned"
  | "confused"
  | "focused"
  | "apologetic"
  | "error"
  | "sleeping";
```

### 10.1 Priority Resolution

Concurrent signals resolve in this order:

1. `error`
2. `speaking`
3. `listening`
4. `thinking`
5. `focused` agent activity
6. `concerned` confirmation/warning
7. `happy` recent completion
8. wake-word standby
9. `idle`
10. `sleeping`

Higher-priority states temporarily override lower states. Short-lived emotions such as pleased or surprised have explicit expiry times and return to the underlying lifecycle state.

### 10.2 Existing Event Mapping

| Existing signal | Avatar state and response |
|---|---|
| Wake word detected | `listening`; eyes widen slightly, posture rises, gaze returns to camera |
| Listening for command | `listening`; stable attentive gaze and slight forward posture |
| User started speaking | `listening`; reduced blinking and idle motion |
| User stopped speaking | `thinking`; brief downward glance |
| Transcribing | `thinking`; restrained brow movement and processing gaze |
| Agent/LLM responding | `thinking`; slow gaze shift and subtle posture hold |
| TTS speaking true | `speaking`; audio-driven jaw and mouth movement |
| Active agent job | `focused`; occasional side glance, reduced idle motion |
| Confirmation required | `concerned`; direct attentive gaze |
| Agent completed | brief `happy`; small smile or nod |
| Agent failed | `error` or `concerned` depending on severity |
| Session failed | `error` |
| Wake-word passive standby | calm `idle` |
| Extended inactivity | `sleeping` when enabled |

## 11. Facial Expressions

Supported expressions:

- neutral
- attentive
- warm
- pleased
- concerned
- confused
- apologetic
- thinking
- focused
- surprised
- error

Each expression is a set of normalized targets, not a one-off animation clip.

```js
{
  browInner: 0,
  browOuter: 0,
  browAsymmetry: 0,
  eyelid: 0,
  eyeWiden: 0,
  jawOpen: 0,
  mouthWidth: 0,
  mouthCorner: 0,
  cheekRaise: 0,
  headPitch: 0,
  headYaw: 0,
  headRoll: 0
}
```

All targets are multiplied by persona expressiveness and the global expression-intensity setting. The controller uses damped interpolation so state changes do not snap.

## 12. Eye and Gaze Behavior

The butler profile defaults to restrained natural motion:

```js
{
  blinkIntervalSeconds: [3.5, 7.5],
  doubleBlinkChance: 0.12,
  saccadeIntervalSeconds: [1.8, 4.5],
  saccadeIntensity: 0.18,
  eyeContact: 0.82,
  idleIntensity: 0.28,
  expressionIntensity: 0.62,
  speakingHeadMotion: 0.16
}
```

Behavior rules:

- Blinks are slightly asymmetric.
- Double blinks are uncommon.
- Listening reduces blink frequency and saccade intensity.
- Thinking introduces small off-camera and downward gaze shifts.
- Speaking softens eye contact without creating constant wandering.
- Error states suppress playful micro-motion.
- Gaze remains bounded to anatomically plausible ranges.
- Pointer movement is not used as eye tracking in the first release.

## 13. Idle Motion

Idle life includes:

- Subtle shoulder and neck breathing.
- Small head stabilization movement.
- Rare posture reset.
- Occasional micro-expression.
- Natural blinking and saccades.
- Attentive reset when user activity begins.

The butler must remain composed. Constant swaying, repetitive nodding, exaggerated breathing, or large random eye motion is prohibited.

## 14. TTS Audio Envelope and Lip-Sync

### 14.1 Problem

The browser currently receives bot speaking start/stop events but does not play the TTS audio. Audio is emitted by the Python Pipecat local output transport. A browser AudioContext therefore cannot directly analyze the real signal.

### 14.2 Pipeline Tap

Add `AvatarAudioTap` after the selected TTS service and before local audio output:

```text
TTS service
    |
    v
AvatarAudioTap
    |
    v
LocalAudioTransport output
```

The tap observes `TTSAudioRawFrame` PCM data, calculates an envelope, and always forwards the original frame unchanged.

```py
@dataclass(frozen=True)
class AvatarAudioEnvelope:
    rms: float
    peak: float
    voiced: bool
    sample_rate: int
    channels: int
    timestamp: float
```

Requirements:

- Values are clamped to `0.0..1.0`.
- Silence is stable and does not chatter.
- Calculation supports the PCM format produced by the current pipeline.
- Frames are never delayed or modified.
- Emission is rate-limited to approximately 20 Hz.
- The tap performs no network I/O directly.

### 14.3 Envelope Hub and SSE

A small thread-safe `AvatarAudioEnvelopeHub` stores only the latest sample and sequence number. The web server exposes a same-origin server-sent event endpoint:

```text
GET /api/avatar-audio
```

The SSE payload contains only amplitude telemetry:

```json
{
  "seq": 104,
  "rms": 0.31,
  "peak": 0.58,
  "voiced": true,
  "timestamp": 1783795200.42
}
```

Rules:

- No raw PCM leaves the pipeline.
- Each client receives bounded latest-value updates, not an unbounded queue.
- A periodic keepalive prevents stale connections.
- Disconnects are caught without logging noisy stack traces.
- The avatar reconnects with backoff.
- If telemetry is unavailable, the existing speaking boolean drives a restrained fallback cadence.

### 14.4 Mouth Motion

Envelope values drive:

- Jaw opening.
- Lower lip movement.
- Small mouth-width variation.
- Cheek compression.
- Tiny speaking head movement.

The controller uses attack/release smoothing. Peak influences consonant-like closures and RMS controls sustained jaw opening. The first release does not pretend amplitude is true phoneme-level lip-sync; it is explicitly an extensible fallback beneath future viseme support.

## 15. Persona-Aware Behavior

```ts
interface PersonaAvatarProfile {
  personaId: string;
  avatarId: string;
  defaultExpression: string;
  speakingStyle: "calm" | "energetic" | "formal" | "warm";
  idleIntensity: number;
  eyeContact: number;
  expressiveness: number;
  emotionBias?: Record<string, number>;
}
```

Initial butler/Jess profile:

```js
{
  personaId: "jess",
  avatarId: "butler",
  defaultExpression: "attentive",
  speakingStyle: "formal",
  idleIntensity: 0.28,
  eyeContact: 0.82,
  expressiveness: 0.62,
  emotionBias: {
    warm: 0.15,
    pleased: 0.10,
    surprised: -0.25,
    error: -0.10
  }
}
```

Persona lookup must be case-insensitive and normalize display names. Unknown/custom personas inherit a neutral profile and the selected default avatar.

## 16. Emotion Input

Emotion resolution order:

1. Explicit future event metadata:

```json
{
  "avatar": {
    "state": "speaking",
    "emotion": "warm",
    "intensity": 0.45
  }
}
```

2. Deterministic application state:
   - error
   - pending confirmation
   - failed/completed job
   - listening/thinking/speaking lifecycle
3. Local text cues from the latest assistant transcript:
   - apology language
   - uncertainty
   - warnings
   - success/completion
   - direct questions
4. Persona default.

Text cue inference must be lightweight, deterministic, and low intensity. It must not invoke another language model.

## 17. Persisted Settings

Extend `AppState` with normalized avatar settings:

```py
avatar_enabled: bool = True
avatar_id: str = "butler"
avatar_quality: str = "high"
avatar_lip_sync: bool = True
avatar_gaze: bool = True
avatar_idle_motion: bool = True
avatar_expression_intensity: float = 0.62
avatar_reduced_motion: bool = False
avatar_show_state: bool = True
avatar_panel_collapsed: bool = False
```

The settings status payload uses camelCase for the browser:

```json
{
  "avatar": {
    "enabled": true,
    "avatarId": "butler",
    "quality": "high",
    "lipSync": true,
    "gaze": true,
    "idleMotion": true,
    "expressionIntensity": 0.62,
    "reducedMotion": false,
    "showState": true,
    "panelCollapsed": false
  }
}
```

Add one `avatar_settings` action to `/api/action`. The backend validates and persists a complete settings object atomically. Unknown fields are ignored. Invalid values normalize to defaults rather than breaking startup.

Settings UI:

- Enable animated avatar.
- Selected avatar.
- Quality: low, medium, high.
- Lip-sync.
- Eye movement and gaze.
- Idle animation.
- Expression intensity slider.
- Reduced motion.
- Show state labels.
- Collapse companion panel.

## 18. Quality and Performance

Quality presets:

```js
const QUALITY = {
  low: {
    maxPixelRatio: 1,
    targetFps: 24,
    antialias: false,
    shadows: false
  },
  medium: {
    maxPixelRatio: 1.5,
    targetFps: 30,
    antialias: true,
    shadows: false
  },
  high: {
    maxPixelRatio: 2,
    targetFps: 60,
    antialias: true,
    shadows: true
  }
};
```

Performance requirements:

- Three.js is loaded only when enabled.
- Rendering pauses when the document is hidden.
- Rendering pauses when the panel is collapsed or outside the viewport.
- `ResizeObserver` controls renderer size.
- Pixel ratio is capped by the selected quality.
- Low mode uses simpler geometry and materials where practical.
- The animation loop updates Three.js state directly and does not trigger application DOM rerenders.
- Renderer, render lists, textures, materials, geometries, mixers, EventSource, observers, and timers are disposed on teardown.
- WebGL context loss displays a fallback and attempts at most one controlled reinitialization.

## 19. Reduced Motion and Accessibility

Reduced-motion mode retains:

- State and emotion text.
- Infrequent blinking.
- Basic gaze.
- Minimal speaking jaw motion.

It disables or sharply limits:

- Breathing movement.
- Posture shifts.
- Head tilts.
- Micro-expressions.
- Animated camera transitions.

The browser's `prefers-reduced-motion` setting activates reduced motion unless the user has explicitly selected an application setting. The avatar remains independently disableable.

Accessibility requirements:

- Status text uses `aria-live="polite"` only for meaningful state changes.
- Canvas has an accessible label and is decorative when equivalent text is visible.
- Controls remain keyboard accessible.
- No flashing, strobing, or high-frequency brightness changes.
- Color is not the only state indicator.

## 20. Failure and Fallback Behavior

| Failure | Required result |
|---|---|
| Three.js module fails to load | Static butler silhouette and normal text state |
| WebGL unavailable | Accessible non-animated companion card |
| GLB absent | Procedural butler loads |
| GLB malformed | Warning logged; procedural butler remains |
| Audio SSE disconnected | Speaking-state fallback mouth motion |
| Main event polling disconnected | Avatar returns to neutral disconnected state |
| Invalid saved settings | Safe normalized defaults |
| Avatar module exception | Main application remains usable |
| WebGL context lost | Static fallback; one guarded reinitialize attempt |

The avatar is never allowed to block application boot, voice startup, TTS, agent execution, or shutdown.

## 21. Testing

### 21.1 Python Unit Tests

Add or update tests for:

- Avatar settings default values.
- Avatar settings save/reload.
- Old state files load with avatar defaults.
- Corrupt avatar values normalize safely.
- `avatar_settings` action persists values.
- Status payload exposes browser settings.
- Envelope RMS and peak remain bounded.
- Silent PCM produces a closed-mouth envelope.
- Audio frames pass through unchanged.
- Envelope emission is rate-limited.
- Pipeline order is TTS, avatar tap, local output.
- Hub stores only the latest sequence/sample.
- SSE serializes the documented payload and handles disconnects.

### 21.2 Browser and Static Integration Tests

Following the repository's existing static frontend test style, assert:

- Avatar panel exists without replacing current controls.
- Settings controls exist.
- Avatar entry point is loaded as a module.
- Three.js is not initialized while disabled.
- Wake detection maps to listening/attentive.
- User speech maps to listening.
- Transcribing maps to thinking.
- Speaking starts lip-sync and stop releases the jaw.
- Session failure maps to error.
- Pending confirmation maps to concerned.
- Completed job briefly maps to pleased.
- Reduced motion suppresses idle transforms.
- Missing GLB invokes the procedural model.
- WebGL failure invokes the static fallback.
- Disposal closes EventSource and releases scene resources.

### 21.3 Regression Tests

The existing test suite must remain green, especially tests covering:

- Session construction.
- Web status and actions.
- App state persistence.
- TTS switching, including Coqui.
- Wake-word state.
- Agent lifecycle events.
- Static asset serving.
- Process guard and shutdown.

## 22. Planned Repository Changes

```text
docs/superpowers/specs/
  2026-07-11-animated-butler-avatar-design.md

remote_agent_protocol/
  app_state.py
  session.py
  session_processors.py
  web_gui.py
  avatar_audio.py
  web_app/
    index.html
    styles.css
    app.js
    avatar/
      avatar-entry.js
      avatar-panel.js
      avatar-controller.js
      avatar-scene.js
      procedural-butler.js
      model-loader.js
      expressions.js
      gaze-controller.js
      lip-sync.js
      persona-profiles.js
      avatar-settings.js
      math.js
    assets/
      avatars/
        butler/
          metadata.json
    vendor/
      three/
        LICENSE
        VERSION
        three.module.min.js
        addons/loaders/GLTFLoader.js

tests/
  test_app_state.py
  test_session_processors.py
  test_web_gui.py
  test_avatar_audio.py
```

The implementation plan may split browser tests into additional files if doing so improves isolation.

## 23. Delivery Sequence

Implementation should proceed in this order:

1. Add avatar settings model, normalization, status payload, and action.
2. Add audio envelope calculation, hub, tap, and tests.
3. Insert the audio tap into the pipeline without changing output behavior.
4. Add SSE endpoint and browser reconnection logic.
5. Vendor pinned Three.js files and license.
6. Add the panel, settings controls, and static fallback.
7. Build the procedural butler scene and disposal path.
8. Add state controller, expressions, gaze, idle motion, and persona profile.
9. Add audio-driven lip-sync and speaking fallback.
10. Add GLB metadata and loader fallback.
11. Complete performance, reduced-motion, WebGL-failure, and responsive behavior.
12. Run focused tests and the full applicable suite.
13. Update README or architecture documentation with operation and extension notes.

## 24. Acceptance Criteria

The feature is complete when:

- The Control Center contains an optional animated butler companion.
- The avatar visibly reacts to listening, thinking, speaking, agent work, confirmations, completion, and errors.
- TTS amplitude drives jaw and mouth movement through local envelope telemetry.
- Speaking start and stop remain synchronized even when envelope telemetry is unavailable.
- Eye movement, blinking, breathing, and micro-motion appear natural and restrained.
- The default butler behavior is calm, formal, attentive, and persona-appropriate.
- Avatar settings are configurable and persist across restarts.
- A missing model cleanly uses the procedural butler.
- GLB/GLTF loading is supported for future assets.
- Reduced motion and complete disablement work.
- WebGL failure leaves a useful accessible fallback.
- Existing voice, persona, memory, wake-word, TTS, Coqui, and agent workflows continue to work.
- Focused and regression tests pass.
- No raw TTS audio is sent to the browser.
- The avatar releases all browser and server-side resources during shutdown or disablement.

## 25. Future Enhancements

The architecture intentionally leaves room for:

- ARKit-compatible blendshape aliases.
- Provider-issued viseme events.
- Forced-alignment phoneme timing for generated audio.
- Multiple persona-specific GLB avatars.
- Hand and torso gestures.
- Camera-aware gaze.
- Additional lighting/environment profiles.
- Avatar authoring and calibration tools.

These enhancements are not required for the first implementation and must not expand the initial scope.
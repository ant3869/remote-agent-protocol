# Animated Butler Avatar Design

**Date:** 2026-07-11  
**Repository:** `ant3869/remote-agent-protocol`  
**Branch:** `feature/animated-butler-avatar`  
**Status:** Approved design, reviewed for implementation

## 1. Summary

Add an optional, local-first animated talking-head companion to the existing Remote Agent Protocol web control center. The first avatar is a refined, slightly stylized procedural butler head and upper bust rendered with native Three.js.

The avatar reacts to the existing voice, wake-word, TTS, session, and agent lifecycle. It adds no alternate agent logic, does not replace current controls, and must never block voice startup, TTS playback, delegation, shutdown, or normal UI operation.

The architecture supports future local GLB/GLTF avatars with morph targets, skeletal animation, and visemes. The initial release deliberately avoids bundling a third-party character model so the repository does not inherit uncertain licensing or attribution requirements.

## 2. Repository Constraints

The current application uses:

- A Python `ThreadingHTTPServer` in `remote_agent_protocol/web_gui.py`.
- A zero-build browser client in `remote_agent_protocol/web_app/` using plain HTML, CSS, and JavaScript.
- A Pipecat voice pipeline owned by `remote_agent_protocol/session.py`.
- Status snapshots and event polling through the existing web API.
- Atomic runtime-setting persistence through `remote_agent_protocol/app_state.py`.
- A dense graphite operational UI with semantic status colors.

The implementation therefore uses native Three.js ES modules. It does not introduce React, React Three Fiber, Vite, npm, or a second frontend application.

## 3. Goals

The feature must:

1. Add a polished butler-style head and upper bust to the Control Center.
2. React visibly to listening, thinking, speaking, agent activity, confirmations, completion, and errors.
3. Use real TTS amplitude for mouth movement when the PCM envelope is available.
4. Provide natural gaze, blinking, breathing, and restrained micro-motion.
5. Persist avatar settings through the existing application state file.
6. Load a future local GLB/GLTF avatar without redesigning the UI or event system.
7. Preserve all voice, persona, memory, wake-word, TTS, Coqui, and delegation behavior.
8. Degrade to a useful static companion card when WebGL or Three.js is unavailable.
9. Release all renderer, model, stream, timer, and observer resources when disabled or unmounted.

## 4. Non-Goals

The first release will not:

- Create a photorealistic face.
- Bundle a third-party avatar model.
- Use cloud avatar services.
- Perform camera-based or pointer-based eye tracking.
- Add neural facial animation.
- Require phoneme or viseme output from every TTS provider.
- Expose raw TTS audio to the browser.
- Replace transcript or status information with animation-only feedback.
- Convert the existing frontend to a framework build.

## 5. Rendering Framework and Local Imports

Vendor a pinned Three.js browser module and GLTFLoader inside the static application:

```text
remote_agent_protocol/web_app/vendor/three/
  LICENSE
  VERSION
  three.module.min.js
  addons/loaders/GLTFLoader.js
```

Retain the upstream Three.js license and record the exact release in `VERSION`. Do not use a CDN.

Use an import map so the unmodified upstream addon can resolve its normal module imports:

```html
<script type="importmap">
{
  "imports": {
    "three": "/vendor/three/three.module.min.js",
    "three/addons/": "/vendor/three/addons/"
  }
}
</script>
<script type="module" src="/avatar/avatar-entry.js"></script>
```

The avatar module must not initialize Three.js until status settings have loaded and the avatar is enabled.

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

`WebVoiceApp` owns one `AvatarAudioEnvelopeHub` and injects its `publish` callback when constructing `VoiceSession`. `VoiceSession` passes that callback into `AvatarAudioTap`. This avoids a process-global singleton and gives shutdown one clear ownership path.

### 6.1 Browser Boundary

`avatar-entry.js` exposes a small runtime interface:

```js
{
  updateRuntime(runtimeState),
  updateSettings(settings),
  setPanelVisible(visible),
  dispose()
}
```

`app.js` remains the owner of general application state. It normalizes current status and events, then passes only the avatar-relevant snapshot into the avatar runtime.

The avatar module may update its own canvas and companion-card text. It must not directly change microphone state, personas, models, TTS settings, agent jobs, memory, or shared navigation.

### 6.2 Browser Files

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

Responsibilities:

- `avatar-entry.js`: bootstrap, feature detection, lazy initialization, public boundary.
- `avatar-panel.js`: companion DOM, labels, collapse behavior, static fallback.
- `avatar-controller.js`: state priority, transitions, emotion resolution.
- `avatar-scene.js`: renderer, camera, lights, animation loop, resize, disposal.
- `procedural-butler.js`: procedural rig and named controls.
- `model-loader.js`: metadata, GLB/GLTF loading, normalization, morph/skeleton discovery.
- `expressions.js`: expression targets and interpolation.
- `gaze-controller.js`: blinking, saccades, eye contact, thinking gaze.
- `lip-sync.js`: SSE envelope smoothing and mouth targets.
- `persona-profiles.js`: persona-to-avatar behavior mapping.
- `avatar-settings.js`: browser normalization and quality presets.
- `math.js`: damp, clamp, timing, and range helpers.

## 7. UI Placement

Place the companion at the top of the existing right-side activity area. Existing agent activity and telemetry remain below it.

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

- Wide desktop: card in the existing right activity column.
- Narrow desktop/tablet: card above agent activity.
- Mobile: compact horizontal card above the conversation or activity block.
- Collapsed: persona, current state, and expand control remain visible.
- Disabled: panel and renderer are removed rather than hidden offscreen.

The panel contains:

- Canvas or static fallback visual.
- Current persona name.
- Resolved avatar state.
- Resolved emotion.
- Speaking/listening/thinking indicator.
- Optional short status text.
- Collapse/expand control.

No essential information is conveyed only through movement or color.

## 8. Procedural Butler

The initial model uses native Three.js geometry and standard materials.

Visual direction:

- Composed, attentive, and professional.
- Slightly stylized rather than pseudo-photorealistic.
- Dark jacket, pale shirt, formal collar, lapels, and restrained bow tie.
- Soft neutral skin and subtle facial definition.
- Graphite studio background matching the existing UI.
- Semantic highlights may use the current status palette; do not restore the obsolete cyan-first motif.

### 8.1 Rig Contract

The procedural model exposes stable named controls:

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

The animation controller depends on this contract, not the geometry implementation. A moustache or similarly restrained formal detail may be included only when it improves the silhouette without making the character comic.

## 9. GLB/GLTF Upgrade Path

Avatar assets live under:

```text
remote_agent_protocol/web_app/assets/avatars/<avatar-id>/
  metadata.json
  <optional-model>.glb
  textures/
  animations/
```

The initial butler metadata contains no model path, preventing a guaranteed 404 while the procedural version is the intended default:

```json
{
  "id": "butler",
  "label": "Butler",
  "model": null,
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

The loader attempts a network request only when `model` is a non-empty local relative path.

`model-loader.js` supports:

- GLB/GLTF loading.
- Morph target discovery.
- Skeleton and `AnimationMixer` support.
- Metadata-provided control aliases.
- Normalized scale and camera framing.
- Loading and error states.
- Disposal of geometry, materials, textures, mixers, clips, and references.

A missing or malformed model always falls back to the procedural butler.

## 10. Runtime States

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

### 10.1 State Priority

Concurrent signals resolve in this order:

1. `error`
2. `speaking`
3. `listening`
4. `thinking`
5. `focused` agent activity
6. `concerned` confirmation or warning
7. `happy` recent completion
8. wake-word standby
9. `idle`
10. `sleeping`

Short-lived emotions have expiry times and return to the underlying lifecycle state. Transitions use damped interpolation rather than snapping.

### 10.2 Event Mapping

| Signal | Avatar response |
|---|---|
| Wake word detected | `listening`; eyes widen slightly, posture rises, gaze returns to camera |
| Listening for command | `listening`; stable attentive gaze and slight forward posture |
| User started speaking | `listening`; reduced blinking and idle motion |
| User stopped speaking | `thinking`; brief downward glance |
| Transcribing | `thinking`; restrained brow movement and processing gaze |
| Agent/LLM responding | `thinking`; slow gaze shift and subtle posture hold |
| TTS speaking true | `speaking`; audio-driven jaw and mouth movement |
| Active agent job | `focused`; occasional side glance and reduced idle motion |
| Confirmation required | `concerned`; direct attentive gaze |
| Agent completed | brief `happy`; small smile or nod |
| Agent failed | `error` or `concerned`, based on severity |
| Session failed | `error` |
| Passive wake-word standby | calm `idle` |
| Extended inactivity | `sleeping`, when enabled |

`TranscriptTap` currently emits user-stop and bot-speaking telemetry. Add `UserStartedSpeakingFrame` handling so the browser receives a real `turn/user_started` event instead of relying only on inferred wake phases.

## 11. Expressions

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

Each expression is a normalized target set:

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

Targets are multiplied by persona expressiveness and global expression intensity. Expression and audio targets blend so lip-sync does not erase emotion.

## 12. Eye and Gaze Behavior

Initial butler profile:

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

Rules:

- Blinks are slightly asymmetric.
- Double blinks are uncommon.
- Listening reduces blink frequency and saccade intensity.
- Thinking introduces small off-camera and downward shifts.
- Speaking softens eye contact without constant wandering.
- Error states suppress playful micro-motion.
- Gaze remains inside anatomically plausible limits.

## 13. Idle Motion

Idle life includes:

- Subtle shoulder and neck breathing.
- Small head stabilization movement.
- Rare posture resets.
- Occasional micro-expressions.
- Natural blinking and saccades.
- An attentive reset as soon as user activity begins.

Constant swaying, repetitive nodding, exaggerated breathing, and large random eye motion are prohibited.

## 14. Audio Envelope and Lip-Sync

### 14.1 Pipeline Tap

The browser does not play TTS audio, so Web Audio cannot analyze the real output. Add `AvatarAudioTap` after the selected TTS service and before local output:

```text
TTS service
    |
    v
AvatarAudioTap
    |
    v
LocalAudioTransport output
```

The tap observes `TTSAudioRawFrame` PCM data, calculates an envelope, and forwards the original frame unchanged.

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

- Clamp `rms` and `peak` to `0.0..1.0`.
- Use a stable silence threshold and avoid mouth chatter.
- Support the PCM format emitted by the current Pipecat pipeline.
- Never delay, replace, or mutate audio frames.
- Rate-limit publication to approximately 20 Hz.
- Perform no HTTP or SSE work inside the pipeline processor.

### 14.2 Envelope Hub and SSE

`AvatarAudioEnvelopeHub` stores only the newest sample and sequence number. The web server exposes:

```text
GET /api/avatar-audio
```

Example SSE data:

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

- Raw PCM never leaves the audio pipeline.
- Each client receives bounded latest-value updates, not an unbounded queue.
- Send a periodic keepalive.
- Stop cleanly when the application stop event is set.
- Catch client disconnects without noisy tracebacks.
- Reconnect in the browser with bounded exponential backoff.
- When telemetry is absent, the existing `speaking` boolean drives restrained fallback mouth motion.

### 14.3 Mouth Motion

The envelope drives:

- Jaw opening.
- Lower-lip movement.
- Small mouth-width variation.
- Cheek compression.
- Tiny speaking head motion.

Use attack/release smoothing. RMS controls sustained opening; peak adds short consonant-like closures. This is amplitude animation, not claimed phoneme-perfect lip-sync. The interface remains ready for future viseme timing.

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

Initial profile:

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

Persona lookup is case-insensitive and normalizes display names. Unknown and custom personas inherit a neutral profile and the selected default avatar.

## 16. Emotion Input

Resolve emotion in this order:

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

2. Deterministic application state: errors, pending confirmations, failed/completed jobs, and lifecycle state.
3. Low-intensity local text cues from the latest assistant transcript: apologies, uncertainty, warnings, completion, or questions.
4. Persona default.

Text cue inference is deterministic and does not invoke another language model.

## 17. Persisted Settings

Extend `AppState`:

```py
avatar_enabled: bool = True
avatar_id: str = "butler"
avatar_quality: str = "high"
avatar_lip_sync: bool = True
avatar_gaze: bool = True
avatar_idle_motion: bool = True
avatar_expression_intensity: float = 0.62
avatar_reduced_motion: bool | None = None
avatar_show_state: bool = True
avatar_panel_collapsed: bool = False
```

`avatar_reduced_motion=None` means follow the browser's `prefers-reduced-motion` setting. `True` forces reduced motion; `False` explicitly permits normal motion.

Browser status payload:

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
    "reducedMotion": null,
    "showState": true,
    "panelCollapsed": false
  }
}
```

Add one `avatar_settings` action to `/api/action`. The backend validates and persists a complete settings object atomically. Unknown fields are ignored. Invalid values normalize to safe defaults.

Settings UI:

- Enable animated avatar.
- Selected avatar.
- Quality: low, medium, high.
- Lip-sync.
- Eye movement and gaze.
- Idle animation.
- Expression intensity.
- Motion preference: system, reduced, normal.
- Show state labels.
- Collapse companion panel.

## 18. Quality and Performance

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

Requirements:

- Lazy-load Three.js only while enabled.
- Pause rendering when the document is hidden.
- Pause rendering when the panel is collapsed or outside the viewport.
- Use `ResizeObserver` for canvas sizing.
- Cap pixel ratio according to quality.
- Keep animation state outside general UI rerender paths.
- Dispose renderer, render lists, context, textures, materials, geometries, mixers, EventSource, observers, and timers.
- On WebGL context loss, show the fallback and attempt at most one guarded reinitialization.

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

Accessibility requirements:

- Use `aria-live="polite"` only for meaningful state changes.
- Give the canvas an accessible label or mark it decorative when equivalent text is visible.
- Keep all settings and collapse controls keyboard accessible.
- Do not flash or strobe.
- Do not use color as the only state indicator.
- Keep non-visual status indicators available when the avatar is disabled.

## 20. Failure Behavior

| Failure | Required result |
|---|---|
| Three.js import fails | Static butler silhouette and normal state text |
| WebGL unavailable | Accessible non-animated companion card |
| GLB absent | Procedural butler loads without requesting a missing file |
| GLB malformed | Warning logged; procedural butler remains |
| Audio SSE disconnects | Speaking-state fallback mouth motion |
| Main event polling disconnects | Neutral disconnected state |
| Invalid saved settings | Safe normalized defaults |
| Avatar module exception | Main application remains usable |
| WebGL context lost | Static fallback and one guarded reinitialize attempt |

## 21. Testing

### 21.1 Python Tests

Add or update tests for:

- Avatar defaults.
- Save/reload persistence.
- Old state files loading with avatar defaults.
- Invalid values normalizing safely.
- `avatar_settings` persistence.
- Status payload shape.
- RMS and peak bounds.
- Silent PCM behavior.
- Audio frames passing through unchanged.
- Envelope rate limiting.
- Hub latest-value semantics.
- Pipeline order: TTS, avatar tap, local output.
- SSE serialization, stop handling, and disconnect handling.
- `UserStartedSpeakingFrame` producing `turn/user_started`.

### 21.2 JavaScript Tests

Use the built-in Node test runner with pure ES modules and hand-written mocks; do not add npm production dependencies.

```text
tests/js/
  avatar-controller.test.mjs
  avatar-settings.test.mjs
  lip-sync.test.mjs
  model-loader.test.mjs
```

Test:

- State priority and event mapping.
- Persona profile fallback.
- Reduced-motion resolution.
- Speaking start/stop and envelope smoothing.
- Missing model metadata choosing procedural fallback without a fetch.
- Malformed model choosing procedural fallback.
- Disabled avatar not initializing Three.js.
- Disposal closing EventSource and observers.

Node is a development-only test tool; the shipped application remains zero-build and has no Node runtime dependency.

### 21.3 Static and Regression Tests

Following current pytest frontend-contract style, assert:

- Avatar panel and settings controls exist.
- Import map and module entry exist.
- Existing controls remain present.
- The status/action API includes avatar settings.
- Static serving supports the new modules and metadata.

Keep existing tests green, especially session construction, app state, TTS switching, Coqui, wake word, agent lifecycle, process guard, and shutdown.

## 22. Planned Files

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
    assets/avatars/butler/
      metadata.json
    vendor/three/
      LICENSE
      VERSION
      three.module.min.js
      addons/loaders/GLTFLoader.js

tests/
  test_app_state.py
  test_session_processors.py
  test_web_gui.py
  test_avatar_audio.py
  js/
    avatar-controller.test.mjs
    avatar-settings.test.mjs
    lip-sync.test.mjs
    model-loader.test.mjs
```

## 23. Delivery Sequence

1. Add settings model, normalization, status payload, and action.
2. Add audio envelope calculation, hub, processor, and tests.
3. Inject the hub into `VoiceSession` and place the tap after TTS.
4. Add the SSE endpoint and clean shutdown behavior.
5. Vendor pinned Three.js files, import map, version, and license.
6. Add panel, settings controls, and static fallback.
7. Build the procedural butler scene and disposal path.
8. Add state controller, expressions, gaze, idle behavior, and persona profile.
9. Add audio-driven lip-sync and speaking fallback.
10. Add metadata and GLB loader fallback.
11. Complete quality, reduced-motion, accessibility, responsive, and WebGL-failure behavior.
12. Run JavaScript, focused Python, and full applicable regression tests.
13. Update README and architecture documentation with operation and extension notes.

## 24. Acceptance Criteria

The feature is complete when:

- The Control Center contains an optional animated butler companion.
- The avatar reacts to listening, thinking, speaking, agent activity, confirmation, completion, and errors.
- Real TTS amplitude drives jaw and mouth motion through local envelope telemetry.
- Speaking start/stop remains synchronized when envelope telemetry is unavailable.
- Facial expressions, eye movement, blinking, breathing, and restrained idle motion are visible.
- Butler behavior feels calm, formal, attentive, and persona-appropriate.
- Settings are configurable and survive restarts.
- Missing or malformed models use the procedural fallback cleanly.
- GLB/GLTF support is ready for later assets.
- Reduced motion, full disablement, and WebGL fallback work.
- Existing voice, persona, memory, wake-word, TTS, Coqui, and agent workflows remain functional.
- JavaScript, focused Python, and regression tests pass.
- No raw TTS audio is sent to the browser.
- All browser and server-side avatar resources are released during disablement and shutdown.

## 25. Future Enhancements

The architecture leaves room for:

- ARKit-compatible blendshape aliases.
- TTS-provider viseme events.
- Forced-alignment phoneme timing.
- Persona-specific GLB avatars.
- Torso and hand gestures.
- Camera-aware gaze.
- Additional lighting profiles.
- Avatar authoring and calibration tools.

These are outside the initial implementation scope.
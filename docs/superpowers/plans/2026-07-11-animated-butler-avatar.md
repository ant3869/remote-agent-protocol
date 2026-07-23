# Animated Butler Avatar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an optional, local-first animated butler companion that reacts to the existing voice and agent lifecycle, uses real TTS amplitude for mouth motion, supports future GLB avatars, and leaves every existing workflow intact.

**Architecture:** Extend the current Python-owned status and audio pipeline with persisted avatar settings and a latest-value audio-envelope hub. The existing zero-build browser UI loads a small native ES-module avatar runtime, which lazily imports vendored Three.js only when enabled and renders either the procedural butler, a future local GLB, or a static accessible fallback.

**Tech Stack:** Python 3.12, Pipecat frame processors, `ThreadingHTTPServer`, server-sent events, plain HTML/CSS/JavaScript ES modules, Three.js r180 / npm `three@0.180.0`, Node built-in test runner, pytest.

## Global Constraints

- Keep the frontend zero-build: no React, React Three Fiber, Vite, bundler, package-lock, or production npm dependency.
- Vendor Three.js r180 locally; retain its MIT license and record `0.180.0` in `remote_agent_protocol/web_app/vendor/three/VERSION`.
- Do not use a CDN at runtime.
- Do not modify the vendored Pipecat framework under `src/pipecat`.
- Do not send raw PCM audio to the browser; only normalized RMS/peak envelope data may cross the web boundary.
- Avatar failures must not block session construction, microphone input, TTS output, Coqui, wake word, agent delegation, shutdown, or the rest of the web UI.
- Preserve the current graphite UI and semantic status palette; do not restore the obsolete cyan-first motif.
- Persist avatar settings through the existing atomic `AppState` file.
- Keep state labels and non-visual status available when the avatar is disabled or WebGL fails.
- Respect `prefers-reduced-motion` when the saved override is `None`.
- Use TDD: each task starts with a failing focused test, ends with passing focused tests, and receives its own commit.

---

## File Map

### Python files

- `remote_agent_protocol/app_state.py`: avatar defaults, normalization, serialization, and payload conversion.
- `remote_agent_protocol/avatar_audio.py`: PCM envelope calculation, latest-value hub, and SSE serialization.
- `remote_agent_protocol/session_processors.py`: `AvatarAudioTap` and real `turn/user_started` telemetry.
- `remote_agent_protocol/session.py`: inject the envelope callback and place the tap between TTS and local output.
- `remote_agent_protocol/web_gui.py`: own the hub, expose `/api/avatar-audio`, publish avatar settings, persist avatar actions, and close resources.
- `pyproject.toml`: package all nested web application assets.

### Browser files

- `remote_agent_protocol/web_app/index.html`: import map, companion panel, and avatar settings controls.
- `remote_agent_protocol/web_app/styles.css`: responsive panel, fallback, controls, and motion-safe styles.
- `remote_agent_protocol/web_app/app.js`: normalize app events into avatar runtime snapshots and persist settings.
- `remote_agent_protocol/web_app/avatar/math.js`: clamp, damp, ranges, and deterministic timing helpers.
- `remote_agent_protocol/web_app/avatar/avatar-settings.js`: quality presets and settings normalization.
- `remote_agent_protocol/web_app/avatar/persona-profiles.js`: butler and neutral persona profiles.
- `remote_agent_protocol/web_app/avatar/expressions.js`: normalized expression target maps and blending.
- `remote_agent_protocol/web_app/avatar/avatar-controller.js`: state priority, emotion resolution, and transient states.
- `remote_agent_protocol/web_app/avatar/gaze-controller.js`: blink and saccade timing.
- `remote_agent_protocol/web_app/avatar/lip-sync.js`: envelope smoothing, fallback speech motion, and managed SSE reconnect.
- `remote_agent_protocol/web_app/avatar/model-loader.js`: metadata plan, GLB load, control discovery, and disposal.
- `remote_agent_protocol/web_app/avatar/procedural-butler.js`: procedural rig implementing the stable control contract.
- `remote_agent_protocol/web_app/avatar/avatar-scene.js`: Three.js renderer, camera, lighting, animation loop, quality control, visibility pausing, and cleanup.
- `remote_agent_protocol/web_app/avatar/avatar-panel.js`: panel DOM state, collapse behavior, and static fallback.
- `remote_agent_protocol/web_app/avatar/avatar-entry.js`: public runtime boundary and lazy scene import.
- `remote_agent_protocol/web_app/assets/avatars/butler/metadata.json`: procedural-first avatar metadata.
- `remote_agent_protocol/web_app/vendor/three/`: pinned Three.js core, loader, required utility, license, and version.

### Tests

- `tests/test_app_state.py`: settings defaults, old-file compatibility, validation, and persistence.
- `tests/test_avatar_audio.py`: envelope math, hub semantics, rate-safe payloads, and SSE bytes.
- `tests/test_session_processors.py`: user-start event and pass-through audio tap behavior.
- `tests/test_web_gui.py`: status/action contract, hub ownership, route, shutdown, static markup, and package declarations.
- `tests/js/avatar-settings.test.mjs`: browser settings and reduced-motion resolution.
- `tests/js/avatar-controller.test.mjs`: state priority, emotion cues, and persona fallback.
- `tests/js/lip-sync.test.mjs`: attack/release smoothing, fallback mouth movement, and disposal.
- `tests/js/model-loader.test.mjs`: procedural selection, no-fetch behavior, failure fallback, and disposal helpers.

---

### Task 1: Persist Avatar Settings and Expose the API Contract

**Files:**
- Modify: `remote_agent_protocol/app_state.py`
- Modify: `remote_agent_protocol/web_gui.py`
- Modify: `tests/test_app_state.py`
- Modify: `tests/test_web_gui.py`

**Interfaces:**
- Produces: `app_state.normalize_avatar_settings(raw, base=None) -> AppState`
- Produces: `app_state.avatar_settings_payload(state) -> dict[str, object]`
- Produces: `/api/action` action `avatar_settings` accepting `{settings: {...}}`
- Produces: `status["avatar"]` using camelCase browser keys

- [ ] **Step 1: Write failing state tests**

Add these tests to `tests/test_app_state.py`:

```python
    def test_avatar_defaults_are_present_for_old_state_files(self):
        self.path.write_text('{"persona": "Jess"}', encoding="utf-8")

        state = app_state.load_state(self.path)

        self.assertTrue(state.avatar_enabled)
        self.assertEqual(state.avatar_id, "butler")
        self.assertEqual(state.avatar_quality, "high")
        self.assertTrue(state.avatar_lip_sync)
        self.assertTrue(state.avatar_gaze)
        self.assertTrue(state.avatar_idle_motion)
        self.assertEqual(state.avatar_expression_intensity, 0.62)
        self.assertIsNone(state.avatar_reduced_motion)
        self.assertTrue(state.avatar_show_state)
        self.assertFalse(state.avatar_panel_collapsed)

    def test_avatar_settings_roundtrip(self):
        state = app_state.normalize_avatar_settings(
            {
                "enabled": False,
                "avatarId": "butler",
                "quality": "low",
                "lipSync": False,
                "gaze": False,
                "idleMotion": False,
                "expressionIntensity": 0.35,
                "reducedMotion": True,
                "showState": False,
                "panelCollapsed": True,
            }
        )
        app_state.save_state(self.path, state)

        loaded = app_state.load_state(self.path)

        self.assertEqual(app_state.avatar_settings_payload(loaded), {
            "enabled": False,
            "avatarId": "butler",
            "quality": "low",
            "lipSync": False,
            "gaze": False,
            "idleMotion": False,
            "expressionIntensity": 0.35,
            "reducedMotion": True,
            "showState": False,
            "panelCollapsed": True,
        })

    def test_invalid_avatar_values_normalize_to_safe_defaults(self):
        state = app_state.normalize_avatar_settings(
            {
                "enabled": "yes",
                "avatarId": "../outside",
                "quality": "ultra",
                "lipSync": 1,
                "expressionIntensity": 8,
                "reducedMotion": "sometimes",
            }
        )

        self.assertEqual(app_state.avatar_settings_payload(state), {
            "enabled": True,
            "avatarId": "butler",
            "quality": "high",
            "lipSync": True,
            "gaze": True,
            "idleMotion": True,
            "expressionIntensity": 1.0,
            "reducedMotion": None,
            "showState": True,
            "panelCollapsed": False,
        })
```

- [ ] **Step 2: Run the state tests and verify failure**

Run:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_app_state.py -v
```

Expected: failures because the avatar fields and helper functions do not exist.

- [ ] **Step 3: Implement state fields and normalization**

In `remote_agent_protocol/app_state.py`, import `replace` and `Mapping`, extend `AppState`, and add these helpers below the dataclass:

```python
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace

AVATAR_QUALITIES = frozenset({"low", "medium", "high"})
_AVATAR_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

@dataclass
class AppState:
    persona: str | None = None
    tool_user: str | None = None
    voice_mode: str = multimodal_prompt.DEFAULT_VOICE_MODE
    model: str | None = None
    voice: str | None = None
    tts_provider: str | None = None
    coqui_model: str | None = None
    coqui_speaker: str | None = None
    coqui_language: str | None = None
    coqui_device: str | None = None
    agent_prompts: dict[str, str] = field(default_factory=dict)
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


def _pick(raw: Mapping[str, object], snake: str, camel: str, default):
    if snake in raw:
        return raw[snake]
    if camel in raw:
        return raw[camel]
    return default


def _bool_or(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _tri_bool_or(value: object, default: bool | None) -> bool | None:
    return value if value is None or isinstance(value, bool) else default


def _avatar_id_or(value: object, default: str) -> str:
    return value if isinstance(value, str) and _AVATAR_ID_RE.fullmatch(value) else default


def _quality_or(value: object, default: str) -> str:
    return value if isinstance(value, str) and value in AVATAR_QUALITIES else default


def _intensity_or(value: object, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return max(0.0, min(1.0, float(value)))


def normalize_avatar_settings(
    raw: Mapping[str, object] | object,
    base: AppState | None = None,
) -> AppState:
    current = base or AppState()
    values = raw if isinstance(raw, Mapping) else {}
    return replace(
        current,
        avatar_enabled=_bool_or(
            _pick(values, "avatar_enabled", "enabled", current.avatar_enabled),
            current.avatar_enabled,
        ),
        avatar_id=_avatar_id_or(
            _pick(values, "avatar_id", "avatarId", current.avatar_id),
            current.avatar_id,
        ),
        avatar_quality=_quality_or(
            _pick(values, "avatar_quality", "quality", current.avatar_quality),
            current.avatar_quality,
        ),
        avatar_lip_sync=_bool_or(
            _pick(values, "avatar_lip_sync", "lipSync", current.avatar_lip_sync),
            current.avatar_lip_sync,
        ),
        avatar_gaze=_bool_or(
            _pick(values, "avatar_gaze", "gaze", current.avatar_gaze),
            current.avatar_gaze,
        ),
        avatar_idle_motion=_bool_or(
            _pick(values, "avatar_idle_motion", "idleMotion", current.avatar_idle_motion),
            current.avatar_idle_motion,
        ),
        avatar_expression_intensity=_intensity_or(
            _pick(
                values,
                "avatar_expression_intensity",
                "expressionIntensity",
                current.avatar_expression_intensity,
            ),
            current.avatar_expression_intensity,
        ),
        avatar_reduced_motion=_tri_bool_or(
            _pick(
                values,
                "avatar_reduced_motion",
                "reducedMotion",
                current.avatar_reduced_motion,
            ),
            current.avatar_reduced_motion,
        ),
        avatar_show_state=_bool_or(
            _pick(values, "avatar_show_state", "showState", current.avatar_show_state),
            current.avatar_show_state,
        ),
        avatar_panel_collapsed=_bool_or(
            _pick(
                values,
                "avatar_panel_collapsed",
                "panelCollapsed",
                current.avatar_panel_collapsed,
            ),
            current.avatar_panel_collapsed,
        ),
    )


def avatar_settings_payload(state: AppState) -> dict[str, object]:
    return {
        "enabled": state.avatar_enabled,
        "avatarId": state.avatar_id,
        "quality": state.avatar_quality,
        "lipSync": state.avatar_lip_sync,
        "gaze": state.avatar_gaze,
        "idleMotion": state.avatar_idle_motion,
        "expressionIntensity": state.avatar_expression_intensity,
        "reducedMotion": state.avatar_reduced_motion,
        "showState": state.avatar_show_state,
        "panelCollapsed": state.avatar_panel_collapsed,
    }
```

Also add `import re`, construct the normal non-avatar `AppState` in `load_state`, then return `normalize_avatar_settings(raw, state)`.

- [ ] **Step 4: Update `WebVoiceApp` persistence and status**

In `_save_state()`, copy every avatar field from `self._app_state` into the new `AppState`. In `_status_payload()`, add:

```python
"avatar": app_state.avatar_settings_payload(self._app_state),
```

In `_action()`, add:

```python
elif name == "avatar_settings":
    self._app_state = app_state.normalize_avatar_settings(
        payload.get("settings"), self._app_state
    )
    self._save_state()
```

- [ ] **Step 5: Add failing and passing web API tests**

Add to `tests/test_web_gui.py`:

```python
def test_status_payload_exposes_avatar_settings():
    app = WebVoiceApp()

    avatar = app._status_payload()["avatar"]

    assert avatar["enabled"] is True
    assert avatar["avatarId"] == "butler"
    assert avatar["quality"] == "high"
    assert avatar["reducedMotion"] is None


def test_avatar_settings_action_normalizes_and_persists(monkeypatch):
    app = WebVoiceApp()
    saved = []
    monkeypatch.setattr(web_gui.app_state, "save_state", lambda path, state: saved.append(state))

    result = app._action(
        "avatar_settings",
        {"settings": {"quality": "low", "expressionIntensity": 0.4, "enabled": False}},
    )

    assert result["ok"] is True
    assert result["status"]["avatar"]["quality"] == "low"
    assert result["status"]["avatar"]["expressionIntensity"] == 0.4
    assert result["status"]["avatar"]["enabled"] is False
    assert saved[-1].avatar_quality == "low"
```

- [ ] **Step 6: Run focused tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_app_state.py tests\test_web_gui.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add remote_agent_protocol\app_state.py remote_agent_protocol\web_gui.py tests\test_app_state.py tests\test_web_gui.py; git commit -m "feat(avatar): persist companion settings"
```

---

### Task 2: Add PCM Envelope Calculation and Latest-Value Hub

**Files:**
- Create: `remote_agent_protocol/avatar_audio.py`
- Create: `tests/test_avatar_audio.py`

**Interfaces:**
- Produces: `AvatarAudioEnvelope`
- Produces: `compute_pcm16_envelope(audio, sample_rate, channels, timestamp=None, silence_threshold=0.012)`
- Produces: `AvatarAudioEnvelopeHub.publish()`, `.wait_after()`, `.close()`
- Produces: `sse_data(seq, envelope) -> bytes`

- [ ] **Step 1: Write failing envelope and hub tests**

Create `tests/test_avatar_audio.py`:

```python
import math
import struct

from remote_agent_protocol.avatar_audio import (
    AvatarAudioEnvelope,
    AvatarAudioEnvelopeHub,
    compute_pcm16_envelope,
    sse_data,
)


def pcm16(*samples: int) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def test_silence_produces_closed_mouth_envelope():
    envelope = compute_pcm16_envelope(b"\x00\x00" * 80, 16000, 1, timestamp=5.0)

    assert envelope.rms == 0.0
    assert envelope.peak == 0.0
    assert envelope.voiced is False
    assert envelope.timestamp == 5.0


def test_pcm_envelope_is_normalized_and_bounded():
    envelope = compute_pcm16_envelope(
        pcm16(32767, -32768, 16384, -16384), 24000, 1, timestamp=7.0
    )

    expected_rms = math.sqrt((32767**2 + 32768**2 + 16384**2 + 16384**2) / 4) / 32768
    assert envelope.rms == pytest.approx(expected_rms)
    assert envelope.peak == 1.0
    assert envelope.voiced is True


def test_hub_keeps_only_latest_value_and_wakes_waiters():
    hub = AvatarAudioEnvelopeHub()
    first = AvatarAudioEnvelope(0.1, 0.2, True, 16000, 1, 1.0)
    second = AvatarAudioEnvelope(0.3, 0.4, True, 16000, 1, 2.0)

    hub.publish(first)
    hub.publish(second)
    seq, latest, closed = hub.wait_after(0, timeout=0)

    assert seq == 2
    assert latest is second
    assert closed is False


def test_closed_hub_returns_immediately():
    hub = AvatarAudioEnvelopeHub()
    hub.close()

    assert hub.wait_after(0, timeout=1) == (0, None, True)


def test_sse_serialization_contains_only_envelope_data():
    payload = sse_data(4, AvatarAudioEnvelope(0.2, 0.5, True, 16000, 1, 9.5))

    assert payload.startswith(b"id: 4\nevent: envelope\ndata: ")
    assert b'"rms":0.2' in payload
    assert b'"peak":0.5' in payload
    assert b"audio" not in payload
    assert payload.endswith(b"\n\n")
```

Add `import pytest` at the top.

- [ ] **Step 2: Run the test and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_avatar_audio.py -v
```

Expected: import failure because `avatar_audio.py` does not exist.

- [ ] **Step 3: Implement the audio envelope module**

Create `remote_agent_protocol/avatar_audio.py`:

```python
"""Bounded avatar mouth telemetry derived from local TTS PCM."""

from __future__ import annotations

import json
import math
import struct
import threading
import time
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class AvatarAudioEnvelope:
    """A normalized latest-value TTS audio envelope."""

    rms: float
    peak: float
    voiced: bool
    sample_rate: int
    channels: int
    timestamp: float


def compute_pcm16_envelope(
    audio: bytes,
    sample_rate: int,
    channels: int,
    *,
    timestamp: float | None = None,
    silence_threshold: float = 0.012,
) -> AvatarAudioEnvelope:
    """Calculate normalized RMS and peak from little-endian signed 16-bit PCM."""
    usable = len(audio) - (len(audio) % 2)
    if usable <= 0:
        return AvatarAudioEnvelope(0.0, 0.0, False, sample_rate, channels, timestamp or time.time())
    count = usable // 2
    samples = struct.unpack(f"<{count}h", audio[:usable])
    peak_raw = max(abs(sample) for sample in samples)
    rms_raw = math.sqrt(sum(sample * sample for sample in samples) / count)
    rms = max(0.0, min(1.0, rms_raw / 32768.0))
    peak = max(0.0, min(1.0, peak_raw / 32768.0))
    return AvatarAudioEnvelope(
        rms=rms,
        peak=peak,
        voiced=rms >= silence_threshold,
        sample_rate=max(0, int(sample_rate)),
        channels=max(1, int(channels)),
        timestamp=time.time() if timestamp is None else float(timestamp),
    )


class AvatarAudioEnvelopeHub:
    """Store one latest envelope and wake bounded SSE consumers."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._seq = 0
        self._latest: AvatarAudioEnvelope | None = None
        self._closed = False

    def publish(self, envelope: AvatarAudioEnvelope) -> None:
        with self._condition:
            if self._closed:
                return
            self._seq += 1
            self._latest = envelope
            self._condition.notify_all()

    def wait_after(
        self, sequence: int, *, timeout: float
    ) -> tuple[int, AvatarAudioEnvelope | None, bool]:
        with self._condition:
            if not self._closed and self._seq <= sequence:
                self._condition.wait_for(
                    lambda: self._closed or self._seq > sequence,
                    timeout=max(0.0, timeout),
                )
            return self._seq, self._latest if self._seq > sequence else None, self._closed

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


def sse_data(sequence: int, envelope: AvatarAudioEnvelope) -> bytes:
    data = json.dumps(
        {"seq": sequence, **asdict(envelope)},
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"id: {sequence}\nevent: envelope\ndata: {data}\n\n".encode("utf-8")
```

- [ ] **Step 4: Run tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_avatar_audio.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add remote_agent_protocol\avatar_audio.py tests\test_avatar_audio.py; git commit -m "feat(avatar): add bounded TTS envelope telemetry"
```

---

### Task 3: Add the Audio Tap and Real User-Start Telemetry

**Files:**
- Modify: `remote_agent_protocol/session_processors.py`
- Modify: `tests/test_session_processors.py`

**Interfaces:**
- Consumes: `compute_pcm16_envelope()` and `AvatarAudioEnvelope`
- Produces: `AvatarAudioTap(on_envelope, publish_interval_secs=0.05, clock=time.monotonic, wall_clock=time.time)`
- Produces: telemetry event `{type: "turn", event: "user_started"}`

- [ ] **Step 1: Write failing processor tests**

Add `TTSAudioRawFrame` and `UserStartedSpeakingFrame` imports in `tests/test_session_processors.py`, then add:

```python
class AvatarAudioTapTests(unittest.IsolatedAsyncioTestCase):
    async def test_tts_audio_passes_through_unchanged_and_emits_envelope(self):
        events = []
        frame = TTSAudioRawFrame(
            audio=b"\xff\x7f" * 80,
            sample_rate=24000,
            num_channels=1,
        )
        tap = AvatarAudioTap(
            events.append,
            publish_interval_secs=0.05,
            clock=lambda: 1.0,
            wall_clock=lambda: 10.0,
        )

        frames, _ = await run_test(tap, frames_to_send=[frame])

        self.assertIs(frames[0], frame)
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].voiced)
        self.assertEqual(events[0].timestamp, 10.0)

    async def test_rate_limit_drops_intermediate_envelopes(self):
        events = []
        ticks = iter([0.0, 0.01, 0.06])
        tap = AvatarAudioTap(events.append, clock=lambda: next(ticks), wall_clock=lambda: 20.0)
        frames = [
            TTSAudioRawFrame(audio=b"\x00\x20" * 40, sample_rate=24000, num_channels=1)
            for _ in range(3)
        ]

        await run_test(tap, frames_to_send=frames)

        self.assertEqual(len(events), 2)


class TranscriptTapUserStartTests(unittest.IsolatedAsyncioTestCase):
    async def test_telemetry_tap_reports_user_started(self):
        events = []

        await run_test(
            TranscriptTap(events.append, role="telemetry"),
            frames_to_send=[UserStartedSpeakingFrame(), UserStoppedSpeakingFrame()],
        )

        self.assertEqual(events, [
            {"type": "turn", "event": "user_started"},
            {"type": "turn", "event": "user_stopped"},
        ])
```

Import `AvatarAudioTap` from `remote_agent_protocol.session_processors`.

- [ ] **Step 2: Run focused tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_session_processors.py -v
```

Expected: import/name failures for `AvatarAudioTap` and missing `user_started` event.

- [ ] **Step 3: Implement `AvatarAudioTap`**

In `session_processors.py`, import `time`, `TTSAudioRawFrame`, `UserStartedSpeakingFrame`, and `compute_pcm16_envelope`. Add:

```python
class AvatarAudioTap(FrameProcessor):
    """Observe TTS PCM without mutating or delaying output frames."""

    def __init__(
        self,
        on_envelope,
        *,
        publish_interval_secs: float = 0.05,
        clock=time.monotonic,
        wall_clock=time.time,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._on_envelope = on_envelope
        self._publish_interval_secs = max(0.01, float(publish_interval_secs))
        self._clock = clock
        self._wall_clock = wall_clock
        self._last_publish = float("-inf")

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSAudioRawFrame) and self._on_envelope is not None:
            now = self._clock()
            if now - self._last_publish >= self._publish_interval_secs:
                envelope = compute_pcm16_envelope(
                    frame.audio,
                    frame.sample_rate,
                    frame.num_channels,
                    timestamp=self._wall_clock(),
                )
                try:
                    self._on_envelope(envelope)
                except Exception as exc:
                    logger.warning(f"Avatar audio envelope callback failed: {exc}")
                self._last_publish = now
        await self.push_frame(frame, direction)
```

- [ ] **Step 4: Emit user-start telemetry**

Inside the telemetry branch of `TranscriptTap.process_frame()`, insert before `UserStoppedSpeakingFrame` handling:

```python
elif isinstance(frame, UserStartedSpeakingFrame):
    self._emit({"type": "turn", "event": "user_started"})
```

- [ ] **Step 5: Run tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_session_processors.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add remote_agent_protocol\session_processors.py tests\test_session_processors.py; git commit -m "feat(avatar): observe TTS audio and user speech"
```

---

### Task 4: Wire the Hub into `VoiceSession` and Serve SSE

**Files:**
- Modify: `remote_agent_protocol/session.py`
- Modify: `remote_agent_protocol/web_gui.py`
- Modify: `tests/test_web_gui.py`
- Modify: `tests/test_avatar_audio.py`

**Interfaces:**
- Consumes: `AvatarAudioTap`, `AvatarAudioEnvelopeHub`, `sse_data`
- Changes: `VoiceSession.__init__(persona, on_event=None, on_avatar_audio=None)`
- Produces: `GET /api/avatar-audio`
- Produces: clean hub closure from `WebVoiceApp._stop_app()`

- [ ] **Step 1: Write failing ownership and route tests**

Add to `tests/test_web_gui.py`:

```python
def test_new_session_receives_avatar_audio_callback(monkeypatch):
    captured = {}

    class FakeSession:
        def __init__(self, persona, on_event=None, on_avatar_audio=None):
            captured["callback"] = on_avatar_audio
        def set_manual_prompt_mode(self, value): pass
        def set_voice_mode(self, value): pass
        def set_muted(self, value): pass
        def set_startup_defaults(self, **kwargs): pass
        def set_voicebox_warmup_personas(self, personas): pass
        def set_default_agent_backend(self, backend): pass

    monkeypatch.setattr(web_gui, "VoiceSession", FakeSession)

    app = WebVoiceApp()

    assert captured["callback"] == app._avatar_audio.publish


def test_stop_app_closes_avatar_hub(monkeypatch):
    app = WebVoiceApp()
    closed = []
    monkeypatch.setattr(app._avatar_audio, "close", lambda: closed.append(True))
    monkeypatch.setattr(app, "_stop_session", lambda **kwargs: None)
    monkeypatch.setattr(app, "_join_thread", lambda *args, **kwargs: None)

    app._stop_app()

    assert closed == [True]


def test_avatar_audio_route_calls_streamer(monkeypatch):
    app = WebVoiceApp()
    calls = []
    monkeypatch.setattr(app, "_stream_avatar_audio", lambda handler: calls.append(handler))
    handler_class = app._handler_class()

    assert "/api/avatar-audio" in inspect.getsource(handler_class.do_GET)
```

Add `import inspect`.

- [ ] **Step 2: Run focused tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_web_gui.py tests\test_avatar_audio.py -v
```

Expected: failures because the hub, route, and callback do not exist.

- [ ] **Step 3: Inject the audio callback into the session pipeline**

In `session.py`, import `AvatarAudioTap`, add `on_avatar_audio=None` to `VoiceSession.__init__`, store `self._on_avatar_audio`, and change the output processor sequence to:

```python
processors += [
    TranscriptTap(self._on_event, role="assistant"),
    self._tts,
    AvatarAudioTap(self._on_avatar_audio),
    transport.output(),
    TranscriptTap(self._on_event, role="telemetry"),
    assistant_aggregator,
]
```

- [ ] **Step 4: Own the hub and expose the SSE route**

In `web_gui.py`, import `AvatarAudioEnvelopeHub` and `sse_data`. Before `_new_session()` in `WebVoiceApp.__init__`, create:

```python
self._avatar_audio = AvatarAudioEnvelopeHub()
```

Pass it in `_new_session()`:

```python
session = VoiceSession(
    self._persona,
    on_event=self._events_in.put,
    on_avatar_audio=self._avatar_audio.publish,
)
```

Add this method:

```python
def _stream_avatar_audio(self, handler: BaseHTTPRequestHandler) -> None:
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.end_headers()
    sequence = 0
    try:
        while not self._stop.is_set():
            sequence, envelope, closed = self._avatar_audio.wait_after(sequence, timeout=10.0)
            if closed or self._stop.is_set():
                break
            payload = b": keepalive\n\n" if envelope is None else sse_data(sequence, envelope)
            handler.wfile.write(payload)
            handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, OSError):
        return
```

Route it before static serving:

```python
if parsed.path == "/api/avatar-audio":
    app._stream_avatar_audio(self)
    return
```

In `_stop_app()`, call `self._avatar_audio.close()` immediately after `self._stop.set()`.

- [ ] **Step 5: Add a pipeline-order contract test**

Add to `tests/test_web_gui.py`:

```python
def test_avatar_audio_tap_sits_between_tts_and_local_output():
    source = Path("remote_agent_protocol/session.py").read_text(encoding="utf-8")
    output_block = source.split('TranscriptTap(self._on_event, role="assistant")', 1)[1]

    assert output_block.index("self._tts") < output_block.index("AvatarAudioTap(")
    assert output_block.index("AvatarAudioTap(") < output_block.index("transport.output()")
```

- [ ] **Step 6: Run focused tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_avatar_audio.py tests\test_session_processors.py tests\test_web_gui.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add remote_agent_protocol\session.py remote_agent_protocol\web_gui.py tests\test_web_gui.py tests\test_avatar_audio.py; git commit -m "feat(avatar): stream local TTS envelope events"
```

---

### Task 5: Vendor Three.js, Avatar Metadata, and Package Assets

**Files:**
- Create: `remote_agent_protocol/web_app/vendor/three/LICENSE`
- Create: `remote_agent_protocol/web_app/vendor/three/VERSION`
- Create: `remote_agent_protocol/web_app/vendor/three/three.module.min.js`
- Create: `remote_agent_protocol/web_app/vendor/three/addons/loaders/GLTFLoader.js`
- Create: `remote_agent_protocol/web_app/vendor/three/addons/utils/BufferGeometryUtils.js`
- Create: `remote_agent_protocol/web_app/assets/avatars/butler/metadata.json`
- Modify: `pyproject.toml`
- Modify: `tests/test_web_gui.py`

**Interfaces:**
- Produces: local import specifiers `three` and `three/addons/`
- Produces: butler metadata with `model: null`
- Produces: wheel/package inclusion for every nested web asset

- [ ] **Step 1: Write failing static asset tests**

Add to `tests/test_web_gui.py`:

```python
def test_avatar_vendor_and_metadata_files_are_declared():
    metadata = json.loads(
        (WEB_APP / "assets/avatars/butler/metadata.json").read_text(encoding="utf-8")
    )
    version = (WEB_APP / "vendor/three/VERSION").read_text(encoding="utf-8").strip()
    project = Path("pyproject.toml").read_text(encoding="utf-8")

    assert metadata["id"] == "butler"
    assert metadata["model"] is None
    assert metadata["fallback"] == "procedural-butler"
    assert version == "0.180.0"
    assert '"web_app/**/*"' in project
    assert (WEB_APP / "vendor/three/LICENSE").is_file()
    assert (WEB_APP / "vendor/three/three.module.min.js").is_file()
    assert (WEB_APP / "vendor/three/addons/loaders/GLTFLoader.js").is_file()
    assert (WEB_APP / "vendor/three/addons/utils/BufferGeometryUtils.js").is_file()
```

- [ ] **Step 2: Run the test and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_web_gui.py::test_avatar_vendor_and_metadata_files_are_declared -v
```

Expected: FAIL because files do not exist.

- [ ] **Step 3: Vendor the exact npm release**

From the repository root, run this PowerShell one-liner to download and extract the official npm tarball:

```powershell
.\.venv\Scripts\python.exe -c "import io,tarfile,urllib.request,pathlib,shutil; root=pathlib.Path('remote_agent_protocol/web_app/vendor/three'); data=urllib.request.urlopen('https://registry.npmjs.org/three/-/three-0.180.0.tgz', timeout=60).read(); tf=tarfile.open(fileobj=io.BytesIO(data), mode='r:gz'); root.mkdir(parents=True, exist_ok=True); files={'package/LICENSE':'LICENSE','package/build/three.module.min.js':'three.module.min.js','package/examples/jsm/loaders/GLTFLoader.js':'addons/loaders/GLTFLoader.js','package/examples/jsm/utils/BufferGeometryUtils.js':'addons/utils/BufferGeometryUtils.js'}; [(root/dst).parent.mkdir(parents=True, exist_ok=True) or (root/dst).write_bytes(tf.extractfile(src).read()) for src,dst in files.items()]; (root/'VERSION').write_text('0.180.0\n', encoding='utf-8')"
```

Confirm `GLTFLoader.js` imports `../utils/BufferGeometryUtils.js`; retain the utility unmodified.

- [ ] **Step 4: Create butler metadata**

Create `remote_agent_protocol/web_app/assets/avatars/butler/metadata.json`:

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

- [ ] **Step 5: Package nested web assets**

Replace the three individual `remote_agent_protocol` package-data entries in `pyproject.toml` with:

```toml
"remote_agent_protocol" = [
    "web_app/**/*",
]
```

- [ ] **Step 6: Run tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_web_gui.py::test_avatar_vendor_and_metadata_files_are_declared -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add pyproject.toml remote_agent_protocol\web_app\vendor remote_agent_protocol\web_app\assets tests\test_web_gui.py; git commit -m "build(avatar): vendor Three.js and butler metadata"
```

---

### Task 6: Build Pure Browser Settings, Persona, Expression, and State Logic

**Files:**
- Create: `remote_agent_protocol/web_app/avatar/math.js`
- Create: `remote_agent_protocol/web_app/avatar/avatar-settings.js`
- Create: `remote_agent_protocol/web_app/avatar/persona-profiles.js`
- Create: `remote_agent_protocol/web_app/avatar/expressions.js`
- Create: `remote_agent_protocol/web_app/avatar/avatar-controller.js`
- Create: `tests/js/avatar-settings.test.mjs`
- Create: `tests/js/avatar-controller.test.mjs`

**Interfaces:**
- Produces: `normalizeAvatarSettings(raw, systemReducedMotion)`
- Produces: `profileForPersona(name, selectedAvatarId)`
- Produces: `expressionFor(name)` and `blendTargets(base, overlay, amount)`
- Produces: `resolveAvatarState(runtime, now)` and `resolveAvatarEmotion(runtime, profile, now)`

- [ ] **Step 1: Write failing Node tests**

Create `tests/js/avatar-settings.test.mjs`:

```javascript
import test from "node:test";
import assert from "node:assert/strict";
import { normalizeAvatarSettings } from "../../remote_agent_protocol/web_app/avatar/avatar-settings.js";

test("system motion preference is used when override is null", () => {
  const settings = normalizeAvatarSettings({ reducedMotion: null }, true);
  assert.equal(settings.effectiveReducedMotion, true);
});

test("saved normal motion overrides the system preference", () => {
  const settings = normalizeAvatarSettings({ reducedMotion: false }, true);
  assert.equal(settings.effectiveReducedMotion, false);
});

test("quality and intensity normalize safely", () => {
  const settings = normalizeAvatarSettings({ quality: "ultra", expressionIntensity: 4 }, false);
  assert.equal(settings.quality, "high");
  assert.equal(settings.expressionIntensity, 1);
  assert.equal(settings.maxPixelRatio, 2);
});
```

Create `tests/js/avatar-controller.test.mjs`:

```javascript
import test from "node:test";
import assert from "node:assert/strict";
import { resolveAvatarEmotion, resolveAvatarState } from "../../remote_agent_protocol/web_app/avatar/avatar-controller.js";
import { profileForPersona } from "../../remote_agent_protocol/web_app/avatar/persona-profiles.js";

test("error outranks speaking and listening", () => {
  assert.equal(resolveAvatarState({ error: true, speaking: true, userSpeaking: true }, 100), "error");
});

test("speaking outranks active agent work", () => {
  assert.equal(resolveAvatarState({ speaking: true, activeAgentCount: 2 }, 100), "speaking");
});

test("pending confirmation resolves concerned", () => {
  assert.equal(resolveAvatarState({ pendingConfirmation: true }, 100), "concerned");
});

test("recent completion resolves happy until expiry", () => {
  assert.equal(resolveAvatarState({ completedAt: 90_000 }, 92_000), "happy");
  assert.equal(resolveAvatarState({ completedAt: 90_000 }, 96_000), "idle");
});

test("Jess maps to the restrained butler profile", () => {
  const profile = profileForPersona("JESS", "butler");
  assert.equal(profile.avatarId, "butler");
  assert.equal(profile.speakingStyle, "formal");
  assert.equal(profile.eyeContact, 0.82);
});

test("apology language creates a low-intensity apologetic emotion", () => {
  const profile = profileForPersona("Custom", "butler");
  const emotion = resolveAvatarEmotion({ latestAssistantText: "I’m sorry, that failed." }, profile, 100);
  assert.equal(emotion.name, "apologetic");
  assert.equal(emotion.intensity, 0.35);
});
```

- [ ] **Step 2: Run Node tests and verify failure**

```powershell
node --test tests\js\avatar-settings.test.mjs tests\js\avatar-controller.test.mjs
```

Expected: module-not-found failures.

- [ ] **Step 3: Implement math and settings modules**

Create `math.js`:

```javascript
export const clamp = (value, min = 0, max = 1) => Math.min(max, Math.max(min, value));
export const damp = (current, target, lambda, delta) => current + (target - current) * (1 - Math.exp(-lambda * delta));
export const range = (pair, random = Math.random) => pair[0] + (pair[1] - pair[0]) * random();
export const normalizeName = (value) => String(value || "").trim().toLowerCase().replace(/\s+/g, " ");
```

Create `avatar-settings.js`:

```javascript
import { clamp } from "./math.js";

export const QUALITY = Object.freeze({
  low: { maxPixelRatio: 1, targetFps: 24, antialias: false, shadows: false },
  medium: { maxPixelRatio: 1.5, targetFps: 30, antialias: true, shadows: false },
  high: { maxPixelRatio: 2, targetFps: 60, antialias: true, shadows: true },
});

export function normalizeAvatarSettings(raw = {}, systemReducedMotion = false) {
  const quality = Object.hasOwn(QUALITY, raw.quality) ? raw.quality : "high";
  const reducedMotion = raw.reducedMotion === null || typeof raw.reducedMotion === "boolean"
    ? raw.reducedMotion
    : null;
  const intensity = Number.isFinite(raw.expressionIntensity)
    ? clamp(Number(raw.expressionIntensity))
    : 0.62;
  return {
    enabled: typeof raw.enabled === "boolean" ? raw.enabled : true,
    avatarId: /^[a-z0-9][a-z0-9_-]{0,63}$/.test(raw.avatarId || "") ? raw.avatarId : "butler",
    quality,
    lipSync: typeof raw.lipSync === "boolean" ? raw.lipSync : true,
    gaze: typeof raw.gaze === "boolean" ? raw.gaze : true,
    idleMotion: typeof raw.idleMotion === "boolean" ? raw.idleMotion : true,
    expressionIntensity: intensity,
    reducedMotion,
    effectiveReducedMotion: reducedMotion === null ? Boolean(systemReducedMotion) : reducedMotion,
    showState: typeof raw.showState === "boolean" ? raw.showState : true,
    panelCollapsed: typeof raw.panelCollapsed === "boolean" ? raw.panelCollapsed : false,
    ...QUALITY[quality],
  };
}
```

- [ ] **Step 4: Implement persona and expressions**

Create `persona-profiles.js`:

```javascript
import { normalizeName } from "./math.js";

const BUTLER = Object.freeze({
  personaId: "jess",
  avatarId: "butler",
  defaultExpression: "attentive",
  speakingStyle: "formal",
  idleIntensity: 0.28,
  eyeContact: 0.82,
  expressiveness: 0.62,
  emotionBias: { warm: 0.15, pleased: 0.1, surprised: -0.25, error: -0.1 },
  blinkIntervalSeconds: [3.5, 7.5],
  doubleBlinkChance: 0.12,
  saccadeIntervalSeconds: [1.8, 4.5],
  saccadeIntensity: 0.18,
  speakingHeadMotion: 0.16,
});

const NEUTRAL = Object.freeze({
  ...BUTLER,
  personaId: "neutral",
  defaultExpression: "neutral",
  idleIntensity: 0.2,
  eyeContact: 0.72,
  expressiveness: 0.5,
  emotionBias: {},
});

export function profileForPersona(name, selectedAvatarId = "butler") {
  const source = normalizeName(name) === "jess" ? BUTLER : NEUTRAL;
  return { ...source, avatarId: selectedAvatarId || source.avatarId };
}
```

Create `expressions.js` with every required expression:

```javascript
import { clamp } from "./math.js";

const neutral = Object.freeze({ browInner: 0, browOuter: 0, browAsymmetry: 0, eyelid: 0, eyeWiden: 0, jawOpen: 0, mouthWidth: 0, mouthCorner: 0, cheekRaise: 0, headPitch: 0, headYaw: 0, headRoll: 0 });

export const EXPRESSIONS = Object.freeze({
  neutral,
  attentive: { ...neutral, browInner: 0.12, eyeWiden: 0.12, headPitch: -0.03 },
  warm: { ...neutral, eyelid: 0.08, mouthCorner: 0.22, cheekRaise: 0.12 },
  pleased: { ...neutral, mouthCorner: 0.35, cheekRaise: 0.2, headPitch: 0.03 },
  concerned: { ...neutral, browInner: 0.4, browOuter: -0.12, mouthCorner: -0.22, headPitch: 0.04 },
  confused: { ...neutral, browAsymmetry: 0.38, headRoll: 0.08, headYaw: 0.04 },
  apologetic: { ...neutral, browInner: 0.28, eyelid: 0.12, mouthCorner: -0.08, headPitch: 0.08 },
  thinking: { ...neutral, browAsymmetry: 0.16, eyelid: 0.06, headYaw: -0.05, headPitch: 0.04 },
  focused: { ...neutral, browOuter: -0.2, eyelid: 0.14, headPitch: -0.02 },
  surprised: { ...neutral, browInner: 0.45, browOuter: 0.4, eyeWiden: 0.55, jawOpen: 0.22 },
  error: { ...neutral, browInner: 0.32, browOuter: -0.28, eyelid: 0.18, mouthCorner: -0.28 },
});

export const expressionFor = (name) => EXPRESSIONS[name] || EXPRESSIONS.neutral;
export const blendTargets = (base, overlay, amount) => Object.fromEntries(
  Object.keys(neutral).map((key) => [key, base[key] + (overlay[key] - base[key]) * clamp(amount)])
);
```

- [ ] **Step 5: Implement state and emotion resolution**

Create `avatar-controller.js`:

```javascript
const RECENT_COMPLETION_MS = 4000;

export function resolveAvatarState(runtime = {}, now = Date.now()) {
  if (runtime.error || ["failed", "stopped"].includes(runtime.session)) return "error";
  if (runtime.speaking) return "speaking";
  if (runtime.userSpeaking || ["wake_word_detected", "listening_for_command"].includes(runtime.wakePhase)) return "listening";
  if (runtime.thinking || ["transcribing", "agent_responding"].includes(runtime.wakePhase)) return "thinking";
  if ((runtime.activeAgentCount || 0) > 0) return "focused";
  if (runtime.pendingConfirmation) return "concerned";
  if (runtime.completedAt && now - runtime.completedAt <= RECENT_COMPLETION_MS) return "happy";
  if (runtime.sleeping) return "sleeping";
  return "idle";
}

export function resolveAvatarEmotion(runtime = {}, profile = {}, now = Date.now()) {
  const explicit = runtime.avatar;
  if (explicit?.emotion) {
    return { name: explicit.emotion, intensity: Math.max(0, Math.min(1, Number(explicit.intensity) || 0.5)) };
  }
  const state = resolveAvatarState(runtime, now);
  if (state === "error") return { name: "error", intensity: 0.72 };
  if (state === "concerned") return { name: "concerned", intensity: 0.58 };
  if (state === "happy") return { name: "pleased", intensity: 0.45 };
  const text = String(runtime.latestAssistantText || "").toLowerCase();
  if (/\b(?:sorry|apologize|apologies)\b/.test(text)) return { name: "apologetic", intensity: 0.35 };
  if (/\b(?:uncertain|not sure|may be|might be)\b/.test(text)) return { name: "confused", intensity: 0.25 };
  if (/\b(?:warning|danger|failed|failure|problem)\b/.test(text)) return { name: "concerned", intensity: 0.3 };
  return { name: profile.defaultExpression || "neutral", intensity: 0.2 };
}

export class AvatarStateController {
  constructor(profile) {
    this.profile = profile;
    this.runtime = {};
    this.state = "idle";
    this.emotion = { name: profile.defaultExpression || "neutral", intensity: 0.2 };
  }

  update(runtime, now = Date.now()) {
    this.runtime = { ...this.runtime, ...runtime };
    this.state = resolveAvatarState(this.runtime, now);
    this.emotion = resolveAvatarEmotion(this.runtime, this.profile, now);
    return { state: this.state, emotion: this.emotion };
  }
}
```

- [ ] **Step 6: Run Node tests**

```powershell
node --test tests\js\avatar-settings.test.mjs tests\js\avatar-controller.test.mjs
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add remote_agent_protocol\web_app\avatar\math.js remote_agent_protocol\web_app\avatar\avatar-settings.js remote_agent_protocol\web_app\avatar\persona-profiles.js remote_agent_protocol\web_app\avatar\expressions.js remote_agent_protocol\web_app\avatar\avatar-controller.js tests\js\avatar-settings.test.mjs tests\js\avatar-controller.test.mjs; git commit -m "feat(avatar): add state and persona behavior engine"
```

---

### Task 7: Add the Companion Panel, Settings UI, and App Runtime Bridge

**Files:**
- Create: `remote_agent_protocol/web_app/avatar/avatar-panel.js`
- Create: `remote_agent_protocol/web_app/avatar/avatar-entry.js`
- Modify: `remote_agent_protocol/web_app/index.html`
- Modify: `remote_agent_protocol/web_app/styles.css`
- Modify: `remote_agent_protocol/web_app/app.js`
- Modify: `tests/test_web_gui.py`

**Interfaces:**
- Produces: `window.remoteAgentAvatar.updateRuntime()`, `.updateSettings()`, `.setPanelVisible()`, `.dispose()`
- Produces: DOM IDs `avatarPanel`, `avatarCanvasHost`, `avatarFallback`, `avatarPersonaName`, `avatarStateLabel`, `avatarEmotionLabel`, `avatarCollapseBtn`
- Produces: settings IDs prefixed `avatarSetting`

- [ ] **Step 1: Write failing static UI tests**

Add to `tests/test_web_gui.py`:

```python
def test_web_shell_contains_avatar_panel_import_map_and_settings():
    html = (WEB_APP / "index.html").read_text(encoding="utf-8")
    script = (WEB_APP / "app.js").read_text(encoding="utf-8")
    css = (WEB_APP / "styles.css").read_text(encoding="utf-8")

    for marker in [
        'id="avatarPanel"',
        'id="avatarCanvasHost"',
        'id="avatarFallback"',
        'id="avatarPersonaName"',
        'id="avatarStateLabel"',
        'id="avatarEmotionLabel"',
        'id="avatarCollapseBtn"',
        'id="avatarSettingEnabled"',
        'id="avatarSettingQuality"',
        'id="avatarSettingMotion"',
        'id="avatarSettingsSaveBtn"',
    ]:
        assert marker in html
    assert 'type="importmap"' in html
    assert '"three": "/vendor/three/three.module.min.js"' in html
    assert 'src="/avatar/avatar-entry.js"' in html
    assert "function syncAvatarRuntime" in script
    assert 'post("avatar_settings"' in script
    assert ".avatar-panel" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
```

- [ ] **Step 2: Run the test and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_web_gui.py::test_web_shell_contains_avatar_panel_import_map_and_settings -v
```

Expected: FAIL.

- [ ] **Step 3: Add the panel markup and import map**

Inside the existing `.activity-panel`, before its current header, add:

```html
<section id="avatarPanel" class="avatar-panel" aria-label="Animated assistant companion">
  <div class="avatar-panel-head">
    <div>
      <p class="section-label">Companion</p>
      <strong id="avatarPersonaName">Assistant</strong>
    </div>
    <button id="avatarCollapseBtn" class="icon-button" type="button" aria-expanded="true" aria-controls="avatarPanelBody">−</button>
  </div>
  <div id="avatarPanelBody" class="avatar-panel-body">
    <div id="avatarCanvasHost" class="avatar-canvas-host" role="img" aria-label="Animated assistant face"></div>
    <div id="avatarFallback" class="avatar-fallback" aria-hidden="true">
      <span class="avatar-fallback-head"></span>
      <span class="avatar-fallback-collar"></span>
    </div>
    <div class="avatar-status-row" aria-live="polite">
      <span id="avatarStateLabel">idle</span>
      <span id="avatarEmotionLabel">attentive</span>
    </div>
  </div>
</section>
```

Before `/app.js`, add:

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

- [ ] **Step 4: Add settings controls**

Add an Avatar settings card to the existing Settings view:

```html
<section class="settings-card avatar-settings-card">
  <div class="panel-header compact">
    <div><p class="section-label">Visual companion</p><h3>Animated avatar</h3></div>
    <button id="avatarSettingsSaveBtn" class="button primary-action" type="button">Save avatar</button>
  </div>
  <div id="avatarSettingsNotice" class="persona-notice hidden" role="status"></div>
  <div class="editor-grid avatar-settings-grid">
    <label>Enabled<select id="avatarSettingEnabled"><option value="true">On</option><option value="false">Off</option></select></label>
    <label>Avatar<select id="avatarSettingAvatar"><option value="butler">Butler</option></select></label>
    <label>Quality<select id="avatarSettingQuality"><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label>
    <label>Motion<select id="avatarSettingMotion"><option value="system">System preference</option><option value="reduced">Reduced</option><option value="normal">Normal</option></select></label>
    <label>Lip-sync<select id="avatarSettingLipSync"><option value="true">On</option><option value="false">Off</option></select></label>
    <label>Eye movement<select id="avatarSettingGaze"><option value="true">On</option><option value="false">Off</option></select></label>
    <label>Idle motion<select id="avatarSettingIdle"><option value="true">On</option><option value="false">Off</option></select></label>
    <label>State labels<select id="avatarSettingShowState"><option value="true">Show</option><option value="false">Hide</option></select></label>
    <label>Panel<select id="avatarSettingCollapsed"><option value="false">Expanded</option><option value="true">Collapsed</option></select></label>
    <label>Expression intensity<input id="avatarSettingIntensity" type="range" min="0" max="1" step="0.05" value="0.62" /></label>
  </div>
</section>
```

- [ ] **Step 5: Implement the panel module and entry boundary**

Create `avatar-panel.js`:

```javascript
export function createAvatarPanel() {
  const panel = document.getElementById("avatarPanel");
  const body = document.getElementById("avatarPanelBody");
  const fallback = document.getElementById("avatarFallback");
  const host = document.getElementById("avatarCanvasHost");
  const collapse = document.getElementById("avatarCollapseBtn");
  const persona = document.getElementById("avatarPersonaName");
  const stateLabel = document.getElementById("avatarStateLabel");
  const emotionLabel = document.getElementById("avatarEmotionLabel");

  function setCollapsed(value) {
    panel?.classList.toggle("collapsed", value);
    body?.classList.toggle("hidden", value);
    collapse?.setAttribute("aria-expanded", String(!value));
    if (collapse) collapse.textContent = value ? "+" : "−";
  }

  return {
    host,
    showFallback(show) {
      fallback?.classList.toggle("active", show);
      fallback?.setAttribute("aria-hidden", String(!show));
    },
    render(runtime, resolved) {
      if (persona) persona.textContent = runtime.persona || "Assistant";
      if (stateLabel) stateLabel.textContent = resolved.state || "idle";
      if (emotionLabel) emotionLabel.textContent = resolved.emotion?.name || "neutral";
    },
    setCollapsed,
    setEnabled(enabled) { panel?.classList.toggle("hidden", !enabled); },
    setLabelsVisible(visible) { panel?.classList.toggle("hide-state-labels", !visible); },
    onCollapse(handler) { collapse?.addEventListener("click", handler); },
  };
}
```

Create `avatar-entry.js`:

```javascript
import { normalizeAvatarSettings } from "./avatar-settings.js";
import { AvatarStateController } from "./avatar-controller.js";
import { createAvatarPanel } from "./avatar-panel.js";
import { profileForPersona } from "./persona-profiles.js";

const panel = createAvatarPanel();
let settings = normalizeAvatarSettings({}, matchMedia("(prefers-reduced-motion: reduce)").matches);
let runtime = {};
let scene = null;
let loading = null;
let controller = new AvatarStateController(profileForPersona("", settings.avatarId));

async function ensureScene() {
  if (!settings.enabled || settings.panelCollapsed || scene || loading) return;
  loading = import("./avatar-scene.js")
    .then(({ createAvatarScene }) => createAvatarScene(panel.host, settings))
    .then((created) => { scene = created; panel.showFallback(false); })
    .catch((error) => { console.warn("Avatar scene unavailable", error); panel.showFallback(true); })
    .finally(() => { loading = null; });
  await loading;
}

async function sync() {
  panel.setEnabled(settings.enabled);
  panel.setCollapsed(settings.panelCollapsed);
  panel.setLabelsVisible(settings.showState);
  if (!settings.enabled || settings.panelCollapsed) {
    scene?.dispose();
    scene = null;
    return;
  }
  const profile = profileForPersona(runtime.persona, settings.avatarId);
  controller.profile = profile;
  const resolved = controller.update(runtime);
  panel.render(runtime, resolved);
  await ensureScene();
  scene?.update({ runtime, resolved, profile, settings });
}

const api = {
  updateRuntime(next) { runtime = { ...runtime, ...next }; void sync(); },
  updateSettings(next) {
    settings = normalizeAvatarSettings(next, matchMedia("(prefers-reduced-motion: reduce)").matches);
    void sync();
  },
  setPanelVisible(visible) { scene?.setVisible(Boolean(visible)); },
  dispose() { scene?.dispose(); scene = null; },
};

window.remoteAgentAvatar = api;
window.dispatchEvent(new Event("rap:avatar-ready"));
window.addEventListener("beforeunload", () => api.dispose(), { once: true });
```

- [ ] **Step 6: Bridge current events and settings from `app.js`**

Add avatar fields to the global `state` object:

```javascript
avatar: { speaking: false, userSpeaking: false, completedAt: 0, failedAt: 0, latestAssistantText: "" },
```

Add:

```javascript
function avatarRuntimeSnapshot() {
  const s = state.status || {};
  return {
    persona: s.persona,
    session: s.session,
    speaking: state.avatar.speaking,
    userSpeaking: state.avatar.userSpeaking,
    thinking: currentWake().phase === "transcribing" || currentWake().phase === "agent_responding",
    wakePhase: currentWake().phase,
    activeAgentCount: s.activeAgentCount || 0,
    pendingConfirmation: Boolean(state.activeConfirm || s.pendingConfirms?.length),
    completedAt: state.avatar.completedAt,
    error: s.session === "failed" || Boolean(state.avatar.failedAt && Date.now() - state.avatar.failedAt < 5000),
    latestAssistantText: state.avatar.latestAssistantText,
  };
}

function syncAvatarRuntime() {
  const api = window.remoteAgentAvatar;
  if (!api || !state.status?.avatar) return;
  api.updateSettings(state.status.avatar);
  api.updateRuntime(avatarRuntimeSnapshot());
}
```

Update `handleEvent()`:

```javascript
if (event.type === "transcript" && event.role !== "user") state.avatar.latestAssistantText = event.text || "";
if (event.type === "speaking") state.avatar.speaking = Boolean(event.value);
if (event.type === "turn" && event.event === "user_started") state.avatar.userSpeaking = true;
if (event.type === "turn" && event.event === "user_stopped") state.avatar.userSpeaking = false;
if (event.type === "agent_job" && event.event === "finished" && event.status === "done") state.avatar.completedAt = Date.now();
if (event.type === "agent_job" && ["failed", "timeout", "cancelled"].includes(event.status)) state.avatar.failedAt = Date.now();
syncAvatarRuntime();
```

Call `syncAvatarRuntime()` at the end of `renderStatus()`, listen for `rap:avatar-ready`, and implement the save handler:

```javascript
window.addEventListener("rap:avatar-ready", syncAvatarRuntime);

function avatarMotionValue() {
  const value = $("avatarSettingMotion").value;
  return value === "system" ? null : value === "reduced";
}

async function saveAvatarSettings() {
  const result = await post("avatar_settings", {
    settings: {
      enabled: $("avatarSettingEnabled").value === "true",
      avatarId: $("avatarSettingAvatar").value,
      quality: $("avatarSettingQuality").value,
      lipSync: $("avatarSettingLipSync").value === "true",
      gaze: $("avatarSettingGaze").value === "true",
      idleMotion: $("avatarSettingIdle").value === "true",
      expressionIntensity: Number($("avatarSettingIntensity").value),
      reducedMotion: avatarMotionValue(),
      showState: $("avatarSettingShowState").value === "true",
      panelCollapsed: $("avatarSettingCollapsed").value === "true",
    },
  });
  $("avatarSettingsNotice").textContent = result.ok ? "Avatar settings saved." : result.error;
  $("avatarSettingsNotice").classList.remove("hidden");
}

$("avatarSettingsSaveBtn").addEventListener("click", saveAvatarSettings);
```

Populate the controls from `s.avatar` inside `renderStatus()`.

- [ ] **Step 7: Add CSS**

Add explicit styles for `.avatar-panel`, `.avatar-panel-head`, `.avatar-panel-body`, `.avatar-canvas-host`, `.avatar-fallback`, `.avatar-fallback-head`, `.avatar-fallback-collar`, `.avatar-status-row`, `.avatar-panel.collapsed`, `.avatar-settings-grid`, mobile layout, and:

```css
@media (prefers-reduced-motion: reduce) {
  .avatar-panel *, .avatar-fallback * { transition-duration: 0.001ms !important; animation-duration: 0.001ms !important; }
}
```

Use existing variables such as `--surface-panel`, `--border-subtle`, `--text-primary`, `--text-muted`, and semantic status colors.

- [ ] **Step 8: Run tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_web_gui.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit**

```powershell
git add remote_agent_protocol\web_app\index.html remote_agent_protocol\web_app\styles.css remote_agent_protocol\web_app\app.js remote_agent_protocol\web_app\avatar\avatar-panel.js remote_agent_protocol\web_app\avatar\avatar-entry.js tests\test_web_gui.py; git commit -m "feat(avatar): add companion panel and settings UI"
```

---

### Task 8: Build the Procedural Butler Rig and Three.js Scene

**Files:**
- Create: `remote_agent_protocol/web_app/avatar/procedural-butler.js`
- Create: `remote_agent_protocol/web_app/avatar/avatar-scene.js`
- Modify: `tests/test_web_gui.py`

**Interfaces:**
- Produces: `createProceduralButler(THREE) -> { object, controls, dispose }`
- Produces: `createAvatarScene(host, settings) -> { update, setVisible, dispose }`

- [ ] **Step 1: Write failing source-contract tests**

Add to `tests/test_web_gui.py`:

```python
def test_procedural_butler_exposes_required_control_contract():
    source = (WEB_APP / "avatar/procedural-butler.js").read_text(encoding="utf-8")
    scene = (WEB_APP / "avatar/avatar-scene.js").read_text(encoding="utf-8")

    for control in [
        "root", "bust", "neck", "head", "jaw", "mouthUpper", "mouthLower",
        "mouthCornerLeft", "mouthCornerRight", "cheekLeft", "cheekRight",
        "browLeft", "browRight", "eyeLeft", "eyeRight", "pupilLeft",
        "pupilRight", "lidLeft", "lidRight",
    ]:
        assert control in source
    assert "new THREE.WebGLRenderer" in scene
    assert "ResizeObserver" in scene
    assert "renderer.dispose()" in scene
    assert "renderer.forceContextLoss()" in scene
```

- [ ] **Step 2: Run the test and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_web_gui.py::test_procedural_butler_exposes_required_control_contract -v
```

Expected: FAIL.

- [ ] **Step 3: Implement the procedural rig**

Create `procedural-butler.js`. Use a `THREE.Group` root, `MeshStandardMaterial`, and these exact geometry families:

```javascript
export function createProceduralButler(THREE) {
  const root = new THREE.Group();
  root.name = "procedural-butler";
  const skin = new THREE.MeshStandardMaterial({ color: 0xb98972, roughness: 0.72, metalness: 0.02 });
  const white = new THREE.MeshStandardMaterial({ color: 0xe8e4dc, roughness: 0.8 });
  const jacket = new THREE.MeshStandardMaterial({ color: 0x17191f, roughness: 0.7 });
  const dark = new THREE.MeshStandardMaterial({ color: 0x111216, roughness: 0.65 });
  const iris = new THREE.MeshStandardMaterial({ color: 0x3b5261, roughness: 0.45 });
  const mouthMaterial = new THREE.MeshStandardMaterial({ color: 0x5f3032, roughness: 0.8 });
  const materials = [skin, white, jacket, dark, iris, mouthMaterial];
  const geometries = [];
  const make = (geometry, material, name) => {
    geometries.push(geometry);
    const mesh = new THREE.Mesh(geometry, material);
    mesh.name = name;
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    return mesh;
  };

  const bust = make(new THREE.SphereGeometry(0.82, 32, 20), jacket, "bust");
  bust.scale.set(1.28, 0.72, 0.6);
  bust.position.y = 0.55;
  root.add(bust);

  const shirt = make(new THREE.CylinderGeometry(0.3, 0.43, 0.72, 24), white, "shirt");
  shirt.position.set(0, 0.72, 0.2);
  root.add(shirt);

  const neck = make(new THREE.CylinderGeometry(0.22, 0.25, 0.42, 24), skin, "neck");
  neck.position.y = 1.13;
  root.add(neck);

  const head = new THREE.Group();
  head.name = "head";
  head.position.y = 1.65;
  root.add(head);

  const face = make(new THREE.SphereGeometry(0.48, 40, 28), skin, "face");
  face.scale.set(0.86, 1.08, 0.82);
  head.add(face);

  const jaw = new THREE.Group();
  jaw.name = "jaw";
  jaw.position.set(0, -0.18, 0.39);
  head.add(jaw);

  const eye = (x, name) => {
    const group = new THREE.Group();
    group.name = name;
    group.position.set(x, 0.08, 0.39);
    const whiteMesh = make(new THREE.SphereGeometry(0.092, 24, 16), white, `${name}-white`);
    whiteMesh.scale.set(1.05, 0.72, 0.48);
    const pupil = make(new THREE.SphereGeometry(0.038, 20, 12), iris, `${name}-pupil`);
    pupil.position.z = 0.07;
    const lid = make(new THREE.SphereGeometry(0.098, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2), skin, `${name}-lid`);
    lid.scale.set(1.06, 0.74, 0.5);
    lid.position.z = 0.015;
    group.add(whiteMesh, pupil, lid);
    head.add(group);
    return { group, pupil, lid };
  };

  const leftEye = eye(-0.17, "eyeLeft");
  const rightEye = eye(0.17, "eyeRight");

  const browLeft = make(new THREE.BoxGeometry(0.2, 0.028, 0.035), dark, "browLeft");
  const browRight = make(new THREE.BoxGeometry(0.2, 0.028, 0.035), dark, "browRight");
  browLeft.position.set(-0.17, 0.22, 0.43);
  browRight.position.set(0.17, 0.22, 0.43);
  head.add(browLeft, browRight);

  const mouthUpper = make(new THREE.BoxGeometry(0.23, 0.022, 0.028), mouthMaterial, "mouthUpper");
  const mouthLower = make(new THREE.BoxGeometry(0.2, 0.025, 0.028), mouthMaterial, "mouthLower");
  mouthUpper.position.set(0, 0, 0);
  mouthLower.position.set(0, -0.035, 0);
  jaw.add(mouthUpper, mouthLower);

  const mouthCornerLeft = new THREE.Object3D();
  const mouthCornerRight = new THREE.Object3D();
  mouthCornerLeft.name = "mouthCornerLeft";
  mouthCornerRight.name = "mouthCornerRight";
  mouthCornerLeft.position.set(-0.12, -0.01, 0);
  mouthCornerRight.position.set(0.12, -0.01, 0);
  jaw.add(mouthCornerLeft, mouthCornerRight);

  const cheekLeft = new THREE.Object3D();
  const cheekRight = new THREE.Object3D();
  cheekLeft.name = "cheekLeft";
  cheekRight.name = "cheekRight";
  cheekLeft.position.set(-0.26, -0.04, 0.34);
  cheekRight.position.set(0.26, -0.04, 0.34);
  head.add(cheekLeft, cheekRight);

  const collarLeft = make(new THREE.ConeGeometry(0.22, 0.45, 3), white, "collarLeft");
  const collarRight = make(new THREE.ConeGeometry(0.22, 0.45, 3), white, "collarRight");
  collarLeft.position.set(-0.22, 0.92, 0.31);
  collarRight.position.set(0.22, 0.92, 0.31);
  collarLeft.rotation.z = -0.32;
  collarRight.rotation.z = 0.32;
  root.add(collarLeft, collarRight);

  const bowLeft = make(new THREE.ConeGeometry(0.16, 0.3, 3), dark, "bowLeft");
  const bowRight = make(new THREE.ConeGeometry(0.16, 0.3, 3), dark, "bowRight");
  bowLeft.position.set(-0.13, 0.91, 0.47);
  bowRight.position.set(0.13, 0.91, 0.47);
  bowLeft.rotation.z = -Math.PI / 2;
  bowRight.rotation.z = Math.PI / 2;
  root.add(bowLeft, bowRight);

  root.position.y = -0.55;

  return {
    object: root,
    controls: {
      root, bust, neck, head, jaw, mouthUpper, mouthLower,
      mouthCornerLeft, mouthCornerRight, cheekLeft, cheekRight,
      browLeft, browRight, eyeLeft: leftEye.group, eyeRight: rightEye.group,
      pupilLeft: leftEye.pupil, pupilRight: rightEye.pupil,
      lidLeft: leftEye.lid, lidRight: rightEye.lid,
    },
    dispose() {
      geometries.forEach((geometry) => geometry.dispose());
      materials.forEach((material) => material.dispose());
      root.clear();
    },
  };
}
```

- [ ] **Step 4: Implement the scene shell**

Create `avatar-scene.js` with:

```javascript
import * as THREE from "three";
import { createProceduralButler } from "./procedural-butler.js";

export async function createAvatarScene(host, settings) {
  if (!host) throw new Error("Avatar canvas host is missing");
  const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: settings.antialias, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, settings.maxPixelRatio));
  renderer.shadowMap.enabled = settings.shadows;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  host.replaceChildren(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 20);
  camera.position.set(0, 1.35, 4.4);
  camera.lookAt(0, 1.15, 0);

  const key = new THREE.DirectionalLight(0xffffff, 2.3);
  key.position.set(2.5, 3.2, 3.5);
  key.castShadow = settings.shadows;
  const fill = new THREE.DirectionalLight(0x8aa0c4, 1.1);
  fill.position.set(-2.5, 1.8, 2.5);
  const rim = new THREE.DirectionalLight(0xa78bfa, 0.75);
  rim.position.set(0, 2.5, -2.5);
  const ambient = new THREE.HemisphereLight(0xb9c6dc, 0x111113, 0.9);
  scene.add(key, fill, rim, ambient);

  const rig = createProceduralButler(THREE);
  scene.add(rig.object);

  let visible = true;
  let disposed = false;
  let lastFrame = 0;
  let latest = null;
  const targetInterval = 1000 / settings.targetFps;

  const resize = () => {
    const width = Math.max(1, host.clientWidth);
    const height = Math.max(1, host.clientHeight);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  };
  const observer = new ResizeObserver(resize);
  observer.observe(host);
  resize();

  const animate = (time) => {
    if (disposed) return;
    requestAnimationFrame(animate);
    if (!visible || document.hidden || time - lastFrame < targetInterval) return;
    lastFrame = time;
    if (latest) applyAvatarFrame(rig.controls, latest, time / 1000);
    renderer.render(scene, camera);
  };
  requestAnimationFrame(animate);

  return {
    update(value) { latest = value; },
    setVisible(value) { visible = Boolean(value); },
    dispose() {
      if (disposed) return;
      disposed = true;
      observer.disconnect();
      rig.dispose();
      scene.clear();
      renderer.renderLists.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
      renderer.domElement.remove();
    },
  };
}

function applyAvatarFrame(controls, frame, seconds) {
  const reduced = frame.settings.effectiveReducedMotion;
  const breathing = reduced || !frame.settings.idleMotion ? 0 : Math.sin(seconds * 1.35) * 0.008 * frame.profile.idleIntensity;
  controls.bust.scale.y = 0.72 + breathing;
  controls.head.rotation.y = frame.resolved.state === "thinking" ? -0.05 : 0;
}
```

Later tasks replace the minimal `applyAvatarFrame` body with expressions, gaze, and lip-sync.

- [ ] **Step 5: Run static tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_web_gui.py::test_procedural_butler_exposes_required_control_contract -v
```

Expected: PASS.

- [ ] **Step 6: Run a manual renderer smoke test**

```powershell
.\.venv\Scripts\python.exe -m remote_agent_protocol
```

Verify: the Control Center opens, the butler bust renders in the companion card, resizing the window keeps correct aspect ratio, and disabling/collapsing the avatar removes the canvas without affecting voice controls.

- [ ] **Step 7: Commit**

```powershell
git add remote_agent_protocol\web_app\avatar\procedural-butler.js remote_agent_protocol\web_app\avatar\avatar-scene.js tests\test_web_gui.py; git commit -m "feat(avatar): render procedural butler companion"
```

---

### Task 9: Add Expressions, Blinking, Gaze, Idle Motion, and Reduced Motion

**Files:**
- Create: `remote_agent_protocol/web_app/avatar/gaze-controller.js`
- Modify: `remote_agent_protocol/web_app/avatar/avatar-scene.js`
- Modify: `tests/js/avatar-controller.test.mjs`
- Modify: `tests/test_web_gui.py`

**Interfaces:**
- Produces: `GazeController.update(delta, state, enabled, reducedMotion)`
- Consumes: expression targets from `expressions.js`
- Applies: brows, lids, pupils, mouth corners, cheeks, jaw baseline, and head pose

- [ ] **Step 1: Add failing gaze tests**

Append to `tests/js/avatar-controller.test.mjs`:

```javascript
import { GazeController } from "../../remote_agent_protocol/web_app/avatar/gaze-controller.js";

test("listening gaze remains close to camera and reduces saccades", () => {
  const gaze = new GazeController({ random: () => 0.5 });
  const result = gaze.update(0.016, "listening", true, false);
  assert.equal(result.enabled, true);
  assert.ok(Math.abs(result.x) <= 0.03);
  assert.ok(Math.abs(result.y) <= 0.03);
});

test("reduced motion keeps blink but suppresses large gaze offsets", () => {
  const gaze = new GazeController({ random: () => 1 });
  const result = gaze.update(8, "thinking", true, true);
  assert.ok(Math.abs(result.x) <= 0.04);
  assert.ok(Math.abs(result.y) <= 0.04);
});
```

- [ ] **Step 2: Run the Node test and verify failure**

```powershell
node --test tests\js\avatar-controller.test.mjs
```

Expected: missing `gaze-controller.js`.

- [ ] **Step 3: Implement gaze timing**

Create `gaze-controller.js`:

```javascript
import { clamp, range } from "./math.js";

export class GazeController {
  constructor({ random = Math.random } = {}) {
    this.random = random;
    this.timeToBlink = range([3.5, 7.5], random);
    this.blinkTime = 0;
    this.timeToSaccade = range([1.8, 4.5], random);
    this.x = 0;
    this.y = 0;
  }

  update(delta, state, enabled, reducedMotion) {
    if (!enabled) return { enabled: false, x: 0, y: 0, blink: 0 };
    this.timeToBlink -= delta;
    if (this.timeToBlink <= 0 && this.blinkTime <= 0) {
      this.blinkTime = 0.14;
      this.timeToBlink = range(state === "listening" ? [5, 9] : [3.5, 7.5], this.random);
    }
    let blink = 0;
    if (this.blinkTime > 0) {
      this.blinkTime -= delta;
      const phase = clamp(1 - this.blinkTime / 0.14);
      blink = Math.sin(Math.PI * phase);
    }
    this.timeToSaccade -= delta;
    if (this.timeToSaccade <= 0) {
      const limit = reducedMotion || state === "listening" ? 0.03 : state === "thinking" ? 0.12 : 0.07;
      this.x = (this.random() * 2 - 1) * limit;
      this.y = (this.random() * 2 - 1) * limit;
      if (state === "thinking" && !reducedMotion) this.y -= 0.04;
      this.timeToSaccade = range([1.8, 4.5], this.random);
    }
    return { enabled: true, x: this.x, y: this.y, blink };
  }
}
```

- [ ] **Step 4: Integrate expression and gaze application**

In `avatar-scene.js`, import `damp`, `expressionFor`, `blendTargets`, and `GazeController`. Create one gaze controller and a mutable `currentTargets`. Replace `applyAvatarFrame` with code that:

```javascript
function applyAvatarFrame(controls, frame, seconds, delta, gazeController, currentTargets) {
  const expression = expressionFor(frame.resolved.emotion.name);
  const base = expressionFor(frame.profile.defaultExpression);
  const target = blendTargets(base, expression, frame.resolved.emotion.intensity * frame.settings.expressionIntensity);
  for (const key of Object.keys(currentTargets)) currentTargets[key] = damp(currentTargets[key], target[key], 8, delta);

  const gaze = gazeController.update(delta, frame.resolved.state, frame.settings.gaze, frame.settings.effectiveReducedMotion);
  controls.pupilLeft.position.x = gaze.x;
  controls.pupilRight.position.x = gaze.x;
  controls.pupilLeft.position.y = gaze.y;
  controls.pupilRight.position.y = gaze.y;
  controls.lidLeft.scale.y = Math.max(0.04, 0.74 * (1 - gaze.blink));
  controls.lidRight.scale.y = Math.max(0.04, 0.74 * (1 - gaze.blink * 0.96));
  controls.browLeft.position.y = 0.22 + currentTargets.browInner * 0.035 + currentTargets.browAsymmetry * 0.02;
  controls.browRight.position.y = 0.22 + currentTargets.browInner * 0.035 - currentTargets.browAsymmetry * 0.02;
  controls.browLeft.rotation.z = currentTargets.browOuter * -0.15;
  controls.browRight.rotation.z = currentTargets.browOuter * 0.15;
  controls.mouthCornerLeft.position.y = -0.01 + currentTargets.mouthCorner * 0.035;
  controls.mouthCornerRight.position.y = -0.01 + currentTargets.mouthCorner * 0.035;
  controls.jaw.rotation.x = currentTargets.jawOpen * 0.18;
  controls.head.rotation.x = currentTargets.headPitch;
  controls.head.rotation.y = currentTargets.headYaw;
  controls.head.rotation.z = currentTargets.headRoll;

  const canIdle = frame.settings.idleMotion && !frame.settings.effectiveReducedMotion;
  const breathing = canIdle ? Math.sin(seconds * 1.35) * 0.008 * frame.profile.idleIntensity : 0;
  controls.bust.scale.y = 0.72 + breathing;
}
```

Track `delta` using the render-loop timestamp and initialize every `currentTargets` key to `0`.

- [ ] **Step 5: Add a static reduced-motion test**

In `tests/test_web_gui.py`, assert the scene source contains both `effectiveReducedMotion` and the `idleMotion` gate.

- [ ] **Step 6: Run tests**

```powershell
node --test tests\js\avatar-controller.test.mjs; .\.venv\Scripts\python.exe -m pytest tests\test_web_gui.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add remote_agent_protocol\web_app\avatar\gaze-controller.js remote_agent_protocol\web_app\avatar\avatar-scene.js tests\js\avatar-controller.test.mjs tests\test_web_gui.py; git commit -m "feat(avatar): animate expressions gaze and idle behavior"
```

---

### Task 10: Add Audio-Driven Lip-Sync and Managed SSE Reconnect

**Files:**
- Create: `remote_agent_protocol/web_app/avatar/lip-sync.js`
- Create: `tests/js/lip-sync.test.mjs`
- Modify: `remote_agent_protocol/web_app/avatar/avatar-scene.js`

**Interfaces:**
- Produces: `LipSyncController.ingest(sample)` and `.update(delta, speaking, enabled)`
- Produces: `AvatarEnvelopeStream.start()` and `.dispose()`
- Consumes: `/api/avatar-audio` SSE

- [ ] **Step 1: Write failing lip-sync tests**

Create `tests/js/lip-sync.test.mjs`:

```javascript
import test from "node:test";
import assert from "node:assert/strict";
import { AvatarEnvelopeStream, LipSyncController } from "../../remote_agent_protocol/web_app/avatar/lip-sync.js";

test("RMS opens the jaw and release closes it", () => {
  const lip = new LipSyncController();
  lip.ingest({ rms: 0.5, peak: 0.8, voiced: true, timestamp: 1 });
  const open = lip.update(0.05, true, true);
  const closing = lip.update(0.5, false, true);
  assert.ok(open.jawOpen > 0.2);
  assert.ok(closing.jawOpen < open.jawOpen);
});

test("speaking fallback moves the mouth without telemetry", () => {
  const lip = new LipSyncController({ clock: () => 1.2 });
  const value = lip.update(0.05, true, true);
  assert.ok(value.jawOpen > 0);
  assert.equal(value.usingEnvelope, false);
});

test("disabled lip sync keeps the mouth neutral", () => {
  const lip = new LipSyncController();
  lip.ingest({ rms: 0.8, peak: 1, voiced: true, timestamp: 1 });
  assert.deepEqual(lip.update(0.05, true, false), { jawOpen: 0, mouthWidth: 0, cheek: 0, usingEnvelope: false });
});

test("disposing the stream closes EventSource and timers", () => {
  const closed = [];
  class FakeEventSource { constructor() {} close() { closed.push(true); } }
  const stream = new AvatarEnvelopeStream(() => {}, { EventSourceImpl: FakeEventSource, setTimer: () => 7, clearTimer: (id) => closed.push(id) });
  stream.start();
  stream.dispose();
  assert.deepEqual(closed, [true]);
});
```

- [ ] **Step 2: Run tests and verify failure**

```powershell
node --test tests\js\lip-sync.test.mjs
```

Expected: module-not-found failure.

- [ ] **Step 3: Implement lip-sync and SSE lifecycle**

Create `lip-sync.js`:

```javascript
import { clamp, damp } from "./math.js";

export class LipSyncController {
  constructor({ clock = () => performance.now() / 1000 } = {}) {
    this.clock = clock;
    this.sample = null;
    this.lastEnvelopeAt = 0;
    this.jaw = 0;
  }

  ingest(sample) {
    if (!sample || !Number.isFinite(sample.rms) || !Number.isFinite(sample.peak)) return;
    this.sample = sample;
    this.lastEnvelopeAt = this.clock();
  }

  update(delta, speaking, enabled) {
    if (!enabled) {
      this.jaw = 0;
      return { jawOpen: 0, mouthWidth: 0, cheek: 0, usingEnvelope: false };
    }
    const fresh = this.sample && this.clock() - this.lastEnvelopeAt < 0.35;
    const fallback = speaking ? 0.12 + Math.abs(Math.sin(this.clock() * 10.5)) * 0.16 : 0;
    const target = fresh
      ? clamp(this.sample.rms * 1.45 + Math.max(0, this.sample.peak - this.sample.rms) * 0.22)
      : fallback;
    this.jaw = damp(this.jaw, target, target > this.jaw ? 24 : 10, delta);
    return {
      jawOpen: this.jaw,
      mouthWidth: fresh ? clamp((this.sample.peak - this.sample.rms) * 0.45) : this.jaw * 0.15,
      cheek: this.jaw * 0.18,
      usingEnvelope: Boolean(fresh),
    };
  }
}

export class AvatarEnvelopeStream {
  constructor(onSample, options = {}) {
    this.onSample = onSample;
    this.EventSourceImpl = options.EventSourceImpl || EventSource;
    this.setTimer = options.setTimer || setTimeout;
    this.clearTimer = options.clearTimer || clearTimeout;
    this.url = options.url || "/api/avatar-audio";
    this.source = null;
    this.timer = null;
    this.retryMs = 500;
    this.disposed = false;
  }

  start() {
    if (this.disposed || this.source) return;
    const source = new this.EventSourceImpl(this.url);
    this.source = source;
    source.addEventListener?.("envelope", (event) => {
      this.retryMs = 500;
      try { this.onSample(JSON.parse(event.data)); } catch (error) { console.warn("Invalid avatar envelope", error); }
    });
    source.onerror = () => {
      source.close();
      if (this.source === source) this.source = null;
      if (this.disposed) return;
      const delay = this.retryMs;
      this.retryMs = Math.min(8000, this.retryMs * 2);
      this.timer = this.setTimer(() => { this.timer = null; this.start(); }, delay);
    };
  }

  dispose() {
    this.disposed = true;
    if (this.timer !== null) this.clearTimer(this.timer);
    this.timer = null;
    this.source?.close();
    this.source = null;
  }
}
```

- [ ] **Step 4: Integrate mouth motion into the scene**

In `avatar-scene.js`, create `LipSyncController` and `AvatarEnvelopeStream`. Start the stream only when `settings.lipSync` is enabled. During each frame:

```javascript
const mouth = lipSync.update(delta, frame.runtime.speaking, frame.settings.lipSync);
controls.jaw.rotation.x = (currentTargets.jawOpen + mouth.jawOpen) * 0.22;
controls.mouthLower.position.y = -0.035 - mouth.jawOpen * 0.06;
controls.mouthUpper.scale.x = 1 + mouth.mouthWidth;
controls.mouthLower.scale.x = 1 + mouth.mouthWidth * 0.8;
controls.cheekLeft.scale.y = 1 - mouth.cheek;
controls.cheekRight.scale.y = 1 - mouth.cheek;
```

Dispose the stream in `scene.dispose()`.

- [ ] **Step 5: Run tests**

```powershell
node --test tests\js\lip-sync.test.mjs
```

Expected: PASS.

- [ ] **Step 6: Manual TTS test**

Run the app, use Settings → Test voice with Kokoro and Coqui when available, and verify jaw movement follows loud/quiet portions, stops when TTS stops, and falls back to restrained cadence if `/api/avatar-audio` is intentionally disconnected.

- [ ] **Step 7: Commit**

```powershell
git add remote_agent_protocol\web_app\avatar\lip-sync.js remote_agent_protocol\web_app\avatar\avatar-scene.js tests\js\lip-sync.test.mjs; git commit -m "feat(avatar): drive mouth motion from TTS amplitude"
```

---

### Task 11: Add GLB Metadata Resolution, Loading, and Procedural Fallback

**Files:**
- Create: `remote_agent_protocol/web_app/avatar/model-loader.js`
- Create: `tests/js/model-loader.test.mjs`
- Modify: `remote_agent_protocol/web_app/avatar/avatar-scene.js`

**Interfaces:**
- Produces: `resolveAvatarPlan(metadata, baseUrl)`
- Produces: `loadAvatarModel({ metadata, baseUrl, loadGltf })`
- Produces: `{ kind, object, controls, mixer, dispose }` or `{ kind: "procedural" }`

- [ ] **Step 1: Write failing loader tests**

Create `tests/js/model-loader.test.mjs`:

```javascript
import test from "node:test";
import assert from "node:assert/strict";
import { loadAvatarModel, resolveAvatarPlan } from "../../remote_agent_protocol/web_app/avatar/model-loader.js";

test("null model selects procedural fallback without a request", async () => {
  let calls = 0;
  const result = await loadAvatarModel({
    metadata: { model: null, fallback: "procedural-butler" },
    baseUrl: "/assets/avatars/butler/",
    loadGltf: async () => { calls += 1; },
  });
  assert.equal(result.kind, "procedural");
  assert.equal(calls, 0);
});

test("relative local model resolves under avatar directory", () => {
  assert.deepEqual(resolveAvatarPlan({ model: "butler.glb" }, "/assets/avatars/butler/"), {
    kind: "gltf",
    url: "/assets/avatars/butler/butler.glb",
  });
});

test("absolute and traversal model paths are rejected", () => {
  assert.equal(resolveAvatarPlan({ model: "https://example.com/a.glb" }, "/assets/avatars/butler/").kind, "procedural");
  assert.equal(resolveAvatarPlan({ model: "../a.glb" }, "/assets/avatars/butler/").kind, "procedural");
});

test("load failure returns procedural fallback", async () => {
  const result = await loadAvatarModel({
    metadata: { model: "butler.glb" },
    baseUrl: "/assets/avatars/butler/",
    loadGltf: async () => { throw new Error("bad model"); },
  });
  assert.equal(result.kind, "procedural");
  assert.match(result.error.message, /bad model/);
});
```

- [ ] **Step 2: Run tests and verify failure**

```powershell
node --test tests\js\model-loader.test.mjs
```

Expected: module-not-found failure.

- [ ] **Step 3: Implement safe metadata planning and loading**

Create `model-loader.js`:

```javascript
export function resolveAvatarPlan(metadata = {}, baseUrl = "/") {
  const model = metadata.model;
  if (typeof model !== "string" || !model || model.includes("..") || /^[a-z]+:/i.test(model) || model.startsWith("/")) {
    return { kind: "procedural" };
  }
  return { kind: "gltf", url: new URL(model, `${location.origin}${baseUrl}`).pathname };
}

export async function loadAvatarModel({ metadata, baseUrl, loadGltf }) {
  const plan = resolveAvatarPlan(metadata, baseUrl);
  if (plan.kind === "procedural") return plan;
  try {
    const gltf = await loadGltf(plan.url);
    const controls = discoverControls(gltf.scene, metadata.controls || {});
    return {
      kind: "gltf",
      object: gltf.scene,
      controls,
      animations: gltf.animations || [],
      dispose() { disposeObject(gltf.scene); },
    };
  } catch (error) {
    console.warn("Avatar model load failed", error);
    return { kind: "procedural", error };
  }
}

function discoverControls(root, aliases) {
  const named = new Map();
  const morphs = new Map();
  root.traverse((object) => {
    if (object.name) named.set(object.name, object);
    if (object.morphTargetDictionary) {
      for (const [name, index] of Object.entries(object.morphTargetDictionary)) morphs.set(name, { object, index });
    }
  });
  const result = {};
  for (const [key, names] of Object.entries(aliases)) {
    result[key] = names.map((name) => named.get(name) || morphs.get(name)).find(Boolean) || null;
  }
  return result;
}

export function disposeObject(root) {
  root.traverse((object) => {
    object.geometry?.dispose?.();
    const materials = Array.isArray(object.material) ? object.material : object.material ? [object.material] : [];
    for (const material of materials) {
      for (const value of Object.values(material)) if (value?.isTexture) value.dispose();
      material.dispose?.();
    }
  });
  root.clear();
}
```

Replace `location.origin` in tests by passing browser-like base URLs through a helper; for Node compatibility, implement URL joining as:

```javascript
const cleanBase = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
return { kind: "gltf", url: `${cleanBase}${model}`.replace(/\/+/g, "/") };
```

Use that version in the committed file.

- [ ] **Step 4: Integrate metadata and dynamic GLTFLoader import**

In `avatar-scene.js`, before creating the procedural rig:

```javascript
const avatarBase = `/assets/avatars/${settings.avatarId}/`;
const metadata = await fetch(`${avatarBase}metadata.json`, { cache: "no-cache" }).then((response) => {
  if (!response.ok) throw new Error(`Avatar metadata HTTP ${response.status}`);
  return response.json();
});
const loaded = await loadAvatarModel({
  metadata,
  baseUrl: avatarBase,
  loadGltf: async (url) => {
    const { GLTFLoader } = await import("three/addons/loaders/GLTFLoader.js");
    return new GLTFLoader().loadAsync(url);
  },
});
const rig = loaded.kind === "gltf" ? adaptLoadedRig(loaded, THREE) : createProceduralButler(THREE);
```

Implement `adaptLoadedRig` so missing GLB controls receive safe no-op `THREE.Object3D` controls and disposal delegates to `loaded.dispose()`. Do not fetch a model when metadata has `model: null`.

- [ ] **Step 5: Run Node tests**

```powershell
node --test tests\js\model-loader.test.mjs
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add remote_agent_protocol\web_app\avatar\model-loader.js remote_agent_protocol\web_app\avatar\avatar-scene.js tests\js\model-loader.test.mjs; git commit -m "feat(avatar): support local GLB models with fallback"
```

---

### Task 12: Complete Visibility Pausing, Context Loss, Accessibility, and Disposal

**Files:**
- Modify: `remote_agent_protocol/web_app/avatar/avatar-entry.js`
- Modify: `remote_agent_protocol/web_app/avatar/avatar-scene.js`
- Modify: `remote_agent_protocol/web_app/avatar/avatar-panel.js`
- Modify: `remote_agent_protocol/web_app/styles.css`
- Modify: `tests/js/lip-sync.test.mjs`
- Modify: `tests/test_web_gui.py`

**Interfaces:**
- Adds: one guarded WebGL reinitialize attempt
- Adds: `IntersectionObserver` visibility control
- Guarantees: renderer, context, model, EventSource, observer, timer, and DOM cleanup

- [ ] **Step 1: Add failing disposal/static tests**

Add to `tests/test_web_gui.py`:

```python
def test_avatar_scene_has_visibility_context_loss_and_complete_cleanup():
    source = (WEB_APP / "avatar/avatar-scene.js").read_text(encoding="utf-8")
    entry = (WEB_APP / "avatar/avatar-entry.js").read_text(encoding="utf-8")

    assert "IntersectionObserver" in entry
    assert "webglcontextlost" in source
    assert "webglcontextrestored" in source
    assert "attemptedContextRestore" in source
    for marker in ["observer.disconnect()", "stream.dispose()", "rig.dispose()", "renderer.renderLists.dispose()", "renderer.dispose()", "renderer.forceContextLoss()"]:
        assert marker in source
```

- [ ] **Step 2: Run the test and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_web_gui.py::test_avatar_scene_has_visibility_context_loss_and_complete_cleanup -v
```

Expected: FAIL.

- [ ] **Step 3: Add panel visibility observation**

In `avatar-entry.js`, observe `avatarPanel`:

```javascript
const visibilityObserver = new IntersectionObserver(
  ([entry]) => api.setPanelVisible(Boolean(entry?.isIntersecting)),
  { threshold: 0.05 },
);
const panelElement = document.getElementById("avatarPanel");
if (panelElement) visibilityObserver.observe(panelElement);
```

Disconnect it in `api.dispose()`.

- [ ] **Step 4: Handle WebGL context loss once**

In `avatar-scene.js`, add:

```javascript
let attemptedContextRestore = false;
const onContextLost = (event) => {
  event.preventDefault();
  visible = false;
  host.dispatchEvent(new CustomEvent("rap:avatar-fallback", { detail: { reason: "context-lost" } }));
};
const onContextRestored = () => {
  if (attemptedContextRestore) return;
  attemptedContextRestore = true;
  visible = true;
  resize();
};
renderer.domElement.addEventListener("webglcontextlost", onContextLost, false);
renderer.domElement.addEventListener("webglcontextrestored", onContextRestored, false);
```

Remove both listeners during disposal. The entry module listens for `rap:avatar-fallback` and displays the static fallback. It does not loop repeated renderer reconstruction.

- [ ] **Step 5: Make the fallback and labels accessible**

In `avatar-panel.js`, set the canvas host `aria-label` to include persona and resolved state when state labels are hidden. Ensure the fallback is exposed as an image only when active. In CSS, add visible `:focus-visible` styles to the collapse and settings buttons and keep every state word visible without relying on color.

- [ ] **Step 6: Run Node and Python tests**

```powershell
node --test tests\js\*.test.mjs; .\.venv\Scripts\python.exe -m pytest tests\test_web_gui.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add remote_agent_protocol\web_app\avatar remote_agent_protocol\web_app\styles.css tests\js tests\test_web_gui.py; git commit -m "fix(avatar): harden visibility accessibility and cleanup"
```

---

### Task 13: Documentation, Packaging Verification, and Full Regression

**Files:**
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_web_gui.py`

**Interfaces:**
- Documents: operation, settings, local asset path, model metadata, and fallback behavior
- Verifies: wheel contains nested avatar assets

- [ ] **Step 1: Add a packaging contract test**

Add to `tests/test_web_gui.py`:

```python
def test_package_data_covers_nested_avatar_assets():
    project = Path("pyproject.toml").read_text(encoding="utf-8")
    assert '"web_app/**/*"' in project
    for relative in [
        "avatar/avatar-entry.js",
        "avatar/avatar-scene.js",
        "assets/avatars/butler/metadata.json",
        "vendor/three/three.module.min.js",
        "vendor/three/addons/loaders/GLTFLoader.js",
        "vendor/three/addons/utils/BufferGeometryUtils.js",
    ]:
        assert (WEB_APP / relative).is_file(), relative
```

- [ ] **Step 2: Update README**

Add an “Animated companion” section covering:

```markdown
## Animated companion

The web Control Center includes an optional local Three.js butler companion. It reacts to wake-word, user-speaking, thinking, agent-job, error, and TTS-speaking states. Mouth movement uses normalized local TTS amplitude; raw audio never reaches the browser.

Configure it under **Settings → Animated avatar**. Disabling the feature removes the WebGL renderer and audio-envelope connection. Reduced motion can follow the operating-system preference or be overridden.

Avatar assets live under `remote_agent_protocol/web_app/assets/avatars/<avatar-id>/`. The bundled `butler` metadata intentionally selects the procedural fallback. A future local `.glb` can be enabled by setting the metadata `model` field to a relative filename.
```

- [ ] **Step 3: Update architecture docs**

Add the avatar path to the runtime diagram and document:

```text
TTS -> AvatarAudioTap -> local audio output
             |
             +-> latest normalized envelope -> loopback SSE -> avatar renderer
```

State that `WebVoiceApp` owns the hub and `VoiceSession` receives only its publish callback.

- [ ] **Step 4: Update changelog**

Add an unreleased entry describing the procedural butler, lifecycle reactions, amplitude lip-sync, GLB-ready loader, settings, reduced-motion support, and static fallback.

- [ ] **Step 5: Run JavaScript tests**

```powershell
node --test tests\js\*.test.mjs
```

Expected: all tests PASS.

- [ ] **Step 6: Run focused Python tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_app_state.py tests\test_avatar_audio.py tests\test_session_processors.py tests\test_web_gui.py -v
```

Expected: all tests PASS.

- [ ] **Step 7: Run the broader application regression suite**

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_bridge.py tests\test_session_controls.py tests\test_tts_factory.py tests\test_coqui_tts.py tests\test_wake_word.py tests\test_web_gui.py -v
```

Expected: all applicable tests PASS; environment-dependent Coqui tests may skip only when their existing skip conditions apply.

- [ ] **Step 8: Build and inspect the wheel**

```powershell
.\.venv\Scripts\python.exe -m build --wheel; .\.venv\Scripts\python.exe -c "import glob,zipfile; wheel=sorted(glob.glob('dist/*.whl'))[-1]; names=zipfile.ZipFile(wheel).namelist(); required=['remote_agent_protocol/web_app/avatar/avatar-entry.js','remote_agent_protocol/web_app/assets/avatars/butler/metadata.json','remote_agent_protocol/web_app/vendor/three/three.module.min.js','remote_agent_protocol/web_app/vendor/three/addons/loaders/GLTFLoader.js','remote_agent_protocol/web_app/vendor/three/addons/utils/BufferGeometryUtils.js']; missing=[name for name in required if name not in names]; print('OK' if not missing else f'MISSING: {missing}'); raise SystemExit(bool(missing))"
```

Expected: `OK`.

- [ ] **Step 9: Manual end-to-end acceptance pass**

Run:

```powershell
.\.venv\Scripts\python.exe -m remote_agent_protocol
```

Verify all of the following in one session:

1. Butler appears and idles subtly.
2. Wake detection and user speech move to listening.
3. Transcribing and generation move to thinking.
4. Kokoro/Voicebox/Coqui/Cartesia TTS, when configured, moves the jaw from real amplitude.
5. TTS stop closes the mouth.
6. Agent activity produces focused state; completed work briefly appears pleased; failure appears concerned/error.
7. Settings persist after application restart.
8. Reduced motion suppresses breathing and head posture motion while retaining labels, blink, gaze, and minimal jaw movement.
9. Collapsing or disabling the avatar releases the canvas and SSE connection.
10. Blocking WebGL or corrupting a test metadata model path shows the static fallback without damaging the rest of the UI.
11. Existing microphone, persona, model, voice, Coqui, memory, wake-word, agent, diagnostics, restart, and shutdown controls still work.

- [ ] **Step 10: Commit**

```powershell
git add README.md docs\architecture.md CHANGELOG.md tests\test_web_gui.py; git commit -m "docs(avatar): document companion operation and verification"
```

---

## Plan Self-Review Results

- **Spec coverage:** Every approved requirement maps to Tasks 1–13, including persisted settings, real PCM amplitude, SSE, user-start telemetry, procedural rig, GLB fallback, expressions, gaze, idle motion, persona mapping, reduced motion, quality levels, accessibility, cleanup, packaging, and regression testing.
- **Dependency correction:** Vendoring includes `addons/utils/BufferGeometryUtils.js`, which unmodified `GLTFLoader.js` imports.
- **Type consistency:** Backend uses snake_case `AppState` fields and camelCase browser payloads through one converter. The browser runtime consistently receives `{ runtime, resolved, profile, settings }`.
- **Ownership consistency:** `WebVoiceApp` owns and closes one hub; `VoiceSession` and `AvatarAudioTap` only publish envelopes.
- **No unresolved values:** Three.js is pinned to `0.180.0`, publication is 20 Hz through a `0.05` second interval, SSE keepalive is 10 seconds, and reconnect backoff is bounded from 500 ms to 8 seconds.

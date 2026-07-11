from __future__ import annotations

import subprocess
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


avatar = Path("remote_agent_protocol/web_app/avatar")
(avatar / "lip-sync.js").write_text(
    '''import { clamp, damp } from "./math.js";

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
      try {
        this.onSample(JSON.parse(event.data));
      } catch (error) {
        console.warn("Invalid avatar envelope", error);
      }
    });
    source.onerror = () => {
      source.close();
      if (this.source === source) this.source = null;
      if (this.disposed) return;
      const delay = this.retryMs;
      this.retryMs = Math.min(8000, this.retryMs * 2);
      this.timer = this.setTimer(() => {
        this.timer = null;
        this.start();
      }, delay);
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
''',
    encoding="utf-8",
)

scene_path = avatar / "avatar-scene.js"
scene = scene_path.read_text(encoding="utf-8")
scene = replace_once(
    scene,
    'import { GazeController } from "./gaze-controller.js";\nimport { damp } from "./math.js";',
    'import { GazeController } from "./gaze-controller.js";\nimport { AvatarEnvelopeStream, LipSyncController } from "./lip-sync.js";\nimport { damp } from "./math.js";',
    "lip-sync import",
)
scene = replace_once(
    scene,
    '  const gazeController = new GazeController();\n  const currentTargets = Object.fromEntries(',
    '''  const gazeController = new GazeController();
  const lipSync = new LipSyncController();
  const stream = new AvatarEnvelopeStream((sample) => lipSync.ingest(sample));
  if (settings.lipSync) stream.start();
  const currentTargets = Object.fromEntries(''',
    "lip-sync controllers",
)
scene = replace_once(
    scene,
    '    if (latest) applyAvatarFrame(rig.controls, latest, time / 1000, delta, gazeController, currentTargets);',
    '    if (latest) applyAvatarFrame(rig.controls, latest, time / 1000, delta, gazeController, lipSync, currentTargets);',
    "frame call",
)
scene = replace_once(
    scene,
    '      renderer.shadowMap.enabled = value.settings.shadows;\n    },',
    '      renderer.shadowMap.enabled = value.settings.shadows;\n      if (value.settings.lipSync) stream.start();\n    },',
    "stream update",
)
scene = replace_once(
    scene,
    '      observer.disconnect();\n      rig.dispose();',
    '      observer.disconnect();\n      stream.dispose();\n      rig.dispose();',
    "stream disposal",
)
scene = replace_once(
    scene,
    'function applyAvatarFrame(controls, frame, seconds, delta, gazeController, currentTargets) {',
    'function applyAvatarFrame(controls, frame, seconds, delta, gazeController, lipSync, currentTargets) {',
    "frame signature",
)
scene = replace_once(
    scene,
    '''  controls.mouthUpper.scale.x = 1 + currentTargets.mouthWidth * 0.2;
  controls.mouthLower.scale.x = 1 + currentTargets.mouthWidth * 0.18;
  controls.cheekLeft.position.y = -0.04 + currentTargets.cheekRaise * 0.025;
  controls.cheekRight.position.y = -0.04 + currentTargets.cheekRaise * 0.025;
  controls.jaw.rotation.x = currentTargets.jawOpen * 0.18;
  controls.head.rotation.x = currentTargets.headPitch;''',
    '''  const mouth = lipSync.update(delta, frame.runtime.speaking, frame.settings.lipSync);
  controls.jaw.rotation.x = (currentTargets.jawOpen + mouth.jawOpen) * 0.22;
  controls.mouthLower.position.y = -0.035 - mouth.jawOpen * 0.06;
  controls.mouthUpper.scale.x = 1 + currentTargets.mouthWidth * 0.2 + mouth.mouthWidth;
  controls.mouthLower.scale.x = 1 + currentTargets.mouthWidth * 0.18 + mouth.mouthWidth * 0.8;
  controls.cheekLeft.position.y = -0.04 + currentTargets.cheekRaise * 0.025;
  controls.cheekRight.position.y = -0.04 + currentTargets.cheekRaise * 0.025;
  controls.cheekLeft.scale.y = 1 - mouth.cheek;
  controls.cheekRight.scale.y = 1 - mouth.cheek;
  controls.head.rotation.x = currentTargets.headPitch;''',
    "mouth application",
)
scene_path.write_text(scene, encoding="utf-8")

test_path = Path("tests/js/lip-sync.test.mjs")
test_path.parent.mkdir(parents=True, exist_ok=True)
test_path.write_text(
    '''import test from "node:test";
import assert from "node:assert/strict";
import { AvatarEnvelopeStream, LipSyncController } from "../../remote_agent_protocol/web_app/avatar/lip-sync.js";

test("RMS opens the jaw and release closes it", () => {
  let now = 1;
  const lip = new LipSyncController({ clock: () => now });
  lip.ingest({ rms: 0.5, peak: 0.8, voiced: true, timestamp: 1 });
  const open = lip.update(0.05, true, true);
  now = 2;
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
  const lip = new LipSyncController({ clock: () => 1 });
  lip.ingest({ rms: 0.8, peak: 1, voiced: true, timestamp: 1 });
  assert.deepEqual(lip.update(0.05, true, false), { jawOpen: 0, mouthWidth: 0, cheek: 0, usingEnvelope: false });
});

test("disposing the stream closes EventSource", () => {
  const closed = [];
  class FakeEventSource {
    constructor() {}
    close() { closed.push(true); }
  }
  const stream = new AvatarEnvelopeStream(() => {}, {
    EventSourceImpl: FakeEventSource,
    setTimer: () => 7,
    clearTimer: (id) => closed.push(id),
  });
  stream.start();
  stream.dispose();
  assert.deepEqual(closed, [true]);
});

test("stream reconnect uses bounded exponential backoff", () => {
  const delays = [];
  const sources = [];
  class FakeEventSource {
    constructor() { sources.push(this); }
    close() {}
  }
  const stream = new AvatarEnvelopeStream(() => {}, {
    EventSourceImpl: FakeEventSource,
    setTimer: (_callback, delay) => { delays.push(delay); return delay; },
  });
  stream.start();
  sources[0].onerror();
  assert.equal(delays[0], 500);
  assert.equal(stream.retryMs, 1000);
  stream.dispose();
});
''',
    encoding="utf-8",
)

subprocess.run(["node", "--test", str(test_path)], check=True)
Path(__file__).unlink()
subprocess.run(["git", "add", str(avatar), str(test_path), ".github/avatar_tasks/task10.py"], check=True)
subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
subprocess.run(["git", "commit", "-m", "feat(avatar): drive mouth motion from TTS amplitude"], check=True)
subprocess.run(["git", "push", "origin", "HEAD:feature/animated-butler-avatar"], check=True)
print("TASK 10 DONE: audio-driven lip-sync and managed SSE reconnect committed")

import test from "node:test";
import assert from "node:assert/strict";
import { GazeController } from "../../remote_agent_protocol/web_app/avatar/gaze-controller.js";

// A deterministic random source that replays a fixed sequence.
const sequence = (values) => {
  let index = 0;
  return () => values[index++ % values.length];
};

test("blink timing is deterministic with an injected random", () => {
  const build = () => new GazeController({ random: sequence([0.5, 0.2, 0.9, 0.4]) });
  const run = (gaze) => {
    const blinks = [];
    for (let i = 0; i < 600; i += 1) {
      const result = gaze.update(1 / 60, "idle", true, false, {});
      if (result.blink > 0) blinks.push(i);
    }
    return blinks;
  };
  assert.deepEqual(run(build()), run(build()));
});

test("double blink queues a quick second blink when chance hits", () => {
  const gaze = new GazeController({ random: () => 0 });
  // random()=0 -> doubleBlinkChance must exceed 0 to trigger; with chance 1 the
  // second blink follows ~0.28s after the first completes.
  const profile = { doubleBlinkChance: 1, blinkIntervalSeconds: [1, 1] };
  const blinkStarts = [];
  let wasBlinking = false;
  for (let i = 0; i < 240; i += 1) {
    const result = gaze.update(1 / 60, "idle", true, false, profile);
    const blinking = result.blink > 0;
    if (blinking && !wasBlinking) blinkStarts.push(i / 60);
    wasBlinking = blinking;
  }
  assert.ok(blinkStarts.length >= 2, "expected at least two blinks");
  assert.ok(blinkStarts[1] - blinkStarts[0] < 0.6, `double blink too late: ${blinkStarts[1] - blinkStarts[0]}s`);
});

test("thinking gaze drifts upward and to the side", () => {
  const gaze = new GazeController({ random: () => 0.99 });
  const result = gaze.update(10, "thinking", true, false, {});
  assert.ok(result.y > 0, `thinking should look up, got y=${result.y}`);
  assert.ok(Math.abs(result.x) > 0.05, "thinking should glance sideways");
});

test("listening gaze remains close to camera and reduces saccades", () => {
  const gaze = new GazeController({ random: () => 0.5 });
  const result = gaze.update(0.016, "listening", true, false, {});
  assert.equal(result.enabled, true);
  assert.ok(Math.abs(result.x) <= 0.05);
  assert.ok(Math.abs(result.y) <= 0.05);
});

test("reduced motion keeps blink but suppresses large gaze offsets", () => {
  const gaze = new GazeController({ random: () => 1 });
  const result = gaze.update(8, "thinking", true, true, {});
  assert.ok(Math.abs(result.x) <= 0.06);
  assert.ok(Math.abs(result.y) <= 0.06);
});

test("blink phase is exposed without breaking legacy callers", () => {
  const gaze = new GazeController({ random: () => 0.01 });
  let seenPhase = 0;
  for (let i = 0; i < 300; i += 1) {
    const result = gaze.update(1 / 60, "idle", true, false);
    assert.ok(result.blinkPhase >= 0 && result.blinkPhase <= 1);
    seenPhase = Math.max(seenPhase, result.blinkPhase);
  }
  assert.ok(seenPhase > 0.5, "blink phase should progress during a blink");
});

test("disabled gaze returns a neutral frame", () => {
  const gaze = new GazeController({ random: () => 0.5 });
  assert.deepEqual(gaze.update(0.016, "idle", false, false), {
    enabled: false, x: 0, y: 0, blink: 0, blinkPhase: 0,
  });
});

import test from "node:test";
import assert from "node:assert/strict";
import { GlitchScheduler, GLITCH_TYPES } from "../../remote_agent_protocol/web_app/avatar/butler-glitch.js";

const sequence = (values) => {
  let index = 0;
  return () => values[index++ % values.length];
};

const countEvents = (scheduler, seconds, delta = 1 / 30) => {
  const seen = [];
  let previous = null;
  for (let t = 0; t < seconds; t += delta) {
    const frame = scheduler.update(delta);
    if (frame.type && frame.type !== previous) seen.push(frame.type);
    previous = frame.type;
  }
  return seen;
};

test("schedule is deterministic for a fixed random sequence", () => {
  const build = () => new GlitchScheduler({ random: sequence([0.3, 0.8, 0.1, 0.6, 0.45]) });
  const a = countEvents(build(), 60);
  const b = countEvents(build(), 60);
  assert.deepEqual(a, b);
  assert.ok(a.length > 2, "expected several events over a minute");
});

test("error state glitches far more often than idle", () => {
  const idle = new GlitchScheduler({ random: sequence([0.5, 0.2, 0.8, 0.4]) });
  idle.setState("idle");
  const errored = new GlitchScheduler({ random: sequence([0.5, 0.2, 0.8, 0.4]) });
  errored.setState("error");
  const idleEvents = countEvents(idle, 40).length;
  const errorEvents = countEvents(errored, 40).length;
  assert.ok(errorEvents > idleEvents * 2, `error=${errorEvents} idle=${idleEvents}`);
});

test("sleeping is almost glitch-free", () => {
  const scheduler = new GlitchScheduler({ random: sequence([0.5, 0.5, 0.5]) });
  scheduler.setState("sleeping");
  const events = countEvents(scheduler, 30);
  assert.ok(events.length <= 2, `sleeping glitched ${events.length} times in 30s`);
});

test("reduced motion downgrades every event to micro-flicker with no displacement", () => {
  const scheduler = new GlitchScheduler({ random: sequence([0.9, 0.1, 0.7, 0.3]) });
  scheduler.setState("error");
  scheduler.setReducedMotion(true);
  for (let t = 0; t < 30; t += 1 / 30) {
    const frame = scheduler.update(1 / 30);
    if (frame.type) {
      assert.equal(frame.type, "micro-flicker");
      assert.equal(frame.offsetX, 0);
      assert.equal(frame.bands.length, 0);
      assert.equal(frame.channel, 0);
      assert.equal(frame.fragments, 0);
    }
  }
});

test("low quality never emits channel splits, scatters, or fragments", () => {
  const scheduler = new GlitchScheduler({ random: sequence([0.99, 0.01, 0.6, 0.35, 0.77]), quality: "low" });
  scheduler.setState("error");
  const events = countEvents(scheduler, 60);
  for (const type of events) {
    assert.ok(!["channel-split", "point-scatter", "data-fragment"].includes(type), `low quality emitted ${type}`);
  }
});

test("manual trigger honors type, duration bounds, and strength clamp", () => {
  const scheduler = new GlitchScheduler({ random: () => 0.5 });
  assert.equal(scheduler.trigger("nonsense"), false);
  assert.equal(scheduler.trigger("horizontal-tear", 9), true);
  assert.equal(scheduler.activeType, "horizontal-tear");
  const frame = scheduler.update(0.01);
  assert.equal(frame.type, "horizontal-tear");
  assert.ok(frame.strength <= 1.5);
  assert.ok(frame.bands.length >= 1);
  // Duration midpoint of [0.08, 0.22] at random()=0.5 is 0.15s; the event must
  // finish by then.
  let last = frame;
  for (let t = 0; t < 0.3; t += 0.01) last = scheduler.update(0.01);
  assert.equal(last.type, null);
});

test("reconstruction reveals the figure from bottom to top", () => {
  const scheduler = new GlitchScheduler({ random: () => 0.5 });
  scheduler.trigger("reconstruction", 1);
  const first = scheduler.update(0.05);
  assert.equal(first.type, "reconstruction");
  assert.ok(first.reveal < 0.4);
  let last = first;
  for (let t = 0; t < 2; t += 0.05) last = scheduler.update(0.05);
  assert.equal(last.reveal, 1);
});

test("disabling stops events and clears the active glitch", () => {
  const scheduler = new GlitchScheduler({ random: () => 0.5 });
  scheduler.trigger("monocle-desync", 1);
  scheduler.setEnabled(false);
  const frame = scheduler.update(0.05);
  assert.equal(frame.type, null);
  assert.equal(frame.monocle, 0);
  assert.equal(scheduler.activeType, null);
});

test("exports the full glitch vocabulary", () => {
  for (const type of [
    "micro-flicker", "horizontal-tear", "channel-split", "transmission-dropout",
    "point-scatter", "monocle-desync", "data-fragment", "reconstruction",
  ]) {
    assert.ok(GLITCH_TYPES.includes(type), `missing ${type}`);
  }
});

import test from "node:test";
import assert from "node:assert/strict";
import {
  FRAME_NAMES,
  createAvatarScene,
  criticalFrameNames,
  frameForLevel,
  stateForResolved,
  frameForState,
  frameUrls,
} from "../../remote_agent_protocol/web_app/avatar/frame-avatar-scene.js";


test("ported Butler frame inventory includes expressions, visemes, and effects", () => {
  for (const name of [
    "base", "halfsmile", "smile", "confused", "lookup", "lookdown", "glow_eyes",
    "ah_small", "e_sound", "oh", "oo", "open", "grit", "fv",
    "eyes_mid_close", "eyes_closed", "eyes_closed_smile",
    "materialize_01", "materialize_13", "glitch_01", "glitch_07",
  ]) assert.ok(FRAME_NAMES.includes(name), name);
  assert.equal(new Set(FRAME_NAMES).size, FRAME_NAMES.length);
});


test("cold load is limited to common state and speech frames", () => {
  const critical = criticalFrameNames();

  assert.ok(critical.length < FRAME_NAMES.length / 2);
  for (const required of [
    "base", "eyes_closed", "lookup", "confused", "glow_eyes", "open", "oh", "e_sound", "ah_small",
  ]) assert.ok(critical.includes(required), required);
  assert.equal(critical.some((name) => name.startsWith("glitch_")), false);
  assert.equal(critical.some((name) => name.startsWith("materialize_")), false);
});


test("audio envelope levels use the source avatar viseme thresholds", () => {
  assert.equal(frameForLevel(0), "base");
  assert.equal(frameForLevel(0.054), "base");
  assert.equal(frameForLevel(0.055), "halfsmile");
  assert.equal(frameForLevel(0.2), "ah_small");
  assert.equal(frameForLevel(0.36), "e_sound");
  assert.equal(frameForLevel(0.52), "oh");
  assert.equal(frameForLevel(0.7), "open");
  assert.equal(frameForLevel(99), "open");
});


test("RAP controller states map onto reworked Butler states", () => {
  const expected = {
    idle: "idle", passive: "idle", sleeping: "sleeping", listening: "listening",
    transcribing: "thinking", thinking: "thinking", focused: "working",
    concerned: "waiting", happy: "completed", error: "failed", disconnected: "failed",
    speaking: "speaking",
  };
  for (const [input, output] of Object.entries(expected)) {
    assert.equal(stateForResolved(input), output, input);
  }
  assert.equal(frameForState("working"), "glow_eyes");
  assert.equal(frameForState("sleeping"), "eyes_closed");
});


function fakeBrowser(t, { failFrame = "" } = {}) {
  const original = {
    document: globalThis.document,
    Image: globalThis.Image,
    EventSource: globalThis.EventSource,
    requestAnimationFrame: globalThis.requestAnimationFrame,
    cancelAnimationFrame: globalThis.cancelAnimationFrame,
  };
  const context = {
    save() {}, restore() {}, translate() {}, rotate() {}, scale() {},
    drawImage() {}, clearRect() {},
  };
  const canvas = {
    width: 0, height: 0, removed: false,
    setAttribute() {}, getContext: () => context, remove() { this.removed = true; },
  };
  globalThis.document = {
    hidden: false,
    createElement: (name) => {
      assert.equal(name, "canvas");
      return canvas;
    },
  };
  globalThis.EventSource = class { close() {} };
  globalThis.Image = class {
    set src(value) {
      this.value = value;
      queueMicrotask(() => failFrame && value.includes(failFrame) ? this.onerror?.() : this.onload?.());
    }
  };
  globalThis.requestAnimationFrame = () => 1;
  globalThis.cancelAnimationFrame = () => {};
  t.after(() => Object.assign(globalThis, original));
  return canvas;
}

function fakeHost() {
  const classes = new Set();
  return {
    child: null,
    classList: { add: (value) => classes.add(value), remove: (value) => classes.delete(value) },
    replaceChildren(value) { this.child = value; },
    hasClass: (value) => classes.has(value),
  };
}


test("scene creates, updates, and disposes its canvas lifecycle", async (t) => {
  const canvas = fakeBrowser(t);
  const host = fakeHost();
  const settings = { lipSync: false, effectiveReducedMotion: true };
  const scene = await createAvatarScene(host, settings);

  assert.equal(host.child, canvas);
  assert.equal(host.hasClass("avatar-frame-butler"), true);
  scene.update({ runtime: {}, resolved: { state: "focused" }, settings });
  assert.equal(scene.debug.getDiagnostics().state, "working");

  scene.dispose();
  assert.equal(canvas.removed, true);
  assert.equal(host.hasClass("avatar-frame-butler"), false);
});


test("critical frame failure rejects without leaving canvas mutations", async (t) => {
  const canvas = fakeBrowser(t, { failFrame: "base.webp" });
  const host = fakeHost();
  await assert.rejects(
    createAvatarScene(host, { lipSync: false, effectiveReducedMotion: true }),
    /Unable to load Butler frame: base/,
  );
  assert.equal(host.child, null);
  assert.equal(host.hasClass("avatar-frame-butler"), false);
  assert.equal(canvas.removed, false);
});


test("all frame URLs are same-origin RAP assets", () => {
  const urls = frameUrls();

  assert.deepEqual(Object.keys(urls), FRAME_NAMES);
  assert.ok(Object.values(urls).every((url) => url.startsWith("/assets/avatars/butler/runtime_512_v1/")));
  assert.ok(Object.values(urls).every((url) => !url.includes("4188")));
});

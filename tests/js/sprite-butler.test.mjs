import test from "node:test";
import assert from "node:assert/strict";
import * as THREE from "../../remote_agent_protocol/web_app/vendor/three/three.module.min.js";
import { createSpriteButler, spriteFrameFor } from "../../remote_agent_protocol/web_app/avatar/sprite-butler.js";

test("sleep and blink use the reviewed closed-eye cells", () => {
  assert.deepEqual(spriteFrameFor({ state: "sleeping" }), {
    animation: "sleep", sheet: 2, column: 3, row: 2, index: 11,
  });
  assert.equal(spriteFrameFor({ lidScale: 0.4 }).index, 10);
  assert.equal(spriteFrameFor({ lidScale: 0.1 }).index, 11);
});

test("glow remains coherent while idle skips wink and expression cells", () => {
  const frames = Array.from({ length: 16 }, (_, index) => spriteFrameFor({
    state: "focused", seconds: index / 6,
  }));
  assert.deepEqual(frames.map((frame) => frame.index), Array.from({ length: 16 }, (_, index) => index));
  assert.ok(frames.every((frame) => frame.sheet === 1));
  const idle = Array.from({ length: 12 }, (_, index) => spriteFrameFor({ seconds: index / 2 }));
  assert.ok(idle.every((frame) => frame.sheet === 0 && ![8, 10, 11].includes(frame.index)));
});

test("speech uses only the neutral and three mouth cells from the full sheet", () => {
  assert.equal(spriteFrameFor({ speaking: true }).index, 0);
  assert.equal(spriteFrameFor({ speaking: true, mouth: { jawOpen: 0.5, closure: 0 } }).index, 12);
  assert.equal(spriteFrameFor({ speaking: true, mouth: {
    jawOpen: 0.5, closure: 0, mouthWidth: 0.2,
  } }).index, 13);
  assert.equal(spriteFrameFor({ speaking: true, mouth: {
    jawOpen: 0.5, closure: 0, roundness: 0.4,
  } }).index, 14);
});

test("final frame pack uses supplied materialize, pose, speech, material, and glitch art", () => {
  const final = (values) => spriteFrameFor({ pack: "final-frames", ...values });
  assert.deepEqual(final({ age: 0 }), {
    animation: "materialize", sheet: 2, column: 0, row: 0, index: 0,
  });
  assert.equal(final({ age: 1.6 }).index, 12);
  assert.equal(final({ state: "sleeping" }).index, 2);
  assert.equal(final({ emotion: "happy" }).index, 11);
  assert.equal(final({ emotion: "confused" }).index, 1);
  assert.equal(final({ speaking: true, mouth: {
    jawOpen: 0.5, closure: 0, roundness: 0.4,
  } }).index, 10);
  assert.deepEqual(final({ state: "focused", seconds: 0.5 }), {
    animation: "material", sheet: 1, column: 2, row: 0, index: 2,
  });
  assert.equal(final({ state: "transcribing", emotion: "thinking" }).animation, "material");
  assert.equal(final({ glitch: "horizontal-tear", seconds: 0.5 }).animation, "glitch");
  assert.ok(Array.from({ length: 20 }, (_, seconds) => final({ seconds })).every(
    (frame) => frame.animation === "idle" && frame.index === 0,
  ));
});

test("final frame pack preserves source colors and fits the full portrait", async () => {
  const texture = new THREE.Texture();
  const rig = await createSpriteButler(THREE, {
    sprites: { pack: "final-frames", sheets: ["states.png", "effects.png", "materialize.png"] },
    loadTexture: async () => texture,
  });
  const portrait = rig.object.getObjectByName("spritePortrait");
  assert.equal(texture.colorSpace, THREE.NoColorSpace);
  assert.equal(portrait.geometry.parameters.width, 1.48);
  assert.doesNotMatch(portrait.material.fragmentShader, /pow\(max\(color/);
  rig.dispose();
});

test("sprite rig changes cells, glitches, and disposes its sheet", async () => {
  const texture = new THREE.Texture();
  const loaded = [];
  const dispose = texture.dispose.bind(texture);
  texture.disposed = false;
  texture.dispose = () => { texture.disposed = true; dispose(); };
  const rig = await createSpriteButler(THREE, { loadTexture: async (url) => { loaded.push(url); return texture; } });
  assert.deepEqual(loaded, [
    "/assets/avatars/butler/sheet_idle.png",
    "/assets/avatars/butler/sheet_glow.png",
    "/assets/avatars/butler/sheet_full.png",
  ]);
  assert.equal(texture.colorSpace, THREE.SRGBColorSpace);
  assert.equal(rig.getDiagnostics().animation, "idle");
  rig.applyState({ resolved: { state: "idle", emotion: { name: "neutral" } }, runtime: {} });
  assert.equal(rig.getDiagnostics().sheet, 0);
  rig.applyState({ resolved: { state: "focused", emotion: { name: "neutral" } }, runtime: {} });
  assert.equal(rig.getDiagnostics().sheet, 1);
  rig.applyState({ resolved: { state: "happy", emotion: { name: "happy" } }, runtime: {} });
  assert.equal(rig.getDiagnostics().sheet, 2);
  rig.applyState({ resolved: { state: "sleeping", emotion: { name: "neutral" } }, runtime: {} });
  assert.equal(rig.getDiagnostics().index, 11);
  assert.equal(rig.triggerGlitch("channel-split"), true);
  rig.update(0.05);
  assert.equal(rig.getDiagnostics().glitch, "channel-split");
  rig.dispose();
  assert.equal(texture.disposed, true);
});

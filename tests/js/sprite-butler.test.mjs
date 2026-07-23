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

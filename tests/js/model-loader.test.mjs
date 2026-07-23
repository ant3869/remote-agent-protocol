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
  assert.equal(resolveAvatarPlan({ model: "/a.glb" }, "/assets/avatars/butler/").kind, "procedural");
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

test("successful load discovers named controls and disposes assets", async () => {
  const disposed = [];
  const jaw = { name: "JawOpen" };
  const root = {
    children: [],
    traverse(callback) { callback(this); callback(jaw); },
    clear() { disposed.push("clear"); },
  };
  const result = await loadAvatarModel({
    metadata: { model: "butler.glb", controls: { jaw: ["JawOpen"] } },
    baseUrl: "/assets/avatars/butler/",
    loadGltf: async () => ({ scene: root, animations: [] }),
  });
  assert.equal(result.kind, "gltf");
  assert.equal(result.controls.jaw, jaw);
  result.dispose();
  assert.deepEqual(disposed, ["clear"]);
});


test("morph aliases are discovered and malformed alias values are ignored", async () => {
  const morph = { name: "Face", morphTargetDictionary: { jawOpen: 0 }, morphTargetInfluences: [0] };
  const root = {
    traverse(callback) { callback(this); callback(morph); },
    clear() {},
  };
  const result = await loadAvatarModel({
    metadata: { model: "butler.glb", controls: { jaw: ["jawOpen"], blinkLeft: "bad" } },
    baseUrl: "/assets/avatars/butler/",
    loadGltf: async () => ({ scene: root, animations: [] }),
  });
  assert.equal(result.controls.jaw.kind, "morph");
  assert.equal(result.controls.jaw.index, 0);
  assert.equal(result.controls.blinkLeft, null);
});

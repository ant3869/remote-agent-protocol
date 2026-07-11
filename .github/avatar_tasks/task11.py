from __future__ import annotations

import subprocess
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


avatar = Path("remote_agent_protocol/web_app/avatar")
(avatar / "model-loader.js").write_text(
    '''export function resolveAvatarPlan(metadata = {}, baseUrl = "/") {
  const model = metadata.model;
  if (
    typeof model !== "string" ||
    !model ||
    model.includes("..") ||
    /^[a-z]+:/i.test(model) ||
    model.startsWith("/") ||
    !/\\.(?:glb|gltf)$/i.test(model)
  ) {
    return { kind: "procedural" };
  }
  const cleanBase = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
  return { kind: "gltf", url: `${cleanBase}${model}`.replace(/\\/{2,}/g, "/") };
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
      for (const [name, index] of Object.entries(object.morphTargetDictionary)) {
        morphs.set(name, { object, index, kind: "morph" });
      }
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
    const materials = Array.isArray(object.material)
      ? object.material
      : object.material ? [object.material] : [];
    for (const material of materials) {
      for (const value of Object.values(material)) if (value?.isTexture) value.dispose();
      material.dispose?.();
    }
  });
  root.clear();
}
''',
    encoding="utf-8",
)

scene_path = avatar / "avatar-scene.js"
scene = scene_path.read_text(encoding="utf-8")
scene = replace_once(
    scene,
    'import { damp } from "./math.js";\nimport { createProceduralButler } from "./procedural-butler.js";',
    'import { damp } from "./math.js";\nimport { loadAvatarModel } from "./model-loader.js";\nimport { createProceduralButler } from "./procedural-butler.js";',
    "model loader import",
)
scene = replace_once(
    scene,
    '''  const rig = createProceduralButler(THREE);
  scene.add(rig.object);
  const gazeController = new GazeController();''',
    '''  const avatarBase = `/assets/avatars/${settings.avatarId}/`;
  let metadata = { model: null, fallback: "procedural-butler", scale: 1 };
  let loaded = { kind: "procedural" };
  try {
    const response = await fetch(`${avatarBase}metadata.json`, { cache: "no-cache" });
    if (!response.ok) throw new Error(`Avatar metadata HTTP ${response.status}`);
    metadata = await response.json();
    loaded = await loadAvatarModel({
      metadata,
      baseUrl: avatarBase,
      loadGltf: async (url) => {
        const { GLTFLoader } = await import("three/addons/loaders/GLTFLoader.js");
        return new GLTFLoader().loadAsync(url);
      },
    });
  } catch (error) {
    console.warn("Avatar metadata unavailable; using procedural fallback", error);
  }
  const rig = loaded.kind === "gltf"
    ? adaptLoadedRig(loaded, metadata, THREE)
    : createProceduralButler(THREE);
  scene.add(rig.object);
  const gazeController = new GazeController();''',
    "model selection",
)
scene += '''

function adaptLoadedRig(loaded, metadata, THREE) {
  const safe = () => new THREE.Object3D();
  const objectControl = (value) => value?.isObject3D ? value : null;
  loaded.object.scale.setScalar(Number.isFinite(metadata.scale) ? metadata.scale : 1);
  const controls = {
    root: loaded.object,
    bust: objectControl(loaded.controls.bust) || safe(),
    neck: objectControl(loaded.controls.neck) || safe(),
    head: objectControl(loaded.controls.head) || safe(),
    jaw: objectControl(loaded.controls.jaw) || safe(),
    mouthUpper: objectControl(loaded.controls.mouthUpper) || safe(),
    mouthLower: objectControl(loaded.controls.mouthLower) || safe(),
    mouthCornerLeft: objectControl(loaded.controls.mouthCornerLeft) || safe(),
    mouthCornerRight: objectControl(loaded.controls.mouthCornerRight) || safe(),
    cheekLeft: objectControl(loaded.controls.cheekLeft) || safe(),
    cheekRight: objectControl(loaded.controls.cheekRight) || safe(),
    browLeft: objectControl(loaded.controls.browLeft) || safe(),
    browRight: objectControl(loaded.controls.browRight) || safe(),
    eyeLeft: objectControl(loaded.controls.eyeLeft) || safe(),
    eyeRight: objectControl(loaded.controls.eyeRight) || safe(),
    pupilLeft: objectControl(loaded.controls.pupilLeft) || safe(),
    pupilRight: objectControl(loaded.controls.pupilRight) || safe(),
    lidLeft: objectControl(loaded.controls.blinkLeft) || safe(),
    lidRight: objectControl(loaded.controls.blinkRight) || safe(),
  };
  return { object: loaded.object, controls, dispose: loaded.dispose };
}
'''
scene_path.write_text(scene, encoding="utf-8")

test_path = Path("tests/js/model-loader.test.mjs")
test_path.write_text(
    '''import test from "node:test";
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
''',
    encoding="utf-8",
)

subprocess.run(["node", "--test", str(test_path)], check=True)
Path(__file__).unlink()
subprocess.run(["git", "add", str(avatar), str(test_path), ".github/avatar_tasks/task11.py"], check=True)
subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
subprocess.run(["git", "commit", "-m", "feat(avatar): support local GLB models with fallback"], check=True)
subprocess.run(["git", "pull", "--rebase", "origin", "feature/animated-butler-avatar"], check=True)
subprocess.run(["git", "push", "origin", "HEAD:feature/animated-butler-avatar"], check=True)
print("TASK 11 DONE: safe local GLB loading and procedural fallback committed")

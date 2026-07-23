export function resolveAvatarPlan(metadata = {}, baseUrl = "/") {
  const model = metadata.model;
  if (
    typeof model !== "string" ||
    !model ||
    model.includes("..") ||
    /^[a-z]+:/i.test(model) ||
    model.startsWith("/") ||
    !/\.(?:glb|gltf)$/i.test(model)
  ) {
    return { kind: "procedural" };
  }
  const cleanBase = baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`;
  return { kind: "gltf", url: `${cleanBase}${model}`.replace(/\/{2,}/g, "/") };
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

const CONVENTIONAL_CONTROLS = Object.freeze({
  root: ["Armature", "Root", "root"],
  bust: ["Bust", "Chest", "Spine2", "spine_02"],
  neck: ["Neck", "neck"],
  head: ["Head", "head"],
  jaw: ["Jaw", "jaw", "jawOpen", "JawOpen"],
  mouthUpper: ["UpperLip", "mouthUpper"],
  mouthLower: ["LowerLip", "mouthLower"],
  browLeft: ["BrowLeft", "browLeft"],
  browRight: ["BrowRight", "browRight"],
  eyeLeft: ["EyeLeft", "LeftEye", "eyeLeft"],
  eyeRight: ["EyeRight", "RightEye", "eyeRight"],
  blinkLeft: ["eyeBlinkLeft", "Blink_L", "blinkLeft"],
  blinkRight: ["eyeBlinkRight", "Blink_R", "blinkRight"],
  smileLeft: ["mouthSmileLeft", "Smile_L"],
  smileRight: ["mouthSmileRight", "Smile_R"],
  frownLeft: ["mouthFrownLeft", "Frown_L"],
  frownRight: ["mouthFrownRight", "Frown_R"],
  browInnerUp: ["browInnerUp"],
  browDownLeft: ["browDownLeft"],
  browDownRight: ["browDownRight"],
  eyeWideLeft: ["eyeWideLeft"],
  eyeWideRight: ["eyeWideRight"],
});

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
  const keys = new Set([...Object.keys(CONVENTIONAL_CONTROLS), ...Object.keys(aliases || {})]);
  for (const key of keys) {
    const configured = Array.isArray(aliases?.[key]) ? aliases[key] : [];
    const candidates = [...configured, ...(CONVENTIONAL_CONTROLS[key] || [])];
    result[key] = candidates.map((name) => named.get(name) || morphs.get(name)).find(Boolean) || null;
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

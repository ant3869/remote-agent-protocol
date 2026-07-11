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

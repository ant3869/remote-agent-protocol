import * as THREE from "three";
import { expressionFor, blendTargets } from "./expressions.js";
import { GazeController } from "./gaze-controller.js";
import { AvatarEnvelopeStream, LipSyncController } from "./lip-sync.js";
import { damp } from "./math.js";
import { loadAvatarModel } from "./model-loader.js";
import { createProceduralButler } from "./procedural-butler.js";

export async function createAvatarScene(host, settings) {
  if (!host) throw new Error("Avatar canvas host is missing");
  const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: settings.antialias, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, settings.maxPixelRatio));
  renderer.shadowMap.enabled = settings.shadows;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  host.replaceChildren(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 20);
  camera.position.set(0, 1.35, 4.4);
  camera.lookAt(0, 1.15, 0);

  const key = new THREE.DirectionalLight(0xffffff, 2.3);
  key.position.set(2.5, 3.2, 3.5);
  key.castShadow = settings.shadows;
  const fill = new THREE.DirectionalLight(0x8aa0c4, 1.1);
  fill.position.set(-2.5, 1.8, 2.5);
  const rim = new THREE.DirectionalLight(0xa78bfa, 0.75);
  rim.position.set(0, 2.5, -2.5);
  const ambient = new THREE.HemisphereLight(0xb9c6dc, 0x111113, 0.9);
  scene.add(key, fill, rim, ambient);

  const avatarBase = `/assets/avatars/${settings.avatarId}/`;
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
  const gazeController = new GazeController();
  const lipSync = new LipSyncController();
  const stream = new AvatarEnvelopeStream((sample) => lipSync.ingest(sample));
  if (settings.lipSync) stream.start();
  const currentTargets = Object.fromEntries(
    Object.keys(expressionFor("neutral")).map((keyName) => [keyName, 0]),
  );

  let visible = true;
  let disposed = false;
  let lastFrame = 0;
  let lastAnimatedAt = 0;
  let latest = null;
  let targetInterval = 1000 / settings.targetFps;

  const resize = () => {
    const width = Math.max(1, host.clientWidth);
    const height = Math.max(1, host.clientHeight);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  };
  const observer = new ResizeObserver(resize);
  observer.observe(host);
  resize();

  const animate = (time) => {
    if (disposed) return;
    requestAnimationFrame(animate);
    if (!visible || document.hidden || time - lastFrame < targetInterval) return;
    const delta = Math.min(0.1, Math.max(0.001, (time - (lastAnimatedAt || time - 16)) / 1000));
    lastAnimatedAt = time;
    lastFrame = time;
    if (latest) applyAvatarFrame(rig.controls, latest, time / 1000, delta, gazeController, lipSync, currentTargets);
    renderer.render(scene, camera);
  };
  requestAnimationFrame(animate);

  return {
    update(value) {
      latest = value;
      targetInterval = 1000 / value.settings.targetFps;
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, value.settings.maxPixelRatio));
      renderer.shadowMap.enabled = value.settings.shadows;
      if (value.settings.lipSync) stream.start();
    },
    setVisible(value) { visible = Boolean(value); },
    dispose() {
      if (disposed) return;
      disposed = true;
      observer.disconnect();
      stream.dispose();
      rig.dispose();
      scene.clear();
      renderer.renderLists.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
      renderer.domElement.remove();
    },
  };
}

function applyAvatarFrame(controls, frame, seconds, delta, gazeController, lipSync, currentTargets) {
  const expression = expressionFor(frame.resolved.emotion.name);
  const base = expressionFor(frame.profile.defaultExpression);
  const amount = frame.resolved.emotion.intensity * frame.settings.expressionIntensity;
  const target = blendTargets(base, expression, amount);
  for (const key of Object.keys(currentTargets)) {
    currentTargets[key] = damp(currentTargets[key], target[key], 8, delta);
  }

  const gaze = gazeController.update(
    delta,
    frame.resolved.state,
    frame.settings.gaze,
    frame.settings.effectiveReducedMotion,
  );
  controls.pupilLeft.position.x = gaze.x;
  controls.pupilRight.position.x = gaze.x;
  controls.pupilLeft.position.y = gaze.y;
  controls.pupilRight.position.y = gaze.y;
  controls.lidLeft.scale.y = Math.max(0.04, 0.74 * (1 - gaze.blink));
  controls.lidRight.scale.y = Math.max(0.04, 0.74 * (1 - gaze.blink * 0.96));
  controls.browLeft.position.y = 0.22 + currentTargets.browInner * 0.035 + currentTargets.browAsymmetry * 0.02;
  controls.browRight.position.y = 0.22 + currentTargets.browInner * 0.035 - currentTargets.browAsymmetry * 0.02;
  controls.browLeft.rotation.z = currentTargets.browOuter * -0.15;
  controls.browRight.rotation.z = currentTargets.browOuter * 0.15;
  controls.mouthCornerLeft.position.y = -0.01 + currentTargets.mouthCorner * 0.035;
  controls.mouthCornerRight.position.y = -0.01 + currentTargets.mouthCorner * 0.035;
  const mouth = lipSync.update(delta, frame.runtime.speaking, frame.settings.lipSync);
  controls.jaw.rotation.x = (currentTargets.jawOpen + mouth.jawOpen) * 0.22;
  controls.mouthLower.position.y = -0.035 - mouth.jawOpen * 0.06;
  controls.mouthUpper.scale.x = 1 + currentTargets.mouthWidth * 0.2 + mouth.mouthWidth;
  controls.mouthLower.scale.x = 1 + currentTargets.mouthWidth * 0.18 + mouth.mouthWidth * 0.8;
  controls.cheekLeft.position.y = -0.04 + currentTargets.cheekRaise * 0.025;
  controls.cheekRight.position.y = -0.04 + currentTargets.cheekRaise * 0.025;
  controls.cheekLeft.scale.y = 1 - mouth.cheek;
  controls.cheekRight.scale.y = 1 - mouth.cheek;
  controls.head.rotation.x = currentTargets.headPitch;
  controls.head.rotation.y = currentTargets.headYaw;
  controls.head.rotation.z = currentTargets.headRoll;

  const canIdle = frame.settings.idleMotion && !frame.settings.effectiveReducedMotion;
  const breathing = canIdle ? Math.sin(seconds * 1.35) * 0.008 * frame.profile.idleIntensity : 0;
  const stabilization = canIdle ? Math.sin(seconds * 0.41) * 0.006 * frame.profile.idleIntensity : 0;
  controls.bust.scale.y = 0.72 + breathing;
  controls.root.rotation.y = stabilization;
}


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

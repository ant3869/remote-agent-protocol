import * as THREE from "three";
import { expressionFor, blendTargets } from "./expressions.js";
import { GazeController } from "./gaze-controller.js";
import { AvatarEnvelopeStream, LipSyncController } from "./lip-sync.js";
import { damp } from "./math.js";
import { loadAvatarModel } from "./model-loader.js";
import { createProceduralButler } from "./procedural-butler.js";
import { createProceduralVisage } from "./procedural-visage.js";

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

  // Lighting serves GLTF models and the conventional procedural butler; the
  // holographic visage is emissive and ignores it.
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
  let metadata = { model: null, fallback: "procedural-visage", scale: 1 };
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
  const cameraTarget = Array.isArray(metadata.cameraTarget) && metadata.cameraTarget.length === 3
    ? metadata.cameraTarget.map(Number)
    : [0, 1.15, 0];
  const target = cameraTarget.every(Number.isFinite)
    ? new THREE.Vector3(...cameraTarget)
    : new THREE.Vector3(0, 1.15, 0);
  const rig = loaded.kind === "gltf"
    ? adaptLoadedRig(loaded, metadata, THREE)
    : metadata.fallback === "procedural-butler"
      ? createProceduralButler(THREE)
      : createProceduralVisage(THREE, {
        quality: settings.quality,
        reducedMotion: settings.effectiveReducedMotion,
      });
  scene.add(rig.object);
  if (rig.hostClass) host.classList.add(rig.hostClass);
  rig.setQuality?.(settings.quality);
  rig.setReducedMotion?.(settings.effectiveReducedMotion);
  const gazeController = new GazeController();
  const lipSync = new LipSyncController();
  const stream = new AvatarEnvelopeStream((sample) => lipSync.ingest(sample));
  if (settings.lipSync) stream.start();
  const currentTargets = Object.fromEntries(
    Object.keys(expressionFor("neutral")).map((keyName) => [keyName, 0]),
  );

  let visible = true;
  let disposed = false;
  let attemptedContextRestore = false;
  let animationFrame = 0;
  let lastFrame = 0;
  let lastAnimatedAt = 0;
  let latest = null;
  let targetInterval = 1000 / settings.targetFps;

  // Debug overrides (driven through window.remoteAgentAvatar.debug).
  const debugState = {
    state: null, emotion: null, intensity: 0.8, speaking: null,
    audioLevel: null, lookTarget: null, reducedMotion: null,
  };

  const resize = () => {
    const width = Math.max(1, host.clientWidth);
    const height = Math.max(1, host.clientHeight);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    // Responsive portrait framing: tall hosts pull in for head-and-shoulders,
    // wide hosts back off to include lapels and the projection ring.
    const tightness = Math.min(1, Math.max(0, (1.25 - camera.aspect) / 0.6));
    camera.position.set(0, target.y + 0.14 + tightness * 0.16, 4.55 - tightness * 0.55);
    camera.lookAt(target.x, target.y + tightness * 0.12, target.z);
    camera.updateProjectionMatrix();
  };
  const observer = new ResizeObserver(resize);
  observer.observe(host);
  const onContextLost = (event) => {
    event.preventDefault();
    visible = false;
    host.dispatchEvent(new CustomEvent("rap:avatar-fallback", { detail: { reason: "context-lost" } }));
  };
  const onContextRestored = () => {
    if (attemptedContextRestore || disposed) return;
    attemptedContextRestore = true;
    visible = true;
    resize();
    host.dispatchEvent(new CustomEvent("rap:avatar-recovered"));
  };
  renderer.domElement.addEventListener("webglcontextlost", onContextLost, false);
  renderer.domElement.addEventListener("webglcontextrestored", onContextRestored, false);
  resize();

  const animate = (time) => {
    if (disposed) return;
    animationFrame = requestAnimationFrame(animate);
    if (!visible || document.hidden || time - lastFrame < targetInterval) return;
    const delta = Math.min(0.1, Math.max(0.001, (time - (lastAnimatedAt || time - 16)) / 1000));
    lastAnimatedAt = time;
    lastFrame = time;
    rig.update?.(delta);
    if (latest) {
      applyAvatarFrame(rig, withDebugOverrides(latest, debugState), time / 1000, delta, gazeController, lipSync, currentTargets, debugState);
    }
    renderer.render(scene, camera);
  };
  animationFrame = requestAnimationFrame(animate);

  return {
    update(value) {
      latest = value;
      targetInterval = 1000 / value.settings.targetFps;
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, value.settings.maxPixelRatio));
      renderer.shadowMap.enabled = value.settings.shadows;
      rig.setReducedMotion?.(debugState.reducedMotion ?? value.settings.effectiveReducedMotion);
      if (value.settings.lipSync) stream.start();
      else stream.stop();
    },
    setVisible(value) { visible = Boolean(value); },
    debug: {
      setState(name) { debugState.state = name || null; },
      setEmotion(name, intensity = 0.8) {
        debugState.emotion = name || null;
        debugState.intensity = intensity;
      },
      setSpeaking(value) { debugState.speaking = value === null ? null : Boolean(value); },
      setAudioLevel(value) {
        debugState.audioLevel = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : null;
      },
      setLookTarget(x, y) {
        debugState.lookTarget = Number.isFinite(x) && Number.isFinite(y) ? { x, y } : null;
      },
      setReducedMotion(value) {
        debugState.reducedMotion = value === null ? null : Boolean(value);
        rig.setReducedMotion?.(debugState.reducedMotion ?? latest?.settings.effectiveReducedMotion ?? false);
      },
      triggerGlitch(type, strength = 1) { return rig.triggerGlitch?.(type, strength) ?? false; },
      setGlitchesEnabled(value) { rig.setGlitchesEnabled?.(value); },
      reset() {
        debugState.state = null;
        debugState.emotion = null;
        debugState.speaking = null;
        debugState.audioLevel = null;
        debugState.lookTarget = null;
        debugState.reducedMotion = null;
        rig.setGlitchesEnabled?.(true);
        rig.setReducedMotion?.(latest?.settings.effectiveReducedMotion ?? false);
      },
      getDiagnostics() {
        return {
          kind: loaded.kind,
          state: latest?.resolved?.state ?? null,
          emotion: latest?.resolved?.emotion ?? null,
          overrides: { ...debugState },
          rig: rig.getDiagnostics?.() ?? null,
        };
      },
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      cancelAnimationFrame(animationFrame);
      observer.disconnect();
      renderer.domElement.removeEventListener("webglcontextlost", onContextLost, false);
      renderer.domElement.removeEventListener("webglcontextrestored", onContextRestored, false);
      stream.dispose();
      if (rig.hostClass) host.classList.remove(rig.hostClass);
      rig.dispose();
      scene.clear();
      renderer.renderLists.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
      renderer.domElement.remove();
    },
  };
}

function withDebugOverrides(frame, debugState) {
  if (debugState.state === null && debugState.emotion === null
    && debugState.speaking === null && debugState.reducedMotion === null) return frame;
  return {
    ...frame,
    runtime: debugState.speaking === null ? frame.runtime : { ...frame.runtime, speaking: debugState.speaking },
    resolved: {
      state: debugState.state ?? frame.resolved.state,
      emotion: debugState.emotion
        ? { name: debugState.emotion, intensity: debugState.intensity }
        : frame.resolved.emotion,
    },
    settings: debugState.reducedMotion === null
      ? frame.settings
      : { ...frame.settings, effectiveReducedMotion: debugState.reducedMotion },
  };
}

function applyAvatarFrame(rig, frame, seconds, delta, gazeController, lipSync, currentTargets, debugState) {
  const controls = rig.controls;
  const profile = frame.profile;
  const expression = expressionFor(frame.resolved.emotion.name);
  const base = expressionFor(profile.defaultExpression);
  const amount = frame.resolved.emotion.intensity * frame.settings.expressionIntensity;
  const target = blendTargets(base, expression, amount);
  for (const key of Object.keys(currentTargets)) {
    currentTargets[key] = damp(currentTargets[key], target[key], 8, delta);
  }
  const state = frame.resolved.state;
  const reducedMotion = frame.settings.effectiveReducedMotion;

  const gaze = gazeController.update(delta, state, frame.settings.gaze, reducedMotion, profile);
  const look = debugState?.lookTarget;
  const gazeX = look ? look.x : gaze.x;
  const gazeY = look ? look.y : gaze.y;
  if (controls.pupilLeft) {
    controls.pupilLeft.position.x = gazeX;
    controls.pupilLeft.position.y = gazeY;
  }
  if (controls.pupilRight) {
    controls.pupilRight.position.x = gazeX;
    controls.pupilRight.position.y = gazeY;
  }
  const sleeping = state === "sleeping";
  const leftBlink = sleeping ? 1 : gaze.blink;
  const rightBlink = sleeping ? 1 : gaze.blink * 0.96;
  if (controls.lidLeft) controls.lidLeft.scale.y = Math.max(0.04, 0.74 * (1 - leftBlink));
  if (controls.lidRight) controls.lidRight.scale.y = Math.max(0.04, 0.74 * (1 - rightBlink));
  setMorph(rig.morphControls?.blinkLeft, leftBlink);
  setMorph(rig.morphControls?.blinkRight, rightBlink);
  controls.browLeft.position.y = 0.22 + currentTargets.browInner * 0.035 + currentTargets.browAsymmetry * 0.02;
  controls.browRight.position.y = 0.22 + currentTargets.browInner * 0.035 - currentTargets.browAsymmetry * 0.02;
  controls.browLeft.rotation.z = currentTargets.browOuter * -0.15;
  controls.browRight.rotation.z = currentTargets.browOuter * 0.15;
  controls.mouthCornerLeft.position.y = -0.01 + (currentTargets.mouthCorner - currentTargets.mouthAsymmetry * 0.4) * 0.035;
  controls.mouthCornerRight.position.y = -0.01 + (currentTargets.mouthCorner + currentTargets.mouthAsymmetry * 0.4) * 0.035;
  const mouthScale = profile.mouthMotionScale ?? 1;
  const mouth = lipSync.update(delta, frame.runtime.speaking, frame.settings.lipSync);
  if (debugState?.audioLevel !== null && debugState?.audioLevel !== undefined) {
    lipSync.ingest({ rms: debugState.audioLevel, peak: Math.min(1, debugState.audioLevel * 1.35) });
  }
  const jawOpen = Math.min(1, currentTargets.jawOpen + mouth.jawOpen * mouthScale);
  controls.jaw.rotation.x = jawOpen * 0.22;
  setMorph(rig.morphControls?.jaw, jawOpen);
  controls.mouthLower.position.y = -0.035 - mouth.jawOpen * mouthScale * 0.06;
  controls.mouthUpper.scale.x = 1 + currentTargets.mouthWidth * 0.2 + mouth.mouthWidth;
  controls.mouthLower.scale.x = 1 + currentTargets.mouthWidth * 0.18 + mouth.mouthWidth * 0.8;
  controls.cheekLeft.position.y = -0.04 + currentTargets.cheekRaise * 0.025;
  controls.cheekRight.position.y = -0.04 + currentTargets.cheekRaise * 0.025;
  controls.cheekLeft.scale.y = 1 - mouth.cheek;
  controls.cheekRight.scale.y = 1 - mouth.cheek;

  // Layered idle motion: several slow sinusoids at unrelated frequencies so
  // the sway never reads as one pendulum. Listening steadies the head,
  // thinking adds micro-tilts, speaking nods with the envelope.
  const canIdle = frame.settings.idleMotion && !reducedMotion;
  const idleScale = (canIdle ? profile.idleIntensity ?? 0.25 : 0)
    * (state === "listening" ? 0.3 : state === "focused" ? 0.55 : 1);
  const idleYaw = (Math.sin(seconds * 0.31) * 0.5 + Math.sin(seconds * 0.73 + 1.7) * 0.3) * 0.14 * idleScale;
  const idlePitch = Math.sin(seconds * 0.47 + 0.6) * 0.08 * idleScale
    + (state === "thinking" && !reducedMotion ? Math.sin(seconds * 1.9) * 0.012 : 0);
  const idleRoll = Math.sin(seconds * 0.23 + 2.1) * 0.05 * idleScale;
  const nod = frame.runtime.speaking && !reducedMotion
    ? mouth.jawOpen * (profile.speakingHeadMotion ?? 0.16) * 0.16 * Math.sin(seconds * 2.2)
    : 0;
  controls.head.rotation.x = currentTargets.headPitch + idlePitch + nod + (sleeping ? 0.09 : 0);
  controls.head.rotation.y = currentTargets.headYaw + idleYaw;
  controls.head.rotation.z = currentTargets.headRoll + idleRoll;

  setMorph(rig.morphControls?.smileLeft, Math.max(0, currentTargets.mouthCorner));
  setMorph(rig.morphControls?.smileRight, Math.max(0, currentTargets.mouthCorner));
  setMorph(rig.morphControls?.frownLeft, Math.max(0, -currentTargets.mouthCorner));
  setMorph(rig.morphControls?.frownRight, Math.max(0, -currentTargets.mouthCorner));
  setMorph(rig.morphControls?.browInnerUp, Math.max(0, currentTargets.browInner));
  setMorph(rig.morphControls?.browDownLeft, Math.max(0, -currentTargets.browOuter));
  setMorph(rig.morphControls?.browDownRight, Math.max(0, -currentTargets.browOuter));
  setMorph(rig.morphControls?.eyeWideLeft, Math.max(0, currentTargets.eyeWiden));
  setMorph(rig.morphControls?.eyeWideRight, Math.max(0, currentTargets.eyeWiden));

  const breathing = canIdle ? Math.sin(seconds * 1.35) * 0.008 * (profile.idleIntensity ?? 0.25) : 0;
  const stabilization = canIdle ? Math.sin(seconds * 0.41) * 0.006 * (profile.idleIntensity ?? 0.25) : 0;
  controls.bust.scale.y = 0.72 + breathing;
  controls.root.rotation.y = stabilization;

  rig.applyExpression?.({
    targets: currentTargets,
    emotion: frame.resolved.emotion,
    state,
    profile,
    delta,
    seconds,
  });
  rig.applyMouth?.({
    mouth,
    jawOpen,
    targets: currentTargets,
    speaking: frame.runtime.speaking,
    delta,
    seconds,
  });
  rig.applyState?.(frame, currentTargets, mouth, seconds, delta);
}


function adaptLoadedRig(loaded, metadata, THREE) {
  const safe = () => new THREE.Object3D();
  const objectControl = (value) => value?.isObject3D ? value : null;
  const morphControl = (value) => value?.kind === "morph" ? value : null;
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
  const morphControls = Object.fromEntries(
    Object.entries(loaded.controls).map(([key, value]) => [key, morphControl(value)]),
  );
  const mixer = loaded.animations.length ? new THREE.AnimationMixer(loaded.object) : null;
  const idleClip = loaded.animations.find((clip) => /idle/i.test(clip.name)) || loaded.animations[0];
  if (mixer && idleClip) mixer.clipAction(idleClip).play();
  return {
    object: loaded.object,
    controls,
    morphControls,
    update(delta) { mixer?.update(delta); },
    dispose() {
      mixer?.stopAllAction();
      if (mixer) mixer.uncacheRoot(loaded.object);
      loaded.dispose();
    },
  };
}

function setMorph(control, value) {
  if (!control?.object?.morphTargetInfluences) return;
  control.object.morphTargetInfluences[control.index] = Math.max(0, Math.min(1, value));
}

import test from "node:test";
import assert from "node:assert/strict";
import * as THREE from "../../remote_agent_protocol/web_app/vendor/three/three.module.min.js";
import { createProceduralVisage } from "../../remote_agent_protocol/web_app/avatar/procedural-visage.js";

const CONTROL_KEYS = [
  "root", "bust", "neck", "head", "jaw", "mouthUpper", "mouthLower",
  "mouthCornerLeft", "mouthCornerRight", "cheekLeft", "cheekRight",
  "browLeft", "browRight", "eyeLeft", "eyeRight",
  "pupilLeft", "pupilRight", "lidLeft", "lidRight",
];

const BUTLER_CONTROL_KEYS = [
  "monocleRoot", "monocleRingPrimary", "monocleRingSecondary", "monocleReticle", "monocleTicks",
  "hairRoot", "moustacheLeft", "moustacheRight",
  "bowTieRoot", "bowTieLeft", "bowTieRight", "lapelLeft", "lapelRight",
  "haloRoot", "fragmentRoot", "glitchDuplicateRoot",
];

const TARGET_KEYS = [
  "browInner", "browOuter", "browAsymmetry", "browArch", "eyelid", "eyeWiden",
  "eyeSquint", "pupilScale", "jawOpen", "mouthWidth", "mouthCorner",
  "mouthRoundness", "mouthAsymmetry", "cheekRaise", "headPitch", "headYaw",
  "headRoll", "monocleSpeed", "monocleScale", "glitchBias", "glowBias",
];

function makeTargets(overrides = {}) {
  return { ...Object.fromEntries(TARGET_KEYS.map((key) => [key, 0])), ...overrides };
}

function makeMouth(overrides = {}) {
  return {
    jawOpen: 0, mouthWidth: 0, cheek: 0, roundness: 0, closure: 0,
    asymmetry: 0, usingEnvelope: false, ...overrides,
  };
}

function makeFrame(overrides = {}) {
  return {
    runtime: { speaking: false, ...overrides.runtime },
    resolved: { state: "idle", emotion: { name: "neutral", intensity: 0.2 }, ...overrides.resolved },
    profile: {
      defaultExpression: "neutral", idleIntensity: 0.28, glitchIntensity: 0.9,
      monocleActivity: 1, scanlineIntensity: 1, speakingGlow: 1.1, mouthMotionScale: 0.9,
      ...overrides.profile,
    },
    settings: {
      lipSync: true,
      gaze: true,
      idleMotion: true,
      effectiveReducedMotion: false,
      expressionIntensity: 0.62,
      ...overrides.settings,
    },
  };
}

// Mirrors the absolute transform writes applyAvatarFrame performs each frame.
function driveControls(controls, { blink = 0.2, jaw = 0.3 } = {}) {
  controls.pupilLeft.position.x = 0.12;
  controls.pupilRight.position.x = 0.12;
  controls.pupilLeft.position.y = -0.06;
  controls.pupilRight.position.y = -0.06;
  controls.lidLeft.scale.y = Math.max(0.04, 0.74 * (1 - blink));
  controls.lidRight.scale.y = Math.max(0.04, 0.74 * (1 - blink * 0.96));
  controls.browLeft.position.y = 0.22 + 0.035 + 0.02;
  controls.browRight.position.y = 0.22 + 0.035 - 0.02;
  controls.browLeft.rotation.z = -0.15;
  controls.browRight.rotation.z = 0.15;
  controls.mouthCornerLeft.position.y = -0.01 + 0.012;
  controls.mouthCornerRight.position.y = -0.01 + 0.012;
  controls.jaw.rotation.x = jaw * 0.22;
  controls.mouthLower.position.y = -0.035 - jaw * 0.06;
  controls.mouthUpper.scale.x = 1.2;
  controls.mouthLower.scale.x = 1.18;
  controls.cheekLeft.position.y = -0.04 + 0.025;
  controls.cheekRight.position.y = -0.04 + 0.025;
  controls.cheekLeft.scale.y = 0.9;
  controls.cheekRight.scale.y = 0.9;
  controls.head.rotation.set(0.05, -0.04, 0.03);
  controls.bust.scale.y = 0.72 + 0.005;
  controls.root.rotation.y = 0.004;
}

function assertFiniteTree(object) {
  object.updateMatrixWorld(true);
  object.traverse((child) => {
    for (const value of child.matrixWorld.elements) {
      assert.ok(Number.isFinite(value), `non-finite matrix in ${child.name || child.type}`);
    }
  });
}

test("visage exposes the full controls contract plus butler extras", () => {
  const rig = createProceduralVisage(THREE);
  for (const key of CONTROL_KEYS) {
    assert.ok(rig.controls[key]?.isObject3D, `missing control: ${key}`);
  }
  for (const key of BUTLER_CONTROL_KEYS) {
    assert.ok(rig.controls[key]?.isObject3D, `missing butler control: ${key}`);
  }
  assert.equal(typeof rig.update, "function");
  assert.equal(typeof rig.applyState, "function");
  assert.equal(typeof rig.applyExpression, "function");
  assert.equal(typeof rig.applyMouth, "function");
  assert.equal(typeof rig.setQuality, "function");
  assert.equal(typeof rig.setReducedMotion, "function");
  assert.equal(typeof rig.triggerGlitch, "function");
  assert.equal(typeof rig.setGlitchesEnabled, "function");
  assert.equal(rig.hostClass, "avatar-visage");
  rig.dispose();
});

test("visage survives scene-style animation across every state", () => {
  const rig = createProceduralVisage(THREE, { random: () => 0.42 });
  const targets = makeTargets({
    browInner: 0.3, browOuter: -0.1, browAsymmetry: 0.1, eyelid: 0.1, eyeWiden: 0.4,
    jawOpen: 0.1, mouthWidth: 0.1, mouthCorner: 0.3, cheekRaise: 0.2,
    headPitch: 0.05, headYaw: -0.03, headRoll: 0.02, mouthRoundness: 0.2,
    mouthAsymmetry: 0.1, browArch: 0.2, eyeSquint: 0.1, pupilScale: 0.1,
  });
  const states = [
    "idle", "listening", "transcribing", "thinking", "speaking", "focused",
    "concerned", "error", "happy", "disconnected", "passive", "sleeping",
  ];
  let seconds = 0;
  for (const state of states) {
    for (let i = 0; i < 12; i += 1) {
      seconds += 1 / 30;
      rig.update(1 / 30);
      driveControls(rig.controls, { blink: (i % 4) / 4, jaw: (i % 3) / 3 });
      rig.applyExpression({ targets, state, delta: 1 / 30, seconds });
      rig.applyMouth({
        mouth: makeMouth({ jawOpen: 0.5, mouthWidth: 0.1, roundness: 0.2 }),
        jawOpen: 0.5, targets, speaking: state === "speaking", delta: 1 / 30, seconds,
      });
      rig.applyState(
        makeFrame({ resolved: { state } }),
        targets,
        makeMouth({ jawOpen: 0.5, mouthWidth: 0.1, cheek: 0.08, usingEnvelope: true }),
        seconds,
        1 / 30,
      );
    }
  }
  assertFiniteTree(rig.object);
  rig.dispose();
});

test("manual glitches drive shader bands and echo layers", () => {
  const rig = createProceduralVisage(THREE, { random: () => 0.5 });
  // Let the boot reconstruction finish first.
  for (let i = 0; i < 80; i += 1) rig.update(1 / 30);
  assert.equal(rig.triggerGlitch("channel-split", 1), true);
  rig.update(0.02);
  assert.equal(rig.controls.glitchDuplicateRoot.visible, true, "channel split shows echo layers");
  for (let i = 0; i < 30; i += 1) rig.update(1 / 30);
  assert.equal(rig.controls.glitchDuplicateRoot.visible, false, "echo layers hide after the event");
  assert.equal(rig.triggerGlitch("data-fragment", 1), true);
  rig.update(0.05);
  assert.equal(rig.controls.fragmentRoot.visible, true, "fragments detach during data-fragment");
  assert.equal(rig.triggerGlitch("bogus-type", 1), false);
  rig.dispose();
});

test("reduced motion disables displacement and quality gates features", () => {
  const rig = createProceduralVisage(THREE, { quality: "low", reducedMotion: true, random: () => 0.5 });
  assert.equal(rig.controls.fragmentRoot.children.length, 0, "low quality builds no fragments");
  const targets = makeTargets();
  for (let i = 0; i < 40; i += 1) {
    rig.update(1 / 30);
    rig.applyState(
      makeFrame({ resolved: { state: "error" }, settings: { effectiveReducedMotion: true } }),
      targets,
      makeMouth(),
      i / 30,
      1 / 30,
    );
    assert.equal(rig.object.position.x, 0, "reduced motion never displaces the root");
  }
  const diagnostics = rig.getDiagnostics();
  assert.equal(diagnostics.quality, "low");
  assert.equal(diagnostics.reducedMotion, true);
  assertFiniteTree(rig.object);
  rig.dispose();
});

test("visage respects reduced motion and zero-audio silence", () => {
  const rig = createProceduralVisage(THREE);
  for (let i = 0; i < 30; i += 1) {
    rig.update(1 / 30);
    rig.applyState(
      makeFrame({ settings: { effectiveReducedMotion: true } }),
      makeTargets(),
      makeMouth(),
      i / 30,
      1 / 30,
    );
  }
  const visibleBars = rig.controls.jaw.children.filter(
    (child) => child.name.startsWith("waveBar") && child.visible,
  ).length;
  assert.equal(visibleBars, 0, "waveform bars should hide when there is no audio");
  assertFiniteTree(rig.object);
  rig.dispose();
});

test("diagnostics describe the rig and dispose clears the graph", () => {
  const rig = createProceduralVisage(THREE);
  const diagnostics = rig.getDiagnostics();
  assert.equal(diagnostics.rig, "holographic-butler");
  assert.ok(diagnostics.glitchTypes.includes("horizontal-tear"));
  assert.ok(diagnostics.geometries > 0);
  assert.ok(diagnostics.materials > 0);
  rig.dispose();
  assert.equal(rig.object.children.length, 0);
});

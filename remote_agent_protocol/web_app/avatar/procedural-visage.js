// Scanline retro holographic butler: a formal digital butler rendered as a
// slightly unstable cyan hologram. This module orchestrates the part builders
// (head, hair, monocle, clothing, HUD) over the shared hologram shader kit,
// runs the glitch scheduler, and honors the same controls contract as the
// procedural butler (the scene writes absolute transforms to controls.* every
// frame) plus the optional applyExpression/applyMouth/applyState/setQuality/
// setReducedMotion rig hooks.

import { createHologramMaterials } from "./hologram-materials.js";
import { GlitchScheduler, GLITCH_TYPES } from "./butler-glitch.js";
import { createButlerHead } from "./butler-head.js";
import { createButlerHair } from "./butler-hair.js";
import { createButlerMonocle } from "./butler-monocle.js";
import { createButlerClothing } from "./butler-clothing.js";
import { createButlerHud } from "./butler-hud.js";

const TAU = Math.PI * 2;

// The projection stays overwhelmingly cyan; states move accents, glow, and
// pacing rather than recoloring the whole figure.
const STATE_THEMES = Object.freeze({
  idle: { primary: 0x22d3ee, accent: 0x38e0f0, glitch: 0xc084fc, glow: 1.0, speed: 1.0 },
  listening: { primary: 0x2be0d8, accent: 0x34d399, glitch: 0xc084fc, glow: 1.1, speed: 1.05 },
  transcribing: { primary: 0x22d3ee, accent: 0x67e8f9, glitch: 0xc084fc, glow: 1.05, speed: 1.2 },
  thinking: { primary: 0x22d3ee, accent: 0xa78bfa, glitch: 0xa78bfa, glow: 1.05, speed: 1.5 },
  speaking: { primary: 0x45ddf2, accent: 0x8be9f9, glitch: 0xc084fc, glow: 1.18, speed: 1.15 },
  focused: { primary: 0x38bdf8, accent: 0x60a5fa, glitch: 0xc084fc, glow: 1.02, speed: 1.2 },
  concerned: { primary: 0x22d3ee, accent: 0xfbbf24, glitch: 0xc084fc, glow: 0.98, speed: 0.9 },
  happy: { primary: 0x3ce4ee, accent: 0xfdf6d8, glitch: 0xc084fc, glow: 1.14, speed: 1.1 },
  error: { primary: 0x22d3ee, accent: 0xf87171, glitch: 0xd946ef, glow: 1.05, speed: 0.85 },
  disconnected: { primary: 0x1b8fa6, accent: 0x94a3b8, glitch: 0x64748b, glow: 0.72, speed: 0.6 },
  sleeping: { primary: 0x4152c9, accent: 0x4152c9, glitch: 0x4152c9, glow: 0.36, speed: 0.22 },
  passive: { primary: 0x1eb8d0, accent: 0x1eb8d0, glitch: 0x818cf8, glow: 0.6, speed: 0.5 },
});

const FRAGMENT_COUNTS = Object.freeze({ low: 0, medium: 6, high: 12 });

export function createProceduralVisage(THREE, options = {}) {
  const quality = ["low", "medium", "high"].includes(options.quality) ? options.quality : "high";
  const random = options.random || Math.random;
  const root = new THREE.Group();
  root.name = "procedural-visage";
  root.position.y = -0.55;

  const geometries = [];
  const trackGeometry = (geometry) => { geometries.push(geometry); return geometry; };
  const pixelRatio = typeof window !== "undefined" ? Math.min(window.devicePixelRatio || 1, 2) : 1;
  const kit = createHologramMaterials(THREE, {
    pixelRatio,
    scanlineDensity: quality === "low" ? 26 : quality === "medium" ? 34 : 40,
    scanlineStrength: 0.5,
  });

  // --- Assemble the figure.
  const headModule = createButlerHead(THREE, { kit, trackGeometry, quality });
  const head = headModule.head;
  head.position.y = 1.65;
  root.add(head);

  const hair = createButlerHair(THREE, {
    kit, trackGeometry, quality, surfacePoint: headModule.surfacePoint,
  });
  head.add(hair.group);

  const monocleAnchor = headModule.controls.eyeRight.position.clone();
  monocleAnchor.z += 0.075;
  monocleAnchor.x += 0.012;
  const monocle = createButlerMonocle(THREE, { kit, trackGeometry, quality, anchor: monocleAnchor });
  head.add(monocle.group);

  const neck = new THREE.Group();
  neck.name = "neck";
  neck.position.y = 1.13;
  root.add(neck);
  const neckMaterial = kit.line(0.28);
  [[0.2, -0.08], [0.185, 0], [0.17, 0.08]].forEach(([radius, y]) => {
    const points = [];
    for (let i = 0; i <= 36; i += 1) {
      const angle = (i / 36) * TAU;
      points.push(new THREE.Vector3(Math.cos(angle) * radius, 0, Math.sin(angle) * radius * 0.86));
    }
    const ring = new THREE.LineLoop(trackGeometry(new THREE.BufferGeometry().setFromPoints(points)), neckMaterial);
    ring.position.y = y;
    neck.add(ring);
  });

  // The scene breathes bust.scale.y around 0.72, so clothing is authored in
  // natural units inside an unsquash wrapper.
  const bust = new THREE.Group();
  bust.name = "bust";
  bust.position.y = 0.55;
  bust.scale.y = 0.72;
  root.add(bust);
  const clothingSpace = new THREE.Group();
  clothingSpace.scale.y = 1 / 0.72;
  bust.add(clothingSpace);
  // clothingSpace's unsquash makes natural clothing units equal world units
  // (root -0.55 + bust 0.55 cancel), so no extra offset is needed here.
  const clothing = createButlerClothing(THREE, { kit, trackGeometry, quality });
  clothingSpace.add(clothing.group);

  const coreMaterial = kit.glow(0.12, 0.3);
  const core = new THREE.Mesh(trackGeometry(new THREE.PlaneGeometry(0.2, 0.2)), coreMaterial);
  core.position.set(0, 0.1, 0.27);
  clothing.group.add(core);

  const hud = createButlerHud(THREE, { kit, trackGeometry, quality, random });
  root.add(hud.group);

  // --- Channel-split echoes: the head wireframe duplicated in violet and
  // magenta, sharing geometry, hidden until a split event.
  const echoGeometry = trackGeometry(new THREE.WireframeGeometry(headModule.headShellGeometry));
  const echoRoot = new THREE.Group();
  echoRoot.name = "glitchDuplicateRoot";
  const echoA = new THREE.LineSegments(echoGeometry, kit.line(0, { color: 0x8b5cf6, scanlineStrength: 0.2 }));
  const echoB = new THREE.LineSegments(echoGeometry, kit.line(0, { color: 0xec4899, scanlineStrength: 0.2 }));
  echoRoot.add(echoA, echoB);
  echoRoot.visible = false;
  head.add(echoRoot);

  // --- Data fragments: prebuilt shards sampled from the head shell that
  // drift out of the silhouette during data-fragment glitches.
  const fragmentRoot = new THREE.Group();
  fragmentRoot.name = "fragmentRoot";
  fragmentRoot.visible = false;
  head.add(fragmentRoot);
  const fragmentMaterial = kit.line(0);
  const fragments = [];
  const fragmentCount = FRAGMENT_COUNTS[quality];
  const shellPosition = headModule.headShellGeometry.attributes.position;
  const triangleCount = Math.floor(shellPosition.count / 3);
  for (let i = 0; i < fragmentCount; i += 1) {
    const tri = Math.floor(((i * 0.61803398875) % 1) * triangleCount) * 3;
    const ax = shellPosition.getX(tri), ay = shellPosition.getY(tri), az = shellPosition.getZ(tri);
    const cx = (ax + shellPosition.getX(tri + 1) + shellPosition.getX(tri + 2)) / 3;
    const cy = (ay + shellPosition.getY(tri + 1) + shellPosition.getY(tri + 2)) / 3;
    const cz = (az + shellPosition.getZ(tri + 1) + shellPosition.getZ(tri + 2)) / 3;
    const shard = new THREE.LineLoop(
      trackGeometry(new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(shellPosition.getX(tri) - cx, shellPosition.getY(tri) - cy, shellPosition.getZ(tri) - cz),
        new THREE.Vector3(shellPosition.getX(tri + 1) - cx, shellPosition.getY(tri + 1) - cy, shellPosition.getZ(tri + 1) - cz),
        new THREE.Vector3(shellPosition.getX(tri + 2) - cx, shellPosition.getY(tri + 2) - cy, shellPosition.getZ(tri + 2) - cz),
      ])),
      fragmentMaterial,
    );
    shard.position.set(cx, cy, cz);
    const direction = new THREE.Vector3(cx, cy, cz).normalize();
    fragments.push({ shard, base: new THREE.Vector3(cx, cy, cz), direction, seed: (i * 0.7548776662) % 1 });
    fragmentRoot.add(shard);
  }

  // --- Glitch scheduler + shared fx state.
  const scheduler = new GlitchScheduler({
    random, quality, yRange: [-0.05, 1.65],
  });
  const primaryColor = new THREE.Color(STATE_THEMES.idle.primary);
  const accentColor = new THREE.Color(STATE_THEMES.idle.accent);
  const glitchColor = new THREE.Color(STATE_THEMES.idle.glitch);
  const dampValue = (current, target, lambda, delta) =>
    current + (target - current) * (1 - Math.exp(-lambda * delta));

  const fx = {
    time: 0,
    state: "idle",
    lastState: "idle",
    motion: 1,
    motionTarget: 1,
    speed: 1,
    speedTarget: 1,
    glow: 1,
    glowTarget: 1,
    glitchGain: 1,
    envelope: 0,
    pulseRate: 1,
    reducedMotion: Boolean(options.reducedMotion),
    glitchFrame: null,
    expressionApplied: false,
    mouthApplied: false,
    scanlineBase: 0.5,
    glowBias: 0,
  };
  scheduler.setReducedMotion(fx.reducedMotion);
  kit.shared.uGlitchStrength.value = fx.reducedMotion ? 0 : 1;

  // Boot: assemble the transmission.
  scheduler.trigger("reconstruction", 1);
  hud.pulseSweep("boot");

  const applyGlitchFrame = (frame) => {
    fx.glitchFrame = frame;
    fx.glitchGain = 1 + frame.flicker;
    root.position.x = frame.offsetX;
    const bandA = frame.bands[0];
    const bandB = frame.bands[1];
    kit.shared.uBandAStart.value = bandA ? bandA.start : -100;
    kit.shared.uBandAEnd.value = bandA ? bandA.end : -100;
    kit.shared.uBandAOffset.value = bandA ? bandA.offset : 0;
    kit.shared.uBandBStart.value = bandB ? bandB.start : -100;
    kit.shared.uBandBEnd.value = bandB ? bandB.end : -100;
    kit.shared.uBandBOffset.value = bandB ? bandB.offset : 0;
    kit.shared.uBandTint.value = frame.type === "horizontal-tear" ? 0.55 * frame.strength : 0;
    const dropout = frame.dropout;
    kit.shared.uDropStart.value = dropout ? dropout.start : -100;
    kit.shared.uDropEnd.value = dropout ? dropout.end : -100;
    kit.shared.uDropAmount.value = dropout ? dropout.amount : 0;
    kit.shared.uReveal.value = frame.reveal >= 1 ? 100 : -0.3 + frame.reveal * 2.5;
    if (frame.seed) kit.shared.uGlitchSeed.value = frame.seed;

    const split = frame.channel;
    echoRoot.visible = split > 0.01;
    if (echoRoot.visible) {
      echoA.position.x = 0.028 * split;
      echoB.position.x = -0.024 * split;
      echoA.material.uniforms.uOpacity.value = 0.4 * split;
      echoB.material.uniforms.uOpacity.value = 0.34 * split;
    }

    headModule.headPointsMaterial.uniforms.uScatter.value = frame.scatter * 0.24;

    fragmentRoot.visible = frame.fragments > 0.01;
    if (fragmentRoot.visible) {
      fragmentMaterial.uniforms.uOpacity.value = Math.min(0.85, frame.fragments * 1.2);
      for (const fragment of fragments) {
        const drift = frame.fragments * (0.08 + 0.3 * fragment.seed);
        fragment.shard.position.set(
          fragment.base.x + fragment.direction.x * drift,
          fragment.base.y + fragment.direction.y * drift * 0.6,
          fragment.base.z + fragment.direction.z * drift,
        );
        fragment.shard.rotation.z = frame.phase * (fragment.seed - 0.5) * 2.4;
      }
    }
  };

  const update = (delta) => {
    fx.time += delta;
    kit.shared.uTime.value = fx.time;
    fx.motion = dampValue(fx.motion, fx.motionTarget, 4, delta);
    fx.speed = dampValue(fx.speed, fx.speedTarget, 3, delta);
    fx.glow = dampValue(fx.glow, fx.glowTarget, 5, delta);
    const flow = fx.motion * fx.speed;

    root.position.y = -0.55 + Math.sin(fx.time * 0.8) * 0.01 * fx.motion;

    headModule.update(fx.time, delta, flow);
    hair.update(fx.time);
    hud.update({
      delta, seconds: fx.time, flow, state: fx.state, reducedMotion: fx.reducedMotion,
      rootY: root.position.y,
    });
    applyGlitchFrame(scheduler.update(delta));

    const beat = Math.pow(Math.max(0, Math.sin(fx.time * 2.2 * fx.pulseRate)), 3);
    coreMaterial.uniforms.uOpacity.value = 0.08 + beat * 0.12 * (0.5 + 0.5 * fx.motion);
    kit.shared.uGlow.value = Math.max(0.05, fx.glow * fx.glitchGain * (1 + fx.glowBias));
    fx.expressionApplied = false;
    fx.mouthApplied = false;
  };

  const applyExpression = ({ targets }) => {
    headModule.applyExpressionAccents(targets);
    fx.glowBias = (targets.glowBias ?? 0) * 0.3;
    fx.expressionApplied = true;
  };

  const applyMouth = ({ mouth, jawOpen, targets, speaking }) => {
    headModule.shapeMouth({
      jawOpen,
      mouthWidth: (targets.mouthWidth ?? 0) + (mouth.mouthWidth ?? 0) * 2,
      cornerLift: targets.mouthCorner ?? 0,
      asymmetry: (targets.mouthAsymmetry ?? 0) * 0.05 + (mouth.asymmetry ?? 0) * 0.02,
      roundness: (targets.mouthRoundness ?? 0) + (mouth.roundness ?? 0),
      closure: mouth.closure ?? 0,
      speaking,
      envelope: mouth,
    });
    fx.mouthApplied = true;
  };

  const applyState = (frame, targets, mouth, seconds, delta) => {
    const state = frame.resolved.state || "idle";
    const profile = frame.profile || {};
    fx.state = state;
    const theme = STATE_THEMES[state] || STATE_THEMES.idle;
    const basePrimary = state === "idle" && Number.isFinite(profile.primaryColor)
      ? profile.primaryColor
      : theme.primary;
    primaryColor.setHex(basePrimary);
    accentColor.setHex(theme.accent);
    glitchColor.setHex(theme.glitch);
    const lerp = 1 - Math.exp(-(state === "error" ? 9 : 3.5) * delta);
    kit.shared.uColor.value.lerp(primaryColor, lerp);
    kit.shared.uAccent.value.lerp(accentColor, lerp);
    kit.shared.uGlitchColor.value.lerp(glitchColor, lerp);

    const reduced = Boolean(frame.settings.effectiveReducedMotion);
    if (reduced !== fx.reducedMotion) setReducedMotion(reduced);
    scheduler.setState(state);
    scheduler.setIntensity(profile.glitchIntensity ?? 1);
    kit.shared.uScanlineStrength.value = fx.scanlineBase * (profile.scanlineIntensity ?? 1);

    fx.glowTarget = (theme.glow + mouth.jawOpen * 0.4 * (profile.speakingGlow ?? 1))
      * (1 + (targets.glowBias ?? 0) * 0.2);
    fx.speedTarget = theme.speed;
    fx.motionTarget = reduced ? 0 : frame.settings.idleMotion ? 1 : 0.55;
    fx.pulseRate = state === "speaking" ? 1.6 : state === "sleeping" ? 0.4 : 1;
    fx.envelope = dampValue(fx.envelope, Math.min(1, mouth.jawOpen * 1.6), 7, delta);
    kit.shared.uAudioLevel.value = fx.envelope;

    if (!fx.expressionApplied) applyExpression({ targets });
    if (!fx.mouthApplied) {
      applyMouth({ mouth, jawOpen: Math.min(1, (targets.jawOpen ?? 0) + mouth.jawOpen), targets, speaking: frame.runtime.speaking });
    }

    monocle.update({
      state,
      seconds,
      delta,
      reducedMotion: reduced,
      desync: fx.glitchFrame?.monocle ?? 0,
      envelope: fx.envelope,
      activity: (profile.monocleActivity ?? 1) * (0.5 + 0.5 * fx.motion)
        * (1 + (targets.monocleSpeed ?? 0)),
    });
    clothing.update({ envelope: fx.envelope, seconds });

    // A recovered connection announces itself with a dual sweep.
    if (fx.lastState === "disconnected" && state !== "disconnected") hud.pulseSweep("dual");
    fx.lastState = state;
  };

  const setQuality = (value) => { scheduler.setQuality(value); };
  const setReducedMotion = (value) => {
    fx.reducedMotion = Boolean(value);
    scheduler.setReducedMotion(fx.reducedMotion);
    kit.shared.uGlitchStrength.value = fx.reducedMotion ? 0 : 1;
    kit.shared.uFlicker.value = fx.reducedMotion ? 0.012 : 0.035;
  };
  setReducedMotion(fx.reducedMotion);

  return {
    object: root,
    hostClass: "avatar-visage",
    controls: {
      root,
      bust,
      neck,
      head,
      ...headModule.controls,
      ...monocle.controls,
      ...clothing.controls,
      hairRoot: hair.group,
      haloRoot: hud.group,
      fragmentRoot,
      glitchDuplicateRoot: echoRoot,
    },
    update,
    applyState,
    applyExpression,
    applyMouth,
    setQuality,
    setReducedMotion,
    triggerGlitch(type, strength = 1) { return scheduler.trigger(type, strength); },
    setGlitchesEnabled(value) { scheduler.setEnabled(value); },
    getDiagnostics() {
      return {
        rig: "holographic-butler",
        quality,
        state: fx.state,
        reducedMotion: fx.reducedMotion,
        activeGlitch: scheduler.activeType,
        glitchTypes: [...GLITCH_TYPES],
        geometries: geometries.length,
        materials: kit.materials.length,
      };
    },
    dispose() {
      geometries.forEach((geometry) => geometry.dispose());
      kit.dispose();
      root.clear();
    },
  };
}

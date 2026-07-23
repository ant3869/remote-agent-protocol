import { GlitchScheduler } from "./butler-glitch.js";

const DEFAULT_SHEETS = [
  "/assets/avatars/butler/sheet_idle.png",
  "/assets/avatars/butler/sheet_glow.png",
  "/assets/avatars/butler/sheet_full.png",
];

const IDLE_CELLS = Object.freeze([0, 1, 2, 3, 5, 6, 7, 9, 12, 13, 14, 15]);
const EMOTION_CELLS = Object.freeze({
  warm: 1, pleased: 2, happy: 2, excited: 3, surprised: 8,
  thinking: 5, concerned: 10, error: 10, angry: 10,
  confused: 9, skeptical: 9, sad: 9, apologetic: 9,
});

function cell(animation, sheet, index) {
  return { animation, sheet, column: index % 4, row: Math.floor(index / 4), index };
}

export function spriteFrameFor({ lidScale = 0.74, state, emotion = "neutral", speaking = false,
  mouth, seconds = 0, reducedMotion = false } = {}) {
  if (state === "sleeping") return cell("sleep", 2, 11);
  if (lidScale < 0.2) return cell("blink", 2, 11);
  if (lidScale < 0.58) return cell("blink", 0, 10);
  if (speaking) {
    if (!mouth || mouth.closure >= 0.55 || mouth.jawOpen <= 0.08) return cell("speech", 2, 0);
    if (mouth.roundness > 0.18) return cell("speech", 2, 14);
    return cell("speech", 2, mouth.mouthWidth > 0.1 ? 13 : 12);
  }
  if (EMOTION_CELLS[emotion] !== undefined) return cell("emotion", 2, EMOTION_CELLS[emotion]);
  if (["focused", "listening", "transcribing", "thinking"].includes(state)) {
    return cell("glow", 1, reducedMotion ? 0 : Math.floor(seconds * 6) % 16);
  }
  const idleIndex = reducedMotion ? 0 : Math.floor(seconds * 2) % IDLE_CELLS.length;
  return cell("idle", 0, IDLE_CELLS[idleIndex]);
}

function makeMaterial(THREE, texture) {
  return new THREE.ShaderMaterial({
    uniforms: {
      map: { value: texture }, uTime: { value: 0 }, uFlicker: { value: 0 },
      uTear: { value: 0 }, uChannel: { value: 0 }, uDropStart: { value: -1 },
      uDropEnd: { value: -1 }, uDropAmount: { value: 0 }, uReveal: { value: 1 },
      uCell: { value: new THREE.Vector2(0, 0.75) },
    },
    vertexShader: `
      varying vec2 vUv;
      void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }
    `,
    fragmentShader: `
      uniform sampler2D map;
      uniform float uTime, uFlicker, uTear, uChannel, uDropStart, uDropEnd, uDropAmount, uReveal;
      uniform vec2 uCell;
      varying vec2 vUv;
      void main() {
        if (vUv.y > uReveal) discard;
        float stepped = step(0.6, fract(vUv.y * 23.0 + uTime * 3.4));
        vec2 uv = vec2(clamp(vUv.x + uTear * stepped * 1.25, 0.002, 0.998), vUv.y);
        float split = uChannel * 0.018;
        vec2 atlasUv = uCell + uv * 0.25;
        vec4 base = texture2D(map, atlasUv);
        vec3 color = vec3(texture2D(map, atlasUv + vec2(split, 0.0)).r, base.g,
                          texture2D(map, atlasUv - vec2(split, 0.0)).b);
        float inDrop = step(uDropStart, vUv.y) * step(vUv.y, uDropEnd);
        color = pow(max(color, vec3(0.0)), vec3(0.76)) * 1.35;
        float scanline = 0.9 + 0.1 * sin(vUv.y * 1050.0 + uTime * 6.5);
        color *= scanline * (1.0 + uFlicker) * (1.0 - inDrop * min(1.0, uDropAmount * 1.15));
        gl_FragColor = vec4(color, base.a);
      }
    `,
    transparent: false,
    depthWrite: false,
    toneMapped: false,
  });
}

export async function createSpriteButler(THREE, options = {}) {
  const loader = new THREE.TextureLoader();
  const loadTexture = options.loadTexture || ((url) => loader.loadAsync(url));
  const sheetUrls = options.sprites?.sheets || (options.sprites?.sheet ? [options.sprites.sheet] : DEFAULT_SHEETS);
  const textures = await Promise.all(sheetUrls.map((url) => loadTexture(url)));
  for (const texture of textures) {
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.minFilter = THREE.LinearFilter;
    texture.magFilter = THREE.LinearFilter;
  }

  const object = new THREE.Group();
  object.name = "sprite-butler";
  const material = makeMaterial(THREE, textures[0]);
  const geometry = new THREE.PlaneGeometry(1.92, 1.92);
  const portrait = new THREE.Mesh(geometry, material);
  portrait.name = "spritePortrait";
  portrait.position.y = 0.95;
  object.add(portrait);

  const safe = () => new THREE.Object3D();
  const controls = {
    root: safe(), bust: safe(), neck: safe(), head: safe(), jaw: safe(),
    mouthUpper: safe(), mouthLower: safe(), mouthCornerLeft: safe(), mouthCornerRight: safe(),
    cheekLeft: safe(), cheekRight: safe(), browLeft: safe(), browRight: safe(),
    eyeLeft: safe(), eyeRight: safe(), pupilLeft: safe(), pupilRight: safe(),
    lidLeft: safe(), lidRight: safe(),
  };
  controls.lidLeft.scale.y = 0.74;
  controls.lidRight.scale.y = 0.74;
  const glitches = new GlitchScheduler({ quality: options.quality, yRange: [0, 1], intensity: 1.8 });
  let reducedMotion = Boolean(options.reducedMotion);
  let frame = spriteFrameFor({ reducedMotion });
  let seconds = 0;

  const showFrame = (next) => {
    const { sheet, column, row } = next;
    material.uniforms.map.value = textures[sheet] || textures[0];
    material.uniforms.uCell.value.set(column * 0.25, 0.75 - row * 0.25);
    frame = next;
  };
  showFrame(frame);

  return {
    object,
    controls,
    hostClass: "avatar-sprite",
    update(delta) {
      seconds += delta;
      const effect = glitches.update(delta);
      const uniforms = material.uniforms;
      uniforms.uTime.value = seconds;
      uniforms.uFlicker.value = effect.flicker;
      uniforms.uTear.value = effect.bands[0]?.offset || effect.offsetX;
      uniforms.uChannel.value = effect.channel;
      uniforms.uDropStart.value = effect.dropout?.start ?? -1;
      uniforms.uDropEnd.value = effect.dropout?.end ?? -1;
      uniforms.uDropAmount.value = effect.dropout?.amount ?? 0;
      uniforms.uReveal.value = effect.reveal;
    },
    applyState(value, targets, mouth, secondsNow = seconds) {
      const next = spriteFrameFor({
        lidScale: controls.lidLeft.scale.y,
        state: value?.resolved?.state,
        emotion: value?.resolved?.emotion?.name,
        speaking: value?.runtime?.speaking,
        mouth,
        seconds: secondsNow,
        reducedMotion,
      });
      glitches.setState(value?.resolved?.state || "idle");
      if (next.animation !== frame.animation || next.index !== frame.index) showFrame(next);
    },
    setQuality(value) { glitches.setQuality(value); },
    setReducedMotion(value) {
      reducedMotion = Boolean(value);
      glitches.setReducedMotion(reducedMotion);
    },
    setGlitchesEnabled(value) { glitches.setEnabled(value); },
    triggerGlitch(type, strength = 1) { return glitches.trigger(type, strength); },
    getDiagnostics() {
      return { rig: "sprite-butler", ...frame, glitch: glitches.activeType, reducedMotion };
    },
    dispose() {
      geometry.dispose();
      material.dispose();
      for (const texture of new Set(textures)) texture.dispose();
      object.clear();
    },
  };
}

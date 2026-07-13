import { clamp } from "./math.js";

// Expression targets. Every expression is normalized against neutral so
// consumers can rely on the full key set being present; rigs that predate the
// extended fields simply ignore them.
const neutral = Object.freeze({
  browInner: 0,
  browOuter: 0,
  browAsymmetry: 0,
  browArch: 0,
  eyelid: 0,
  eyeWiden: 0,
  eyeSquint: 0,
  pupilScale: 0,
  jawOpen: 0,
  mouthWidth: 0,
  mouthCorner: 0,
  mouthRoundness: 0,
  mouthAsymmetry: 0,
  cheekRaise: 0,
  headPitch: 0,
  headYaw: 0,
  headRoll: 0,
  monocleSpeed: 0,
  monocleScale: 0,
  glitchBias: 0,
  glowBias: 0,
});

const define = (overrides) => Object.freeze({ ...neutral, ...overrides });

export const EXPRESSIONS = Object.freeze({
  neutral,
  attentive: define({ browInner: 0.12, eyeWiden: 0.12, headPitch: -0.03 }),
  warm: define({ eyelid: 0.08, mouthCorner: 0.22, cheekRaise: 0.12, glowBias: 0.06 }),
  pleased: define({ mouthCorner: 0.35, cheekRaise: 0.2, headPitch: 0.03, glowBias: 0.1 }),
  happy: define({
    mouthCorner: 0.5, cheekRaise: 0.3, eyeSquint: 0.24, mouthWidth: 0.18,
    browArch: 0.15, glowBias: 0.2, glitchBias: -0.4,
  }),
  excited: define({
    eyeWiden: 0.5, browInner: 0.3, browOuter: 0.28, browArch: 0.3,
    mouthCorner: 0.55, jawOpen: 0.2, mouthWidth: 0.24, pupilScale: 0.2,
    headPitch: -0.04, glowBias: 0.28, monocleSpeed: 0.3,
  }),
  sad: define({
    browInner: 0.48, browOuter: -0.3, mouthCorner: -0.34, eyelid: 0.2,
    headPitch: 0.09, glowBias: -0.24, pupilScale: -0.1,
  }),
  angry: define({
    browInner: -0.42, browOuter: -0.38, browArch: -0.3, eyeSquint: 0.34,
    mouthCorner: -0.2, mouthWidth: -0.22, headPitch: -0.02, glitchBias: 0.35,
  }),
  concerned: define({ browInner: 0.4, browOuter: -0.12, mouthCorner: -0.22, headPitch: 0.04 }),
  confused: define({
    browAsymmetry: 0.42, headRoll: 0.08, headYaw: 0.04,
    mouthAsymmetry: 0.3, mouthCorner: -0.06,
  }),
  apologetic: define({ browInner: 0.28, eyelid: 0.12, mouthCorner: -0.08, headPitch: 0.08, glowBias: -0.12 }),
  thinking: define({
    browAsymmetry: 0.16, eyelid: 0.06, headYaw: -0.05, headPitch: 0.04,
    mouthWidth: -0.12, monocleSpeed: 0.5,
  }),
  focused: define({ browOuter: -0.2, eyelid: 0.14, eyeSquint: 0.18, headPitch: -0.02, glitchBias: -0.3 }),
  surprised: define({
    browInner: 0.45, browOuter: 0.4, browArch: 0.35, eyeWiden: 0.55,
    jawOpen: 0.22, mouthRoundness: 0.55, headPitch: -0.05, pupilScale: 0.25,
  }),
  calm: define({
    eyelid: 0.28, mouthCorner: 0.08, browArch: 0.08,
    glitchBias: -0.6, glowBias: -0.06,
  }),
  skeptical: define({
    browAsymmetry: 0.5, eyeSquint: 0.26, mouthAsymmetry: 0.4,
    mouthCorner: 0.06, headRoll: -0.05, headYaw: 0.03,
  }),
  listening: define({ eyeWiden: 0.1, mouthWidth: -0.06, monocleSpeed: 0.15, glitchBias: -0.3 }),
  sleeping: define({
    eyelid: 0.9, browArch: -0.05, mouthCorner: -0.02,
    glowBias: -0.5, glitchBias: -0.9, monocleSpeed: -0.8,
  }),
  error: define({
    browInner: 0.32, browOuter: -0.28, eyelid: 0.18, mouthCorner: -0.28,
    glitchBias: 0.8, monocleSpeed: -0.2,
  }),
});

export const expressionFor = (name) => EXPRESSIONS[name] || EXPRESSIONS.neutral;

// Blend base toward overlay by amount; missing keys fall back to the base (or
// neutral) value so partial target objects blend safely.
export const blendTargets = (base, overlay, amount) => {
  const mix = clamp(amount);
  return Object.fromEntries(Object.keys(neutral).map((key) => {
    const from = base[key] ?? neutral[key];
    const to = overlay[key] ?? from;
    return [key, from + (to - from) * mix];
  }));
};

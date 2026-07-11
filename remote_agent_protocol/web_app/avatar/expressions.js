import { clamp } from "./math.js";

const neutral = Object.freeze({ browInner: 0, browOuter: 0, browAsymmetry: 0, eyelid: 0, eyeWiden: 0, jawOpen: 0, mouthWidth: 0, mouthCorner: 0, cheekRaise: 0, headPitch: 0, headYaw: 0, headRoll: 0 });

export const EXPRESSIONS = Object.freeze({
  neutral,
  attentive: Object.freeze({ ...neutral, browInner: 0.12, eyeWiden: 0.12, headPitch: -0.03 }),
  warm: Object.freeze({ ...neutral, eyelid: 0.08, mouthCorner: 0.22, cheekRaise: 0.12 }),
  pleased: Object.freeze({ ...neutral, mouthCorner: 0.35, cheekRaise: 0.2, headPitch: 0.03 }),
  concerned: Object.freeze({ ...neutral, browInner: 0.4, browOuter: -0.12, mouthCorner: -0.22, headPitch: 0.04 }),
  confused: Object.freeze({ ...neutral, browAsymmetry: 0.38, headRoll: 0.08, headYaw: 0.04 }),
  apologetic: Object.freeze({ ...neutral, browInner: 0.28, eyelid: 0.12, mouthCorner: -0.08, headPitch: 0.08 }),
  thinking: Object.freeze({ ...neutral, browAsymmetry: 0.16, eyelid: 0.06, headYaw: -0.05, headPitch: 0.04 }),
  focused: Object.freeze({ ...neutral, browOuter: -0.2, eyelid: 0.14, headPitch: -0.02 }),
  surprised: Object.freeze({ ...neutral, browInner: 0.45, browOuter: 0.4, eyeWiden: 0.55, jawOpen: 0.22 }),
  error: Object.freeze({ ...neutral, browInner: 0.32, browOuter: -0.28, eyelid: 0.18, mouthCorner: -0.28 }),
});

export const expressionFor = (name) => EXPRESSIONS[name] || EXPRESSIONS.neutral;
export const blendTargets = (base, overlay, amount) => Object.fromEntries(
  Object.keys(neutral).map((key) => [key, base[key] + (overlay[key] - base[key]) * clamp(amount)]),
);

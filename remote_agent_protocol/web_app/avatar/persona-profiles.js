import { normalizeName } from "./math.js";

// Persona-level character tuning consumed by the controller, gaze controller,
// and rig. Profiles are frozen; profileForPersona returns a mutable copy.
const BUTLER = Object.freeze({
  personaId: "jess",
  avatarId: "butler",
  defaultExpression: "attentive",
  speakingStyle: "formal",
  idleIntensity: 0.28,
  eyeContact: 0.82,
  expressiveness: 0.62,
  emotionBias: Object.freeze({ warm: 0.15, pleased: 0.1, surprised: -0.25, error: -0.1 }),
  blinkIntervalSeconds: Object.freeze([3.5, 7.5]),
  doubleBlinkChance: 0.12,
  saccadeIntervalSeconds: Object.freeze([1.8, 4.5]),
  saccadeIntensity: 0.18,
  speakingHeadMotion: 0.16,
  glitchIntensity: 0.9,
  glitchFrequency: 0.9,
  monocleActivity: 1,
  scanlineIntensity: 1,
  primaryColor: 0x22d3ee,
  secondaryColor: 0x38e0f0,
  errorAccent: 0xf87171,
  warningAccent: 0xfbbf24,
  speakingGlow: 1.1,
  formality: 0.9,
  mouthMotionScale: 0.9,
});

const NEUTRAL = Object.freeze({
  ...BUTLER,
  personaId: "neutral",
  defaultExpression: "neutral",
  idleIntensity: 0.2,
  eyeContact: 0.72,
  expressiveness: 0.5,
  emotionBias: Object.freeze({}),
  doubleBlinkChance: 0.06,
  glitchIntensity: 0.75,
  monocleActivity: 0.8,
  formality: 0.6,
});

export function profileForPersona(name, selectedAvatarId = "butler") {
  const source = normalizeName(name) === "jess" ? BUTLER : NEUTRAL;
  return { ...source, avatarId: selectedAvatarId || source.avatarId };
}

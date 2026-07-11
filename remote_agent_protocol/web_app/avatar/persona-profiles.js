import { normalizeName } from "./math.js";

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
});

const NEUTRAL = Object.freeze({
  ...BUTLER,
  personaId: "neutral",
  defaultExpression: "neutral",
  idleIntensity: 0.2,
  eyeContact: 0.72,
  expressiveness: 0.5,
  emotionBias: Object.freeze({}),
});

export function profileForPersona(name, selectedAvatarId = "butler") {
  const source = normalizeName(name) === "jess" ? BUTLER : NEUTRAL;
  return { ...source, avatarId: selectedAvatarId || source.avatarId };
}

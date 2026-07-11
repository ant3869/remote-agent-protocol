from __future__ import annotations

import subprocess
from pathlib import Path

files = {
    "remote_agent_protocol/web_app/avatar/package.json": '''{"type":"module"}\n''',
    "remote_agent_protocol/web_app/avatar/math.js": '''export const clamp = (value, min = 0, max = 1) => Math.min(max, Math.max(min, value));
export const damp = (current, target, lambda, delta) => current + (target - current) * (1 - Math.exp(-lambda * delta));
export const range = (pair, random = Math.random) => pair[0] + (pair[1] - pair[0]) * random();
export const normalizeName = (value) => String(value || "").trim().toLowerCase().replace(/\\s+/g, " ");
''',
    "remote_agent_protocol/web_app/avatar/avatar-settings.js": '''import { clamp } from "./math.js";

export const QUALITY = Object.freeze({
  low: Object.freeze({ maxPixelRatio: 1, targetFps: 24, antialias: false, shadows: false }),
  medium: Object.freeze({ maxPixelRatio: 1.5, targetFps: 30, antialias: true, shadows: false }),
  high: Object.freeze({ maxPixelRatio: 2, targetFps: 60, antialias: true, shadows: true }),
});

export function normalizeAvatarSettings(raw = {}, systemReducedMotion = false) {
  const quality = Object.hasOwn(QUALITY, raw.quality) ? raw.quality : "high";
  const reducedMotion = raw.reducedMotion === null || typeof raw.reducedMotion === "boolean"
    ? raw.reducedMotion
    : null;
  const intensity = Number.isFinite(raw.expressionIntensity)
    ? clamp(Number(raw.expressionIntensity))
    : 0.62;
  return {
    enabled: typeof raw.enabled === "boolean" ? raw.enabled : true,
    avatarId: /^[a-z0-9][a-z0-9_-]{0,63}$/.test(raw.avatarId || "") ? raw.avatarId : "butler",
    quality,
    lipSync: typeof raw.lipSync === "boolean" ? raw.lipSync : true,
    gaze: typeof raw.gaze === "boolean" ? raw.gaze : true,
    idleMotion: typeof raw.idleMotion === "boolean" ? raw.idleMotion : true,
    expressionIntensity: intensity,
    reducedMotion,
    effectiveReducedMotion: reducedMotion === null ? Boolean(systemReducedMotion) : reducedMotion,
    showState: typeof raw.showState === "boolean" ? raw.showState : true,
    panelCollapsed: typeof raw.panelCollapsed === "boolean" ? raw.panelCollapsed : false,
    ...QUALITY[quality],
  };
}
''',
    "remote_agent_protocol/web_app/avatar/persona-profiles.js": '''import { normalizeName } from "./math.js";

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
''',
    "remote_agent_protocol/web_app/avatar/expressions.js": '''import { clamp } from "./math.js";

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
''',
    "remote_agent_protocol/web_app/avatar/avatar-controller.js": '''const RECENT_COMPLETION_MS = 4000;

export function resolveAvatarState(runtime = {}, now = Date.now()) {
  if (runtime.error || ["failed", "stopped"].includes(runtime.session)) return "error";
  if (runtime.speaking) return "speaking";
  if (runtime.userSpeaking || ["wake_word_detected", "listening_for_command"].includes(runtime.wakePhase)) return "listening";
  if (runtime.thinking || ["transcribing", "agent_responding"].includes(runtime.wakePhase)) return "thinking";
  if ((runtime.activeAgentCount || 0) > 0) return "focused";
  if (runtime.pendingConfirmation) return "concerned";
  if (runtime.completedAt && now - runtime.completedAt <= RECENT_COMPLETION_MS) return "happy";
  if (runtime.sleeping) return "sleeping";
  return "idle";
}

export function resolveAvatarEmotion(runtime = {}, profile = {}, now = Date.now()) {
  const explicit = runtime.avatar;
  if (explicit?.emotion) {
    return { name: explicit.emotion, intensity: Math.max(0, Math.min(1, Number(explicit.intensity) || 0.5)) };
  }
  const state = resolveAvatarState(runtime, now);
  if (state === "error") return { name: "error", intensity: 0.72 };
  if (state === "concerned") return { name: "concerned", intensity: 0.58 };
  if (state === "happy") return { name: "pleased", intensity: 0.45 };
  const text = String(runtime.latestAssistantText || "").toLowerCase();
  if (/\\b(?:sorry|apologize|apologies)\\b/.test(text)) return { name: "apologetic", intensity: 0.35 };
  if (/\\b(?:uncertain|not sure|may be|might be)\\b/.test(text)) return { name: "confused", intensity: 0.25 };
  if (/\\b(?:warning|danger|failed|failure|problem)\\b/.test(text)) return { name: "concerned", intensity: 0.3 };
  return { name: profile.defaultExpression || "neutral", intensity: 0.2 };
}

export class AvatarStateController {
  constructor(profile) {
    this.profile = profile;
    this.runtime = {};
    this.state = "idle";
    this.emotion = { name: profile.defaultExpression || "neutral", intensity: 0.2 };
  }

  update(runtime, now = Date.now()) {
    this.runtime = { ...this.runtime, ...runtime };
    this.state = resolveAvatarState(this.runtime, now);
    this.emotion = resolveAvatarEmotion(this.runtime, this.profile, now);
    return { state: this.state, emotion: this.emotion };
  }
}
''',
    "tests/js/avatar-settings.test.mjs": '''import test from "node:test";
import assert from "node:assert/strict";
import { normalizeAvatarSettings } from "../../remote_agent_protocol/web_app/avatar/avatar-settings.js";

test("system motion preference is used when override is null", () => {
  assert.equal(normalizeAvatarSettings({ reducedMotion: null }, true).effectiveReducedMotion, true);
});

test("saved normal motion overrides the system preference", () => {
  assert.equal(normalizeAvatarSettings({ reducedMotion: false }, true).effectiveReducedMotion, false);
});

test("quality and intensity normalize safely", () => {
  const settings = normalizeAvatarSettings({ quality: "ultra", expressionIntensity: 4 }, false);
  assert.equal(settings.quality, "high");
  assert.equal(settings.expressionIntensity, 1);
  assert.equal(settings.maxPixelRatio, 2);
});
''',
    "tests/js/avatar-controller.test.mjs": '''import test from "node:test";
import assert from "node:assert/strict";
import { resolveAvatarEmotion, resolveAvatarState } from "../../remote_agent_protocol/web_app/avatar/avatar-controller.js";
import { profileForPersona } from "../../remote_agent_protocol/web_app/avatar/persona-profiles.js";

test("error outranks speaking and listening", () => {
  assert.equal(resolveAvatarState({ error: true, speaking: true, userSpeaking: true }, 100), "error");
});

test("speaking outranks active agent work", () => {
  assert.equal(resolveAvatarState({ speaking: true, activeAgentCount: 2 }, 100), "speaking");
});

test("pending confirmation resolves concerned", () => {
  assert.equal(resolveAvatarState({ pendingConfirmation: true }, 100), "concerned");
});

test("recent completion resolves happy until expiry", () => {
  assert.equal(resolveAvatarState({ completedAt: 90_000 }, 92_000), "happy");
  assert.equal(resolveAvatarState({ completedAt: 90_000 }, 96_000), "idle");
});

test("Jess maps to the restrained butler profile", () => {
  const profile = profileForPersona("JESS", "butler");
  assert.equal(profile.avatarId, "butler");
  assert.equal(profile.speakingStyle, "formal");
  assert.equal(profile.eyeContact, 0.82);
});

test("apology language creates a low-intensity apologetic emotion", () => {
  const profile = profileForPersona("Custom", "butler");
  const emotion = resolveAvatarEmotion({ latestAssistantText: "I’m sorry, that failed." }, profile, 100);
  assert.equal(emotion.name, "apologetic");
  assert.equal(emotion.intensity, 0.35);
});
''',
}

for name, content in files.items():
    path = Path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

subprocess.run(
    ["node", "--test", "tests/js/avatar-settings.test.mjs", "tests/js/avatar-controller.test.mjs"],
    check=True,
)
Path(__file__).unlink()
subprocess.run(["git", "add", "remote_agent_protocol/web_app/avatar", "tests/js", ".github/avatar_tasks/task6.py"], check=True)
subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
subprocess.run(["git", "commit", "-m", "feat(avatar): add state and persona behavior engine"], check=True)
subprocess.run(["git", "push", "origin", "HEAD:feature/animated-butler-avatar"], check=True)
print("TASK 6 DONE: deterministic avatar behavior engine committed")

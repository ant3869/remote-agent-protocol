const RECENT_COMPLETION_MS = 4000;

// State priority: connection loss and errors outrank conversation flow, which
// outranks background activity and rest states. Fields like `warning` and
// `passive` are honored when the runtime supplies them.
export function resolveAvatarState(runtime = {}, now = Date.now()) {
  if (runtime.connectionLost) return "disconnected";
  if (runtime.error || runtime.session === "failed") return "error";
  if (runtime.speaking) return "speaking";
  if (runtime.userSpeaking || ["wake_word_detected", "listening_for_command"].includes(runtime.wakePhase)) return "listening";
  if (runtime.wakePhase === "transcribing") return "transcribing";
  if (runtime.thinking || runtime.wakePhase === "agent_responding") return "thinking";
  if ((runtime.activeAgentCount || 0) > 0) return "focused";
  if (runtime.warning || runtime.pendingConfirmation) return "concerned";
  if (runtime.completedAt && now - runtime.completedAt <= RECENT_COMPLETION_MS) return "happy";
  if (runtime.sleeping) return "sleeping";
  if (runtime.passive) return "passive";
  return "idle";
}

const STATE_EMOTIONS = Object.freeze({
  disconnected: { name: "concerned", intensity: 0.5 },
  error: { name: "error", intensity: 0.72 },
  concerned: { name: "concerned", intensity: 0.58 },
  happy: { name: "pleased", intensity: 0.45 },
  listening: { name: "listening", intensity: 0.35 },
  transcribing: { name: "thinking", intensity: 0.3 },
  thinking: { name: "thinking", intensity: 0.4 },
  focused: { name: "focused", intensity: 0.4 },
  sleeping: { name: "sleeping", intensity: 0.85 },
  passive: { name: "calm", intensity: 0.4 },
});

export function resolveAvatarEmotion(runtime = {}, profile = {}, now = Date.now()) {
  const explicit = runtime.avatar;
  if (explicit?.emotion) {
    return { name: explicit.emotion, intensity: Math.max(0, Math.min(1, Number(explicit.intensity) || 0.5)) };
  }
  const state = resolveAvatarState(runtime, now);
  const fromState = STATE_EMOTIONS[state];
  if (fromState) return biased(fromState, profile);
  // Weak text fallback only: low intensities, easily overridden by state.
  const text = String(runtime.latestAssistantText || "").toLowerCase();
  if (/\b(?:sorry|apologize|apologies)\b/.test(text)) return biased({ name: "apologetic", intensity: 0.35 }, profile);
  if (/\b(?:uncertain|not sure|may be|might be)\b/.test(text)) return biased({ name: "confused", intensity: 0.25 }, profile);
  if (/\b(?:warning|danger|failed|failure|problem)\b/.test(text)) return biased({ name: "concerned", intensity: 0.3 }, profile);
  return biased({ name: profile.defaultExpression || "neutral", intensity: 0.2 }, profile);
}

function biased(emotion, profile) {
  const bias = profile.emotionBias?.[emotion.name] || 0;
  if (!bias) return emotion;
  return { name: emotion.name, intensity: Math.max(0.05, Math.min(1, emotion.intensity + bias)) };
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

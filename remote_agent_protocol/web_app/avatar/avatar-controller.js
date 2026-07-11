const RECENT_COMPLETION_MS = 4000;

export function resolveAvatarState(runtime = {}, now = Date.now()) {
  if (runtime.error || runtime.session === "failed") return "error";
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
  if (/\b(?:sorry|apologize|apologies)\b/.test(text)) return { name: "apologetic", intensity: 0.35 };
  if (/\b(?:uncertain|not sure|may be|might be)\b/.test(text)) return { name: "confused", intensity: 0.25 };
  if (/\b(?:warning|danger|failed|failure|problem)\b/.test(text)) return { name: "concerned", intensity: 0.3 };
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

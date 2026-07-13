import test from "node:test";
import assert from "node:assert/strict";
import { AvatarStateController, resolveAvatarEmotion, resolveAvatarState } from "../../remote_agent_protocol/web_app/avatar/avatar-controller.js";
import { profileForPersona } from "../../remote_agent_protocol/web_app/avatar/persona-profiles.js";
import { GazeController } from "../../remote_agent_protocol/web_app/avatar/gaze-controller.js";

test("disconnected outranks every other signal", () => {
  assert.equal(
    resolveAvatarState({ connectionLost: true, error: true, speaking: true, userSpeaking: true }, 100),
    "disconnected",
  );
});

test("error outranks speaking and listening", () => {
  assert.equal(resolveAvatarState({ error: true, speaking: true, userSpeaking: true }, 100), "error");
});

test("speaking outranks active agent work", () => {
  assert.equal(resolveAvatarState({ speaking: true, activeAgentCount: 2 }, 100), "speaking");
});

test("transcribing wake phase gets its own state between listening and thinking", () => {
  assert.equal(resolveAvatarState({ wakePhase: "transcribing", thinking: true }, 100), "transcribing");
  assert.equal(resolveAvatarState({ wakePhase: "transcribing", userSpeaking: true }, 100), "listening");
  assert.equal(resolveAvatarState({ wakePhase: "agent_responding" }, 100), "thinking");
});

test("active agents resolve focused", () => {
  assert.equal(resolveAvatarState({ activeAgentCount: 1 }, 100), "focused");
});

test("warning and pending confirmation resolve concerned", () => {
  assert.equal(resolveAvatarState({ warning: true }, 100), "concerned");
  assert.equal(resolveAvatarState({ pendingConfirmation: true }, 100), "concerned");
});

test("recent completion resolves happy until expiry", () => {
  assert.equal(resolveAvatarState({ completedAt: 90_000 }, 92_000), "happy");
  assert.equal(resolveAvatarState({ completedAt: 90_000 }, 96_000), "idle");
});

test("extended inactivity resolves sleeping while a stopped session stays calm", () => {
  assert.equal(resolveAvatarState({ sleeping: true, session: "stopped" }, 100), "sleeping");
  assert.equal(resolveAvatarState({ session: "stopped" }, 100), "idle");
});

test("passive runtime flag resolves the passive state", () => {
  assert.equal(resolveAvatarState({ passive: true }, 100), "passive");
  assert.equal(resolveAvatarState({ passive: true, sleeping: true }, 100), "sleeping");
});

test("explicit avatar emotion overrides state-derived emotion", () => {
  const emotion = resolveAvatarEmotion(
    { avatar: { emotion: "skeptical", intensity: 0.9 }, error: true },
    profileForPersona("Jess"),
    100,
  );
  assert.deepEqual(emotion, { name: "skeptical", intensity: 0.9 });
});

test("states map to emotions directly", () => {
  const profile = { defaultExpression: "neutral" };
  assert.equal(resolveAvatarEmotion({ userSpeaking: true }, profile, 100).name, "listening");
  assert.equal(resolveAvatarEmotion({ thinking: true }, profile, 100).name, "thinking");
  assert.equal(resolveAvatarEmotion({ sleeping: true }, profile, 100).name, "sleeping");
  assert.equal(resolveAvatarEmotion({ connectionLost: true }, profile, 100).name, "concerned");
});

test("Jess maps to the restrained butler profile", () => {
  const profile = profileForPersona("JESS", "butler");
  assert.equal(profile.avatarId, "butler");
  assert.equal(profile.speakingStyle, "formal");
  assert.equal(profile.eyeContact, 0.82);
  assert.ok(profile.glitchIntensity > 0);
  assert.ok(profile.monocleActivity > 0);
});

test("apology language creates a low-intensity apologetic emotion", () => {
  const profile = profileForPersona("Custom", "butler");
  const emotion = resolveAvatarEmotion({ latestAssistantText: "I’m sorry, that failed." }, profile, 100);
  assert.equal(emotion.name, "apologetic");
  assert.equal(emotion.intensity, 0.35);
});

test("keyword fallback stays weaker than any state emotion", () => {
  const profile = { defaultExpression: "neutral" };
  const fromText = resolveAvatarEmotion({ latestAssistantText: "there is a problem" }, profile, 100);
  const fromState = resolveAvatarEmotion({ error: true }, profile, 100);
  assert.ok(fromText.intensity < fromState.intensity);
});

test("persona emotion bias adjusts intensity within bounds", () => {
  const profile = { defaultExpression: "neutral", emotionBias: { pleased: 0.2 } };
  const emotion = resolveAvatarEmotion({ completedAt: 100 }, profile, 200);
  assert.equal(emotion.name, "pleased");
  assert.ok(Math.abs(emotion.intensity - 0.65) < 1e-9);
});

test("controller accumulates runtime patches", () => {
  const controller = new AvatarStateController(profileForPersona("Jess"));
  controller.update({ speaking: true }, 100);
  const resolved = controller.update({ userSpeaking: true }, 100);
  assert.equal(resolved.state, "speaking", "speaking persists across partial updates");
});

test("listening gaze remains close to camera and reduces saccades", () => {
  const gaze = new GazeController({ random: () => 0.5 });
  const result = gaze.update(0.016, "listening", true, false);
  assert.equal(result.enabled, true);
  assert.ok(Math.abs(result.x) <= 0.03);
  assert.ok(Math.abs(result.y) <= 0.03);
});

test("reduced motion keeps blink but suppresses large gaze offsets", () => {
  const gaze = new GazeController({ random: () => 1 });
  const result = gaze.update(8, "thinking", true, true);
  assert.ok(Math.abs(result.x) <= 0.06);
  assert.ok(Math.abs(result.y) <= 0.06);
});

import test from "node:test";
import assert from "node:assert/strict";
import { resolveAvatarEmotion, resolveAvatarState } from "../../remote_agent_protocol/web_app/avatar/avatar-controller.js";
import { profileForPersona } from "../../remote_agent_protocol/web_app/avatar/persona-profiles.js";
import { GazeController } from "../../remote_agent_protocol/web_app/avatar/gaze-controller.js";

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
  assert.ok(Math.abs(result.x) <= 0.04);
  assert.ok(Math.abs(result.y) <= 0.04);
});


test("extended inactivity resolves sleeping while a stopped session stays calm", () => {
  assert.equal(resolveAvatarState({ sleeping: true, session: "stopped" }, 100), "sleeping");
  assert.equal(resolveAvatarState({ session: "stopped" }, 100), "idle");
});

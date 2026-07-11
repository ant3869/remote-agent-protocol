import test from "node:test";
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

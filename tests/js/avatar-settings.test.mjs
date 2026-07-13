import test from "node:test";
import assert from "node:assert/strict";
import { QUALITY, normalizeAvatarSettings } from "../../remote_agent_protocol/web_app/avatar/avatar-settings.js";

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

test("legacy renderer keys survive alongside the visage block", () => {
  const settings = normalizeAvatarSettings({ quality: "medium" }, false);
  assert.equal(settings.targetFps, 30);
  assert.equal(settings.antialias, true);
  assert.equal(settings.shadows, false);
  assert.ok(settings.visage, "visage quality parameters missing");
});

test("visage quality parameters scale with tier", () => {
  const low = QUALITY.low.visage;
  const medium = QUALITY.medium.visage;
  const high = QUALITY.high.visage;
  assert.ok(low.motes < medium.motes && medium.motes < high.motes);
  assert.ok(low.hairStrands < high.hairStrands);
  assert.equal(low.channelSplit, false);
  assert.equal(medium.channelSplit, true);
  assert.equal(low.maxFragments, 0);
  assert.ok(medium.maxFragments < high.maxFragments);
  assert.equal(low.glitchBands, 1);
  assert.equal(high.pointScatter, true);
  assert.ok(low.scanlineDensity < high.scanlineDensity);
});

test("quality tiers stay frozen", () => {
  assert.ok(Object.isFrozen(QUALITY.high));
  assert.ok(Object.isFrozen(QUALITY.high.visage));
});

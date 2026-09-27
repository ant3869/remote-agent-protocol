import assert from "node:assert/strict";
import { readdirSync } from "node:fs";
import test from "node:test";

import { FRAME_NAMES } from "../../remote_agent_protocol/web_app/avatar/frame-avatar-scene.js";
import { EFFECT_FRAMES, PORTRAIT_FRAMES, jessSvg } from "../../scripts/avatar/jess-portrait.mjs";

test("Jess has every frame the avatar scene asks for, rendered and committed", () => {
  assert.deepEqual([...PORTRAIT_FRAMES, ...EFFECT_FRAMES].sort(), [...FRAME_NAMES].sort());
  const rendered = readdirSync(new URL("../../remote_agent_protocol/web_app/assets/avatars/jess/runtime_512_v1/", import.meta.url));
  assert.deepEqual(rendered.filter((n) => n.endsWith(".webp")).map((n) => n.slice(0, -5)).sort(), [...FRAME_NAMES].sort());
});

test("each frame is a 512 square SVG and mouth shapes differ from rest", () => {
  for (const name of FRAME_NAMES) {
    assert.match(jessSvg(name), /^<svg [^>]*width="512" height="512"/, name);
  }
  assert.notEqual(jessSvg("oh"), jessSvg("base"));
  assert.notEqual(jessSvg("fv"), jessSvg("e_sound"));
  assert.throws(() => jessSvg("nope"), /Unknown Jess frame/);
});

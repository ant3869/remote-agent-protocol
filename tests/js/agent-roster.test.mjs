import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("agent roster marks the persisted default backend", () => {
  const source = readFileSync("remote_agent_protocol/web_app/app.js", "utf8");
  assert.match(source, /backend === state\.status\.toolUser/);
  assert.match(source, /· Default/);
});

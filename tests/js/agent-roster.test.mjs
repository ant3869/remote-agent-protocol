import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

test("agent roster marks the persisted default backend", () => {
  const source = readFileSync("remote_agent_protocol/web_app/app.js", "utf8");
  assert.match(source, /backend === state\.status\.toolUser/);
  assert.match(source, /· Default/);
});

test("remote machines appear beside the roster, and only when configured", () => {
  const source = readFileSync("remote_agent_protocol/web_app/app.js", "utf8");
  const html = readFileSync("remote_agent_protocol/web_app/index.html", "utf8");

  assert.match(html, /id="agentMachinesPanel"[^>]*hidden/);
  assert.match(source, /state\.remoteHosts = data\.remoteHosts \|\| \[\]/);
  // An operator with one machine should never see an empty section about hosts.
  assert.match(source, /panel\.hidden = hosts\.length === 0/);
  // Offline hosts say why, rather than only that they are gone.
  assert.match(source, /host\.error \|\| "not answering"/);
});

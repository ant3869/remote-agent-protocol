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

test("cached catalogs survive both the poll and an action response", () => {
  const source = readFileSync("remote_agent_protocol/web_app/app.js", "utf8");

  // Both paths must merge, or an action response would drop the cached
  // catalogs the whole UI renders from.
  assert.match(source, /state\.status = mergeCatalogs\(data\.status\)/);
  const merges = source.match(/mergeCatalogs\(data\.status\)/g) || [];
  // Poll, action response, and the Agents page each assign status.
  assert.equal(merges.length, 3, "every path that assigns status merges first");
  assert.match(source, /version === state\.catalogVersion\) return/);
});

test("a job's log is fetched only for the job being inspected", () => {
  const source = readFileSync("remote_agent_protocol/web_app/app.js", "utf8");
  const html = readFileSync("remote_agent_protocol/web_app/index.html", "utf8");

  assert.match(source, /\/api\/agent-lines\?job=/);
  // Selecting a job and opening the page both need to ask for its log.
  const asks = source.match(/ensureJobLines\(/g) || [];
  assert.ok(asks.length >= 3, "defined plus both call sites");
  // The agents page must merge catalogs like the poll does.
  assert.match(source, /state\.status = data\.status \? mergeCatalogs\(data\.status\) : state\.status/);
  assert.match(html, /id="agentMachinesCheckBtn"/);
});

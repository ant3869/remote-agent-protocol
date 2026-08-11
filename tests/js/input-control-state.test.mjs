import assert from "node:assert/strict";
import test from "node:test";

await import("../../remote_agent_protocol/web_app/input-control-state.js");
const { createInputControlCoordinator, deriveInputControlView } = globalThis.RapInputControls;

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test("mode transition stays confirmed until rejection rolls back authoritatively", async () => {
  let status = { mode: "brain", voiceMode: "free_talk", muted: true, inputControl: { muteReady: true } };
  let connectionLost = false;
  const request = deferred();
  const renders = [];
  let calls = 0;
  const coordinator = createInputControlCoordinator({
    request: async () => { calls += 1; return request.promise; },
    getStatus: () => status,
    setStatus: (value) => { status = value; },
    getConnectionLost: () => connectionLost,
    setConnectionLost: (value) => { connectionLost = value; },
    render: (view) => renders.push(view),
  });

  const transition = coordinator.run("voice_mode", { mode: "wake_word" });
  assert.equal(status.voiceMode, "free_talk");
  assert.equal(renders.at(-1).text, "Switching to Wake Word…");
  assert.equal(renders.at(-1).disabled, true);
  assert.deepEqual(await coordinator.run("mute", { muted: false }), { ignored: true });
  assert.equal(calls, 1);

  request.resolve({
    ok: false,
    error: "The external wake-word detector could not load.",
    status: { mode: "brain", voiceMode: "free_talk", muted: true, inputControl: { muteReady: true } },
  });
  await transition;

  assert.equal(status.voiceMode, "free_talk");
  assert.equal(renders.at(-1).disabled, false);
  assert.match(renders.at(-1).text, /detector could not load/i);
  assert.match(renders.at(-1).text, /Free Talk remains active/i);
});

test("transport failure disables stale controls until authoritative reconnect", async () => {
  let status = { mode: "brain", voiceMode: "wake_word", muted: false, inputControl: { muteReady: true } };
  let connectionLost = false;
  const renders = [];
  const coordinator = createInputControlCoordinator({
    request: async () => { throw new Error("network down"); },
    getStatus: () => status,
    setStatus: (value) => { status = value; },
    getConnectionLost: () => connectionLost,
    setConnectionLost: (value) => { connectionLost = value; },
    render: (view) => renders.push(view),
  });

  await coordinator.run("mute", { muted: true });

  assert.equal(status.muted, false);
  assert.equal(connectionLost, true);
  assert.equal(renders.at(-1).disabled, true);
  assert.match(renders.at(-1).text, /connection lost/i);

  status = { ...status, muted: true };
  coordinator.connected();
  assert.equal(connectionLost, false);
  assert.equal(renders.at(-1).disabled, false);
  assert.match(renders.at(-1).text, /synchronized/i);
});

test("derived view exposes detector and external mute fail-closed states", () => {
  const detector = deriveInputControlView({
    status: { mode: "brain", voiceMode: "wake_word", muted: false, wake: { phase: "error", error: "model failed" }, inputControl: { muteReady: true } },
  });
  assert.equal(detector.tone, "error");
  assert.match(detector.text, /Wake Word unavailable/i);

  const modeSync = deriveInputControlView({
    status: { mode: "brain", voiceMode: "wake_word", muted: true, inputControl: { muteReady: true, modeReady: false } },
  });
  assert.equal(modeSync.disabled, true);
  assert.match(modeSync.text, /input mode synchronization is unconfirmed/i);

  const mute = deriveInputControlView({
    status: { mode: "brain", voiceMode: "free_talk", muted: true, inputControl: { muteReady: false } },
  });
  assert.equal(mute.tone, "error");
  assert.match(mute.text, /mute state is unconfirmed/i);
});

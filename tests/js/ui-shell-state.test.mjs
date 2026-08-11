import assert from "node:assert/strict";
import test from "node:test";

await import("../../remote_agent_protocol/web_app/ui-shell-state.js");
const { applyResponsiveDisclosures, createMomentaryControl, healthSummary, setModalState, trapModalTab, viewTitle } = globalThis.RapUiShell;

test("view titles provide stable page wayfinding", () => {
  assert.equal(viewTitle("control"), "Control Center");
  assert.equal(viewTitle("personas"), "Personas");
  assert.equal(viewTitle("unknown"), "Remote Agent Protocol");
});

test("responsive disclosures change defaults only when crossing shell modes", () => {
  const health = { open: true };
  const activity = { open: true };
  let previous = { health: null, activity: null };

  previous = applyResponsiveDisclosures({ width: 1024, health, activity, previous });
  assert.equal(health.open, false);
  assert.equal(activity.open, false);

  health.open = true;
  activity.open = true;
  previous = applyResponsiveDisclosures({ width: 768, health, activity, previous });
  assert.equal(health.open, true);
  assert.equal(activity.open, true);

  applyResponsiveDisclosures({ width: 1536, health, activity, previous });
  assert.equal(health.open, true);
  assert.equal(activity.open, true);
});

test("health summary never presents a false green state", () => {
  assert.deepEqual(healthSummary(null, true), { tone: "status-error", label: "Offline" });
  assert.deepEqual(healthSummary({ session: "ready", health: { ok: false, label: "offline" }, ttsHealth: { ok: true }, activeAgentCount: 0 }), { tone: "status-error", label: "Service failure" });
  assert.deepEqual(healthSummary({ session: "starting", health: { ok: true }, ttsHealth: { ok: true }, activeAgentCount: 0 }), { tone: "status-warning", label: "Starting" });
  assert.deepEqual(healthSummary({ session: "ready", health: { ok: true }, ttsHealth: { ok: true }, activeAgentCount: 2 }), { tone: "status-success", label: "2 agents active" });
});

test("momentary controls release on keyboard and cancellation paths", () => {
  const values = [];
  const control = createMomentaryControl((active) => values.push(active));
  const key = { key: " ", repeat: false, preventDefault() {} };
  control.keyDown(key);
  control.keyDown({ ...key, repeat: true });
  control.keyUp(key);
  control.start();
  control.cancel();
  control.stop();
  assert.deepEqual(values, [true, false, true, false]);
});

test("modal state makes the application shell inert and restores opener focus", () => {
  let focused = false;
  const shell = { inert: false };
  const body = { classList: { toggle: (_name, value) => { body.open = value; } } };
  const opener = { focus: () => { focused = true; } };

  setModalState({ shell, body, open: true, opener });
  assert.equal(shell.inert, true);
  assert.equal(body.open, true);

  setModalState({ shell, body, open: false, opener });
  assert.equal(shell.inert, false);
  assert.equal(body.open, false);
  assert.equal(focused, true);
});

test("tab trapping wraps focus inside the command palette", () => {
  const first = { focusCalled: false, focus() { this.focusCalled = true; } };
  const last = { focusCalled: false, focus() { this.focusCalled = true; } };
  const container = { querySelectorAll: () => [first, last] };
  const forward = { key: "Tab", shiftKey: false, target: last, prevented: false, preventDefault() { this.prevented = true; } };
  const backward = { key: "Tab", shiftKey: true, target: first, prevented: false, preventDefault() { this.prevented = true; } };

  assert.equal(trapModalTab(forward, container), true);
  assert.equal(forward.prevented, true);
  assert.equal(first.focusCalled, true);
  assert.equal(trapModalTab(backward, container), true);
  assert.equal(last.focusCalled, true);
});

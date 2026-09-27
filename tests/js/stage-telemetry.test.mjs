import assert from "node:assert/strict";
import test from "node:test";

await import("../../remote_agent_protocol/web_app/stage-telemetry.js");
const { fmtSecs, stepLabel, timelineView, STALL_SECS } = globalThis.RapStageTelemetry;

test("model and tool steps read as the model name and the tool in words", () => {
  assert.equal(stepLabel({ kind: "model", label: "cloud gpt-5.5" }), "gpt-5.5");
  assert.equal(stepLabel({ kind: "tool", label: "check_agents" }), "check agents");
  assert.equal(fmtSecs(1.234), "1.23s");
  assert.equal(fmtSecs(26.33), "26.3s");
  assert.equal(fmtSecs(undefined), "--");
});

test("the running step and the turn total keep counting between updates", () => {
  const timing = {
    steps: [{ kind: "model", label: "cloud gpt-5.5", secs: 1.2 }],
    active: { kind: "tool", label: "check_agents", secs: 0.5 },
    elapsed: 1.7,
    done: false,
    receivedAt: 10_000,
  };
  const view = timelineView(timing, 12_000);
  assert.equal(view.live, true);
  assert.deepEqual(view.steps.map((s) => [s.label, s.secs, s.active]), [
    ["gpt-5.5", "1.20s", false],
    ["check agents", "2.50s", true],
  ]);
  assert.equal(view.total, "3.70s");
});

test("a finished turn is frozen, and a silent or replayed one stops ticking", () => {
  const done = timelineView({ steps: [], active: null, elapsed: 4, done: true, receivedAt: 0 }, 99_000);
  assert.equal(done.live, false);
  assert.equal(done.total, "4.00s");

  const silent = { steps: [], active: { kind: "model", label: "local x", secs: 1 }, elapsed: 1, done: false, receivedAt: 0 };
  const stalled = timelineView(silent, (STALL_SECS + 1) * 1000);
  assert.equal(stalled.live, false);
  assert.equal(stalled.stalled, true);
  assert.equal(stalled.total, "1.00s");

  const replayed = timelineView({ ...silent, replayed: true }, 500);
  assert.equal(replayed.live, false);
  assert.equal(replayed.stalled, false);
  assert.equal(timelineView(null, 0), null);
});

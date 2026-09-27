(function installStageTelemetry(root) {
  "use strict";

  // A turn that stops reporting (brain restarted mid-turn) stops ticking here.
  const STALL_SECS = 180;

  function stepLabel(step) {
    if (!step) return "";
    const label = String(step.label || "");
    if (step.kind === "model") return label.replace(/^(cloud|local)\s+/, "") || "model";
    return label.replace(/_/g, " ");
  }

  function fmtSecs(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) return "--";
    return value < 10 ? `${value.toFixed(2)}s` : `${value.toFixed(1)}s`;
  }

  // Snapshot of the Butler's latest turn as it would read `nowMs` after it was
  // received: finished steps, the running one with its live seconds, and the
  // whole turn so far.
  function timelineView(timing, nowMs) {
    if (!timing || !Array.isArray(timing.steps)) return null;
    const since = Math.max(0, (nowMs - (timing.receivedAt ?? nowMs)) / 1000);
    const live = !timing.done && !timing.replayed && since < STALL_SECS;
    const extra = live ? since : 0;
    const steps = timing.steps.map((step) => ({
      kind: step.kind === "tool" ? "tool" : "model",
      label: stepLabel(step),
      secs: fmtSecs(step.secs),
      active: false,
    }));
    if (timing.active && !timing.done) {
      steps.push({
        kind: timing.active.kind === "tool" ? "tool" : "model",
        label: stepLabel(timing.active),
        secs: fmtSecs((timing.active.secs || 0) + extra),
        active: live,
      });
    }
    return {
      steps,
      total: fmtSecs((timing.elapsed || 0) + extra),
      live,
      stalled: !timing.done && !timing.replayed && !live,
    };
  }

  root.RapStageTelemetry = { fmtSecs, stepLabel, timelineView, STALL_SECS };
})(globalThis);

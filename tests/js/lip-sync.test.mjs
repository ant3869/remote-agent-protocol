import test from "node:test";
import assert from "node:assert/strict";
import { AvatarEnvelopeStream, LipSyncController } from "../../remote_agent_protocol/web_app/avatar/lip-sync.js";

test("RMS opens the jaw and release closes it", () => {
  let now = 1;
  const lip = new LipSyncController({ clock: () => now });
  lip.ingest({ rms: 0.5, peak: 0.8, voiced: true, timestamp: 1 });
  const open = lip.update(0.05, true, true);
  now = 2;
  const closing = lip.update(0.5, false, true);
  assert.ok(open.jawOpen > 0.2);
  assert.ok(closing.jawOpen < open.jawOpen);
  assert.equal(open.usingEnvelope, true);
  assert.equal(closing.usingEnvelope, false);
});

test("invalid envelope samples are ignored", () => {
  const lip = new LipSyncController({ clock: () => 1 });
  lip.ingest(null);
  lip.ingest({ rms: Number.NaN, peak: 0.4 });
  lip.ingest({ rms: 0.4 });
  assert.equal(lip.sample, null);
});

test("speaking fallback moves the mouth without telemetry", () => {
  const lip = new LipSyncController({ clock: () => 1.2 });
  const value = lip.update(0.05, true, true);
  assert.ok(value.jawOpen > 0);
  assert.equal(value.usingEnvelope, false);
});

test("synthetic fallback is phrase-like: varied amplitudes with pauses", () => {
  let now = 0;
  const lip = new LipSyncController({ clock: () => now });
  const targets = [];
  for (let i = 0; i < 400; i += 1) {
    now += 1 / 50;
    targets.push(lip.syntheticTargets().jaw);
  }
  const quiet = targets.filter((value) => value < 0.02).length;
  const loud = targets.filter((value) => value > 0.3).length;
  assert.ok(quiet > 10, `expected pauses/closures, got ${quiet} quiet samples`);
  assert.ok(loud > 40, `expected open vowels, got ${loud} loud samples`);
  // A pure sinusoid would visit its peak on a fixed cadence; phrase syllables
  // must differ in peak amplitude.
  const peaks = new Set(targets.map((value) => value.toFixed(2)));
  assert.ok(peaks.size > 20, "fallback looks like a fixed waveform");
});

test("articulation values remain bounded across noisy input", () => {
  let now = 0;
  const lip = new LipSyncController({ clock: () => now });
  for (let i = 0; i < 200; i += 1) {
    now += 1 / 30;
    if (i % 3 === 0) lip.ingest({ rms: Math.abs(Math.sin(i)), peak: 1, voiced: true });
    const value = lip.update(1 / 30, i % 2 === 0, true);
    for (const key of ["jawOpen", "mouthWidth", "roundness", "closure"]) {
      assert.ok(value[key] >= 0 && value[key] <= 1.2, `${key} out of range: ${value[key]}`);
    }
    assert.ok(Math.abs(value.asymmetry) <= 0.5);
  }
});

test("stale envelope falls back to synthetic speech", () => {
  let now = 1;
  const lip = new LipSyncController({ clock: () => now });
  lip.ingest({ rms: 0.6, peak: 0.9 });
  assert.equal(lip.update(0.05, true, true).usingEnvelope, true);
  now = 2.5;
  assert.equal(lip.update(0.05, true, true).usingEnvelope, false);
});

test("disabled lip sync keeps the mouth neutral", () => {
  const lip = new LipSyncController({ clock: () => 1 });
  lip.ingest({ rms: 0.8, peak: 1, voiced: true, timestamp: 1 });
  assert.deepEqual(lip.update(0.05, true, false), {
    jawOpen: 0, mouthWidth: 0, cheek: 0,
    roundness: 0, closure: 0, asymmetry: 0, usingEnvelope: false,
  });
});

test("closure inference fires on a sharp dip against recent history", () => {
  let now = 0;
  const lip = new LipSyncController({ clock: () => now });
  for (let i = 0; i < 10; i += 1) {
    now += 0.05;
    lip.ingest({ rms: 0.7, peak: 0.9 });
    lip.update(0.05, true, true);
  }
  now += 0.05;
  lip.ingest({ rms: 0.05, peak: 0.1 });
  let value = null;
  for (let i = 0; i < 8; i += 1) value = lip.update(0.05, true, true);
  assert.ok(value.closure > 0.3, `expected closure pulse, got ${value.closure}`);
});

test("disposing the stream closes EventSource", () => {
  const closed = [];
  class FakeEventSource {
    constructor() {}
    close() { closed.push(true); }
  }
  const stream = new AvatarEnvelopeStream(() => {}, {
    EventSourceImpl: FakeEventSource,
    setTimer: () => 7,
    clearTimer: (id) => closed.push(id),
  });
  stream.start();
  stream.dispose();
  assert.deepEqual(closed, [true]);
});

test("stream reconnect uses bounded exponential backoff", () => {
  const delays = [];
  const sources = [];
  class FakeEventSource {
    constructor() { sources.push(this); }
    close() {}
  }
  const stream = new AvatarEnvelopeStream(() => {}, {
    EventSourceImpl: FakeEventSource,
    setTimer: (_callback, delay) => { delays.push(delay); return delay; },
  });
  stream.start();
  sources[0].onerror();
  assert.equal(delays[0], 500);
  assert.equal(stream.retryMs, 1000);
  stream.dispose();
});

test("default timers are wrapped so retries do not throw Illegal invocation", () => {
  const sources = [];
  class FakeEventSource {
    constructor() { sources.push(this); }
    close() {}
  }
  const stream = new AvatarEnvelopeStream(() => {}, { EventSourceImpl: FakeEventSource });
  stream.start();
  // With raw setTimeout stored as a method this would throw in browsers;
  // the wrapper must schedule without touching `this`.
  assert.doesNotThrow(() => sources[0].onerror());
  stream.dispose();
});

test("stopping the stream closes source and clears a pending reconnect", () => {
  const events = [];
  const sources = [];
  class FakeEventSource {
    constructor() { sources.push(this); }
    close() { events.push("close"); }
  }
  const stream = new AvatarEnvelopeStream(() => {}, {
    EventSourceImpl: FakeEventSource,
    setTimer: () => 17,
    clearTimer: (id) => events.push(`clear:${id}`),
  });
  stream.start();
  sources[0].onerror();
  stream.stop();
  assert.deepEqual(events, ["close", "clear:17"]);
  assert.equal(stream.source, null);
  assert.equal(stream.timer, null);
});

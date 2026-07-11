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
});

test("speaking fallback moves the mouth without telemetry", () => {
  const lip = new LipSyncController({ clock: () => 1.2 });
  const value = lip.update(0.05, true, true);
  assert.ok(value.jawOpen > 0);
  assert.equal(value.usingEnvelope, false);
});

test("disabled lip sync keeps the mouth neutral", () => {
  const lip = new LipSyncController({ clock: () => 1 });
  lip.ingest({ rms: 0.8, peak: 1, voiced: true, timestamp: 1 });
  assert.deepEqual(lip.update(0.05, true, false), { jawOpen: 0, mouthWidth: 0, cheek: 0, usingEnvelope: false });
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

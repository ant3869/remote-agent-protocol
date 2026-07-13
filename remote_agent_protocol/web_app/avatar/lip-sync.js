import { clamp, damp } from "./math.js";

const ENVELOPE_FRESH_SECONDS = 0.35;
const HISTORY_SECONDS = 0.6;

// Deterministic hash for the synthetic phrase generator.
const hash = (n) => {
  const x = Math.sin(n * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
};

export class LipSyncController {
  constructor({ clock = () => performance.now() / 1000 } = {}) {
    this.clock = clock;
    this.sample = null;
    this.lastEnvelopeAt = 0;
    this.jaw = 0;
    this.width = 0;
    this.round = 0;
    this.close = 0;
    this.history = [];
  }

  ingest(sample) {
    if (!sample || !Number.isFinite(sample.rms) || !Number.isFinite(sample.peak)) return;
    this.sample = sample;
    const now = this.clock();
    this.lastEnvelopeAt = now;
    this.history.push({ at: now, rms: sample.rms });
    const cutoff = now - HISTORY_SECONDS;
    while (this.history.length && this.history[0].at < cutoff) this.history.shift();
  }

  // Synthetic phrase generator: smoothed syllable pulses with occasional
  // consonant closures and brief pauses, alternating narrow and broad shapes.
  // Purely clock-driven, so it is deterministic and never claims to be
  // viseme-accurate.
  syntheticTargets() {
    const t = this.clock();
    const rate = 3.4;
    const n = Math.floor(t * rate);
    const phase = t * rate - n;
    const kind = hash(n * 3 + 1);
    if (kind < 0.14) {
      // Brief pause between words.
      return { jaw: 0, width: 0, round: 0, close: 0 };
    }
    if (kind < 0.32) {
      // Consonant closure: lips nearly shut for the syllable.
      return { jaw: 0.04, width: 0.05, round: 0, close: 0.85 };
    }
    const amplitude = 0.3 + 0.5 * hash(n);
    const shape = Math.pow(Math.sin(Math.PI * Math.min(1, phase * 1.2)), 0.85);
    const broad = hash(n * 7 + 3) > 0.5;
    return {
      jaw: amplitude * shape,
      width: broad ? 0.18 * shape : -0.05 * shape,
      round: broad ? 0.05 : 0.4 * shape,
      close: 0,
    };
  }

  update(delta, speaking, enabled) {
    if (!enabled) {
      this.jaw = 0;
      this.width = 0;
      this.round = 0;
      this.close = 0;
      return {
        jawOpen: 0, mouthWidth: 0, cheek: 0,
        roundness: 0, closure: 0, asymmetry: 0, usingEnvelope: false,
      };
    }
    const now = this.clock();
    const fresh = this.sample && now - this.lastEnvelopeAt < ENVELOPE_FRESH_SECONDS;
    let jawTarget = 0;
    let widthTarget = 0;
    let roundTarget = 0;
    let closeTarget = 0;
    if (fresh) {
      const sharp = Math.max(0, this.sample.peak - this.sample.rms);
      jawTarget = clamp(this.sample.rms * 1.45 + sharp * 0.22);
      widthTarget = clamp(sharp * 0.45);
      roundTarget = clamp(this.sample.rms * 1.1 - sharp * 1.6);
      // Closure inference: a sharp dip against the recent envelope ceiling.
      let recentMax = 0;
      for (const entry of this.history) recentMax = Math.max(recentMax, entry.rms);
      closeTarget = recentMax > 0.1 && this.sample.rms < recentMax * 0.3 ? 0.8 : 0;
    } else if (speaking) {
      const synthetic = this.syntheticTargets();
      jawTarget = synthetic.jaw;
      widthTarget = synthetic.width;
      roundTarget = synthetic.round;
      closeTarget = synthetic.close;
    }
    // Jaw opens faster than it closes.
    this.jaw = damp(this.jaw, jawTarget, jawTarget > this.jaw ? 24 : 10, delta);
    this.width = damp(this.width, widthTarget, 12, delta);
    this.round = damp(this.round, roundTarget, 10, delta);
    this.close = damp(this.close, closeTarget, 18, delta);
    const asymmetry = speaking || fresh ? Math.sin(now * 1.9) * 0.3 : 0;
    return {
      jawOpen: this.jaw,
      mouthWidth: fresh ? this.width : this.width + this.jaw * 0.08,
      cheek: this.jaw * 0.18,
      roundness: clamp(this.round),
      closure: clamp(this.close),
      asymmetry,
      usingEnvelope: Boolean(fresh),
    };
  }
}

export class AvatarEnvelopeStream {
  constructor(onSample, options = {}) {
    this.onSample = onSample;
    this.EventSourceImpl = options.EventSourceImpl || EventSource;
    // Wrap the global timer functions: storing them as methods and calling
    // this.setTimer(...) would otherwise invoke them with the stream as
    // `this`, which browsers reject ("Illegal invocation").
    this.setTimer = options.setTimer || ((handler, delay) => setTimeout(handler, delay));
    this.clearTimer = options.clearTimer || ((id) => clearTimeout(id));
    this.url = options.url || "/api/avatar-audio";
    this.source = null;
    this.timer = null;
    this.retryMs = 500;
    this.disposed = false;
    this.enabled = false;
  }

  start() {
    if (this.disposed || this.source) return;
    this.enabled = true;
    const source = new this.EventSourceImpl(this.url);
    this.source = source;
    source.addEventListener?.("envelope", (event) => {
      this.retryMs = 500;
      try {
        this.onSample(JSON.parse(event.data));
      } catch (error) {
        console.warn("Invalid avatar envelope", error);
      }
    });
    source.onerror = () => {
      source.close();
      if (this.source === source) this.source = null;
      if (this.disposed || !this.enabled) return;
      const delay = this.retryMs;
      this.retryMs = Math.min(8000, this.retryMs * 2);
      this.timer = this.setTimer(() => {
        this.timer = null;
        this.start();
      }, delay);
    };
  }

  stop() {
    this.enabled = false;
    if (this.timer !== null) this.clearTimer(this.timer);
    this.timer = null;
    this.source?.close();
    this.source = null;
    this.retryMs = 500;
  }

  dispose() {
    this.disposed = true;
    this.stop();
  }
}

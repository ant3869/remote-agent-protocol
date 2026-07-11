import { clamp, damp } from "./math.js";

export class LipSyncController {
  constructor({ clock = () => performance.now() / 1000 } = {}) {
    this.clock = clock;
    this.sample = null;
    this.lastEnvelopeAt = 0;
    this.jaw = 0;
  }

  ingest(sample) {
    if (!sample || !Number.isFinite(sample.rms) || !Number.isFinite(sample.peak)) return;
    this.sample = sample;
    this.lastEnvelopeAt = this.clock();
  }

  update(delta, speaking, enabled) {
    if (!enabled) {
      this.jaw = 0;
      return { jawOpen: 0, mouthWidth: 0, cheek: 0, usingEnvelope: false };
    }
    const fresh = this.sample && this.clock() - this.lastEnvelopeAt < 0.35;
    const fallback = speaking ? 0.12 + Math.abs(Math.sin(this.clock() * 10.5)) * 0.16 : 0;
    const target = fresh
      ? clamp(this.sample.rms * 1.45 + Math.max(0, this.sample.peak - this.sample.rms) * 0.22)
      : fallback;
    this.jaw = damp(this.jaw, target, target > this.jaw ? 24 : 10, delta);
    return {
      jawOpen: this.jaw,
      mouthWidth: fresh ? clamp((this.sample.peak - this.sample.rms) * 0.45) : this.jaw * 0.15,
      cheek: this.jaw * 0.18,
      usingEnvelope: Boolean(fresh),
    };
  }
}

export class AvatarEnvelopeStream {
  constructor(onSample, options = {}) {
    this.onSample = onSample;
    this.EventSourceImpl = options.EventSourceImpl || EventSource;
    this.setTimer = options.setTimer || setTimeout;
    this.clearTimer = options.clearTimer || clearTimeout;
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

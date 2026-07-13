// Stateful glitch scheduler for the holographic butler. Pure logic (no THREE):
// the rig maps each frame descriptor onto shader uniforms and echo groups.
// All randomness flows through the injected random function so tests can pin
// the schedule; time only advances through update(delta).

export const GLITCH_TYPES = Object.freeze([
  "micro-flicker",
  "horizontal-tear",
  "channel-split",
  "transmission-dropout",
  "point-scatter",
  "monocle-desync",
  "data-fragment",
  "reconstruction",
]);

const DURATIONS = Object.freeze({
  "micro-flicker": [0.03, 0.09],
  "horizontal-tear": [0.08, 0.22],
  "channel-split": [0.05, 0.16],
  "transmission-dropout": [0.1, 0.3],
  "point-scatter": [0.15, 0.4],
  "monocle-desync": [0.1, 0.25],
  "data-fragment": [0.18, 0.5],
  reconstruction: [1.15, 1.45],
});

// Per-state event cadence and type weights. "Destructive" types thin out while
// speaking and vanish while sleeping; error keeps the face readable by favoring
// tears/desync over full dropouts.
const STATE_PROFILES = Object.freeze({
  idle: { interval: [4, 10], strength: 0.7, weights: { "micro-flicker": 0.66, "horizontal-tear": 0.09, "transmission-dropout": 0.06, "monocle-desync": 0.07, "channel-split": 0.04, "data-fragment": 0.05, "point-scatter": 0.03 } },
  listening: { interval: [7, 14], strength: 0.5, weights: { "micro-flicker": 0.85, "monocle-desync": 0.1, "horizontal-tear": 0.05 } },
  transcribing: { interval: [5, 10], strength: 0.55, weights: { "micro-flicker": 0.7, "horizontal-tear": 0.15, "monocle-desync": 0.15 } },
  thinking: { interval: [3, 7], strength: 0.65, weights: { "micro-flicker": 0.5, "horizontal-tear": 0.22, "monocle-desync": 0.16, "data-fragment": 0.08, "channel-split": 0.04 } },
  speaking: { interval: [6, 12], strength: 0.45, weights: { "micro-flicker": 0.8, "monocle-desync": 0.14, "horizontal-tear": 0.06 } },
  focused: { interval: [6, 12], strength: 0.5, weights: { "micro-flicker": 0.68, "monocle-desync": 0.26, "horizontal-tear": 0.06 } },
  concerned: { interval: [3.5, 8], strength: 0.6, weights: { "micro-flicker": 0.55, "transmission-dropout": 0.2, "horizontal-tear": 0.15, "monocle-desync": 0.1 } },
  happy: { interval: [8, 16], strength: 0.35, weights: { "micro-flicker": 0.92, "monocle-desync": 0.08 } },
  error: { interval: [0.9, 2.2], strength: 1, weights: { "horizontal-tear": 0.3, "monocle-desync": 0.2, "channel-split": 0.18, "transmission-dropout": 0.14, "micro-flicker": 0.1, "data-fragment": 0.08 } },
  disconnected: { interval: [2.2, 4.5], strength: 0.85, weights: { "transmission-dropout": 0.34, "channel-split": 0.22, "horizontal-tear": 0.22, "micro-flicker": 0.22 } },
  sleeping: { interval: [20, 40], strength: 0.2, weights: { "micro-flicker": 1 } },
});

const QUALITY_ALLOWED = Object.freeze({
  low: Object.freeze(["micro-flicker", "horizontal-tear", "transmission-dropout", "monocle-desync", "reconstruction"]),
  medium: Object.freeze(GLITCH_TYPES.filter((type) => type !== "point-scatter")),
  high: GLITCH_TYPES,
});

const IDLE_FRAME = Object.freeze({
  type: null, phase: 0, strength: 0, seed: 0,
  flicker: 0, offsetX: 0, bands: Object.freeze([]),
  dropout: null, channel: 0, scatter: 0, fragments: 0, monocle: 0, reveal: 1,
});

const pick = (pair, random) => pair[0] + (pair[1] - pair[0]) * random();

export class GlitchScheduler {
  constructor({ random = Math.random, yRange = [0.1, 2.3], quality = "high", intensity = 1 } = {}) {
    this.random = random;
    this.yRange = yRange;
    this.quality = QUALITY_ALLOWED[quality] ? quality : "high";
    this.intensity = intensity;
    this.state = "idle";
    this.reducedMotion = false;
    this.enabled = true;
    this.active = null;
    this.nextIn = pick(STATE_PROFILES.idle.interval, this.random);
    this.frame = { ...IDLE_FRAME, bands: [] };
  }

  setState(state) {
    if (this.state === state) return;
    this.state = STATE_PROFILES[state] ? state : "idle";
    const profile = STATE_PROFILES[this.state];
    this.nextIn = Math.min(this.nextIn, pick(profile.interval, this.random));
  }

  setReducedMotion(value) { this.reducedMotion = Boolean(value); }

  setQuality(quality) { if (QUALITY_ALLOWED[quality]) this.quality = quality; }

  setIntensity(value) { if (Number.isFinite(value)) this.intensity = Math.max(0, value); }

  setEnabled(value) {
    this.enabled = Boolean(value);
    if (!this.enabled) this.active = null;
  }

  get activeType() { return this.active?.type ?? null; }

  trigger(type, strength = 1) {
    if (!GLITCH_TYPES.includes(type)) return false;
    let applied = type;
    if (this.reducedMotion && type !== "reconstruction") applied = "micro-flicker";
    if (!QUALITY_ALLOWED[this.quality].includes(applied)) applied = "horizontal-tear";
    if (this.reducedMotion) applied = applied === "reconstruction" ? "reconstruction" : "micro-flicker";
    this.active = {
      type: applied,
      elapsed: 0,
      duration: pick(DURATIONS[applied], this.random),
      strength: Math.max(0.05, Math.min(1.5, strength)),
      seed: this.random() * 1000,
      step: -1,
      bands: [],
      dropout: null,
    };
    return true;
  }

  scheduleNext() {
    const profile = STATE_PROFILES[this.state] || STATE_PROFILES.idle;
    this.nextIn = pick(profile.interval, this.random) / Math.max(0.2, this.intensity);
  }

  chooseType() {
    const profile = STATE_PROFILES[this.state] || STATE_PROFILES.idle;
    if (this.reducedMotion) return "micro-flicker";
    const allowed = QUALITY_ALLOWED[this.quality];
    const entries = Object.entries(profile.weights).filter(([type]) => allowed.includes(type));
    const total = entries.reduce((sum, [, weight]) => sum + weight, 0);
    let roll = this.random() * total;
    for (const [type, weight] of entries) {
      roll -= weight;
      if (roll <= 0) return type;
    }
    return "micro-flicker";
  }

  update(delta) {
    const frame = this.frame;
    frame.type = null;
    frame.phase = 0;
    frame.strength = 0;
    frame.seed = 0;
    frame.flicker = 0;
    frame.offsetX = 0;
    frame.bands.length = 0;
    frame.dropout = null;
    frame.channel = 0;
    frame.scatter = 0;
    frame.fragments = 0;
    frame.monocle = 0;
    frame.reveal = 1;
    if (!this.enabled) return frame;

    if (!this.active) {
      this.nextIn -= delta;
      if (this.nextIn <= 0) {
        const profile = STATE_PROFILES[this.state] || STATE_PROFILES.idle;
        this.trigger(this.chooseType(), profile.strength * (0.7 + 0.5 * this.random()) * this.intensity);
        this.scheduleNext();
      }
      if (!this.active) return frame;
    }

    const active = this.active;
    active.elapsed += delta;
    const phase = Math.min(1, active.elapsed / active.duration);
    const envelope = Math.sin(Math.PI * Math.min(1, phase));
    const strength = active.strength * (this.reducedMotion && active.type !== "reconstruction" ? 0.5 : 1);
    frame.type = active.type;
    frame.phase = phase;
    frame.strength = strength;
    frame.seed = active.seed;

    // Tears/dropouts re-roll their band layout a few times mid-event so the
    // displacement looks stepped like a sync fault, not a smooth wobble.
    const step = Math.floor(phase * 3);
    const stepChanged = step !== active.step;
    active.step = step;
    const [yMin, yMax] = this.yRange;
    const ySpan = yMax - yMin;

    switch (active.type) {
      case "micro-flicker": {
        frame.flicker = (this.random() - 0.35) * 0.9 * strength;
        if (!this.reducedMotion) frame.offsetX = (this.random() - 0.5) * 0.02 * strength;
        break;
      }
      case "horizontal-tear": {
        if (stepChanged) {
          active.bands = [];
          const count = 1 + (this.random() < 0.4 ? 1 : 0);
          for (let i = 0; i < count; i += 1) {
            const start = yMin + this.random() * ySpan * 0.85;
            active.bands.push({
              start,
              end: start + 0.04 + this.random() * 0.12,
              offset: (this.random() - 0.5) * 0.16 * strength,
            });
          }
        }
        for (const band of active.bands) frame.bands.push(band);
        frame.flicker = -0.12 * envelope * strength;
        break;
      }
      case "channel-split": {
        frame.channel = envelope * strength;
        frame.flicker = -0.08 * envelope * strength;
        break;
      }
      case "transmission-dropout": {
        if (stepChanged) {
          const start = yMin + this.random() * ySpan * 0.8;
          active.dropout = { start, end: start + 0.12 + this.random() * 0.3, amount: 0.55 + 0.4 * this.random() };
        }
        frame.dropout = active.dropout;
        frame.flicker = -0.1 * envelope * strength;
        break;
      }
      case "point-scatter": {
        frame.scatter = envelope * strength;
        break;
      }
      case "monocle-desync": {
        frame.monocle = envelope * strength;
        break;
      }
      case "data-fragment": {
        frame.fragments = envelope * strength;
        break;
      }
      case "reconstruction": {
        const eased = 1 - Math.pow(1 - phase, 3);
        frame.reveal = eased;
        if (!this.reducedMotion) {
          frame.channel = (1 - phase) * 0.5 * strength;
          if (stepChanged) {
            const start = yMin + this.random() * ySpan * eased;
            active.dropout = { start, end: start + 0.1 + this.random() * 0.2, amount: 0.6 };
          }
          frame.dropout = phase < 0.85 ? active.dropout : null;
        }
        frame.flicker = (1 - phase) * 0.35 * (this.random() - 0.4);
        break;
      }
      default:
        break;
    }

    if (phase >= 1) {
      this.active = null;
      if (this.nextIn <= 0) this.scheduleNext();
    }
    return frame;
  }
}

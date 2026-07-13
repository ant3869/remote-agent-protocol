import { clamp, range } from "./math.js";

const BLINK_SECONDS = 0.14;
const DEFAULT_BLINK_INTERVAL = [3.5, 7.5];
const DEFAULT_SACCADE_INTERVAL = [1.8, 4.5];

// Autonomous saccades and blinking. Deterministic when a random function is
// injected; profile (optional) supplies persona blink/saccade pacing and a
// double-blink chance.
export class GazeController {
  constructor({ random = Math.random } = {}) {
    this.random = random;
    this.timeToBlink = range(DEFAULT_BLINK_INTERVAL, random);
    this.blinkTime = 0;
    this.doubleBlinkFollowup = false;
    this.timeToSaccade = range(DEFAULT_SACCADE_INTERVAL, random);
    this.x = 0;
    this.y = 0;
  }

  update(delta, state, enabled, reducedMotion, profile = {}) {
    if (!enabled) return { enabled: false, x: 0, y: 0, blink: 0, blinkPhase: 0 };
    const blinkInterval = Array.isArray(profile.blinkIntervalSeconds)
      ? profile.blinkIntervalSeconds
      : DEFAULT_BLINK_INTERVAL;
    const saccadeInterval = Array.isArray(profile.saccadeIntervalSeconds)
      ? profile.saccadeIntervalSeconds
      : DEFAULT_SACCADE_INTERVAL;

    this.timeToBlink -= delta;
    if (this.timeToBlink <= 0 && this.blinkTime <= 0) {
      this.blinkTime = BLINK_SECONDS;
      // Occasionally pair blinks: shorten the gap so a quick second blink
      // follows, and never chain a third off the follow-up.
      if (!this.doubleBlinkFollowup && this.random() < (profile.doubleBlinkChance ?? 0)) {
        this.timeToBlink = 0.28;
        this.doubleBlinkFollowup = true;
      } else {
        this.doubleBlinkFollowup = false;
        // Listening keeps the gaze steady with slower blinks.
        const interval = state === "listening"
          ? [blinkInterval[0] * 1.4, blinkInterval[1] * 1.25]
          : blinkInterval;
        this.timeToBlink = range(interval, this.random);
      }
    }
    let blink = 0;
    let blinkPhase = 0;
    if (this.blinkTime > 0) {
      this.blinkTime -= delta;
      blinkPhase = clamp(1 - Math.max(0, this.blinkTime) / BLINK_SECONDS);
      blink = Math.sin(Math.PI * blinkPhase);
    }

    this.timeToSaccade -= delta;
    if (this.timeToSaccade <= 0) {
      // saccadeIntensity is calibrated so the butler profile (0.18) is 1x.
      const intensity = Math.min(1.6, Math.max(0.4, (profile.saccadeIntensity ?? 0.18) / 0.18));
      const limit = (reducedMotion || state === "listening" ? 0.03
        : state === "thinking" ? 0.12
          : 0.07) * intensity;
      this.x = (this.random() * 2 - 1) * limit;
      this.y = (this.random() * 2 - 1) * limit;
      if (state === "thinking" && !reducedMotion) {
        // Recall gaze: up and to the side.
        this.y = Math.abs(this.y) + 0.03;
        this.x += Math.sign(this.x || 1) * 0.03;
      }
      const interval = state === "listening"
        ? [saccadeInterval[0] * 1.6, saccadeInterval[1] * 1.5]
        : saccadeInterval;
      this.timeToSaccade = range(interval, this.random);
    }
    return { enabled: true, x: this.x, y: this.y, blink, blinkPhase };
  }
}

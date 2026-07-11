import { clamp, range } from "./math.js";

export class GazeController {
  constructor({ random = Math.random } = {}) {
    this.random = random;
    this.timeToBlink = range([3.5, 7.5], random);
    this.blinkTime = 0;
    this.timeToSaccade = range([1.8, 4.5], random);
    this.x = 0;
    this.y = 0;
  }

  update(delta, state, enabled, reducedMotion) {
    if (!enabled) return { enabled: false, x: 0, y: 0, blink: 0 };
    this.timeToBlink -= delta;
    if (this.timeToBlink <= 0 && this.blinkTime <= 0) {
      this.blinkTime = 0.14;
      this.timeToBlink = range(state === "listening" ? [5, 9] : [3.5, 7.5], this.random);
    }
    let blink = 0;
    if (this.blinkTime > 0) {
      this.blinkTime -= delta;
      const phase = clamp(1 - Math.max(0, this.blinkTime) / 0.14);
      blink = Math.sin(Math.PI * phase);
    }
    this.timeToSaccade -= delta;
    if (this.timeToSaccade <= 0) {
      const limit = reducedMotion || state === "listening" ? 0.03 : state === "thinking" ? 0.12 : 0.07;
      this.x = (this.random() * 2 - 1) * limit;
      this.y = (this.random() * 2 - 1) * limit;
      if (state === "thinking" && !reducedMotion) this.y -= 0.04;
      this.timeToSaccade = range([1.8, 4.5], this.random);
    }
    return { enabled: true, x: this.x, y: this.y, blink };
  }
}

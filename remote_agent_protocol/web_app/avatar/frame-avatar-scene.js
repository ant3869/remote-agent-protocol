import { AvatarEnvelopeStream } from "./lip-sync.js";

const ASSET_BASE = "/assets/avatars/butler/runtime_512_v1/";
// Asset responses are immutable for a year.  Advance this whenever a delivery
// fix changes their availability so a running browser cannot reuse a failed
// (for example, previously wrong-MIME) response from an earlier server.
const ASSET_REVISION = "20260920";
const FRAME_LOAD_TIMEOUT_MS = 6000;
const FRAME_INTERVAL_MS = 1000 / 30;
// Mouth shapes change on speech timing; everything else is an expression
// change that reads better as a slower, eased dissolve.
const VISEMES = new Set(["base", "halfsmile", "ah_small", "e_sound", "oh", "oo", "open", "fv", "grit"]);
const VISEME_FADE_MS = 40;
const EXPRESSION_FADE_MS = 150;
const VISEME_HOLD_MS = 60;
const LEVEL_ATTACK_MS = 25;
const LEVEL_RELEASE_MS = 90;
const POSE_SETTLE_MS = 240;
const MATERIALIZE_DURATIONS = [55, 55, 55, 55, 55, 72, 72, 72, 72, 72, 95, 110, 220];
const FAILURE_DURATIONS = [80, 65, 65, 75, 70, 75, 80, 100, 650];

export const FRAME_NAMES = Object.freeze([
  "base", "halfsmile", "smile", "mid_laugh", "full_laugh", "confused", "angry",
  "lookup", "lookdown", "glow_eyes", "eyes_mid_close", "eyes_closed",
  "eyes_closed_smile", "e_sound", "oh", "open", "grit", "ah_small", "oo", "fv",
  ...Array.from({ length: 13 }, (_, index) => `materialize_${String(index + 1).padStart(2, "0")}`),
  ...Array.from({ length: 7 }, (_, index) => `glitch_${String(index + 1).padStart(2, "0")}`),
]);

const STATE_MAP = Object.freeze({
  idle: "idle", passive: "idle", sleeping: "sleeping", listening: "listening",
  transcribing: "thinking", thinking: "thinking", focused: "working",
  concerned: "waiting", happy: "completed", error: "failed", disconnected: "failed",
  speaking: "speaking",
});

const STATE_FRAMES = Object.freeze({
  idle: "base", sleeping: "eyes_closed", listening: "halfsmile", thinking: "lookup",
  speaking: "base", working: "glow_eyes", waiting: "confused", completed: "smile",
  failed: "lookdown",
});

const CRITICAL_FRAMES = Object.freeze([
  "base", "halfsmile", "smile", "confused", "lookup", "lookdown", "glow_eyes",
  "eyes_mid_close", "eyes_closed", "eyes_closed_smile", "e_sound", "oh", "open",
  "ah_small", "oo",
]);

export function criticalFrameNames() {
  return [...CRITICAL_FRAMES];
}

export function frameUrls(base = ASSET_BASE) {
  return Object.fromEntries(
    FRAME_NAMES.map((name) => [name, `${base}${name}.webp?v=${ASSET_REVISION}`]),
  );
}

export function stateForResolved(state) {
  if (STATE_MAP[state]) return STATE_MAP[state];
  return Object.hasOwn(STATE_FRAMES, state) ? state : "idle";
}

export function transitionMs(previous, next) {
  return VISEMES.has(previous) && VISEMES.has(next) ? VISEME_FADE_MS : EXPRESSION_FADE_MS;
}

// Exponential approach toward ``target`` over ``dtMs``: fast when rising
// (a syllable onset) and slower when falling, so the mouth doesn't chatter.
export function smoothToward(current, target, dtMs, riseMs = LEVEL_ATTACK_MS, fallMs = LEVEL_RELEASE_MS) {
  const tau = target > current ? riseMs : fallMs;
  if (!(dtMs > 0) || !(tau > 0)) return target;
  return current + (target - current) * (1 - Math.exp(-dtMs / tau));
}

function easeInOut(value) {
  const t = Math.max(0, Math.min(1, value));
  return t * t * (3 - 2 * t);
}

export function frameForState(state) {
  return STATE_FRAMES[state] || "base";
}

export function frameForLevel(level) {
  const value = Math.max(0, Math.min(1, Number(level) || 0));
  if (value < 0.055) return "base";
  if (value < 0.20) return "halfsmile";
  if (value < 0.36) return "ah_small";
  if (value < 0.52) return "e_sound";
  if (value < 0.70) return "oh";
  return "open";
}

function sequence(names, durations, startedAt, onComplete) {
  let elapsed = 0;
  return {
    names,
    offsets: durations.map((duration) => (elapsed += duration)),
    startedAt,
    onComplete,
  };
}

function randomBetween(minimum, maximum) {
  return minimum + Math.random() * (maximum - minimum);
}

export async function preloadFrames(
  urls,
  names = FRAME_NAMES,
  ImageImpl = Image,
  timeoutMs = FRAME_LOAD_TIMEOUT_MS,
) {
  const entries = await Promise.all(names.map((name) => new Promise((resolve, reject) => {
    const image = new ImageImpl();
    const timeout = setTimeout(
      () => reject(new Error(`Timed out loading Butler frame: ${name}`)),
      timeoutMs,
    );
    image.onload = () => {
      clearTimeout(timeout);
      resolve([name, image]);
    };
    image.onerror = () => {
      clearTimeout(timeout);
      reject(new Error(`Unable to load Butler frame: ${name}`));
    };
    image.src = urls[name];
  })));
  return Object.fromEntries(entries);
}

export async function createAvatarScene(host, settings) {
  if (!host) throw new Error("Avatar canvas host is missing");
  const canvas = document.createElement("canvas");
  canvas.width = 512;
  canvas.height = 512;
  canvas.setAttribute("aria-hidden", "true");
  const context = canvas.getContext("2d", { alpha: true });
  if (!context) throw new Error("2D canvas is unavailable");
  const urls = frameUrls();
  // A companion that has already loaded its base portrait is useful even if a
  // secondary expression was evicted from cache or arrives late.  Keeping the
  // base frame mandatory preserves the static fallback for a genuinely broken
  // asset path without holding first paint hostage to every animation frame.
  const images = await preloadFrames(urls, ["base"]);
  const optionalFrames = await Promise.allSettled(
    CRITICAL_FRAMES.filter((name) => name !== "base").map(async (name) => {
      const loaded = await preloadFrames(urls, [name]);
      return [name, loaded[name]];
    }),
  );
  for (const result of optionalFrames) {
    if (result.status === "fulfilled") images[result.value[0]] = result.value[1];
  }
  host.classList.add("avatar-frame-butler");
  host.replaceChildren(canvas);
  let currentSettings = settings;
  let runtime = {};
  let resolvedState = "idle";
  let visualState = "idle";
  let previousResolvedState = null;
  let currentFrame = "base";
  let previousFrame = "base";
  let frameChangedAt = performance.now();
  let effect = null;
  let blink = null;
  let nextBlinkAt = 0;
  let glance = null;
  let nextGlanceAt = 0;
  let audioTarget = 0;
  let audioLevel = 0;
  let speechFrame = "base";
  let speechFrameAt = 0;
  let lastDrawAt = 0;
  let pose = { y: 0, scale: 1, angle: 0 };
  let audioAt = -Infinity;
  let fallbackFrame = "base";
  let nextFallbackAt = 0;
  let visible = true;
  let disposed = false;
  let animationFrame = 0;
  let debugState = null;
  let debugLevel = null;

  const reducedMotion = () => Boolean(currentSettings.effectiveReducedMotion);
  const scheduleBlink = (now = performance.now()) => {
    nextBlinkAt = now + randomBetween(2200, 5500);
  };
  const scheduleGlance = (now = performance.now()) => {
    nextGlanceAt = now + randomBetween(9000, 18000);
  };
  const play = (names, durations, onComplete) => {
    if (reducedMotion()) {
      effect = null;
      onComplete?.();
      return;
    }
    effect = sequence(names, durations, performance.now(), onComplete);
  };
  const playMaterialize = () => play(
    Array.from({ length: 13 }, (_, index) => `materialize_${String(index + 1).padStart(2, "0")}`),
    MATERIALIZE_DURATIONS,
  );
  const playFailure = () => play(
    ["base", ...Array.from({ length: 7 }, (_, index) => `glitch_${String(index + 1).padStart(2, "0")}`), "lookdown"],
    FAILURE_DURATIONS,
  );
  const playCompleted = () => play(
    ["smile", "eyes_closed_smile", "smile"],
    [350, 180, 900],
    () => { visualState = "idle"; },
  );

  const enterState = (next, force = false) => {
    if (!force && next === previousResolvedState) return;
    previousResolvedState = next;
    resolvedState = next;
    visualState = stateForResolved(next);
    effect = null;
    blink = null;
    glance = null;
    fallbackFrame = "base";
    nextFallbackAt = performance.now();
    scheduleBlink();
    scheduleGlance();
    if (visualState === "completed") playCompleted();
    else if (visualState === "failed") playFailure();
  };

  const stream = new AvatarEnvelopeStream((sample) => {
    const rms = Math.max(0, Math.min(1, Number(sample?.rms) || 0));
    const peak = Math.max(rms, Math.min(1, Number(sample?.peak) || 0));
    audioTarget = Math.max(0, Math.min(1, rms * 1.45 + (peak - rms) * 0.22));
    audioAt = performance.now();
  });
  if (settings.lipSync) stream.start();

  const activeSequenceFrame = (value, now) => {
    if (!value) return null;
    const elapsed = now - value.startedAt;
    const index = value.offsets.findIndex((offset) => elapsed < offset);
    if (index >= 0) return value.names[index];
    value.onComplete?.();
    return null;
  };

  const blinkFrame = (now) => {
    if (!blink && now >= nextBlinkAt && !effect && visualState !== "failed" && visualState !== "sleeping") {
      const friendly = visualState === "completed" || (visualState === "listening" && Math.random() < 0.25);
      blink = friendly
        ? sequence(["halfsmile", "eyes_closed_smile", "halfsmile"], [40, 95, 70], now)
        : sequence(
            [frameForState(visualState), "eyes_mid_close", "eyes_closed", "eyes_mid_close", frameForState(visualState)],
            [35, 55, 85, 55, 40],
            now,
          );
    }
    const frame = activeSequenceFrame(blink, now);
    if (blink && !frame) {
      blink = null;
      // Now and then a second blink follows the first, as people do.
      if (Math.random() < 0.18) nextBlinkAt = now + randomBetween(140, 220);
      else scheduleBlink(now);
    }
    return frame;
  };

  // Idle and listening hold one frame for long stretches; an occasional
  // glance or half smile keeps him present without implying a state change.
  const glanceFrame = (now) => {
    if (reducedMotion() || effect || blink) return null;
    if (!glance && now >= nextGlanceAt && (visualState === "idle" || visualState === "listening")) {
      const choices = visualState === "idle"
        ? [["lookup", 900], ["lookdown", 750], ["halfsmile", 1400]]
        : [["halfsmile", 1600]];
      const [name, duration] = choices[Math.floor(Math.random() * choices.length)];
      glance = sequence([name], [duration], now);
    }
    const frame = activeSequenceFrame(glance, now);
    if (glance && !frame) {
      glance = null;
      scheduleGlance(now);
    }
    return frame;
  };

  const fallbackViseme = (now) => {
    if (now < nextFallbackAt) return fallbackFrame;
    const choices = fallbackFrame === "base"
      ? ["ah_small", "e_sound", "oh", "oo", "open", "halfsmile"]
      : ["base", "base", "ah_small", "e_sound", "oh", "oo", "open"];
    fallbackFrame = choices[Math.floor(Math.random() * choices.length)];
    nextFallbackAt = now + (fallbackFrame === "base" ? randomBetween(45, 85) : randomBetween(75, 135));
    return fallbackFrame;
  };

  const desiredFrame = (now) => {
    if (effect) {
      const frame = activeSequenceFrame(effect, now);
      if (frame) return frame;
      effect = null;
    }
    const blinking = blinkFrame(now);
    if (blinking) return blinking;
    if (visualState !== "speaking") return glanceFrame(now) || frameForState(visualState);
    const level = debugLevel ?? audioLevel;
    const candidate = now - audioAt < 350 || debugLevel !== null
      ? frameForLevel(level)
      : fallbackViseme(now);
    if (candidate !== speechFrame && now - speechFrameAt >= VISEME_HOLD_MS) {
      speechFrame = candidate;
      speechFrameAt = now;
    }
    return speechFrame;
  };

  const setFrame = (name, now) => {
    if (!images[name] || name === currentFrame) return;
    previousFrame = currentFrame;
    currentFrame = name;
    frameChangedAt = now;
  };

  const targetPose = (now) => {
    if (reducedMotion() || effect) return { y: 0, scale: 1, angle: 0 };
    const seconds = now / 1000;
    const speakingEnergy = visualState === "speaking" ? Math.max(0.1, audioLevel) : 0;
    const lean = visualState === "listening" ? -1.1
      : visualState === "thinking" ? 0.6 + Math.sin(seconds * 0.9) * 0.35
        : Math.sin(seconds * 0.55) * 0.18;
    return {
      y: Math.sin(seconds * Math.PI * 0.56) * 1.15 + speakingEnergy * Math.sin(seconds * 17) * 0.75,
      scale: 1 + Math.sin(seconds * Math.PI * 0.48) * 0.0035,
      angle: lean,
    };
  };

  // Ease toward the target pose so a state change (a listening tilt, say)
  // settles into place instead of snapping.
  const motion = (now, dt) => {
    const target = targetPose(now);
    if (reducedMotion() || effect) {
      pose = target;
      return pose;
    }
    const k = 1 - Math.exp(-Math.max(0, dt) / POSE_SETTLE_MS);
    pose = {
      y: target.y,
      scale: target.scale,
      angle: pose.angle + (target.angle - pose.angle) * k,
    };
    return pose;
  };

  // A faint band of light that drifts down the figure every few seconds --
  // the hologram reading as projected rather than printed.
  const drawSweep = (now) => {
    if (reducedMotion() || typeof context.createLinearGradient !== "function") return;
    const cycle = (now % 7000) / 7000;
    if (cycle > 0.6) return;
    const y = -80 + (cycle / 0.6) * 672;
    const gradient = context.createLinearGradient(0, y - 40, 0, y + 40);
    gradient.addColorStop(0, "rgba(120, 220, 255, 0)");
    gradient.addColorStop(0.5, "rgba(150, 230, 255, 0.09)");
    gradient.addColorStop(1, "rgba(120, 220, 255, 0)");
    context.save();
    context.globalCompositeOperation = "source-atop";
    context.fillStyle = gradient;
    context.fillRect(0, y - 40, 512, 80);
    context.restore();
  };

  const drawImage = (name, alpha, transform) => {
    if (!images[name] || alpha <= 0) return;
    context.save();
    context.globalAlpha = alpha;
    context.translate(256, 512 + transform.y);
    context.rotate(transform.angle * Math.PI / 180);
    context.scale(transform.scale, transform.scale);
    context.drawImage(images[name], -256, -512, 512, 512);
    context.restore();
  };

  const scheduleDraw = () => {
    if (!animationFrame && !disposed && visible && !document.hidden) {
      animationFrame = requestAnimationFrame(draw);
    }
  };
  const draw = (now) => {
    animationFrame = 0;
    if (disposed || !visible || document.hidden) return;
    const dt = lastDrawAt ? now - lastDrawAt : 0;
    if (lastDrawAt && dt < FRAME_INTERVAL_MS - 1) {
      scheduleDraw();
      return;
    }
    lastDrawAt = now;
    audioLevel = reducedMotion() ? audioTarget : smoothToward(audioLevel, audioTarget, dt);
    setFrame(desiredFrame(now), now);
    context.clearRect(0, 0, 512, 512);
    const transform = motion(now, dt);
    const fade = transitionMs(previousFrame, currentFrame);
    const transition = reducedMotion() ? 1 : easeInOut((now - frameChangedAt) / fade);
    if (transition < 1) drawImage(previousFrame, 1 - transition, transform);
    if (visualState === "working" && currentFrame === "glow_eyes" && images.base && !reducedMotion() && !effect) {
      // The eyes glow in and out while he works rather than staring fixed.
      const pulse = 0.55 + 0.45 * (0.5 + 0.5 * Math.sin(now / 1000 * Math.PI * 0.8));
      drawImage("base", transition, transform);
      drawImage("glow_eyes", transition * pulse, transform);
    } else {
      drawImage(currentFrame, transition, transform);
    }
    if (!effect) drawSweep(now);
    scheduleDraw();
  };
  const onVisibilityChange = () => scheduleDraw();
  document.addEventListener?.("visibilitychange", onVisibilityChange);

  scheduleBlink();
  scheduleGlance();
  scheduleDraw();
  const deferredFrames = FRAME_NAMES.filter((name) => !CRITICAL_FRAMES.includes(name));
  Promise.allSettled(deferredFrames.map((name) => preloadFrames(urls, [name])))
    .then((results) => {
      if (disposed) return;
      for (const result of results) {
        if (result.status === "fulfilled") Object.assign(images, result.value);
        else console.warn("Optional Butler frame failed to load", result.reason);
      }
      const materializeReady = Array.from(
        { length: 13 }, (_, index) => images[`materialize_${String(index + 1).padStart(2, "0")}`]
      ).every(Boolean);
      if (materializeReady) playMaterialize();
    });

  return {
    update(value) {
      runtime = value.runtime || {};
      currentSettings = value.settings;
      const next = debugState || value.resolved?.state || "idle";
      enterState(next);
      if (currentSettings.lipSync) stream.start();
      else stream.stop();
      if (!runtime.speaking && visualState === "speaking") enterState(next, true);
    },
    setVisible(value) {
      visible = Boolean(value);
      if (!visible && animationFrame) {
        cancelAnimationFrame(animationFrame);
        animationFrame = 0;
      }
      if (visible) {
        setFrame(desiredFrame(performance.now()), performance.now());
        scheduleDraw();
      }
    },
    debug: {
      setState(value) { debugState = value || null; enterState(debugState || resolvedState, true); },
      setEmotion() {},
      setSpeaking(value) { enterState(value ? "speaking" : resolvedState, true); },
      setAudioLevel(value) { debugLevel = Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : null; },
      setLookTarget() {},
      setReducedMotion(value) { currentSettings = { ...currentSettings, effectiveReducedMotion: Boolean(value) }; },
      triggerGlitch() { playFailure(); return true; },
      setGlitchesEnabled() {},
      reset() { debugState = null; debugLevel = null; enterState(resolvedState, true); },
      getDiagnostics() {
        return { kind: "frame-butler", state: visualState, frame: currentFrame, usingEnvelope: performance.now() - audioAt < 350 };
      },
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      cancelAnimationFrame(animationFrame);
      document.removeEventListener?.("visibilitychange", onVisibilityChange);
      stream.dispose();
      host.classList.remove("avatar-frame-butler");
      canvas.remove();
    },
  };
}

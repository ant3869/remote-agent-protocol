import { AvatarEnvelopeStream } from "./lip-sync.js";

const ASSET_BASE = "/assets/avatars/butler/runtime_512_v1/";
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
  return Object.fromEntries(FRAME_NAMES.map((name) => [name, `${base}${name}.webp`]));
}

export function stateForResolved(state) {
  return STATE_MAP[state] || "idle";
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

async function preloadFrames(urls, names = FRAME_NAMES, ImageImpl = Image) {
  const entries = await Promise.all(names.map((name) => new Promise((resolve, reject) => {
    const image = new ImageImpl();
    image.onload = () => resolve([name, image]);
    image.onerror = () => reject(new Error(`Unable to load Butler frame: ${name}`));
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
  const images = await preloadFrames(urls, CRITICAL_FRAMES);
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
  let audioLevel = 0;
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
    fallbackFrame = "base";
    nextFallbackAt = performance.now();
    scheduleBlink();
    if (visualState === "completed") playCompleted();
    else if (visualState === "failed") playFailure();
  };

  const stream = new AvatarEnvelopeStream((sample) => {
    const rms = Math.max(0, Math.min(1, Number(sample?.rms) || 0));
    const peak = Math.max(rms, Math.min(1, Number(sample?.peak) || 0));
    audioLevel = Math.max(0, Math.min(1, rms * 1.45 + (peak - rms) * 0.22));
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
      scheduleBlink(now);
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
    if (visualState !== "speaking") return frameForState(visualState);
    const level = debugLevel ?? audioLevel;
    return now - audioAt < 350 || debugLevel !== null ? frameForLevel(level) : fallbackViseme(now);
  };

  const setFrame = (name, now) => {
    if (!images[name] || name === currentFrame) return;
    previousFrame = currentFrame;
    currentFrame = name;
    frameChangedAt = now;
  };

  const motion = (now) => {
    if (reducedMotion() || effect) return { y: 0, scale: 1, angle: 0 };
    const seconds = now / 1000;
    const speakingEnergy = visualState === "speaking" ? Math.max(0.1, audioLevel) : 0;
    return {
      y: Math.sin(seconds * Math.PI * 0.56) * 1.15 + speakingEnergy * Math.sin(seconds * 17) * 0.75,
      scale: 1 + Math.sin(seconds * Math.PI * 0.48) * 0.0035,
      angle: visualState === "listening" ? -1.1 : Math.sin(seconds * 0.55) * 0.18,
    };
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
    setFrame(desiredFrame(now), now);
    context.clearRect(0, 0, 512, 512);
    const transform = motion(now);
    const transition = reducedMotion() ? 1 : Math.min(1, (now - frameChangedAt) / 45);
    if (transition < 1) drawImage(previousFrame, 1 - transition, transform);
    drawImage(currentFrame, transition, transform);
    scheduleDraw();
  };
  const onVisibilityChange = () => scheduleDraw();
  document.addEventListener?.("visibilitychange", onVisibilityChange);

  scheduleBlink();
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

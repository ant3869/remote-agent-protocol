import { clamp } from "./math.js";

// Renderer quality tiers plus rig-specific tuning (`visage`). The base keys
// are consumed by the scene/renderer; the visage block is advisory for
// procedural rigs and ignored by GLTF rigs.
export const QUALITY = Object.freeze({
  low: Object.freeze({
    maxPixelRatio: 1, targetFps: 24, antialias: false, shadows: false,
    visage: Object.freeze({
      motes: 60, hairStrands: 3, glitchBands: 1, channelSplit: false,
      maxFragments: 0, pointScatter: false, monocleDetail: "simple", scanlineDensity: 26,
    }),
  }),
  medium: Object.freeze({
    maxPixelRatio: 1.5, targetFps: 30, antialias: true, shadows: false,
    visage: Object.freeze({
      motes: 130, hairStrands: 5, glitchBands: 2, channelSplit: true,
      maxFragments: 6, pointScatter: false, monocleDetail: "standard", scanlineDensity: 34,
    }),
  }),
  high: Object.freeze({
    maxPixelRatio: 2, targetFps: 60, antialias: true, shadows: true,
    visage: Object.freeze({
      motes: 230, hairStrands: 7, glitchBands: 2, channelSplit: true,
      maxFragments: 12, pointScatter: true, monocleDetail: "full", scanlineDensity: 40,
    }),
  }),
});

export function normalizeAvatarSettings(raw = {}, systemReducedMotion = false) {
  const quality = Object.hasOwn(QUALITY, raw.quality) ? raw.quality : "high";
  const reducedMotion = raw.reducedMotion === null || typeof raw.reducedMotion === "boolean"
    ? raw.reducedMotion
    : null;
  const intensity = Number.isFinite(raw.expressionIntensity)
    ? clamp(Number(raw.expressionIntensity))
    : 0.62;
  return {
    enabled: typeof raw.enabled === "boolean" ? raw.enabled : true,
    avatarId: /^[a-z0-9][a-z0-9_-]{0,63}$/.test(raw.avatarId || "") ? raw.avatarId : "butler",
    quality,
    lipSync: typeof raw.lipSync === "boolean" ? raw.lipSync : true,
    gaze: typeof raw.gaze === "boolean" ? raw.gaze : true,
    idleMotion: typeof raw.idleMotion === "boolean" ? raw.idleMotion : true,
    expressionIntensity: intensity,
    reducedMotion,
    effectiveReducedMotion: reducedMotion === null ? Boolean(systemReducedMotion) : reducedMotion,
    showState: typeof raw.showState === "boolean" ? raw.showState : true,
    panelCollapsed: typeof raw.panelCollapsed === "boolean" ? raw.panelCollapsed : false,
    ...QUALITY[quality],
  };
}

import { normalizeAvatarSettings } from "./avatar-settings.js";
import { AvatarStateController } from "./avatar-controller.js";
import { createAvatarPanel } from "./avatar-panel.js";
import { profileForPersona } from "./persona-profiles.js";
import { SceneLoadGuard } from "./scene-load-guard.js";

// A companion that never appears at all is silent -- nobody sees a console
// error for it. Everything below runs inside one try/catch so that any
// unexpected failure (a persisted setting this build doesn't recognize, a
// browser quirk in one of the constructors above) still leaves the page with
// a working `window.remoteAgentAvatar` stub and the static CSS fallback face
// visible, instead of a permanently empty panel with no diagnostic trail.
try {
  bootstrapAvatar();
} catch (error) {
  console.error("Avatar module failed to initialize; showing the static fallback face.", error);
  showStaticFallback();
  window.remoteAgentAvatar = {
    updateRuntime() {},
    triggerGlitch: () => false,
    getDiagnostics: () => null,
    debug: {},
    updateSettings() {},
    setPanelVisible() {},
    dispose() {},
  };
  window.dispatchEvent(new Event("rap:avatar-ready"));
}

function showStaticFallback() {
  const fallback = document.getElementById("avatarFallback");
  fallback?.classList.add("active");
  fallback?.setAttribute("aria-hidden", "false");
  fallback?.setAttribute("role", "img");
  fallback?.setAttribute("aria-label", "Static assistant companion: renderer-unavailable");
  document.getElementById("avatarCanvasHost")?.classList.add("has-fallback");
}

function bootstrapAvatar() {
const panel = createAvatarPanel();
const panelElement = document.getElementById("avatarPanel");
const motionQuery = window.matchMedia?.("(prefers-reduced-motion: reduce)");
let settings = normalizeAvatarSettings({}, motionQuery?.matches || false);
let runtime = {};
let scene = null;
let loading = null;
const sceneKey = (value) => `${value.avatarId}:${value.quality}`;
const sceneGuard = new SceneLoadGuard(sceneKey(settings));
let panelVisible = true;
let controller = new AvatarStateController(profileForPersona("", settings.avatarId));

async function ensureScene() {
  if (!panel.host || !settings.enabled || settings.panelCollapsed || !panelVisible || scene || loading) return;
  const request = sceneGuard.token();
  const sceneModule = settings.avatarId === "butler"
    ? "./frame-avatar-scene.js"
    : "./avatar-scene.js";
  loading = import(sceneModule)
    .then(({ createAvatarScene }) => createAvatarScene(panel.host, settings))
    .then((created) => {
      if (!sceneGuard.accepts(request) || !settings.enabled || settings.panelCollapsed) {
        created.dispose();
        return;
      }
      scene = created;
      scene.setVisible(panelVisible);
      panel.showFallback(false);
    })
    .catch((error) => {
      if (!sceneGuard.accepts(request)) return;
      console.warn("Avatar scene unavailable", error);
      panel.showFallback(true, "renderer-unavailable");
    })
    .finally(() => {
      loading = null;
      if (!sceneGuard.accepts(request)) void sync();
    });
  await loading;
}

async function sync() {
  panel.setEnabled(settings.enabled);
  panel.setCollapsed(settings.panelCollapsed);
  panel.setLabelsVisible(settings.showState);
  const profile = profileForPersona(runtime.persona, settings.avatarId);
  controller.profile = profile;
  const resolved = controller.update(runtime);
  panel.render(runtime, resolved, settings.showState);
  if (!settings.enabled || settings.panelCollapsed) {
    sceneGuard.invalidate();
    scene?.dispose();
    scene = null;
    return;
  }
  await ensureScene();
  scene?.update({ runtime, resolved, profile, settings });
}

// Development/debug surface: delegates into the active scene (no-ops while no
// scene exists). Exposed on the existing window.remoteAgentAvatar object.
const debugApi = {};
for (const method of [
  "setState", "setEmotion", "setSpeaking", "setAudioLevel", "setLookTarget",
  "setReducedMotion", "triggerGlitch", "setGlitchesEnabled", "reset", "getDiagnostics",
]) {
  debugApi[method] = (...args) => scene?.debug?.[method]?.(...args);
}

const api = {
  updateRuntime(next) { runtime = { ...runtime, ...next }; void sync(); },
  triggerGlitch(type, strength) { return scene?.debug?.triggerGlitch(type, strength) ?? false; },
  getDiagnostics() { return scene?.debug?.getDiagnostics() ?? null; },
  debug: debugApi,
  updateSettings(next) {
    settings = normalizeAvatarSettings(next, motionQuery?.matches || false);
    if (sceneGuard.updateKey(sceneKey(settings))) {
      scene?.dispose();
      scene = null;
    }
    void sync();
  },
  setPanelVisible(visible) {
    panelVisible = Boolean(visible);
    scene?.setVisible(panelVisible);
    if (panelVisible) void ensureScene();
  },
  dispose() {
    sceneGuard.invalidate();
    visibilityObserver?.disconnect();
    panel.host?.removeEventListener("rap:avatar-fallback", onFallback);
    panel.host?.removeEventListener("rap:avatar-recovered", onRecovered);
    scene?.dispose();
    scene = null;
  },
};

const onFallback = (event) => panel.showFallback(true, event.detail?.reason || "renderer-unavailable");
const onRecovered = () => panel.showFallback(false);
panel.host?.addEventListener("rap:avatar-fallback", onFallback);
panel.host?.addEventListener("rap:avatar-recovered", onRecovered);
panel.onCollapse(() => {
  window.dispatchEvent(new CustomEvent("rap:avatar-collapse", {
    detail: { collapsed: !settings.panelCollapsed },
  }));
});
motionQuery?.addEventListener?.("change", () => api.updateSettings(settings));

const visibilityObserver = typeof IntersectionObserver === "function" && panelElement
  ? new IntersectionObserver(
      ([entry]) => api.setPanelVisible(Boolean(entry?.isIntersecting)),
      { threshold: 0.05 },
    )
  : null;
visibilityObserver?.observe(panelElement);

window.remoteAgentAvatar = api;
window.dispatchEvent(new Event("rap:avatar-ready"));
window.addEventListener("beforeunload", () => api.dispose(), { once: true });
}

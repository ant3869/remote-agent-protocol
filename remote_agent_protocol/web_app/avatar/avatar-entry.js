import { normalizeAvatarSettings } from "./avatar-settings.js";
import { AvatarStateController } from "./avatar-controller.js";
import { createAvatarPanel } from "./avatar-panel.js";
import { profileForPersona } from "./persona-profiles.js";

const panel = createAvatarPanel();
const panelElement = document.getElementById("avatarPanel");
const motionQuery = window.matchMedia?.("(prefers-reduced-motion: reduce)");
let settings = normalizeAvatarSettings({}, motionQuery?.matches || false);
let runtime = {};
let scene = null;
let loading = null;
let sceneGeneration = 0;
let panelVisible = true;
let controller = new AvatarStateController(profileForPersona("", settings.avatarId));

async function ensureScene() {
  if (!panel.host || !settings.enabled || settings.panelCollapsed || !panelVisible || scene || loading) return;
  const generation = sceneGeneration;
  loading = import("./avatar-scene.js")
    .then(({ createAvatarScene }) => createAvatarScene(panel.host, settings))
    .then((created) => {
      if (generation !== sceneGeneration || !settings.enabled || settings.panelCollapsed) {
        created.dispose();
        return;
      }
      scene = created;
      scene.setVisible(panelVisible);
      panel.showFallback(false);
    })
    .catch((error) => {
      console.warn("Avatar scene unavailable", error);
      panel.showFallback(true, "renderer-unavailable");
    })
    .finally(() => { loading = null; });
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
    sceneGeneration += 1;
    scene?.dispose();
    scene = null;
    return;
  }
  await ensureScene();
  scene?.update({ runtime, resolved, profile, settings });
}

const api = {
  updateRuntime(next) { runtime = { ...runtime, ...next }; void sync(); },
  updateSettings(next) {
    const previousKey = `${settings.avatarId}:${settings.quality}`;
    settings = normalizeAvatarSettings(next, motionQuery?.matches || false);
    const nextKey = `${settings.avatarId}:${settings.quality}`;
    if (scene && previousKey !== nextKey) {
      sceneGeneration += 1;
      scene.dispose();
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
    sceneGeneration += 1;
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

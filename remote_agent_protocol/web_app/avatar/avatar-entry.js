import { normalizeAvatarSettings } from "./avatar-settings.js";
import { AvatarStateController } from "./avatar-controller.js";
import { createAvatarPanel } from "./avatar-panel.js";
import { profileForPersona } from "./persona-profiles.js";

const panel = createAvatarPanel();
const motionQuery = window.matchMedia?.("(prefers-reduced-motion: reduce)");
let settings = normalizeAvatarSettings({}, motionQuery?.matches || false);
let runtime = {};
let scene = null;
let loading = null;
let controller = new AvatarStateController(profileForPersona("", settings.avatarId));

async function ensureScene() {
  if (!panel.host || !settings.enabled || settings.panelCollapsed || scene || loading) return;
  loading = import("./avatar-scene.js")
    .then(({ createAvatarScene }) => createAvatarScene(panel.host, settings))
    .then((created) => { scene = created; panel.showFallback(false); })
    .catch((error) => { console.warn("Avatar scene unavailable", error); panel.showFallback(true); })
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
  panel.render(runtime, resolved);
  if (!settings.enabled || settings.panelCollapsed) {
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
    settings = normalizeAvatarSettings(next, motionQuery?.matches || false);
    void sync();
  },
  setPanelVisible(visible) { scene?.setVisible(Boolean(visible)); },
  dispose() { scene?.dispose(); scene = null; },
};

panel.onCollapse(() => {
  window.dispatchEvent(new CustomEvent("rap:avatar-collapse", {
    detail: { collapsed: !settings.panelCollapsed },
  }));
});
motionQuery?.addEventListener?.("change", () => api.updateSettings(settings));
window.remoteAgentAvatar = api;
window.dispatchEvent(new Event("rap:avatar-ready"));
window.addEventListener("beforeunload", () => api.dispose(), { once: true });

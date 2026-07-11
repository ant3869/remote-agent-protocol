from __future__ import annotations

import subprocess
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


web = Path("remote_agent_protocol/web_app")
index_path = web / "index.html"
app_path = web / "app.js"
css_path = web / "styles.css"
test_path = Path("tests/test_web_gui.py")

index = index_path.read_text(encoding="utf-8")
index = replace_once(
    index,
    '            <aside class="activity-panel">\n              <div class="panel-header">',
    '''            <aside class="activity-panel">
              <section id="avatarPanel" class="avatar-panel" aria-label="Animated assistant companion">
                <div class="avatar-panel-head">
                  <div><p class="section-label">Companion</p><strong id="avatarPersonaName">Assistant</strong></div>
                  <button id="avatarCollapseBtn" class="icon-button" type="button" aria-expanded="true" aria-controls="avatarPanelBody" aria-label="Collapse companion">−</button>
                </div>
                <div id="avatarPanelBody" class="avatar-panel-body">
                  <div id="avatarCanvasHost" class="avatar-canvas-host" role="img" aria-label="Animated assistant face"></div>
                  <div id="avatarFallback" class="avatar-fallback" aria-hidden="true">
                    <span class="avatar-fallback-head"></span>
                    <span class="avatar-fallback-collar"></span>
                  </div>
                  <div class="avatar-status-row" aria-live="polite">
                    <span id="avatarStateLabel">idle</span>
                    <span id="avatarEmotionLabel">attentive</span>
                  </div>
                </div>
              </section>
              <div class="panel-header">''',
    "companion panel",
)
index = replace_once(
    index,
    '''            <section class="settings-panel">
              <div class="panel-header compact"><div><p class="section-label">Current state</p><h3>What is active now</h3></div></div>''',
    '''            <section class="settings-panel avatar-settings-card">
              <div class="panel-header compact">
                <div><p class="section-label">Visual companion</p><h3>Animated avatar</h3></div>
                <button id="avatarSettingsSaveBtn" class="button primary-action" type="button">Save avatar</button>
              </div>
              <div id="avatarSettingsNotice" class="persona-notice hidden" role="status"></div>
              <div class="settings-form avatar-settings-grid">
                <label>Enabled<select id="avatarSettingEnabled"><option value="true">On</option><option value="false">Off</option></select></label>
                <label>Avatar<select id="avatarSettingAvatar"><option value="butler">Butler</option></select></label>
                <label>Quality<select id="avatarSettingQuality"><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label>
                <label>Motion<select id="avatarSettingMotion"><option value="system">System preference</option><option value="reduced">Reduced</option><option value="normal">Normal</option></select></label>
                <label>Lip-sync<select id="avatarSettingLipSync"><option value="true">On</option><option value="false">Off</option></select></label>
                <label>Eye movement<select id="avatarSettingGaze"><option value="true">On</option><option value="false">Off</option></select></label>
                <label>Idle motion<select id="avatarSettingIdle"><option value="true">On</option><option value="false">Off</option></select></label>
                <label>State labels<select id="avatarSettingShowState"><option value="true">Show</option><option value="false">Hide</option></select></label>
                <label>Panel<select id="avatarSettingCollapsed"><option value="false">Expanded</option><option value="true">Collapsed</option></select></label>
                <label>Expression intensity<input id="avatarSettingIntensity" type="range" min="0" max="1" step="0.05" value="0.62" /></label>
              </div>
            </section>

            <section class="settings-panel">
              <div class="panel-header compact"><div><p class="section-label">Current state</p><h3>What is active now</h3></div></div>''',
    "avatar settings card",
)
index = replace_once(
    index,
    '    <script src="/app.js"></script>',
    '''    <script type="importmap">
    {
      "imports": {
        "three": "/vendor/three/three.module.min.js",
        "three/addons/": "/vendor/three/addons/"
      }
    }
    </script>
    <script type="module" src="/avatar/avatar-entry.js"></script>
    <script src="/app.js"></script>''',
    "avatar import map",
)
index_path.write_text(index, encoding="utf-8")

(web / "avatar/avatar-panel.js").write_text(
    '''export function createAvatarPanel() {
  const panel = document.getElementById("avatarPanel");
  const body = document.getElementById("avatarPanelBody");
  const fallback = document.getElementById("avatarFallback");
  const host = document.getElementById("avatarCanvasHost");
  const collapse = document.getElementById("avatarCollapseBtn");
  const persona = document.getElementById("avatarPersonaName");
  const stateLabel = document.getElementById("avatarStateLabel");
  const emotionLabel = document.getElementById("avatarEmotionLabel");

  function setCollapsed(value) {
    panel?.classList.toggle("collapsed", value);
    body?.classList.toggle("hidden", value);
    collapse?.setAttribute("aria-expanded", String(!value));
    collapse?.setAttribute("aria-label", value ? "Expand companion" : "Collapse companion");
    if (collapse) collapse.textContent = value ? "+" : "−";
  }

  return {
    host,
    showFallback(show) {
      fallback?.classList.toggle("active", show);
      fallback?.setAttribute("aria-hidden", String(!show));
    },
    render(runtime, resolved) {
      if (persona) persona.textContent = runtime.persona || "Assistant";
      if (stateLabel) stateLabel.textContent = resolved.state || "idle";
      if (emotionLabel) emotionLabel.textContent = resolved.emotion?.name || "neutral";
    },
    setCollapsed,
    setEnabled(enabled) { panel?.classList.toggle("hidden", !enabled); },
    setLabelsVisible(visible) { panel?.classList.toggle("hide-state-labels", !visible); },
    onCollapse(handler) { collapse?.addEventListener("click", handler); },
  };
}
''',
    encoding="utf-8",
)

(web / "avatar/avatar-entry.js").write_text(
    '''import { normalizeAvatarSettings } from "./avatar-settings.js";
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
''',
    encoding="utf-8",
)

app = app_path.read_text(encoding="utf-8")
app = replace_once(
    app,
    "  connectionLost: false,\n  wake: null,",
    '  connectionLost: false,\n  avatar: { speaking: false, userSpeaking: false, completedAt: 0, failedAt: 0, latestAssistantText: "" },\n  wake: null,',
    "avatar app state",
)
app = replace_once(
    app,
    "function handleEvent(event) {\n  if (event.type === \"transcript\") {",
    '''function handleEvent(event) {
  if (event.type === "transcript" && event.role !== "user") state.avatar.latestAssistantText = event.text || "";
  if (event.type === "speaking") state.avatar.speaking = Boolean(event.value);
  if (event.type === "turn" && event.event === "user_started") state.avatar.userSpeaking = true;
  if (event.type === "turn" && event.event === "user_stopped") state.avatar.userSpeaking = false;
  if (event.type === "agent_job" && event.event === "finished" && event.status === "done") state.avatar.completedAt = Date.now();
  if (event.type === "agent_job" && ["failed", "timeout", "cancelled"].includes(event.status)) state.avatar.failedAt = Date.now();

  if (event.type === "transcript") {''',
    "avatar event bridge",
)
app = replace_once(
    app,
    "  } else if (event.type === \"wake\") {\n    state.wake = { ...(state.wake || {}), ...event, seen_at: Date.now() };",
    "  } else if (event.type === \"wake\") {\n    state.wake = { ...(state.wake || {}), ...event, seen_at: Date.now() };",
    "wake marker",
)
app = replace_once(
    app,
    "    renderWakeStatus();\n  }\n}\n\nfunction updateWakePhase",
    "    renderWakeStatus();\n  }\n  syncAvatarRuntime();\n}\n\nfunction updateWakePhase",
    "sync after event",
)
app = replace_once(
    app,
    "function currentPersona() {\n  return state.status?.persona || \"Assistant\";\n}\n",
    '''function currentPersona() {
  return state.status?.persona || "Assistant";
}

function avatarRuntimeSnapshot() {
  const s = state.status || {};
  const wake = currentWake();
  return {
    persona: s.persona,
    session: s.session,
    speaking: state.avatar.speaking,
    userSpeaking: state.avatar.userSpeaking,
    thinking: wake.phase === "transcribing" || wake.phase === "agent_responding",
    wakePhase: wake.phase,
    activeAgentCount: s.activeAgentCount || 0,
    pendingConfirmation: Boolean(state.activeConfirm || s.pendingConfirms?.length),
    completedAt: state.avatar.completedAt,
    error: s.session === "failed" || Boolean(state.avatar.failedAt && Date.now() - state.avatar.failedAt < 5000),
    latestAssistantText: state.avatar.latestAssistantText,
  };
}

function syncAvatarRuntime() {
  const api = window.remoteAgentAvatar;
  if (!api || !state.status?.avatar) return;
  api.updateSettings(state.status.avatar);
  api.updateRuntime(avatarRuntimeSnapshot());
}

function populateAvatarSettings(avatar = {}) {
  if (!$('avatarSettingEnabled')) return;
  $('avatarSettingEnabled').value = String(avatar.enabled ?? true);
  $('avatarSettingAvatar').value = avatar.avatarId || 'butler';
  $('avatarSettingQuality').value = avatar.quality || 'high';
  $('avatarSettingLipSync').value = String(avatar.lipSync ?? true);
  $('avatarSettingGaze').value = String(avatar.gaze ?? true);
  $('avatarSettingIdle').value = String(avatar.idleMotion ?? true);
  $('avatarSettingIntensity').value = String(avatar.expressionIntensity ?? 0.62);
  $('avatarSettingShowState').value = String(avatar.showState ?? true);
  $('avatarSettingCollapsed').value = String(avatar.panelCollapsed ?? false);
  $('avatarSettingMotion').value = avatar.reducedMotion === null || avatar.reducedMotion === undefined
    ? 'system'
    : avatar.reducedMotion ? 'reduced' : 'normal';
}

function avatarSettingsPayload(overrides = {}) {
  const motion = $('avatarSettingMotion').value;
  return {
    enabled: $('avatarSettingEnabled').value === 'true',
    avatarId: $('avatarSettingAvatar').value,
    quality: $('avatarSettingQuality').value,
    lipSync: $('avatarSettingLipSync').value === 'true',
    gaze: $('avatarSettingGaze').value === 'true',
    idleMotion: $('avatarSettingIdle').value === 'true',
    expressionIntensity: Number($('avatarSettingIntensity').value),
    reducedMotion: motion === 'system' ? null : motion === 'reduced',
    showState: $('avatarSettingShowState').value === 'true',
    panelCollapsed: $('avatarSettingCollapsed').value === 'true',
    ...overrides,
  };
}

async function saveAvatarSettings(overrides = {}) {
  const result = await post('avatar_settings', { settings: avatarSettingsPayload(overrides) });
  const notice = $('avatarSettingsNotice');
  notice.textContent = result.ok ? 'Avatar settings saved.' : result.error;
  notice.classList.remove('hidden');
}
''',
    "avatar bridge functions",
)
app = replace_once(
    app,
    "  renderStatusDashboard(s);\n  if (!state.activeConfirm",
    "  renderStatusDashboard(s);\n  populateAvatarSettings(s.avatar);\n  syncAvatarRuntime();\n  if (!state.activeConfirm",
    "render avatar sync",
)
app = replace_once(
    app,
    "function bind() {\n  document.querySelectorAll(\".nav-link\").forEach((button) => {",
    '''function bind() {
  window.addEventListener("rap:avatar-ready", syncAvatarRuntime);
  window.addEventListener("rap:avatar-collapse", (event) => {
    const collapsed = Boolean(event.detail?.collapsed);
    $('avatarSettingCollapsed').value = String(collapsed);
    void saveAvatarSettings({ panelCollapsed: collapsed });
  });
  $('avatarSettingsSaveBtn').addEventListener('click', () => void saveAvatarSettings());
  document.querySelectorAll(".nav-link").forEach((button) => {''',
    "avatar bindings",
)
app_path.write_text(app, encoding="utf-8")

css = css_path.read_text(encoding="utf-8")
css += '''

.avatar-panel { margin: 0 0 1rem; border-bottom: 1px solid var(--border-subtle); background: var(--surface-panel); }
.avatar-panel-head { display: flex; align-items: center; justify-content: space-between; gap: 1rem; padding: 1rem; }
.avatar-panel-body { position: relative; padding: 0 1rem 1rem; }
.avatar-canvas-host { width: 100%; min-height: 260px; overflow: hidden; border: 1px solid var(--border-subtle); border-radius: 14px; background: radial-gradient(circle at 50% 35%, var(--surface-raised), var(--surface-app)); }
.avatar-canvas-host canvas { display: block; width: 100%; height: 100%; min-height: 260px; }
.avatar-fallback { position: absolute; inset: 0 1rem 3.2rem; display: none; place-items: center; overflow: hidden; pointer-events: none; }
.avatar-fallback.active { display: grid; }
.avatar-fallback-head { width: 88px; height: 112px; border-radius: 48% 48% 42% 42%; background: var(--surface-raised); border: 1px solid var(--border-strong); box-shadow: 0 18px 50px rgba(0, 0, 0, .35); }
.avatar-fallback-collar { position: absolute; top: 62%; width: 150px; height: 80px; border-radius: 50% 50% 18% 18%; background: var(--surface-elevated); border: 1px solid var(--border-subtle); }
.avatar-status-row { display: flex; justify-content: space-between; gap: .75rem; padding-top: .75rem; color: var(--text-muted); font-size: .8rem; text-transform: capitalize; }
.avatar-panel.hide-state-labels .avatar-status-row { display: none; }
.avatar-panel.collapsed { margin-bottom: .5rem; }
.avatar-settings-card { grid-column: 1 / -1; }
.avatar-settings-grid { grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); }
.avatar-settings-grid input[type="range"] { width: 100%; }
@media (max-width: 900px) {
  .avatar-canvas-host, .avatar-canvas-host canvas { min-height: 210px; }
  .avatar-panel { position: static; }
}
@media (prefers-reduced-motion: reduce) {
  .avatar-panel *, .avatar-fallback * { transition-duration: 0.001ms !important; animation-duration: 0.001ms !important; }
}
'''
css_path.write_text(css, encoding="utf-8")

tests = test_path.read_text(encoding="utf-8")
tests += '''


def test_web_shell_contains_avatar_panel_import_map_and_settings():
    html = (WEB_APP / "index.html").read_text(encoding="utf-8")
    script = (WEB_APP / "app.js").read_text(encoding="utf-8")
    css = (WEB_APP / "styles.css").read_text(encoding="utf-8")

    for marker in [
        'id="avatarPanel"', 'id="avatarCanvasHost"', 'id="avatarFallback"',
        'id="avatarPersonaName"', 'id="avatarStateLabel"', 'id="avatarEmotionLabel"',
        'id="avatarCollapseBtn"', 'id="avatarSettingEnabled"',
        'id="avatarSettingQuality"', 'id="avatarSettingMotion"',
        'id="avatarSettingsSaveBtn"',
    ]:
        assert marker in html
    assert 'type="importmap"' in html
    assert '"three": "/vendor/three/three.module.min.js"' in html
    assert 'src="/avatar/avatar-entry.js"' in html
    assert "function syncAvatarRuntime" in script
    assert "post('avatar_settings'" in script
    assert ".avatar-panel" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
'''
test_path.write_text(tests, encoding="utf-8")

subprocess.run(["python", "-m", "ruff", "format", str(test_path)], check=True)
subprocess.run(
    ["python", "-m", "pytest", "tests/test_web_gui.py", "-q", "--disable-warnings", "--maxfail=1"],
    check=True,
)
Path(__file__).unlink()
subprocess.run(
    ["git", "add", "remote_agent_protocol/web_app", "tests/test_web_gui.py", ".github/avatar_tasks/task7.py"],
    check=True,
)
subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
subprocess.run(["git", "commit", "-m", "feat(avatar): add companion panel and settings UI"], check=True)
subprocess.run(["git", "push", "origin", "HEAD:feature/animated-butler-avatar"], check=True)
print("TASK 7 DONE: companion panel, settings, and runtime bridge committed")

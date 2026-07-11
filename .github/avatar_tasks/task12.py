from __future__ import annotations

import subprocess
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


avatar = Path("remote_agent_protocol/web_app/avatar")
entry_path = avatar / "avatar-entry.js"
panel_path = avatar / "avatar-panel.js"
scene_path = avatar / "avatar-scene.js"
lip_path = avatar / "lip-sync.js"
css_path = Path("remote_agent_protocol/web_app/styles.css")
py_test_path = Path("tests/test_web_gui.py")
js_test_path = Path("tests/js/lip-sync.test.mjs")

entry_path.write_text(
    '''import { normalizeAvatarSettings } from "./avatar-settings.js";
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
    settings = normalizeAvatarSettings(next, motionQuery?.matches || false);
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
    scene?.dispose();
    scene = null;
  },
};

const onFallback = (event) => panel.showFallback(true, event.detail?.reason || "renderer-unavailable");
panel.host?.addEventListener("rap:avatar-fallback", onFallback);
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
''',
    encoding="utf-8",
)

panel_path.write_text(
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
    showFallback(show, reason = "renderer-unavailable") {
      fallback?.classList.toggle("active", show);
      fallback?.setAttribute("aria-hidden", String(!show));
      fallback?.setAttribute("role", show ? "img" : "presentation");
      fallback?.setAttribute("aria-label", show ? `Static assistant companion: ${reason}` : "");
      host?.classList.toggle("has-fallback", show);
    },
    render(runtime, resolved, labelsVisible = true) {
      const personaName = runtime.persona || "Assistant";
      const state = resolved.state || "idle";
      const emotion = resolved.emotion?.name || "neutral";
      if (persona) persona.textContent = personaName;
      if (stateLabel) stateLabel.textContent = state;
      if (emotionLabel) emotionLabel.textContent = emotion;
      host?.setAttribute(
        "aria-label",
        labelsVisible
          ? `Animated assistant face for ${personaName}`
          : `Animated assistant face for ${personaName}, ${state}, ${emotion}`,
      );
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

lip = lip_path.read_text(encoding="utf-8")
lip = replace_once(
    lip,
    '''    this.retryMs = 500;
    this.disposed = false;
  }

  start() {
    if (this.disposed || this.source) return;''',
    '''    this.retryMs = 500;
    this.disposed = false;
    this.enabled = false;
  }

  start() {
    if (this.disposed || this.source) return;
    this.enabled = true;''',
    "stream enable state",
)
lip = replace_once(
    lip,
    '''      if (this.disposed) return;
      const delay = this.retryMs;''',
    '''      if (this.disposed || !this.enabled) return;
      const delay = this.retryMs;''',
    "reconnect enable guard",
)
lip = replace_once(
    lip,
    '''  dispose() {
    this.disposed = true;
    if (this.timer !== null) this.clearTimer(this.timer);
    this.timer = null;
    this.source?.close();
    this.source = null;
  }''',
    '''  stop() {
    this.enabled = false;
    if (this.timer !== null) this.clearTimer(this.timer);
    this.timer = null;
    this.source?.close();
    this.source = null;
    this.retryMs = 500;
  }

  dispose() {
    this.disposed = true;
    this.stop();
  }''',
    "stream stop and dispose",
)
lip_path.write_text(lip, encoding="utf-8")

scene = scene_path.read_text(encoding="utf-8")
scene = replace_once(
    scene,
    '''  let visible = true;
  let disposed = false;
  let lastFrame = 0;''',
    '''  let visible = true;
  let disposed = false;
  let attemptedContextRestore = false;
  let animationFrame = 0;
  let lastFrame = 0;''',
    "context state",
)
scene = replace_once(
    scene,
    '''  const observer = new ResizeObserver(resize);
  observer.observe(host);
  resize();

  const animate = (time) => {
    if (disposed) return;
    requestAnimationFrame(animate);''',
    '''  const observer = new ResizeObserver(resize);
  observer.observe(host);
  const onContextLost = (event) => {
    event.preventDefault();
    visible = false;
    host.dispatchEvent(new CustomEvent("rap:avatar-fallback", { detail: { reason: "context-lost" } }));
  };
  const onContextRestored = () => {
    if (attemptedContextRestore || disposed) return;
    attemptedContextRestore = true;
    visible = true;
    resize();
  };
  renderer.domElement.addEventListener("webglcontextlost", onContextLost, false);
  renderer.domElement.addEventListener("webglcontextrestored", onContextRestored, false);
  resize();

  const animate = (time) => {
    if (disposed) return;
    animationFrame = requestAnimationFrame(animate);''',
    "context listeners",
)
scene = replace_once(
    scene,
    '''  requestAnimationFrame(animate);

  return {''',
    '''  animationFrame = requestAnimationFrame(animate);

  return {''',
    "initial animation frame",
)
scene = replace_once(
    scene,
    '''      renderer.shadowMap.enabled = value.settings.shadows;
      if (value.settings.lipSync) stream.start();
    },''',
    '''      renderer.shadowMap.enabled = value.settings.shadows;
      if (value.settings.lipSync) stream.start();
      else stream.stop();
    },''',
    "lip sync disable",
)
scene = replace_once(
    scene,
    '''      disposed = true;
      observer.disconnect();
      stream.dispose();''',
    '''      disposed = true;
      cancelAnimationFrame(animationFrame);
      observer.disconnect();
      renderer.domElement.removeEventListener("webglcontextlost", onContextLost, false);
      renderer.domElement.removeEventListener("webglcontextrestored", onContextRestored, false);
      stream.dispose();''',
    "complete disposal",
)
scene_path.write_text(scene, encoding="utf-8")

css = css_path.read_text(encoding="utf-8")
css += '''
.avatar-canvas-host.has-fallback canvas { visibility: hidden; }
.avatar-panel button:focus-visible,
.avatar-settings-card button:focus-visible,
.avatar-settings-card select:focus-visible,
.avatar-settings-card input:focus-visible {
  outline: 2px solid var(--status-info);
  outline-offset: 3px;
}
.avatar-status-row span::before { content: attr(id) " "; position: absolute; width: 1px; height: 1px; overflow: hidden; clip-path: inset(50%); }
'''
css_path.write_text(css, encoding="utf-8")

js_tests = js_test_path.read_text(encoding="utf-8")
js_tests += '''

test("stopping the stream closes source and clears a pending reconnect", () => {
  const events = [];
  const sources = [];
  class FakeEventSource {
    constructor() { sources.push(this); }
    close() { events.push("close"); }
  }
  const stream = new AvatarEnvelopeStream(() => {}, {
    EventSourceImpl: FakeEventSource,
    setTimer: () => 17,
    clearTimer: (id) => events.push(`clear:${id}`),
  });
  stream.start();
  sources[0].onerror();
  stream.stop();
  assert.deepEqual(events, ["close", "clear:17"]);
  assert.equal(stream.source, null);
  assert.equal(stream.timer, null);
});
'''
js_test_path.write_text(js_tests, encoding="utf-8")

py_tests = py_test_path.read_text(encoding="utf-8")
py_tests += '''


def test_avatar_scene_has_visibility_context_loss_and_complete_cleanup():
    source = (WEB_APP / "avatar/avatar-scene.js").read_text(encoding="utf-8")
    entry = (WEB_APP / "avatar/avatar-entry.js").read_text(encoding="utf-8")
    panel = (WEB_APP / "avatar/avatar-panel.js").read_text(encoding="utf-8")

    assert "IntersectionObserver" in entry
    assert "webglcontextlost" in source
    assert "webglcontextrestored" in source
    assert "attemptedContextRestore" in source
    assert "aria-label" in panel
    for marker in [
        "observer.disconnect()", "stream.dispose()", "rig.dispose()",
        "renderer.renderLists.dispose()", "renderer.dispose()",
        "renderer.forceContextLoss()", "cancelAnimationFrame(animationFrame)",
    ]:
        assert marker in source
'''
py_test_path.write_text(py_tests, encoding="utf-8")

subprocess.run(["python", "-m", "ruff", "format", str(py_test_path)], check=True)
subprocess.run(["node", "--test", "tests/js/avatar-settings.test.mjs", "tests/js/avatar-controller.test.mjs", "tests/js/lip-sync.test.mjs", "tests/js/model-loader.test.mjs"], check=True)
subprocess.run(["python", "-m", "pytest", str(py_test_path), "-q", "--disable-warnings", "--maxfail=1"], check=True)
Path(__file__).unlink()
subprocess.run(["git", "add", str(avatar), str(css_path), str(js_test_path), str(py_test_path), ".github/avatar_tasks/task12.py"], check=True)
subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
subprocess.run(["git", "commit", "-m", "fix(avatar): harden visibility accessibility and cleanup"], check=True)
subprocess.run(["git", "pull", "--rebase", "origin", "feature/animated-butler-avatar"], check=True)
subprocess.run(["git", "push", "origin", "HEAD:feature/animated-butler-avatar"], check=True)
print("TASK 12 DONE: visibility, context loss, accessibility, and cleanup hardened")

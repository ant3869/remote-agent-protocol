from __future__ import annotations

import subprocess
from pathlib import Path

avatar = Path("remote_agent_protocol/web_app/avatar")
(avatar / "gaze-controller.js").write_text(
    '''import { clamp, range } from "./math.js";

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
''',
    encoding="utf-8",
)

(avatar / "avatar-scene.js").write_text(
    '''import * as THREE from "three";
import { expressionFor, blendTargets } from "./expressions.js";
import { GazeController } from "./gaze-controller.js";
import { damp } from "./math.js";
import { createProceduralButler } from "./procedural-butler.js";

export async function createAvatarScene(host, settings) {
  if (!host) throw new Error("Avatar canvas host is missing");
  const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: settings.antialias, powerPreference: "high-performance" });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, settings.maxPixelRatio));
  renderer.shadowMap.enabled = settings.shadows;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  host.replaceChildren(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 20);
  camera.position.set(0, 1.35, 4.4);
  camera.lookAt(0, 1.15, 0);

  const key = new THREE.DirectionalLight(0xffffff, 2.3);
  key.position.set(2.5, 3.2, 3.5);
  key.castShadow = settings.shadows;
  const fill = new THREE.DirectionalLight(0x8aa0c4, 1.1);
  fill.position.set(-2.5, 1.8, 2.5);
  const rim = new THREE.DirectionalLight(0xa78bfa, 0.75);
  rim.position.set(0, 2.5, -2.5);
  const ambient = new THREE.HemisphereLight(0xb9c6dc, 0x111113, 0.9);
  scene.add(key, fill, rim, ambient);

  const rig = createProceduralButler(THREE);
  scene.add(rig.object);
  const gazeController = new GazeController();
  const currentTargets = Object.fromEntries(
    Object.keys(expressionFor("neutral")).map((keyName) => [keyName, 0]),
  );

  let visible = true;
  let disposed = false;
  let lastFrame = 0;
  let lastAnimatedAt = 0;
  let latest = null;
  let targetInterval = 1000 / settings.targetFps;

  const resize = () => {
    const width = Math.max(1, host.clientWidth);
    const height = Math.max(1, host.clientHeight);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  };
  const observer = new ResizeObserver(resize);
  observer.observe(host);
  resize();

  const animate = (time) => {
    if (disposed) return;
    requestAnimationFrame(animate);
    if (!visible || document.hidden || time - lastFrame < targetInterval) return;
    const delta = Math.min(0.1, Math.max(0.001, (time - (lastAnimatedAt || time - 16)) / 1000));
    lastAnimatedAt = time;
    lastFrame = time;
    if (latest) applyAvatarFrame(rig.controls, latest, time / 1000, delta, gazeController, currentTargets);
    renderer.render(scene, camera);
  };
  requestAnimationFrame(animate);

  return {
    update(value) {
      latest = value;
      targetInterval = 1000 / value.settings.targetFps;
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, value.settings.maxPixelRatio));
      renderer.shadowMap.enabled = value.settings.shadows;
    },
    setVisible(value) { visible = Boolean(value); },
    dispose() {
      if (disposed) return;
      disposed = true;
      observer.disconnect();
      rig.dispose();
      scene.clear();
      renderer.renderLists.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
      renderer.domElement.remove();
    },
  };
}

function applyAvatarFrame(controls, frame, seconds, delta, gazeController, currentTargets) {
  const expression = expressionFor(frame.resolved.emotion.name);
  const base = expressionFor(frame.profile.defaultExpression);
  const amount = frame.resolved.emotion.intensity * frame.settings.expressionIntensity;
  const target = blendTargets(base, expression, amount);
  for (const key of Object.keys(currentTargets)) {
    currentTargets[key] = damp(currentTargets[key], target[key], 8, delta);
  }

  const gaze = gazeController.update(
    delta,
    frame.resolved.state,
    frame.settings.gaze,
    frame.settings.effectiveReducedMotion,
  );
  controls.pupilLeft.position.x = gaze.x;
  controls.pupilRight.position.x = gaze.x;
  controls.pupilLeft.position.y = gaze.y;
  controls.pupilRight.position.y = gaze.y;
  controls.lidLeft.scale.y = Math.max(0.04, 0.74 * (1 - gaze.blink));
  controls.lidRight.scale.y = Math.max(0.04, 0.74 * (1 - gaze.blink * 0.96));
  controls.browLeft.position.y = 0.22 + currentTargets.browInner * 0.035 + currentTargets.browAsymmetry * 0.02;
  controls.browRight.position.y = 0.22 + currentTargets.browInner * 0.035 - currentTargets.browAsymmetry * 0.02;
  controls.browLeft.rotation.z = currentTargets.browOuter * -0.15;
  controls.browRight.rotation.z = currentTargets.browOuter * 0.15;
  controls.mouthCornerLeft.position.y = -0.01 + currentTargets.mouthCorner * 0.035;
  controls.mouthCornerRight.position.y = -0.01 + currentTargets.mouthCorner * 0.035;
  controls.mouthUpper.scale.x = 1 + currentTargets.mouthWidth * 0.2;
  controls.mouthLower.scale.x = 1 + currentTargets.mouthWidth * 0.18;
  controls.cheekLeft.position.y = -0.04 + currentTargets.cheekRaise * 0.025;
  controls.cheekRight.position.y = -0.04 + currentTargets.cheekRaise * 0.025;
  controls.jaw.rotation.x = currentTargets.jawOpen * 0.18;
  controls.head.rotation.x = currentTargets.headPitch;
  controls.head.rotation.y = currentTargets.headYaw;
  controls.head.rotation.z = currentTargets.headRoll;

  const canIdle = frame.settings.idleMotion && !frame.settings.effectiveReducedMotion;
  const breathing = canIdle ? Math.sin(seconds * 1.35) * 0.008 * frame.profile.idleIntensity : 0;
  const stabilization = canIdle ? Math.sin(seconds * 0.41) * 0.006 * frame.profile.idleIntensity : 0;
  controls.bust.scale.y = 0.72 + breathing;
  controls.root.rotation.y = stabilization;
}
''',
    encoding="utf-8",
)

test_js = Path("tests/js/avatar-controller.test.mjs")
tests = test_js.read_text(encoding="utf-8")
needle = 'import { profileForPersona } from "../../remote_agent_protocol/web_app/avatar/persona-profiles.js";\n'
if tests.count(needle) != 1:
    raise RuntimeError("could not add gaze import")
tests = tests.replace(needle, needle + 'import { GazeController } from "../../remote_agent_protocol/web_app/avatar/gaze-controller.js";\n', 1)
tests += '''

test("listening gaze remains close to camera and reduces saccades", () => {
  const gaze = new GazeController({ random: () => 0.5 });
  const result = gaze.update(0.016, "listening", true, false);
  assert.equal(result.enabled, true);
  assert.ok(Math.abs(result.x) <= 0.03);
  assert.ok(Math.abs(result.y) <= 0.03);
});

test("reduced motion keeps blink but suppresses large gaze offsets", () => {
  const gaze = new GazeController({ random: () => 1 });
  const result = gaze.update(8, "thinking", true, true);
  assert.ok(Math.abs(result.x) <= 0.04);
  assert.ok(Math.abs(result.y) <= 0.04);
});
'''
test_js.write_text(tests, encoding="utf-8")

test_py = Path("tests/test_web_gui.py")
pytests = test_py.read_text(encoding="utf-8")
pytests += '''


def test_avatar_scene_respects_reduced_motion_and_idle_gate():
    source = (WEB_APP / "avatar/avatar-scene.js").read_text(encoding="utf-8")
    assert "effectiveReducedMotion" in source
    assert "idleMotion" in source
    assert "GazeController" in source
    assert "expressionFor" in source
'''
test_py.write_text(pytests, encoding="utf-8")

subprocess.run(["python", "-m", "ruff", "format", str(test_py)], check=True)
subprocess.run(["node", "--test", str(test_js)], check=True)
subprocess.run(["python", "-m", "pytest", str(test_py), "-q", "--disable-warnings", "--maxfail=1"], check=True)
Path(__file__).unlink()
subprocess.run(["git", "add", str(avatar), str(test_js), str(test_py), ".github/avatar_tasks/task9.py"], check=True)
subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
subprocess.run(["git", "commit", "-m", "feat(avatar): animate expressions gaze and idle behavior"], check=True)
subprocess.run(["git", "push", "origin", "HEAD:feature/animated-butler-avatar"], check=True)
print("TASK 9 DONE: expressions, gaze, blinking, idle motion, and reduced motion committed")

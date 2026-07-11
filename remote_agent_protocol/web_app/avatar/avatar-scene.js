import * as THREE from "three";
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

  let visible = true;
  let disposed = false;
  let lastFrame = 0;
  let latest = null;
  const targetInterval = 1000 / settings.targetFps;

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
    lastFrame = time;
    if (latest) applyAvatarFrame(rig.controls, latest, time / 1000);
    renderer.render(scene, camera);
  };
  requestAnimationFrame(animate);

  return {
    update(value) { latest = value; },
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

function applyAvatarFrame(controls, frame, seconds) {
  const reduced = frame.settings.effectiveReducedMotion;
  const breathing = reduced || !frame.settings.idleMotion ? 0 : Math.sin(seconds * 1.35) * 0.008 * frame.profile.idleIntensity;
  controls.bust.scale.y = 0.72 + breathing;
  controls.head.rotation.y = frame.resolved.state === "thinking" ? -0.05 : 0;
}

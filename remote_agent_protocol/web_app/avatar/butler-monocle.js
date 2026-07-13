// Holographic diagnostic monocle over the butler's left eye (viewer right).
// The primary ring is built as two half-arcs so desync events can split it
// horizontally and snap it back. All motion funnels through update(), which
// receives the resolved state, the glitch scheduler's monocle desync amount,
// and the current speech envelope.

const TAU = Math.PI * 2;

const arcPoints = (THREE, radius, a0, a1, segments) => {
  const points = [];
  for (let i = 0; i <= segments; i += 1) {
    const angle = a0 + (a1 - a0) * (i / segments);
    points.push(new THREE.Vector3(Math.cos(angle) * radius, Math.sin(angle) * radius, 0));
  }
  return points;
};

export function createButlerMonocle(THREE, { kit, trackGeometry, quality = "high", anchor }) {
  const simplified = quality === "low";
  const group = new THREE.Group();
  group.name = "monocleRoot";
  if (anchor) group.position.copy(anchor);

  const line = (points, material, loop = false) => {
    const geometry = trackGeometry(new THREE.BufferGeometry().setFromPoints(points));
    return loop ? new THREE.LineLoop(geometry, material) : new THREE.Line(geometry, material);
  };

  const ringMaterial = kit.line(0.95);
  const secondaryMaterial = kit.line(0.6, { accent: true });
  const tickMaterial = kit.line(0.55);
  const reticleMaterial = kit.line(0.7, { accent: true });
  const orbiterMaterial = kit.glow(0.85, 0.6, { accent: true });
  const glowMaterial = kit.glow(0.12, 0.2);
  const connectorMaterial = kit.line(0.3);
  const lensSweepMaterial = kit.line(0);

  const R = 0.152;

  // Primary ring as split-capable halves.
  const ringPrimary = new THREE.Group();
  ringPrimary.name = "monocleRingPrimary";
  const upperHalf = new THREE.Group();
  upperHalf.add(line(arcPoints(THREE, R, 0, Math.PI, 30), ringMaterial));
  const lowerHalf = new THREE.Group();
  lowerHalf.add(line(arcPoints(THREE, R, Math.PI, TAU, 30), ringMaterial));
  ringPrimary.add(upperHalf, lowerHalf);
  group.add(ringPrimary);

  // Soft glow behind the lens.
  const glow = new THREE.Mesh(trackGeometry(new THREE.PlaneGeometry(0.5, 0.5)), glowMaterial);
  glow.position.z = -0.015;
  group.add(glow);

  // Incomplete secondary ring.
  const ringSecondary = new THREE.Group();
  ringSecondary.name = "monocleRingSecondary";
  if (!simplified) {
    ringSecondary.add(line(arcPoints(THREE, R + 0.024, 0.35, 0.35 + TAU * 0.62, 40), secondaryMaterial));
    ringSecondary.add(line(arcPoints(THREE, R + 0.024, 0.35 + TAU * 0.72, 0.35 + TAU * 0.82, 10), secondaryMaterial));
  }
  group.add(ringSecondary);

  // Rotating tick marks.
  const ticks = new THREE.Group();
  ticks.name = "monocleTicks";
  const tickPoints = [];
  const tickCount = simplified ? 8 : 16;
  for (let i = 0; i < tickCount; i += 1) {
    const angle = (i / tickCount) * TAU;
    const isMajor = i % 4 === 0;
    const r0 = R + 0.036;
    const r1 = r0 + (isMajor ? 0.02 : 0.01);
    tickPoints.push(
      new THREE.Vector3(Math.cos(angle) * r0, Math.sin(angle) * r0, 0),
      new THREE.Vector3(Math.cos(angle) * r1, Math.sin(angle) * r1, 0),
    );
  }
  ticks.add(new THREE.LineSegments(trackGeometry(new THREE.BufferGeometry().setFromPoints(tickPoints)), tickMaterial));
  group.add(ticks);

  // Reticle: offset crosshair + micro circle inside the lens.
  const reticle = new THREE.Group();
  reticle.name = "monocleReticle";
  reticle.add(line([new THREE.Vector3(-0.035, 0, 0), new THREE.Vector3(0.035, 0, 0)], reticleMaterial));
  reticle.add(line([new THREE.Vector3(0, -0.035, 0), new THREE.Vector3(0, 0.035, 0)], reticleMaterial));
  reticle.add(line(arcPoints(THREE, 0.02, 0, TAU, 14), reticleMaterial, true));
  reticle.position.set(0.03, 0.02, 0.004);
  group.add(reticle);

  // Orbiting marker.
  const orbiter = new THREE.Group();
  const orbiterDot = new THREE.Mesh(trackGeometry(new THREE.PlaneGeometry(0.024, 0.024)), orbiterMaterial);
  orbiterDot.position.x = R + 0.05;
  orbiter.add(orbiterDot);
  if (!simplified) group.add(orbiter);

  // Lens scan sweep: a chord that travels vertically inside the ring.
  const lensSweep = line([new THREE.Vector3(-R, 0, 0.002), new THREE.Vector3(R, 0, 0.002)], lensSweepMaterial);
  if (!simplified) group.add(lensSweep);

  // Connector toward the temple with an anchor node.
  const connector = new THREE.Group();
  connector.add(line([
    new THREE.Vector3(R * 0.92, R * 0.38, 0),
    new THREE.Vector3(R + 0.09, R * 0.62, -0.05),
    new THREE.Vector3(R + 0.16, R * 0.8, -0.14),
  ], connectorMaterial));
  const anchorDot = new THREE.Mesh(trackGeometry(new THREE.PlaneGeometry(0.02, 0.02)), kit.glow(0.4, 0.5));
  anchorDot.position.set(R + 0.16, R * 0.8, -0.14);
  connector.add(anchorDot);
  group.add(connector);

  const fx = {
    opacity: 1,
    scale: 1,
    scaleTarget: 1,
    yOffset: 0,
    yTarget: 0,
    tickStep: 0,
    tickTimer: 2,
    sweepPhase: 0.2,
    pulse: 0,
    lastState: "idle",
    popAt: -10,
  };
  const damp = (current, target, lambda, delta) =>
    current + (target - current) * (1 - Math.exp(-lambda * delta));

  const update = ({ state, seconds, delta, reducedMotion, desync = 0, envelope = 0, activity = 1 }) => {
    const speed = reducedMotion ? 0 : activity;
    const sleeping = state === "sleeping";

    if (state !== fx.lastState) {
      if (state === "happy") fx.popAt = seconds;
      fx.lastState = state;
    }

    // Base ring rotation and tick stepping.
    ringSecondary.rotation.z -= delta * speed * (state === "thinking" ? 1.9 : 0.24);
    fx.tickTimer -= delta;
    if (fx.tickTimer <= 0 && speed > 0) {
      fx.tickStep += (TAU / 16) * (state === "focused" ? 1 : Math.sin(seconds * 3.1) > 0 ? 1 : 2);
      fx.tickTimer = state === "focused" ? 1.1 : state === "thinking" ? 0.6 : 2.4 + 1.8 * Math.abs(Math.sin(seconds * 0.7));
    }
    ticks.rotation.z = damp(ticks.rotation.z, -fx.tickStep, reducedMotion ? 2 : 7, delta);
    orbiter.rotation.z -= delta * speed * (state === "thinking" ? 3.4 : 0.55);

    // Reticle behavior.
    if (state === "thinking" && !reducedMotion) {
      reticle.position.x = 0.03 + Math.sin(seconds * 2.3) * 0.04;
      reticle.position.y = 0.02 + Math.cos(seconds * 1.7) * 0.03;
      reticle.scale.setScalar(1);
    } else if (state === "focused") {
      reticle.position.x = damp(reticle.position.x, 0, 5, delta);
      reticle.position.y = damp(reticle.position.y, 0, 5, delta);
      reticle.scale.setScalar(damp(reticle.scale.x, 0.72, 5, delta));
    } else {
      reticle.position.x = damp(reticle.position.x, 0.03, 3, delta);
      reticle.position.y = damp(reticle.position.y, 0.02, 3, delta);
      reticle.scale.setScalar(damp(reticle.scale.x, 1, 3, delta));
    }

    // Scale: listening contracts a few percent; happy pops outward briefly.
    fx.scaleTarget = state === "listening" ? 0.95 : 1;
    const pop = Math.max(0, 1 - (seconds - fx.popAt) * 3);
    fx.scale = damp(fx.scale, fx.scaleTarget, 6, delta) + pop * 0.06;
    group.scale.setScalar(fx.scale);

    // Concerned: ring drifts downward briefly, then recovers.
    fx.yTarget = state === "concerned" ? -0.014 * (0.5 + 0.5 * Math.sin(seconds * 0.8)) : 0;
    fx.yOffset = damp(fx.yOffset, fx.yTarget, 4, delta);

    // Desync: split halves, lateral shove, abrupt tick skew; snaps back as the
    // scheduler's envelope closes.
    upperHalf.position.x = desync * 0.045;
    lowerHalf.position.x = -desync * 0.038;
    upperHalf.position.y = desync * 0.008;
    group.position.x = (anchor?.x ?? 0) + desync * 0.02;
    group.position.y = (anchor?.y ?? 0) + fx.yOffset - desync * 0.012;
    ticks.rotation.z += desync * 0.4;

    // Opacity/pulse: sleeping fades the monocle almost out; reduced motion
    // keeps only a slow breath.
    const pulseRate = reducedMotion ? 0.5 : state === "listening" ? 1.6 : 0.9;
    fx.pulse = 0.5 + 0.5 * Math.sin(seconds * pulseRate * TAU * 0.2);
    const base = sleeping ? 0.06 : 0.82 + fx.pulse * 0.14 + envelope * 0.18;
    ringMaterial.uniforms.uOpacity.value = base;
    secondaryMaterial.uniforms.uOpacity.value = sleeping ? 0.03 : 0.42 + fx.pulse * 0.12;
    tickMaterial.uniforms.uOpacity.value = sleeping ? 0.03 : 0.4 + fx.pulse * 0.1;
    reticleMaterial.uniforms.uOpacity.value = sleeping ? 0.02 : 0.5 + envelope * 0.2;
    orbiterMaterial.uniforms.uOpacity.value = sleeping ? 0 : 0.6 + fx.pulse * 0.25;
    glowMaterial.uniforms.uOpacity.value = sleeping ? 0.02 : 0.1 + envelope * 0.1;
    connectorMaterial.uniforms.uOpacity.value = sleeping ? 0.04 : 0.28;

    // Lens sweep.
    if (!simplified && !sleeping) {
      fx.sweepPhase = (fx.sweepPhase + delta * (reducedMotion ? 0.05 : 0.16) * activity) % 1;
      const y = (fx.sweepPhase * 2 - 1) * R * 0.9;
      const chord = Math.sqrt(Math.max(0.0001, R * R - y * y));
      lensSweep.position.y = y;
      lensSweep.scale.x = chord / R;
      lensSweepMaterial.uniforms.uOpacity.value = 0.3 * Math.sin(Math.PI * fx.sweepPhase);
    } else {
      lensSweepMaterial.uniforms.uOpacity.value = 0;
    }
  };

  return {
    group,
    controls: {
      monocleRoot: group,
      monocleRingPrimary: ringPrimary,
      monocleRingSecondary: ringSecondary,
      monocleReticle: reticle,
      monocleTicks: ticks,
    },
    update,
  };
}

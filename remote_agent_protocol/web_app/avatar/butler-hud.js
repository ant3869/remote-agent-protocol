// HUD framing for the butler: broken halo rings behind the head, side
// brackets, tiny status glyphs, an elliptical projection ring beneath the
// bust, ambient data motes (rising) plus a sparse data-fall column (sinking),
// and the scan-sweep state machine that drives the shared uScanY/uScan
// uniforms so every surface brightens as the sweep passes.

const TAU = Math.PI * 2;

// Figure silhouette: sweep-ring radius by root-local height (root sits at
// world y -0.55; update() receives rootY to convert for the world-space
// uScanY uniform).
const SWEEP_PROFILE = [
  [0.2, 0.55], [0.45, 0.68], [0.69, 0.72], [0.97, 0.26], [1.2, 0.2],
  [1.35, 0.3], [1.65, 0.48], [1.95, 0.44], [2.3, 0.1],
];

function sweepRadius(y) {
  if (y <= SWEEP_PROFILE[0][0]) return SWEEP_PROFILE[0][1];
  for (let i = 1; i < SWEEP_PROFILE.length; i += 1) {
    if (y <= SWEEP_PROFILE[i][0]) {
      const [y0, r0] = SWEEP_PROFILE[i - 1];
      const [y1, r1] = SWEEP_PROFILE[i];
      const t = (y - y0) / (y1 - y0);
      return r0 + (r1 - r0) * t;
    }
  }
  return SWEEP_PROFILE[SWEEP_PROFILE.length - 1][1];
}

export function createButlerHud(THREE, { kit, trackGeometry, quality = "high", random = Math.random }) {
  const group = new THREE.Group();
  group.name = "hudRoot";

  const line = (points, material, loop = false) => {
    const geometry = trackGeometry(new THREE.BufferGeometry().setFromPoints(points));
    return loop ? new THREE.LineLoop(geometry, material) : new THREE.Line(geometry, material);
  };
  const circlePoints = (radiusX, radiusY, segments, a0 = 0, a1 = TAU) => {
    const points = [];
    for (let i = 0; i <= segments; i += 1) {
      const angle = a0 + (a1 - a0) * (i / segments);
      points.push(new THREE.Vector3(Math.cos(angle) * radiusX, Math.sin(angle) * radiusY, 0));
    }
    return points;
  };

  // --- Halo behind the head.
  const halo = new THREE.Group();
  halo.name = "haloRoot";
  halo.position.set(0, 1.66, -0.36);
  group.add(halo);
  const haloMaterial = kit.line(0.3);
  const haloFaintMaterial = kit.line(0.07);
  const haloAccentMaterial = kit.line(0.22, { accent: true });
  const arcRing = new THREE.Group();
  [[0, 70], [95, 150], [170, 290], [315, 345]].forEach(([a0, a1]) => {
    arcRing.add(line(circlePoints(0.72, 0.72, 40, (a0 / 360) * TAU, (a1 / 360) * TAU), haloMaterial));
  });
  halo.add(arcRing);
  const tickRing = new THREE.Group();
  const tickPoints = [];
  for (let i = 0; i < 40; i += 1) {
    const angle = (i / 40) * TAU;
    const isMajor = i % 5 === 0;
    const r0 = 0.8;
    const r1 = isMajor ? 0.86 : 0.83;
    tickPoints.push(
      new THREE.Vector3(Math.cos(angle) * r0, Math.sin(angle) * r0, 0),
      new THREE.Vector3(Math.cos(angle) * r1, Math.sin(angle) * r1, 0),
    );
  }
  tickRing.add(new THREE.LineSegments(trackGeometry(new THREE.BufferGeometry().setFromPoints(tickPoints)), haloFaintMaterial));
  halo.add(tickRing);
  halo.add(line(circlePoints(0.92, 0.92, 56), haloFaintMaterial, true));
  const accentArc = new THREE.Group();
  accentArc.add(line(circlePoints(0.66, 0.66, 24, 0.4, 1.6), haloAccentMaterial));
  halo.add(accentArc);

  // --- Side brackets.
  const bracketMaterial = kit.line(0.14);
  for (const side of [-1, 1]) {
    const x = 0.95 * side;
    group.add(line([
      new THREE.Vector3(x + 0.07 * side, 1.98, -0.2),
      new THREE.Vector3(x, 1.98, -0.2),
      new THREE.Vector3(x, 1.32, -0.2),
      new THREE.Vector3(x + 0.07 * side, 1.32, -0.2),
    ], bracketMaterial));
  }

  // --- Status glyphs: tiny abstract marks, no text.
  const glyphMaterial = kit.line(0.2, { accent: true });
  const glyphs = new THREE.Group();
  glyphs.position.set(0.78, 2.32, -0.3);
  glyphs.add(line([
    new THREE.Vector3(0, 0.03, 0), new THREE.Vector3(0.026, -0.018, 0),
    new THREE.Vector3(-0.026, -0.018, 0),
  ], glyphMaterial, true));
  glyphs.add(line([new THREE.Vector3(0.06, 0.026, 0), new THREE.Vector3(0.06, -0.02, 0)], glyphMaterial));
  glyphs.add(line([new THREE.Vector3(0.085, 0.012, 0), new THREE.Vector3(0.085, -0.02, 0)], glyphMaterial));
  glyphs.add(line(circlePoints(0.016, 0.016, 10), glyphMaterial, true));
  glyphs.children[3].position.set(-0.05, 0, 0);
  group.add(glyphs);

  // --- Projection ring beneath the bust.
  const projection = new THREE.Group();
  projection.name = "projectionRing";
  projection.position.y = 0.42;
  group.add(projection);
  const projectionMaterial = kit.line(0.34);
  const projectionFaintMaterial = kit.line(0.12);
  const outerRing = new THREE.Group();
  const ringLine = line(circlePoints(0.85, 0.85, 64), projectionMaterial, true);
  ringLine.rotation.x = Math.PI / 2;
  ringLine.scale.y = 0.44;
  outerRing.add(ringLine);
  const radialPoints = [];
  for (let i = 0; i < 16; i += 1) {
    const angle = (i / 16) * TAU;
    radialPoints.push(
      new THREE.Vector3(Math.cos(angle) * 0.88, 0, Math.sin(angle) * 0.88 * 0.44),
      new THREE.Vector3(Math.cos(angle) * 0.95, 0, Math.sin(angle) * 0.95 * 0.44),
    );
  }
  outerRing.add(new THREE.LineSegments(trackGeometry(new THREE.BufferGeometry().setFromPoints(radialPoints)), projectionFaintMaterial));
  projection.add(outerRing);
  const innerRing = line(circlePoints(0.62, 0.62, 48), projectionFaintMaterial, true);
  innerRing.rotation.x = Math.PI / 2;
  innerRing.scale.y = 0.44;
  projection.add(innerRing);

  // --- Ambient motes (rising) and data fall (sinking).
  const moteCount = quality === "low" ? 60 : quality === "medium" ? 130 : 230;
  const moteDrift = { value: 0 };
  const buildParticles = (count, radiusRange, direction, opacity, size, zOffset) => {
    const positions = new Float32Array(count * 3);
    const seeds = new Float32Array(count);
    const speeds = new Float32Array(count);
    for (let i = 0; i < count; i += 1) {
      const radius = radiusRange[0] + random() * (radiusRange[1] - radiusRange[0]);
      const angle = random() * TAU;
      positions[i * 3] = Math.cos(angle) * radius;
      positions[i * 3 + 1] = random() * 3;
      positions[i * 3 + 2] = Math.sin(angle) * radius * 0.7 + zOffset;
      seeds[i] = random();
      speeds[i] = 0.5 + random() * 1.2;
    }
    const geometry = trackGeometry(new THREE.BufferGeometry());
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("aSeed", new THREE.BufferAttribute(seeds, 1));
    geometry.setAttribute("aSpeed", new THREE.BufferAttribute(speeds, 1));
    const material = kit.motes(opacity, size, { drift: moteDrift, direction });
    const points = new THREE.Points(geometry, material);
    points.frustumCulled = false;
    return points;
  };
  group.add(buildParticles(moteCount, [0.35, 1.05], 1, 0.45, 4.6, 0));
  if (quality !== "low") {
    group.add(buildParticles(Math.round(moteCount * 0.25), [0.95, 1.25], -1, 0.2, 3.6, -0.5));
  }

  // --- Depth points: a few static sparks far behind the figure.
  const depthCount = 14;
  const depthPositions = new Float32Array(depthCount * 3);
  const depthSeeds = new Float32Array(depthCount);
  for (let i = 0; i < depthCount; i += 1) {
    depthPositions[i * 3] = (random() * 2 - 1) * 1.3;
    depthPositions[i * 3 + 1] = 0.2 + random() * 1.9;
    depthPositions[i * 3 + 2] = -0.8 - random() * 0.7;
    depthSeeds[i] = random();
  }
  const depthGeometry = trackGeometry(new THREE.BufferGeometry());
  depthGeometry.setAttribute("position", new THREE.BufferAttribute(depthPositions, 3));
  depthGeometry.setAttribute("aSeed", new THREE.BufferAttribute(depthSeeds, 1));
  group.add(new THREE.Points(depthGeometry, kit.points(0.16, 2.4)));

  // --- Scan sweep ring + state machine driving shared uScanY/uScan.
  const sweepMaterial = kit.line(0);
  const sweepRing = line(circlePoints(1, 1, 48), sweepMaterial, true);
  sweepRing.rotation.x = Math.PI / 2;
  sweepRing.visible = false;
  group.add(sweepRing);

  const sweep = {
    active: null,
    cooldown: 1.2,
    queue: [],
  };
  const startSweep = ({ reverse = false, duration = 3.4, brightness = 0.55 } = {}) => {
    sweep.active = {
      t: 0,
      duration,
      brightness,
      y0: reverse ? 2.4 : 0,
      y1: reverse ? 0 : 2.4,
    };
  };
  const pulseSweep = (kind = "single") => {
    if (kind === "dual") {
      startSweep({ duration: 1.4, brightness: 0.75 });
      sweep.queue.push({ duration: 1.4, brightness: 0.6 });
    } else if (kind === "boot") {
      startSweep({ duration: 1.1, brightness: 0.9 });
      sweep.queue.push({ duration: 1.3, brightness: 0.7 });
    } else {
      startSweep({});
    }
  };

  const update = ({ delta, seconds, flow, state, reducedMotion, rootY = -0.55 }) => {
    // Varied slow movement: outer halo circle static, arcs slow cw, ticks
    // slow ccw, accent arc with the thinking speed-up.
    arcRing.rotation.z += delta * 0.14 * flow;
    tickRing.rotation.z -= delta * 0.045 * flow;
    accentArc.rotation.z += delta * flow * (state === "thinking" ? 1.1 : 0.2);
    outerRing.rotation.y += delta * 0.1 * flow;
    innerRing.rotation.z -= delta * 0.035 * flow;
    glyphMaterial.uniforms.uOpacity.value =
      (state === "thinking" ? 0.5 : 0.16) * (0.6 + 0.4 * Math.sin(seconds * (state === "thinking" ? 4.2 : 1.3)));
    moteDrift.value += delta * (0.05 + 0.24 * flow);

    if (!sweep.active) {
      sweep.cooldown -= delta;
      if (sweep.cooldown <= 0) {
        if (sweep.queue.length) {
          startSweep(sweep.queue.shift());
        } else if (flow > 0.04) {
          const thinking = state === "thinking";
          startSweep({
            reverse: !reducedMotion && random() < 0.16,
            duration: (thinking ? 2.1 : 3.6) / Math.max(0.3, flow),
            brightness: thinking ? 0.7 : 0.5,
          });
        }
        sweep.cooldown = state === "thinking" ? 1.4 : 2.6 + random() * 2.4;
      }
    }

    if (sweep.active) {
      const active = sweep.active;
      active.t += delta / active.duration;
      if (active.t >= 1) {
        sweep.active = null;
        sweepRing.visible = false;
        kit.shared.uScan.value = 0;
        sweepMaterial.uniforms.uOpacity.value = 0;
      } else {
        const eased = active.t * active.t * (3 - 2 * active.t);
        const y = active.y0 + (active.y1 - active.y0) * eased;
        const fadeIn = Math.sin(Math.PI * Math.min(1, active.t));
        const radius = Math.max(0.04, sweepRadius(y));
        sweepRing.visible = true;
        sweepRing.position.y = y;
        sweepRing.scale.set(radius, radius * 0.5, 1);
        const brightness = active.brightness * (reducedMotion ? 0.55 : 1);
        sweepMaterial.uniforms.uOpacity.value = fadeIn * brightness * 0.8;
        kit.shared.uScanY.value = y + rootY;
        kit.shared.uScan.value = fadeIn * brightness * 1.6;
      }
    } else {
      kit.shared.uScan.value = Math.max(0, kit.shared.uScan.value - delta * 3);
    }
  };

  return { group, update, pulseSweep };
}

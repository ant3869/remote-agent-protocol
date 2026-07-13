// Swept-back formal hair: a scalp patch grown from the head's own surface
// function (with a side part carved in and a raised front wave), plus bright
// flow strands that sweep front-to-back. Mostly rigid; only a light shimmer
// animates. Direction convention: azimuth 0 faces +z (front), theta from +y.

const PART_AZIMUTH = -0.62;
const SWEEP_DRIFT = 0.5;

const gauss = (x) => Math.exp(-x * x);

function smoothstep(edge0, edge1, x) {
  const t = Math.min(1, Math.max(0, (x - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

// Hairline: high across the front, slight temple recession, low at the nape.
function edgeTheta(azimuth) {
  const front = Math.cos(azimuth);
  let theta = Math.PI * (0.34 + 0.24 * (1 - front) / 2);
  theta += Math.PI * 0.03 * gauss((Math.abs(azimuth) - 0.72) / 0.22);
  return theta;
}

function direction(THREE, azimuth, theta) {
  return new THREE.Vector3(
    Math.sin(theta) * Math.sin(azimuth),
    Math.cos(theta),
    Math.sin(theta) * Math.cos(azimuth),
  );
}

export function createButlerHair(THREE, { kit, trackGeometry, surfacePoint, quality = "high" }) {
  const group = new THREE.Group();
  group.name = "hairRoot";

  const surfaceAt = (azimuth, theta, lift) => {
    const dir = direction(THREE, azimuth, theta);
    return surfacePoint(dir.x, dir.y, dir.z, lift);
  };

  const liftAt = (azimuth, v) => {
    // v: 0 at crown, 1 at hair edge. Base shell lift, pompadour swell near the
    // front hairline, and a carved dent along the side part.
    const frontness = smoothstep(0.1, 0.9, Math.cos(azimuth));
    let lift = 0.014 + 0.012 * (1 - v);
    lift += 0.06 * gauss((v - 0.9) / 0.22) * frontness;
    if (v > 0.12) lift *= 1 - 0.85 * gauss((azimuth - PART_AZIMUTH) / 0.09);
    return lift;
  };

  // --- Scalp patch.
  const segments = quality === "low" ? 18 : 26;
  const rings = quality === "low" ? 6 : 9;
  const grid = [];
  for (let ring = 0; ring <= rings; ring += 1) {
    const v = ring / rings;
    const row = [];
    for (let seg = 0; seg <= segments; seg += 1) {
      const azimuth = (seg / segments) * Math.PI * 2 - Math.PI;
      const theta = Math.PI * 0.05 + (edgeTheta(azimuth) - Math.PI * 0.05) * v;
      row.push(surfaceAt(azimuth, theta, liftAt(azimuth, v)));
    }
    grid.push(row);
  }
  const positions = [];
  const push = (p) => positions.push(p.x, p.y, p.z);
  for (let ring = 0; ring < rings; ring += 1) {
    for (let seg = 0; seg < segments; seg += 1) {
      const a = grid[ring][seg];
      const b = grid[ring][seg + 1];
      const c = grid[ring + 1][seg + 1];
      const d = grid[ring + 1][seg];
      push(a); push(b); push(c);
      push(a); push(c); push(d);
    }
  }
  const patchGeometry = trackGeometry(new THREE.BufferGeometry());
  patchGeometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  patchGeometry.computeVertexNormals();
  const patchMaterial = kit.shell(0.62, 0.05);
  group.add(new THREE.Mesh(patchGeometry, patchMaterial));

  // Hairline rim: a bright contour tracing the patch edge so the silhouette
  // stays readable when the shell fill is subtle.
  const rimMaterial = kit.line(0.5);
  const rimPoints = grid[rings].map((p) => p.clone());
  group.add(new THREE.LineLoop(trackGeometry(new THREE.BufferGeometry().setFromPoints(rimPoints)), rimMaterial));

  // --- Front wave: a raised crest along the front hairline, sweeping from the
  // part toward the opposite temple.
  const waveMaterial = kit.shell(0.85, 0.09);
  const waveEdgeMaterial = kit.line(0.72);
  const wavePath = [];
  for (let i = 0; i <= 10; i += 1) {
    const t = i / 10;
    const azimuth = PART_AZIMUTH + 0.12 + (0.95 - (PART_AZIMUTH + 0.12)) * t;
    const theta = edgeTheta(azimuth) - Math.PI * (0.015 + 0.05 * Math.sin(Math.PI * t));
    wavePath.push(surfaceAt(azimuth, theta, 0.045 + 0.05 * Math.sin(Math.PI * Math.min(1, t * 1.25))));
  }
  const waveCurve = new THREE.CatmullRomCurve3(wavePath);
  group.add(new THREE.Mesh(trackGeometry(new THREE.TubeGeometry(waveCurve, 20, 0.052, 6, false)), waveMaterial));
  group.add(new THREE.Line(
    trackGeometry(new THREE.BufferGeometry().setFromPoints(
      waveCurve.getPoints(24).map((p) => p.clone().multiplyScalar(1.035)),
    )),
    waveEdgeMaterial,
  ));

  // --- Side sections above the ears, brushed backward.
  const sideMaterial = kit.shell(0.7, 0.06);
  for (const side of [-1, 1]) {
    const path = [];
    for (let i = 0; i <= 6; i += 1) {
      const t = i / 6;
      const azimuth = side * (1.15 + 0.85 * t);
      const theta = Math.PI * (0.44 + 0.05 * t);
      path.push(surfaceAt(azimuth, theta, 0.02 + 0.012 * Math.sin(Math.PI * t)));
    }
    group.add(new THREE.Mesh(
      trackGeometry(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(path), 12, 0.026, 5, false)),
      sideMaterial,
    ));
  }

  // --- Flow strands: bright combed-back lines from the hairline over the
  // crown to the nape, drifting toward the sweep side.
  const strandMaterial = kit.line(0.55);
  const strandCount = quality === "low" ? 3 : quality === "medium" ? 5 : 7;
  const strandStarts = [];
  for (let i = 0; i < strandCount; i += 1) {
    strandStarts.push(PART_AZIMUTH + 0.16 + ((i + 0.5) / strandCount) * 1.7);
  }
  for (const start of strandStarts) {
    const path = [];
    for (let i = 0; i <= 8; i += 1) {
      const t = i / 8;
      const azimuth = start * (1 - t) + (Math.PI * Math.sign(start || 1) - start * 0.15) * t
        + SWEEP_DRIFT * t * (1 - t);
      const theta = edgeTheta(start) * (1 - t) * (1 - t)
        + Math.PI * 0.1 * 2 * t * (1 - t)
        + Math.PI * 0.58 * t * t;
      path.push(surfaceAt(azimuth, Math.max(Math.PI * 0.07, theta), 0.028 + 0.014 * Math.sin(Math.PI * t)));
    }
    group.add(new THREE.Line(
      trackGeometry(new THREE.BufferGeometry().setFromPoints(new THREE.CatmullRomCurve3(path).getPoints(26))),
      strandMaterial,
    ));
  }

  // --- Sparse point accents on the patch (skipped on low quality).
  if (quality !== "low") {
    const accentCount = quality === "high" ? 46 : 26;
    const accentPositions = new Float32Array(accentCount * 3);
    const accentSeeds = new Float32Array(accentCount);
    for (let i = 0; i < accentCount; i += 1) {
      const azimuth = ((i * 0.61803398875) % 1) * Math.PI * 2 - Math.PI;
      const v = 0.15 + ((i * 0.3819660113) % 1) * 0.8;
      const theta = Math.PI * 0.05 + (edgeTheta(azimuth) - Math.PI * 0.05) * v;
      const p = surfaceAt(azimuth, theta, liftAt(azimuth, v) + 0.012);
      accentPositions[i * 3] = p.x;
      accentPositions[i * 3 + 1] = p.y;
      accentPositions[i * 3 + 2] = p.z;
      accentSeeds[i] = (i * 0.7548776662) % 1;
    }
    const accentGeometry = trackGeometry(new THREE.BufferGeometry());
    accentGeometry.setAttribute("position", new THREE.BufferAttribute(accentPositions, 3));
    accentGeometry.setAttribute("aSeed", new THREE.BufferAttribute(accentSeeds, 1));
    group.add(new THREE.Points(accentGeometry, kit.points(0.3, 2.6)));
  }

  const update = (seconds) => {
    // Light shimmer only; the hairstyle itself stays rigid.
    strandMaterial.uniforms.uOpacity.value = 0.5 + 0.12 * Math.sin(seconds * 0.9);
    waveEdgeMaterial.uniforms.uOpacity.value = 0.64 + 0.14 * Math.sin(seconds * 0.7 + 1.4);
  };

  return { group, update };
}

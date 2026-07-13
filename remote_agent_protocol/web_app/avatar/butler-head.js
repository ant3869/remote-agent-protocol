// Butler head: sculpted shell with a swinging jaw section, characterful eyes,
// curved brows, shaped lips, and the signature curled mustache. Geometry is
// derived from a single direction-space sculpt function, exposed as
// surfacePoint() so hair and contour lines can hug the same surface.

const TAU = Math.PI * 2;
const LIP_POINTS = 29;
const WAVE_BARS = 9;
const JAW_PIVOT = Object.freeze({ x: 0, y: -0.19, z: -0.02 });
const APERTURE_REST = 0.74;
const UNSQUASH = 1 / APERTURE_REST;

// Gaussian bumps in unit-direction space (x right, y up), weighted toward the
// front hemisphere. amp > 0 pushes out, < 0 carves in. sideBias skews the
// mirrored features slightly so the face is not procedurally sterile.
const FACE_FEATURES = [
  { x: 0, y: 0.52, sx: 0.5, sy: 0.2, amp: 0.016 },          // forehead
  { x: 0, y: 0.3, sx: 0.44, sy: 0.11, amp: 0.032 },         // brow ridge
  { x: 0, y: 0.2, sx: 0.12, sy: 0.08, amp: -0.014 },        // glabella
  { x: -0.4, y: 0.13, sx: 0.17, sy: 0.12, amp: -0.056 },    // eye sockets
  { x: 0.4, y: 0.13, sx: 0.17, sy: 0.12, amp: -0.054 },
  { x: 0, y: 0.08, sx: 0.09, sy: 0.2, amp: 0.045 },         // nose bridge
  { x: 0, y: -0.17, sx: 0.1, sy: 0.09, amp: 0.088 },        // nose tip
  { x: -0.1, y: -0.22, sx: 0.07, sy: 0.06, amp: 0.02 },     // nose wings
  { x: 0.1, y: -0.22, sx: 0.07, sy: 0.06, amp: 0.02 },
  { x: 0, y: -0.31, sx: 0.05, sy: 0.06, amp: 0.013 },       // philtrum
  { x: 0, y: -0.41, sx: 0.19, sy: 0.08, amp: 0.024 },       // lips
  { x: 0, y: -0.53, sx: 0.13, sy: 0.05, amp: -0.01 },       // chin crease
  { x: 0, y: -0.64, sx: 0.16, sy: 0.13, amp: 0.058 },       // chin
  { x: -0.55, y: -0.06, sx: 0.18, sy: 0.14, amp: 0.04 },    // cheekbones
  { x: 0.55, y: -0.06, sx: 0.18, sy: 0.14, amp: 0.037 },
  { x: -0.44, y: -0.3, sx: 0.16, sy: 0.14, amp: -0.02 },    // cheek hollows
  { x: 0.44, y: -0.3, sx: 0.16, sy: 0.14, amp: -0.018 },
  { x: -0.62, y: -0.44, sx: 0.2, sy: 0.16, amp: 0.02 },     // jaw corners
  { x: 0.62, y: -0.44, sx: 0.2, sy: 0.16, amp: 0.02 },
  { x: -0.72, y: 0.38, sx: 0.24, sy: 0.24, amp: -0.03 },    // temples
  { x: 0.72, y: 0.38, sx: 0.24, sy: 0.24, amp: -0.028 },
];

function smoothstep(edge0, edge1, x) {
  const t = Math.min(1, Math.max(0, (x - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

// Deform a unit direction into the head-local surface position: gaussian
// features on the radius, an ellipsoid squash, a jaw-narrowing taper toward
// the chin, and a whisper of smooth asymmetry.
function sculptDirection(direction, target) {
  const front = smoothstep(0.12, 0.68, direction.z);
  let radius = 0.5;
  for (const feature of FACE_FEATURES) {
    radius += feature.amp * front * Math.exp(
      -(((direction.x - feature.x) / feature.sx) ** 2 + ((direction.y - feature.y) / feature.sy) ** 2),
    );
  }
  radius *= 1 + 0.006 * Math.sin(direction.x * 5.1 + direction.y * 8.7) * front;
  target.set(direction.x * radius * 0.86, direction.y * radius * 1.08, direction.z * radius * 0.82);
  const jawTaper = 1 - 0.24 * smoothstep(-0.08, -0.85, direction.y) * (0.4 + 0.6 * front);
  target.x *= jawTaper;
  return target;
}

function sculptHeadGeometry(THREE, geometry) {
  const attribute = geometry.attributes.position;
  const vertex = new THREE.Vector3();
  const out = new THREE.Vector3();
  for (let i = 0; i < attribute.count; i += 1) {
    vertex.fromBufferAttribute(attribute, i).normalize();
    sculptDirection(vertex, out);
    attribute.setXYZ(i, out.x, out.y, out.z);
  }
  attribute.needsUpdate = true;
  geometry.computeVertexNormals();
}

// Partition the sculpted triangle soup into the static skull and the swinging
// jaw section (front-lower triangles), re-anchoring jaw vertices to the pivot.
function splitJaw(THREE, geometry, trackGeometry) {
  const soup = geometry.index ? trackGeometry(geometry.toNonIndexed()) : geometry;
  const position = soup.attributes.position;
  const headTriangles = [];
  const jawTriangles = [];
  for (let i = 0; i < position.count; i += 3) {
    const cy = (position.getY(i) + position.getY(i + 1) + position.getY(i + 2)) / 3;
    const cz = (position.getZ(i) + position.getZ(i + 1) + position.getZ(i + 2)) / 3;
    const target = cy < -0.24 && cz > 0.04 ? jawTriangles : headTriangles;
    for (let j = 0; j < 3; j += 1) {
      target.push(position.getX(i + j), position.getY(i + j), position.getZ(i + j));
    }
  }
  for (let i = 0; i < jawTriangles.length; i += 3) {
    jawTriangles[i] -= JAW_PIVOT.x;
    jawTriangles[i + 1] -= JAW_PIVOT.y;
    jawTriangles[i + 2] -= JAW_PIVOT.z;
  }
  const build = (points) => {
    const built = new THREE.BufferGeometry();
    built.setAttribute("position", new THREE.Float32BufferAttribute(points, 3));
    built.computeVertexNormals();
    return trackGeometry(built);
  };
  return { headShellGeometry: build(headTriangles), jawShellGeometry: build(jawTriangles), headTriangles, jawTriangles };
}

function dedupeVertices(points) {
  const seen = new Set();
  const unique = [];
  for (let i = 0; i < points.length; i += 3) {
    const key = `${points[i].toFixed(3)},${points[i + 1].toFixed(3)},${points[i + 2].toFixed(3)}`;
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(points[i], points[i + 1], points[i + 2]);
  }
  return unique;
}

function seededPoints(THREE, vertices, material, trackGeometry) {
  const geometry = trackGeometry(new THREE.BufferGeometry());
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(vertices, 3));
  const seeds = new Float32Array(vertices.length / 3);
  for (let i = 0; i < seeds.length; i += 1) seeds[i] = (i * 0.61803398875) % 1;
  geometry.setAttribute("aSeed", new THREE.BufferAttribute(seeds, 1));
  return new THREE.Points(geometry, material);
}

export function createButlerHead(THREE, { kit, trackGeometry, quality = "high" }) {
  const head = new THREE.Group();
  head.name = "head";
  const direction = new THREE.Vector3();
  const surfacePoint = (x, y, z, lift = 0) => {
    const out = new THREE.Vector3();
    direction.set(x, y, z).normalize();
    sculptDirection(direction, out);
    if (lift) out.addScaledVector(direction, lift);
    return out;
  };

  const detail = quality === "low" ? 3 : 4;
  const headGeometry = trackGeometry(new THREE.IcosahedronGeometry(0.5, detail));
  sculptHeadGeometry(THREE, headGeometry);
  const { headShellGeometry, jawShellGeometry, headTriangles, jawTriangles } =
    splitJaw(THREE, headGeometry, trackGeometry);

  const wireMaterial = kit.line(0.085);
  const vertexMaterial = kit.points(0.34, 2.9);
  head.add(new THREE.LineSegments(trackGeometry(new THREE.WireframeGeometry(headShellGeometry)), wireMaterial));
  const headPoints = seededPoints(THREE, dedupeVertices(headTriangles), vertexMaterial, trackGeometry);
  head.add(headPoints);
  head.add(new THREE.Mesh(headShellGeometry, kit.shell(0.5, 0.045)));

  const jaw = new THREE.Group();
  jaw.name = "jaw";
  jaw.position.set(JAW_PIVOT.x, JAW_PIVOT.y, JAW_PIVOT.z);
  head.add(jaw);
  jaw.add(new THREE.LineSegments(trackGeometry(new THREE.WireframeGeometry(jawShellGeometry)), wireMaterial));
  jaw.add(seededPoints(THREE, dedupeVertices(jawTriangles), vertexMaterial, trackGeometry));
  jaw.add(new THREE.Mesh(jawShellGeometry, kit.shell(0.42, 0.03)));

  const lineFromPoints = (points, material, loop = false) => {
    const geometry = trackGeometry(new THREE.BufferGeometry().setFromPoints(points));
    return loop ? new THREE.LineLoop(geometry, material) : new THREE.Line(geometry, material);
  };
  const curveThrough = (anchors, samples, material, lift = 0.012) => {
    const points = anchors.map(([x, y, z]) => surfacePoint(x, y, z, lift));
    const curve = new THREE.CatmullRomCurve3(points);
    return lineFromPoints(curve.getPoints(samples), material);
  };

  // Major facial contours: these carry the face when wireframe opacity drops.
  const contourMaterial = kit.line(0.5);
  const softContourMaterial = kit.line(0.24);
  const jawline = curveThrough([
    [-0.82, -0.18, 0.3], [-0.62, -0.5, 0.52], [-0.3, -0.78, 0.62],
    [0, -0.88, 0.66], [0.3, -0.78, 0.62], [0.62, -0.5, 0.52], [0.82, -0.18, 0.3],
  ], 40, contourMaterial);
  jaw.add(jawline);
  jawline.position.set(-JAW_PIVOT.x, -JAW_PIVOT.y, -JAW_PIVOT.z);
  head.add(curveThrough([
    [-0.66, 0.26, 0.6], [-0.34, 0.34, 0.82], [0, 0.36, 0.9], [0.34, 0.34, 0.82], [0.66, 0.26, 0.6],
  ], 30, softContourMaterial));
  head.add(curveThrough([
    [0, 0.3, 0.9], [0, 0.1, 0.95], [0, -0.14, 0.98], [0, -0.24, 0.95],
  ], 16, contourMaterial));
  head.add(curveThrough([
    [-0.62, -0.1, 0.62], [-0.48, -0.22, 0.78], [-0.32, -0.26, 0.86],
  ], 12, softContourMaterial));
  head.add(curveThrough([
    [0.62, -0.1, 0.62], [0.48, -0.22, 0.78], [0.32, -0.26, 0.86],
  ], 12, softContourMaterial));

  // --- Eyes. The aperture group is the scene-driven lid (scale.y within
  // [0.04, 0.74]); children are authored unsquashed so the resting squash
  // renders the intended almond shape, and a full blink collapses everything
  // into a curved lash line.
  const cavityMaterial = kit.dark(0.5);
  const lashMaterial = kit.line(0.9);
  const underMaterial = kit.line(0.42);
  const irisMaterial = kit.line(0.6);
  const pupilMaterial = kit.glow(1, 0.5);
  const catchlightMaterial = kit.glow(0.75, 0.72);
  const eyeGlowMaterial = kit.glow(0.1, 0.12);
  const cornerAccentMaterial = kit.line(0.35);

  const glowPlane = (width, height, material) => new THREE.Mesh(
    trackGeometry(new THREE.PlaneGeometry(width, height)), material,
  );

  const lidProfile = (side, upper) => {
    const points = [];
    const halfW = 0.112;
    for (let i = 0; i <= 20; i += 1) {
      const t = i / 20;
      const x = (t * 2 - 1) * halfW;
      const outer = side < 0 ? 1 - t : t;
      const arc = Math.sin(Math.PI * t) ** 0.92;
      const y = upper
        ? (0.062 * arc - 0.02 * smoothstep(0.62, 1, outer)) * UNSQUASH
        : (-0.04 * Math.sin(Math.PI * t)) * UNSQUASH;
      points.push(new THREE.Vector3(x, y, 0));
    }
    return points;
  };

  const buildEye = (side, name) => {
    const anchor = surfacePoint(0.4 * side, 0.13, 0.86, 0.035);
    const group = new THREE.Group();
    group.name = name;
    group.position.copy(anchor);
    head.add(group);
    const cavity = glowPlane(0.24, 0.17, cavityMaterial);
    cavity.position.z = -0.012;
    group.add(cavity);

    const aperture = new THREE.Group();
    aperture.name = `${name}-aperture`;
    aperture.scale.y = APERTURE_REST;
    group.add(aperture);
    aperture.add(lineFromPoints(lidProfile(side, true), lashMaterial));
    aperture.add(lineFromPoints(lidProfile(side, false), underMaterial));
    const cornerTick = lineFromPoints([
      new THREE.Vector3(0.1 * side, 0.008 * UNSQUASH, 0),
      new THREE.Vector3(0.128 * side, -0.012 * UNSQUASH, 0),
    ], cornerAccentMaterial);
    aperture.add(cornerTick);
    const ambient = glowPlane(0.3, 0.3 * UNSQUASH, eyeGlowMaterial);
    ambient.position.z = -0.006;
    aperture.add(ambient);

    const carrier = new THREE.Group();
    carrier.scale.setScalar(0.55);
    carrier.position.z = 0.01;
    aperture.add(carrier);
    const pupil = new THREE.Group();
    pupil.name = `${name}-pupil`;
    pupil.add(glowPlane(0.115, 0.115 * UNSQUASH, pupilMaterial));
    const catchlight = glowPlane(0.028, 0.028 * UNSQUASH, catchlightMaterial);
    catchlight.position.set(0.02, 0.024 * UNSQUASH, 0.004);
    pupil.add(catchlight);
    const iris = new THREE.Group();
    const irisPoints = [];
    for (let i = 0; i <= 28; i += 1) {
      const angle = (i / 28) * TAU;
      irisPoints.push(new THREE.Vector3(Math.cos(angle) * 0.078, Math.sin(angle) * 0.078 * UNSQUASH, 0));
    }
    iris.add(new THREE.LineLoop(trackGeometry(new THREE.BufferGeometry().setFromPoints(irisPoints)), irisMaterial));
    for (let i = 0; i < 3; i += 1) {
      const start = (i / 3) * TAU;
      const arcPoints = [];
      for (let j = 0; j <= 8; j += 1) {
        const angle = start + (j / 8) * TAU * 0.16;
        arcPoints.push(new THREE.Vector3(Math.cos(angle) * 0.098, Math.sin(angle) * 0.098 * UNSQUASH, 0));
      }
      iris.add(lineFromPoints(arcPoints, irisMaterial));
    }
    pupil.add(iris);
    carrier.add(pupil);
    return { group, aperture, pupil, iris, catchlight };
  };
  const leftEye = buildEye(-1, "eyeLeft");
  const rightEye = buildEye(1, "eyeRight");

  // --- Brows: two-piece curved ridges so expressions can bend inner and outer
  // halves independently underneath the scene-driven group transform.
  const browFillMaterial = kit.line(0.5);
  const browEdgeMaterial = kit.line(0.85);
  const buildBrow = (side, name) => {
    const anchor = surfacePoint(0.4 * side, 0.3, 0.84, 0.03);
    const group = new THREE.Group();
    group.name = name;
    group.position.copy(anchor);
    group.position.y = 0.22;
    head.add(group);
    const buildSegment = (fromX, toX, arcLift, pivotX) => {
      const pivot = new THREE.Group();
      pivot.position.x = pivotX;
      const curve = new THREE.CubicBezierCurve3(
        new THREE.Vector3(fromX - pivotX, -0.004, 0),
        new THREE.Vector3(fromX * 0.4 + toX * 0.6 - pivotX, arcLift, 0.008),
        new THREE.Vector3(fromX * 0.2 + toX * 0.8 - pivotX, arcLift * 0.9, 0.008),
        new THREE.Vector3(toX - pivotX, 0, 0),
      );
      const tube = new THREE.Mesh(trackGeometry(new THREE.TubeGeometry(curve, 10, 0.0125, 5, false)), browFillMaterial);
      const edge = lineFromPoints(curve.getPoints(12).map((p) => new THREE.Vector3(p.x, p.y + 0.012, p.z + 0.004)), browEdgeMaterial);
      pivot.add(tube, edge);
      group.add(pivot);
      return pivot;
    };
    const inner = buildSegment(-0.095 * side, 0.005 * side, 0.02, -0.095 * side);
    const outer = buildSegment(0.005 * side, 0.1 * side, 0.026, 0.1 * side);
    return { group, inner, outer };
  };
  const browLeft = buildBrow(-1, "browLeft");
  const browRight = buildBrow(1, "browRight");

  // --- Mouth: shaped lip curves rebuilt by shapeMouth(), interior darkness,
  // a teeth glint on wide-open poses, and dim waveform bars during speech.
  const lipMaterial = kit.line(0.9);
  const lowerLipMaterial = kit.line(0.7);
  const mouthUpper = new THREE.Group();
  mouthUpper.name = "mouthUpper";
  mouthUpper.position.set(0, -0.185, 0.44);
  head.add(mouthUpper);
  const upperLip = new THREE.Line(trackGeometry(lipBuffer(THREE)), lipMaterial);
  mouthUpper.add(upperLip);
  const mouthLower = new THREE.Group();
  mouthLower.name = "mouthLower";
  mouthLower.position.set(0, -0.035, 0.46);
  jaw.add(mouthLower);
  const lowerLip = new THREE.Line(trackGeometry(lipBuffer(THREE)), lowerLipMaterial);
  mouthLower.add(lowerLip);

  const interiorMaterial = kit.dark(0);
  const interior = glowPlane(0.22, 0.16, interiorMaterial);
  interior.position.set(0, -0.03, 0.415);
  jaw.add(interior);
  // Upper teeth glint stays with the skull so it emerges as the jaw drops.
  const teethMaterial = kit.line(0);
  const teeth = lineFromPoints([
    new THREE.Vector3(-0.055, 0, 0), new THREE.Vector3(0.055, 0, 0),
  ], teethMaterial);
  teeth.position.set(0, -0.198, 0.445);
  head.add(teeth);

  const mouthGlowMaterial = kit.glow(0.04, 0.1);
  const mouthGlow = glowPlane(0.32, 0.2, mouthGlowMaterial);
  mouthGlow.position.set(0, -0.02, 0.4);
  jaw.add(mouthGlow);

  const barMaterial = kit.glow(0.45, 0.35);
  const waveBars = [];
  for (let i = 0; i < WAVE_BARS; i += 1) {
    const bar = glowPlane(0.014, 0.06, barMaterial);
    bar.name = `waveBar${i}`;
    bar.position.set(-0.07 + (0.14 * i) / (WAVE_BARS - 1), -0.03, 0.43);
    bar.visible = false;
    jaw.add(bar);
    waveBars.push(bar);
  }

  const cornerMaterial = kit.glow(0.16, 0.45);
  const buildCorner = (side, name) => {
    const group = new THREE.Group();
    group.name = name;
    group.position.set(0.115 * side, -0.01, 0.45);
    group.add(glowPlane(0.04, 0.04, cornerMaterial));
    jaw.add(group);
    return group;
  };
  const mouthCornerLeft = buildCorner(-1, "mouthCornerLeft");
  const mouthCornerRight = buildCorner(1, "mouthCornerRight");

  // --- Cheek accents (scene drives position.y and scale.y on the groups).
  const cheekMaterial = kit.line(0.09);
  const buildCheek = (side, name) => {
    const group = new THREE.Group();
    group.name = name;
    // The scene writes absolute position.y around a -0.04 baseline each frame;
    // only x/z come from the sculpted surface.
    group.position.copy(surfacePoint(0.5 * side, -0.08, 0.72, 0.02));
    group.position.y = -0.04;
    const arcPoints = [];
    for (let i = 0; i <= 10; i += 1) {
      const angle = Math.PI * (1.2 + 0.6 * (i / 10));
      arcPoints.push(new THREE.Vector3(Math.cos(angle) * 0.07, Math.sin(angle) * 0.045, 0));
    }
    group.add(lineFromPoints(arcPoints, cheekMaterial));
    head.add(group);
    return group;
  };
  const cheekLeft = buildCheek(-1, "cheekLeft");
  const cheekRight = buildCheek(1, "cheekRight");

  // --- Mustache: mirrored tapered curls above the lip, parented to the head
  // so speech only sways them subtly (shapeMouth applies the follow).
  const mustacheFillMaterial = kit.line(0.34);
  const mustacheEdgeMaterial = kit.line(0.8);
  const tipMaterial = kit.glow(0.55, 0.6);
  const buildMustache = (side, name) => {
    const group = new THREE.Group();
    group.name = name;
    group.position.set(0.012 * side, -0.148, 0.443);
    head.add(group);
    const curve = new THREE.CubicBezierCurve3(
      new THREE.Vector3(0, 0, 0),
      new THREE.Vector3(0.045 * side, -0.014, -0.004),
      new THREE.Vector3(0.093 * side, -0.012, -0.02),
      new THREE.Vector3(0.118 * side, 0.016, -0.038),
    );
    const thick = new THREE.Mesh(trackGeometry(new THREE.TubeGeometry(curve, 12, 0.0105, 5, false)), mustacheFillMaterial);
    const tipCurve = new THREE.CubicBezierCurve3(
      new THREE.Vector3(0.118 * side, 0.016, -0.038),
      new THREE.Vector3(0.128 * side, 0.03, -0.045),
      new THREE.Vector3(0.122 * side, 0.042, -0.048),
      new THREE.Vector3(0.112 * side, 0.044, -0.05),
    );
    const tip = new THREE.Mesh(trackGeometry(new THREE.TubeGeometry(tipCurve, 8, 0.005, 4, false)), mustacheFillMaterial);
    const contour = lineFromPoints(
      [...curve.getPoints(12), ...tipCurve.getPoints(6)].map((p) => new THREE.Vector3(p.x, p.y + 0.008, p.z + 0.004)),
      mustacheEdgeMaterial,
    );
    const tipGlow = glowPlane(0.02, 0.02, tipMaterial);
    tipGlow.position.set(0.112 * side, 0.046, -0.046);
    group.add(thick, tip, contour, tipGlow);
    return group;
  };
  const moustacheLeft = buildMustache(-1, "moustacheLeft");
  const moustacheRight = buildMustache(1, "moustacheRight");

  // --- Ear nodes.
  const earMaterial = kit.glow(0.14, 0.4);
  const earRingMaterial = kit.line(0.2);
  for (const side of [-1, 1]) {
    const node = new THREE.Group();
    node.position.copy(surfacePoint(0.94 * side, -0.08, 0.1, 0.01));
    node.rotation.y = side * 0.95;
    node.add(glowPlane(0.06, 0.06, earMaterial));
    const ringPoints = [];
    for (let i = 0; i <= 20; i += 1) {
      const angle = (i / 20) * TAU;
      ringPoints.push(new THREE.Vector3(Math.cos(angle) * 0.045, Math.sin(angle) * 0.045, 0));
    }
    node.add(new THREE.LineLoop(trackGeometry(new THREE.BufferGeometry().setFromPoints(ringPoints)), earRingMaterial));
    head.add(node);
  }

  // Lip shaping: cupid's-bow upper lip, full lower lip, corner lift and
  // asymmetry, and roundness pulling both lips toward an O.
  const setLipCurve = (line, { endLift, bow, fullness, asymmetry, roundness, width }) => {
    const attribute = line.geometry.attributes.position;
    for (let i = 0; i < LIP_POINTS; i += 1) {
      const t = (i / (LIP_POINTS - 1)) * 2 - 1;
      const squeeze = 1 - 0.34 * roundness;
      const y = endLift * Math.pow(Math.abs(t), 1.5)
        + fullness * (1 - t * t)
        + bow * (Math.exp(-(((t - 0.2) / 0.13) ** 2)) + Math.exp(-(((t + 0.2) / 0.13) ** 2)))
        - bow * 1.35 * Math.exp(-((t / 0.09) ** 2))
        + asymmetry * t * 0.5
        - roundness * 0.01 * (1 - t * t);
      attribute.setXYZ(i, 0.112 * t * width * squeeze, y, -0.05 * t * t);
    }
    attribute.needsUpdate = true;
  };

  let mouthShape = null;
  const shapeMouth = (params = {}) => {
    const jawOpen = Math.max(0, params.jawOpen ?? 0);
    const cornerLift = params.cornerLift ?? 0;
    const asymmetry = params.asymmetry ?? 0;
    const roundness = Math.max(0, Math.min(1, params.roundness ?? 0));
    const width = 1 + (params.mouthWidth ?? 0) * 0.1;
    const closure = Math.max(0, Math.min(1, params.closure ?? 0));
    const lift = cornerLift * 0.1;
    setLipCurve(upperLip, {
      endLift: -0.012 + lift, bow: 0.008 + roundness * 0.004,
      fullness: 0.001 + closure * 0.004, asymmetry, roundness, width,
    });
    setLipCurve(lowerLip, {
      endLift: 0.024 + lift * 0.85, bow: 0,
      fullness: -0.006 - jawOpen * 0.008 + closure * 0.006 + roundness * 0.008,
      asymmetry: asymmetry * 0.7, roundness, width: width * 0.96,
    });
    interiorMaterial.uniforms.uOpacity.value = Math.min(0.72, jawOpen * 1.1);
    teethMaterial.uniforms.uOpacity.value =
      Math.max(0, jawOpen - 0.42) * 2 * (1 - roundness) * (1 - closure) * 0.55;
    mouthGlowMaterial.uniforms.uOpacity.value = 0.035 + jawOpen * 0.35;
    const sway = jawOpen * 0.1;
    moustacheLeft.rotation.z = sway * 0.5;
    moustacheRight.rotation.z = -sway * 0.5;
    moustacheLeft.rotation.x = sway;
    moustacheRight.rotation.x = sway;
    const speakingAmp = params.speaking ? Math.min(1, jawOpen * 1.4) : 0;
    mouthShape = { jawOpen, speakingAmp, envelope: params.envelope ?? null };
  };
  shapeMouth({});

  const setWaveBars = (seconds) => {
    const amp = mouthShape?.speakingAmp ?? 0;
    for (let i = 0; i < waveBars.length; i += 1) {
      const level = amp * (0.25 + 0.75 * Math.abs(Math.sin(seconds * (8.5 + i * 1.65) + i * 1.3)));
      waveBars[i].visible = level > 0.05;
      waveBars[i].scale.y = Math.max(0.001, level);
    }
  };

  // Expression accents beyond the scene's base transforms.
  const applyExpressionAccents = (targets) => {
    const widen = 1
      + Math.max(0, targets.eyeWiden ?? 0) * 0.5
      - Math.max(0, targets.eyelid ?? 0) * 0.42
      - Math.max(0, targets.eyeSquint ?? 0) * 0.3;
    leftEye.aperture.scale.y *= widen;
    rightEye.aperture.scale.y *= Math.max(0.04, widen - Math.max(0, targets.browAsymmetry ?? 0) * 0.16);
    const pupilScale = 1 + (targets.pupilScale ?? 0) * 0.4;
    leftEye.pupil.scale.setScalar(pupilScale);
    rightEye.pupil.scale.setScalar(pupilScale);
    const arch = (targets.browArch ?? 0) * 0.3;
    const inner = (targets.browInner ?? 0) * 0.24;
    browLeft.inner.rotation.z = -inner + arch * 0.4;
    browRight.inner.rotation.z = inner - arch * 0.4;
    browLeft.outer.rotation.z = (targets.browOuter ?? 0) * 0.22 + arch * 0.3;
    browRight.outer.rotation.z = -(targets.browOuter ?? 0) * 0.22 - arch * 0.3;
    browEdgeMaterial.uniforms.uOpacity.value =
      0.7 + Math.max(0, targets.browInner ?? 0) * 0.5 + Math.max(0, -(targets.browOuter ?? 0)) * 0.4;
    cornerMaterial.uniforms.uOpacity.value = 0.16 + Math.max(0, targets.mouthCorner ?? 0) * 1.1;
    cheekMaterial.uniforms.uOpacity.value = 0.09 + Math.max(0, targets.cheekRaise ?? 0) * 0.8;
  };

  const update = (seconds, delta, flow) => {
    leftEye.iris.rotation.z += delta * 0.8 * flow;
    rightEye.iris.rotation.z -= delta * 0.8 * flow;
    catchTwinkle(leftEye, seconds, 0);
    catchTwinkle(rightEye, seconds, 2.1);
    setWaveBars(seconds);
  };
  const catchTwinkle = (eye, seconds, phase) => {
    eye.catchlight.material.uniforms.uOpacity.value = 0.55 + 0.3 * Math.sin(seconds * 1.7 + phase);
  };

  return {
    head,
    jaw,
    surfacePoint,
    headShellGeometry,
    controls: {
      jaw, mouthUpper, mouthLower, mouthCornerLeft, mouthCornerRight,
      cheekLeft, cheekRight,
      browLeft: browLeft.group, browRight: browRight.group,
      eyeLeft: leftEye.group, eyeRight: rightEye.group,
      pupilLeft: leftEye.pupil, pupilRight: rightEye.pupil,
      lidLeft: leftEye.aperture, lidRight: rightEye.aperture,
      moustacheLeft, moustacheRight,
    },
    headPointsMaterial: vertexMaterial,
    shapeMouth,
    applyExpressionAccents,
    update,
  };
}

function lipBuffer(THREE) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(new Array(LIP_POINTS * 3).fill(0), 3));
  return geometry;
}

// Formal butler clothing: jacket yoke with sloped shoulders, notched lapels,
// shirt V, collar arcs, and an audio-reactive bow tie, dissolving into points
// toward the projection ring. Deliberately darker than the face — clothing
// sits low in the brightness hierarchy.
//
// The scene drives bust.scale.y around a 0.72 baseline for breathing, so the
// caller mounts this group inside the bust with an unsquash wrapper; all
// coordinates here are authored in world-equivalent units.

const TAU = Math.PI * 2;

// Jacket cross-sections: [y, radiusX, radiusZ] from neck base to lower chest.
const YOKE_PROFILE = [
  [0.5, 0.15, 0.13],
  [0.42, 0.22, 0.17],
  [0.28, 0.5, 0.24],
  [0.14, 0.7, 0.29],
  [-0.02, 0.66, 0.3],
  [-0.22, 0.58, 0.28],
  [-0.42, 0.5, 0.25],
];

export function createButlerClothing(THREE, { kit, trackGeometry, quality = "high" }) {
  const group = new THREE.Group();
  group.name = "clothingRoot";

  const line = (points, material, loop = false) => {
    const geometry = trackGeometry(new THREE.BufferGeometry().setFromPoints(points));
    return loop ? new THREE.LineLoop(geometry, material) : new THREE.Line(geometry, material);
  };

  const fade = { fadeY: 0.06, fadeRange: 0.42 };

  // --- Jacket yoke.
  const segments = quality === "low" ? 20 : 30;
  const ringAt = ([y, rx, rz]) => {
    const ring = [];
    for (let i = 0; i <= segments; i += 1) {
      const angle = (i / segments) * TAU;
      ring.push(new THREE.Vector3(Math.cos(angle) * rx, y, Math.sin(angle) * rz));
    }
    return ring;
  };
  const rings = YOKE_PROFILE.map(ringAt);
  const positions = [];
  const push = (p) => positions.push(p.x, p.y, p.z);
  for (let level = 0; level < rings.length - 1; level += 1) {
    for (let seg = 0; seg < segments; seg += 1) {
      const a = rings[level][seg];
      const b = rings[level][seg + 1];
      const c = rings[level + 1][seg + 1];
      const d = rings[level + 1][seg];
      push(a); push(b); push(c);
      push(a); push(c); push(d);
    }
  }
  const yokeGeometry = trackGeometry(new THREE.BufferGeometry());
  yokeGeometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  yokeGeometry.computeVertexNormals();
  group.add(new THREE.Mesh(yokeGeometry, kit.shell(0.34, 0.022, fade)));

  // Structure rings + shoulder silhouette curves.
  const faintMaterial = kit.line(0.07, fade);
  for (const index of [2, 4]) {
    group.add(line(rings[index], faintMaterial, true));
  }
  const silhouetteMaterial = kit.line(0.3, fade);
  for (const side of [-1, 1]) {
    const path = YOKE_PROFILE.map(([y, rx]) => new THREE.Vector3(rx * side, y, 0.02));
    group.add(line(new THREE.CatmullRomCurve3(path).getPoints(24), silhouetteMaterial));
  }

  // --- Lapels: main panel plus notch flap, with a bright edge overlay.
  const lapelFillMaterial = kit.shell(0.4, 0.03, fade);
  const lapelEdgeMaterial = kit.line(0.45, fade);
  const lapels = {};
  for (const side of [-1, 1]) {
    const sx = (x) => x * side;
    const A = new THREE.Vector3(sx(0.06), 0.44, 0.185);
    const B = new THREE.Vector3(sx(0.31), 0.24, 0.21);
    const C = new THREE.Vector3(sx(0.045), -0.12, 0.3);
    const D = new THREE.Vector3(sx(0.35), 0.34, 0.185);
    const lapelGeometry = trackGeometry(new THREE.BufferGeometry());
    lapelGeometry.setAttribute("position", new THREE.Float32BufferAttribute([
      A.x, A.y, A.z, B.x, B.y, B.z, C.x, C.y, C.z,
      A.x, A.y, A.z, D.x, D.y, D.z, B.x, B.y, B.z,
    ], 3));
    lapelGeometry.computeVertexNormals();
    const lapel = new THREE.Group();
    lapel.name = side < 0 ? "lapelLeft" : "lapelRight";
    lapel.add(new THREE.Mesh(lapelGeometry, lapelFillMaterial));
    lapel.add(line([D, B, C], lapelEdgeMaterial));
    lapel.add(line([A, B], lapelEdgeMaterial));
    group.add(lapel);
    lapels[side < 0 ? "left" : "right"] = lapel;
  }

  // --- Shirt V between the lapels, with stud points.
  const shirtMaterial = kit.line(0.16, fade);
  group.add(line([
    new THREE.Vector3(-0.055, 0.44, 0.19),
    new THREE.Vector3(-0.03, -0.08, 0.305),
    new THREE.Vector3(0, -0.16, 0.31),
    new THREE.Vector3(0.03, -0.08, 0.305),
    new THREE.Vector3(0.055, 0.44, 0.19),
  ], shirtMaterial));
  const studMaterial = kit.glow(0.4, 0.55);
  for (const y of [0.16, 0.0]) {
    const stud = new THREE.Mesh(trackGeometry(new THREE.PlaneGeometry(0.018, 0.018)), studMaterial);
    stud.position.set(0, y, 0.3);
    group.add(stud);
  }

  // --- Collar arcs at the neck base.
  const collarMaterial = kit.line(0.32, fade);
  for (const side of [-1, 1]) {
    const arc = [];
    for (let i = 0; i <= 12; i += 1) {
      const angle = (Math.PI / 2) * (0.24 + 0.66 * (i / 12)) * side + (side < 0 ? Math.PI : 0);
      arc.push(new THREE.Vector3(Math.sin(angle) * 0.2, 0.47 - 0.05 * (i / 12), Math.cos(angle) * 0.16 + 0.02));
    }
    group.add(line(arc, collarMaterial));
  }

  // --- Bow tie.
  const bowTieRoot = new THREE.Group();
  bowTieRoot.name = "bowTieRoot";
  bowTieRoot.position.set(0, 0.455, 0.175);
  group.add(bowTieRoot);
  const bowFillMaterial = kit.shell(0.5, 0.05);
  const bowEdgeMaterial = kit.line(0.6);
  const buildWing = (side) => {
    const wing = new THREE.Group();
    wing.name = side < 0 ? "bowTieLeft" : "bowTieRight";
    const inner = [new THREE.Vector3(0.012 * side, 0.014, 0), new THREE.Vector3(0.012 * side, -0.014, 0)];
    const outerTop = new THREE.Vector3(0.088 * side, 0.037, -0.012);
    const outerBottom = new THREE.Vector3(0.088 * side, -0.037, -0.012);
    const wingGeometry = trackGeometry(new THREE.BufferGeometry());
    wingGeometry.setAttribute("position", new THREE.Float32BufferAttribute([
      inner[0].x, inner[0].y, inner[0].z, outerTop.x, outerTop.y, outerTop.z, outerBottom.x, outerBottom.y, outerBottom.z,
      inner[0].x, inner[0].y, inner[0].z, outerBottom.x, outerBottom.y, outerBottom.z, inner[1].x, inner[1].y, inner[1].z,
    ], 3));
    wingGeometry.computeVertexNormals();
    wing.add(new THREE.Mesh(wingGeometry, bowFillMaterial));
    wing.add(line([inner[0], outerTop, outerBottom, inner[1]], bowEdgeMaterial));
    bowTieRoot.add(wing);
    return wing;
  };
  const bowTieLeft = buildWing(-1);
  const bowTieRight = buildWing(1);
  const knot = new THREE.Group();
  const knotEdge = [
    new THREE.Vector3(-0.016, 0.02, 0.004), new THREE.Vector3(0.016, 0.02, 0.004),
    new THREE.Vector3(0.016, -0.02, 0.004), new THREE.Vector3(-0.016, -0.02, 0.004),
  ];
  knot.add(line(knotEdge, bowEdgeMaterial, true));
  bowTieRoot.add(knot);

  // --- Lower dissolve: sparse points scattering off the jacket's lower edge.
  if (quality !== "low") {
    const dissolveCount = quality === "high" ? 44 : 26;
    const dissolvePositions = new Float32Array(dissolveCount * 3);
    const dissolveSeeds = new Float32Array(dissolveCount);
    for (let i = 0; i < dissolveCount; i += 1) {
      const angle = ((i * 0.61803398875) % 1) * TAU;
      const t = (i * 0.3819660113) % 1;
      const y = -0.18 - t * 0.42;
      const spread = 0.5 + t * 0.14;
      dissolvePositions[i * 3] = Math.cos(angle) * spread;
      dissolvePositions[i * 3 + 1] = y;
      dissolvePositions[i * 3 + 2] = Math.sin(angle) * spread * 0.5;
      dissolveSeeds[i] = (i * 0.7548776662) % 1;
    }
    const dissolveGeometry = trackGeometry(new THREE.BufferGeometry());
    dissolveGeometry.setAttribute("position", new THREE.BufferAttribute(dissolvePositions, 3));
    dissolveGeometry.setAttribute("aSeed", new THREE.BufferAttribute(dissolveSeeds, 1));
    group.add(new THREE.Points(dissolveGeometry, kit.points(0.32, 3.2, { fadeY: -0.5, fadeRange: 0.16 })));
  }

  const update = ({ envelope = 0, seconds = 0 }) => {
    const pulse = 1 + envelope * 0.06;
    bowTieLeft.scale.setScalar(pulse);
    bowTieRight.scale.setScalar(pulse);
    bowEdgeMaterial.uniforms.uOpacity.value = 0.52 + envelope * 0.35 + 0.06 * Math.sin(seconds * 1.3);
    studMaterial.uniforms.uOpacity.value = 0.32 + envelope * 0.2;
  };

  return {
    group,
    controls: {
      bowTieRoot,
      bowTieLeft,
      bowTieRight,
      lapelLeft: lapels.left,
      lapelRight: lapels.right,
    },
    update,
  };
}

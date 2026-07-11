export function createProceduralButler(THREE) {
  const root = new THREE.Group();
  root.name = "procedural-butler";
  const skin = new THREE.MeshStandardMaterial({ color: 0xb98972, roughness: 0.72, metalness: 0.02 });
  const white = new THREE.MeshStandardMaterial({ color: 0xe8e4dc, roughness: 0.8 });
  const jacket = new THREE.MeshStandardMaterial({ color: 0x17191f, roughness: 0.7 });
  const dark = new THREE.MeshStandardMaterial({ color: 0x111216, roughness: 0.65 });
  const iris = new THREE.MeshStandardMaterial({ color: 0x3b5261, roughness: 0.45 });
  const mouthMaterial = new THREE.MeshStandardMaterial({ color: 0x5f3032, roughness: 0.8 });
  const materials = [skin, white, jacket, dark, iris, mouthMaterial];
  const geometries = [];
  const make = (geometry, material, name) => {
    geometries.push(geometry);
    const mesh = new THREE.Mesh(geometry, material);
    mesh.name = name;
    mesh.castShadow = true;
    mesh.receiveShadow = true;
    return mesh;
  };

  const bust = make(new THREE.SphereGeometry(0.82, 32, 20), jacket, "bust");
  bust.scale.set(1.28, 0.72, 0.6);
  bust.position.y = 0.55;
  root.add(bust);

  const shirt = make(new THREE.CylinderGeometry(0.3, 0.43, 0.72, 24), white, "shirt");
  shirt.position.set(0, 0.72, 0.2);
  root.add(shirt);

  const neck = make(new THREE.CylinderGeometry(0.22, 0.25, 0.42, 24), skin, "neck");
  neck.position.y = 1.13;
  root.add(neck);

  const head = new THREE.Group();
  head.name = "head";
  head.position.y = 1.65;
  root.add(head);

  const face = make(new THREE.SphereGeometry(0.48, 40, 28), skin, "face");
  face.scale.set(0.86, 1.08, 0.82);
  head.add(face);

  const hair = make(new THREE.SphereGeometry(0.49, 32, 20, 0, Math.PI * 2, 0, Math.PI / 2), dark, "hair");
  hair.scale.set(0.88, 0.55, 0.84);
  hair.position.y = 0.2;
  head.add(hair);

  const nose = make(new THREE.ConeGeometry(0.075, 0.24, 16), skin, "nose");
  nose.rotation.x = Math.PI / 2;
  nose.position.set(0, -0.015, 0.49);
  head.add(nose);

  const jaw = new THREE.Group();
  jaw.name = "jaw";
  jaw.position.set(0, -0.18, 0.39);
  head.add(jaw);

  const eye = (x, name) => {
    const group = new THREE.Group();
    group.name = name;
    group.position.set(x, 0.08, 0.39);
    const whiteMesh = make(new THREE.SphereGeometry(0.092, 24, 16), white, `${name}-white`);
    whiteMesh.scale.set(1.05, 0.72, 0.48);
    const pupil = make(new THREE.SphereGeometry(0.038, 20, 12), iris, `${name}-pupil`);
    pupil.position.z = 0.07;
    const lid = make(new THREE.SphereGeometry(0.098, 24, 12, 0, Math.PI * 2, 0, Math.PI / 2), skin, `${name}-lid`);
    lid.scale.set(1.06, 0.74, 0.5);
    lid.position.z = 0.015;
    group.add(whiteMesh, pupil, lid);
    head.add(group);
    return { group, pupil, lid };
  };

  const leftEye = eye(-0.17, "eyeLeft");
  const rightEye = eye(0.17, "eyeRight");

  const browLeft = make(new THREE.BoxGeometry(0.2, 0.028, 0.035), dark, "browLeft");
  const browRight = make(new THREE.BoxGeometry(0.2, 0.028, 0.035), dark, "browRight");
  browLeft.position.set(-0.17, 0.22, 0.43);
  browRight.position.set(0.17, 0.22, 0.43);
  head.add(browLeft, browRight);

  const mouthUpper = make(new THREE.BoxGeometry(0.23, 0.022, 0.028), mouthMaterial, "mouthUpper");
  const mouthLower = make(new THREE.BoxGeometry(0.2, 0.025, 0.028), mouthMaterial, "mouthLower");
  mouthLower.position.y = -0.035;
  jaw.add(mouthUpper, mouthLower);

  const moustacheLeft = make(new THREE.ConeGeometry(0.055, 0.18, 12), dark, "moustacheLeft");
  const moustacheRight = make(new THREE.ConeGeometry(0.055, 0.18, 12), dark, "moustacheRight");
  moustacheLeft.rotation.z = Math.PI / 2;
  moustacheRight.rotation.z = -Math.PI / 2;
  moustacheLeft.position.set(-0.075, 0.035, 0.018);
  moustacheRight.position.set(0.075, 0.035, 0.018);
  jaw.add(moustacheLeft, moustacheRight);

  const mouthCornerLeft = new THREE.Object3D();
  const mouthCornerRight = new THREE.Object3D();
  mouthCornerLeft.name = "mouthCornerLeft";
  mouthCornerRight.name = "mouthCornerRight";
  mouthCornerLeft.position.set(-0.12, -0.01, 0);
  mouthCornerRight.position.set(0.12, -0.01, 0);
  jaw.add(mouthCornerLeft, mouthCornerRight);

  const cheekLeft = new THREE.Object3D();
  const cheekRight = new THREE.Object3D();
  cheekLeft.name = "cheekLeft";
  cheekRight.name = "cheekRight";
  cheekLeft.position.set(-0.26, -0.04, 0.34);
  cheekRight.position.set(0.26, -0.04, 0.34);
  head.add(cheekLeft, cheekRight);

  const collarLeft = make(new THREE.ConeGeometry(0.22, 0.45, 3), white, "collarLeft");
  const collarRight = make(new THREE.ConeGeometry(0.22, 0.45, 3), white, "collarRight");
  collarLeft.position.set(-0.22, 0.92, 0.31);
  collarRight.position.set(0.22, 0.92, 0.31);
  collarLeft.rotation.z = -0.32;
  collarRight.rotation.z = 0.32;
  root.add(collarLeft, collarRight);

  const bowLeft = make(new THREE.ConeGeometry(0.16, 0.3, 3), dark, "bowLeft");
  const bowRight = make(new THREE.ConeGeometry(0.16, 0.3, 3), dark, "bowRight");
  bowLeft.position.set(-0.13, 0.91, 0.47);
  bowRight.position.set(0.13, 0.91, 0.47);
  bowLeft.rotation.z = -Math.PI / 2;
  bowRight.rotation.z = Math.PI / 2;
  root.add(bowLeft, bowRight);

  root.position.y = -0.55;

  return {
    object: root,
    controls: {
      root, bust, neck, head, jaw, mouthUpper, mouthLower,
      mouthCornerLeft, mouthCornerRight, cheekLeft, cheekRight,
      browLeft, browRight, eyeLeft: leftEye.group, eyeRight: rightEye.group,
      pupilLeft: leftEye.pupil, pupilRight: rightEye.pupil,
      lidLeft: leftEye.lid, lidRight: rightEye.lid,
    },
    dispose() {
      geometries.forEach((geometry) => geometry.dispose());
      materials.forEach((material) => material.dispose());
      root.clear();
    },
  };
}

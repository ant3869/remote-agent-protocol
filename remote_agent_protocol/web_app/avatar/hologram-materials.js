// Shared shader kit for the holographic butler. Every visible material samples
// the same uniform block so the whole projection reads as one transmission:
// CRT scanlines and flicker ride on world Y, glitch bands displace vertices
// horizontally, dropout/reveal windows gate alpha, and a scan sweep brightens
// the band it passes through. Factories return tracked ShaderMaterials; call
// dispose() once to release everything the kit created.

const COMMON_UNIFORM_DECLS = /* glsl */ `
  uniform vec3 uColor;
  uniform vec3 uGlitchColor;
  uniform float uTime;
  uniform float uGlow;
  uniform float uOpacity;
  uniform float uScanY;
  uniform float uScan;
  uniform float uScanlineDensity;
  uniform float uScanlineStrength;
  uniform float uFlicker;
  uniform float uGlitchSeed;
  uniform float uBandTint;
  uniform float uDropStart;
  uniform float uDropEnd;
  uniform float uDropAmount;
  uniform float uReveal;
  uniform float uFadeY;
  uniform float uFadeRange;
`;

const COMMON_FRAG_HELPERS = /* glsl */ `
  float holoHash(float n) { return fract(sin(n * 12.9898 + uGlitchSeed) * 43758.5453); }

  float holoAlphaFactor(float worldY) {
    float row = worldY * uScanlineDensity - uTime * 1.35;
    float rowIndex = floor(row);
    float linePhase = fract(row);
    float lineShape = smoothstep(0.0, 0.16, linePhase) * (1.0 - smoothstep(0.42, 0.82, linePhase));
    float rowSeed = holoHash(rowIndex + floor(uTime * 0.4) * 31.0);
    float missing = step(0.05, rowSeed);
    float scanline = mix(1.0, lineShape * missing * (0.74 + 0.5 * rowSeed), uScanlineStrength);
    float flicker = 1.0 + uFlicker * (0.55 * sin(uTime * 23.0 + worldY * 42.0)
      + 0.45 * (holoHash(floor(uTime * 30.0)) - 0.5) * 2.0);
    float drop = 1.0 - uDropAmount * step(uDropStart, worldY) * step(worldY, uDropEnd);
    float reveal = 1.0 - smoothstep(uReveal - 0.05, uReveal + 0.07, worldY);
    float fade = smoothstep(uFadeY - uFadeRange, uFadeY, worldY);
    return max(0.0, scanline * flicker * drop * reveal * fade);
  }

  float holoSweep(float worldY) {
    return exp(-pow((worldY - uScanY) * 6.0, 2.0)) * uScan;
  }

  vec3 holoColor(float bandMask, float sweep) {
    vec3 tinted = mix(uColor, uGlitchColor, clamp(bandMask * uBandTint, 0.0, 1.0));
    return tinted * (1.0 + sweep * 1.35);
  }
`;

const COMMON_VERT_DECLS = /* glsl */ `
  uniform float uGlitchStrength;
  uniform float uBandAStart;
  uniform float uBandAEnd;
  uniform float uBandAOffset;
  uniform float uBandBStart;
  uniform float uBandBEnd;
  uniform float uBandBOffset;
  varying float vWorldY;
  varying float vBand;

  vec4 holoDisplace(vec4 worldPosition) {
    float bandA = step(uBandAStart, worldPosition.y) * step(worldPosition.y, uBandAEnd);
    float bandB = step(uBandBStart, worldPosition.y) * step(worldPosition.y, uBandBEnd);
    vBand = clamp(bandA + bandB, 0.0, 1.0);
    worldPosition.x += (bandA * uBandAOffset + bandB * uBandBOffset) * uGlitchStrength;
    worldPosition.z += (bandA * uBandAOffset - bandB * uBandBOffset) * 0.3 * uGlitchStrength;
    vWorldY = worldPosition.y;
    return worldPosition;
  }
`;

const LINE_VERT = /* glsl */ `
  ${COMMON_VERT_DECLS}
  void main() {
    vec4 worldPosition = holoDisplace(modelMatrix * vec4(position, 1.0));
    gl_Position = projectionMatrix * viewMatrix * worldPosition;
  }
`;

const LINE_FRAG = /* glsl */ `
  ${COMMON_UNIFORM_DECLS}
  varying float vWorldY;
  varying float vBand;
  ${COMMON_FRAG_HELPERS}
  void main() {
    float sweep = holoSweep(vWorldY);
    float alpha = clamp((uOpacity * holoAlphaFactor(vWorldY) + sweep * 0.4) * uGlow, 0.0, 1.0);
    gl_FragColor = vec4(holoColor(vBand, sweep), alpha);
  }
`;

const POINT_VERT = /* glsl */ `
  ${COMMON_VERT_DECLS}
  attribute float aSeed;
  uniform float uTime;
  uniform float uSize;
  uniform float uPixelRatio;
  uniform float uScatter;
  varying float vTwinkle;
  void main() {
    vec3 local = position;
    if (uScatter > 0.0) {
      local += normalize(local + vec3(0.0, 0.0001, 0.0)) * uScatter * (0.25 + 0.75 * aSeed);
    }
    vec4 worldPosition = holoDisplace(modelMatrix * vec4(local, 1.0));
    vTwinkle = 0.62 + 0.38 * sin(uTime * (1.3 + aSeed * 2.4) + aSeed * 40.0);
    vec4 mvPosition = viewMatrix * worldPosition;
    gl_PointSize = uSize * uPixelRatio * (2.2 / max(0.4, -mvPosition.z)) * (0.7 + 0.5 * vTwinkle);
    gl_Position = projectionMatrix * mvPosition;
  }
`;

const POINT_FRAG = /* glsl */ `
  ${COMMON_UNIFORM_DECLS}
  varying float vWorldY;
  varying float vBand;
  varying float vTwinkle;
  ${COMMON_FRAG_HELPERS}
  void main() {
    vec2 offset = gl_PointCoord - 0.5;
    float disc = smoothstep(0.5, 0.1, length(offset));
    float sweep = holoSweep(vWorldY);
    float alpha = clamp(disc * (uOpacity * vTwinkle * holoAlphaFactor(vWorldY) + sweep * 0.5) * uGlow, 0.0, 1.0);
    gl_FragColor = vec4(holoColor(vBand, sweep), alpha);
  }
`;

const SHELL_VERT = /* glsl */ `
  ${COMMON_VERT_DECLS}
  varying vec3 vNormal;
  varying vec3 vView;
  void main() {
    vec4 worldPosition = holoDisplace(modelMatrix * vec4(position, 1.0));
    vNormal = normalize(mat3(modelMatrix) * normal);
    vView = normalize(cameraPosition - worldPosition.xyz);
    gl_Position = projectionMatrix * viewMatrix * worldPosition;
  }
`;

const SHELL_FRAG = /* glsl */ `
  ${COMMON_UNIFORM_DECLS}
  uniform float uFill;
  varying vec3 vNormal;
  varying vec3 vView;
  varying float vWorldY;
  varying float vBand;
  ${COMMON_FRAG_HELPERS}
  void main() {
    float fresnel = pow(1.0 - abs(dot(normalize(vNormal), normalize(vView))), 2.6);
    float sweep = holoSweep(vWorldY);
    float body = fresnel * uOpacity + uFill;
    float alpha = clamp((body * holoAlphaFactor(vWorldY) + sweep * 0.08) * uGlow, 0.0, 1.0);
    gl_FragColor = vec4(holoColor(vBand, sweep), alpha);
  }
`;

const GLOW_VERT = /* glsl */ `
  ${COMMON_VERT_DECLS}
  varying vec2 vUv;
  void main() {
    vUv = uv;
    vec4 worldPosition = holoDisplace(modelMatrix * vec4(position, 1.0));
    gl_Position = projectionMatrix * viewMatrix * worldPosition;
  }
`;

const GLOW_FRAG = /* glsl */ `
  ${COMMON_UNIFORM_DECLS}
  uniform float uCore;
  varying vec2 vUv;
  varying float vWorldY;
  varying float vBand;
  ${COMMON_FRAG_HELPERS}
  void main() {
    float d = length(vUv - 0.5) * 2.0;
    float halo = exp(-d * d * 4.5);
    float core = 1.0 - smoothstep(uCore * 0.45, max(0.02, uCore), d);
    float sweep = holoSweep(vWorldY);
    float alpha = clamp((halo * 0.45 + core) * uOpacity * holoAlphaFactor(vWorldY) * uGlow, 0.0, 1.0);
    gl_FragColor = vec4(holoColor(vBand, sweep) * (1.0 + core * 1.5), alpha);
  }
`;

const DARK_FRAG = /* glsl */ `
  ${COMMON_UNIFORM_DECLS}
  varying vec2 vUv;
  varying float vWorldY;
  varying float vBand;
  ${COMMON_FRAG_HELPERS}
  void main() {
    float d = length(vUv - 0.5) * 2.0;
    float body = 1.0 - smoothstep(0.35, 1.0, d);
    float reveal = 1.0 - smoothstep(uReveal - 0.05, uReveal + 0.07, vWorldY);
    gl_FragColor = vec4(vec3(0.004, 0.012, 0.02), clamp(body * uOpacity * reveal, 0.0, 1.0));
  }
`;

const MOTE_VERT = /* glsl */ `
  ${COMMON_VERT_DECLS}
  attribute float aSeed;
  attribute float aSpeed;
  uniform float uTime;
  uniform float uDrift;
  uniform float uSize;
  uniform float uPixelRatio;
  uniform float uDirection;
  varying float vFade;
  void main() {
    float span = 3.0;
    float travel = uDrift * aSpeed * uDirection;
    float y = mod(mod(position.y + travel, span) + span, span);
    vec3 local = vec3(
      position.x + sin(uDrift * (0.4 + aSeed) + aSeed * 20.0) * 0.06,
      y - 0.25,
      position.z
    );
    vec4 worldPosition = holoDisplace(modelMatrix * vec4(local, 1.0));
    float edge = smoothstep(0.0, 0.3, y) * (1.0 - smoothstep(span - 0.4, span, y));
    vFade = edge * (0.3 + 0.7 * (0.5 + 0.5 * sin(uTime * (0.8 + aSeed * 2.0) + aSeed * 30.0)));
    vec4 mvPosition = viewMatrix * worldPosition;
    gl_PointSize = uSize * uPixelRatio * (2.2 / max(0.4, -mvPosition.z));
    gl_Position = projectionMatrix * mvPosition;
  }
`;

const MOTE_FRAG = /* glsl */ `
  ${COMMON_UNIFORM_DECLS}
  varying float vFade;
  varying float vWorldY;
  varying float vBand;
  ${COMMON_FRAG_HELPERS}
  void main() {
    vec2 offset = gl_PointCoord - 0.5;
    float disc = smoothstep(0.5, 0.12, length(offset));
    float reveal = 1.0 - smoothstep(uReveal - 0.05, uReveal + 0.07, vWorldY);
    gl_FragColor = vec4(holoColor(vBand, 0.0), clamp(disc * vFade * uOpacity * reveal * uGlow, 0.0, 1.0));
  }
`;

export function createHologramMaterials(THREE, options = {}) {
  const pixelRatio = Number.isFinite(options.pixelRatio) ? options.pixelRatio : 1;
  const primary = new THREE.Color(options.primaryColor ?? 0x22d3ee);
  const accent = new THREE.Color(options.accentColor ?? 0x22d3ee);
  const glitchColor = new THREE.Color(options.glitchColor ?? 0xc084fc);

  const shared = {
    uColor: { value: primary },
    uAccent: { value: accent },
    uGlitchColor: { value: glitchColor },
    uTime: { value: 0 },
    uGlow: { value: 1 },
    uScanY: { value: -10 },
    uScan: { value: 0 },
    uScanlineDensity: { value: options.scanlineDensity ?? 120 },
    uScanlineStrength: { value: options.scanlineStrength ?? 0.55 },
    uFlicker: { value: 0.035 },
    uGlitchSeed: { value: 0 },
    uGlitchStrength: { value: 0 },
    uBandTint: { value: 0 },
    uBandAStart: { value: -100 },
    uBandAEnd: { value: -100 },
    uBandAOffset: { value: 0 },
    uBandBStart: { value: -100 },
    uBandBEnd: { value: -100 },
    uBandBOffset: { value: 0 },
    uDropStart: { value: -100 },
    uDropEnd: { value: -100 },
    uDropAmount: { value: 0 },
    uReveal: { value: 100 },
    uPixelRatio: { value: pixelRatio },
    uAudioLevel: { value: 0 },
  };

  const materials = [];
  const track = (material) => { materials.push(material); return material; };

  const baseUniforms = (opts = {}) => ({
    uColor: opts.color !== undefined
      ? { value: new THREE.Color(opts.color) }
      : opts.accent ? shared.uAccent : shared.uColor,
    uGlitchColor: shared.uGlitchColor,
    uTime: shared.uTime,
    uGlow: shared.uGlow,
    uScanY: shared.uScanY,
    uScan: shared.uScan,
    uScanlineDensity: shared.uScanlineDensity,
    uScanlineStrength: opts.scanlineStrength !== undefined
      ? { value: opts.scanlineStrength }
      : shared.uScanlineStrength,
    uFlicker: shared.uFlicker,
    uGlitchSeed: shared.uGlitchSeed,
    uGlitchStrength: shared.uGlitchStrength,
    uBandTint: shared.uBandTint,
    uBandAStart: shared.uBandAStart,
    uBandAEnd: shared.uBandAEnd,
    uBandAOffset: shared.uBandAOffset,
    uBandBStart: shared.uBandBStart,
    uBandBEnd: shared.uBandBEnd,
    uBandBOffset: shared.uBandBOffset,
    uDropStart: shared.uDropStart,
    uDropEnd: shared.uDropEnd,
    uDropAmount: shared.uDropAmount,
    uReveal: shared.uReveal,
    uFadeY: { value: opts.fadeY ?? -100 },
    uFadeRange: { value: opts.fadeRange ?? 0.4 },
  });

  const make = (vertexShader, fragmentShader, uniforms, blending) => track(new THREE.ShaderMaterial({
    uniforms,
    vertexShader,
    fragmentShader,
    transparent: true,
    blending: blending ?? THREE.AdditiveBlending,
    depthWrite: false,
    side: THREE.DoubleSide,
  }));

  return {
    shared,
    materials,
    setPrimaryColor(hex) { primary.setHex(hex); },
    line(opacity, opts = {}) {
      return make(LINE_VERT, LINE_FRAG, {
        ...baseUniforms(opts), uOpacity: { value: opacity },
      });
    },
    points(opacity, size, opts = {}) {
      return make(POINT_VERT, POINT_FRAG, {
        ...baseUniforms(opts), uOpacity: { value: opacity }, uSize: { value: size },
        uPixelRatio: shared.uPixelRatio, uScatter: { value: 0 },
      });
    },
    shell(opacity, fill, opts = {}) {
      return make(SHELL_VERT, SHELL_FRAG, {
        ...baseUniforms(opts), uOpacity: { value: opacity }, uFill: { value: fill },
      });
    },
    glow(opacity, core, opts = {}) {
      return make(GLOW_VERT, GLOW_FRAG, {
        ...baseUniforms(opts), uOpacity: { value: opacity }, uCore: { value: core },
      });
    },
    dark(opacity, THREEBlending) {
      return make(GLOW_VERT, DARK_FRAG, {
        ...baseUniforms(), uOpacity: { value: opacity }, uCore: { value: 0.4 },
      }, THREEBlending ?? THREE.NormalBlending);
    },
    motes(opacity, size, opts = {}) {
      return make(MOTE_VERT, MOTE_FRAG, {
        ...baseUniforms(opts), uOpacity: { value: opacity }, uSize: { value: size },
        uPixelRatio: shared.uPixelRatio, uDrift: opts.drift ?? { value: 0 },
        uDirection: { value: opts.direction ?? 1 },
      });
    },
    dispose() {
      materials.forEach((material) => material.dispose());
      materials.length = 0;
    },
  };
}

// Jess, drawn in SVG: the source of her avatar frames.
//
// jessSvg(name) returns a 512x512 SVG for one frame of the frame avatar's set
// (the same names as Butler's: expressions, mouth shapes, blinks, and the
// materialize/glitch effects). Every portrait frame shares one head position,
// so the scene can dissolve between them without a second head showing.
// render-frames.mjs turns each into a transparent WebP.

export const PORTRAIT_FRAMES = Object.freeze([
  "base", "halfsmile", "smile", "mid_laugh", "full_laugh", "confused", "angry",
  "lookup", "lookdown", "glow_eyes", "eyes_mid_close", "eyes_closed",
  "eyes_closed_smile", "e_sound", "oh", "open", "grit", "ah_small", "oo", "fv",
]);
export const EFFECT_FRAMES = Object.freeze([
  ...Array.from({ length: 13 }, (_, i) => `materialize_${String(i + 1).padStart(2, "0")}`),
  ...Array.from({ length: 7 }, (_, i) => `glitch_${String(i + 1).padStart(2, "0")}`),
]);

const NEON = "#e879f9";
const NEON_SOFT = "#c084fc";
const IRIS = "#f0abfc";

// Mouth: open (0-1), width scale, round (0-1, pulls corners in), smile (-1-1),
// teeth: "none" | "top" | "both", bite: lower lip tucked under top teeth (f/v).
// Eyes: open (0-1), squint (0-1, lower lid rises), gaze {x, y} in px,
// closed: "line" | "smile" when open is 0. Brows: raise (-1-1), frown (0-1),
// lift: {left, right} extra raise for one-sided looks.
const NEUTRAL = Object.freeze({
  mouth: { open: 0, width: 1, round: 0, smile: 0.38, teeth: "none", bite: false },
  eyes: { open: 1, squint: 0, gaze: { x: 0, y: 0 }, closed: "line", glow: 0.35 },
  brows: { raise: 0, frown: 0, lift: { left: 0, right: 0 } },
});

const EXPRESSIONS = {
  base: {},
  halfsmile: { mouth: { smile: 0.62, open: 0.08, width: 1.04 }, eyes: { squint: 0.18 } },
  smile: { mouth: { smile: 1, open: 0.32, width: 1.1, teeth: "top" }, eyes: { squint: 0.42 } },
  mid_laugh: {
    mouth: { smile: 1, open: 0.62, width: 1.12, teeth: "top" },
    eyes: { squint: 0.7, open: 0.72 },
    brows: { raise: 0.25 },
  },
  full_laugh: {
    mouth: { smile: 1, open: 1, width: 1.12, teeth: "top" },
    eyes: { open: 0, closed: "smile" },
    brows: { raise: 0.4 },
  },
  confused: {
    mouth: { smile: -0.25, open: 0.05, width: 0.92 },
    brows: { lift: { left: 0.9, right: -0.3 } },
    eyes: { gaze: { x: 2.5, y: -1 } },
  },
  angry: { mouth: { smile: -0.45, width: 0.94 }, brows: { frown: 1, raise: -0.35 }, eyes: { open: 0.8 } },
  lookup: { eyes: { gaze: { x: 1.5, y: -5 } }, brows: { raise: 0.45 } },
  lookdown: { eyes: { gaze: { x: -1, y: 5 }, open: 0.66 }, brows: { raise: -0.15 } },
  glow_eyes: { eyes: { glow: 1 } },
  eyes_mid_close: { eyes: { open: 0.42 } },
  eyes_closed: { eyes: { open: 0 } },
  eyes_closed_smile: { mouth: { smile: 0.85, open: 0.05 }, eyes: { open: 0, closed: "smile" } },
  e_sound: { mouth: { open: 0.34, width: 1.16, smile: 0.35, teeth: "both" } },
  oh: { mouth: { open: 0.72, width: 0.84, round: 0.75, smile: 0 } },
  open: { mouth: { open: 1, width: 1.04, smile: 0.1, teeth: "top" } },
  grit: { mouth: { open: 0.16, width: 1.14, smile: 0.12, teeth: "both" } },
  ah_small: { mouth: { open: 0.42, width: 1, smile: 0.15, teeth: "top" } },
  oo: { mouth: { open: 0.3, width: 0.7, round: 1, smile: 0 } },
  fv: { mouth: { open: 0.1, width: 1.02, smile: 0.1, teeth: "top", bite: true } },
};

function merge(base, over = {}) {
  const out = { ...base };
  for (const [key, value] of Object.entries(over)) {
    out[key] = value && typeof value === "object" && !Array.isArray(value)
      ? merge(base[key] || {}, value)
      : value;
  }
  return out;
}

const f = (n) => Number(n.toFixed(2));

// ---------------------------------------------------------------------------
// Pieces

function defs(glow) {
  return `
  <defs>
    <filter id="neon" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur in="SourceGraphic" stdDeviation="2.4" result="b1"/>
      <feGaussianBlur in="SourceGraphic" stdDeviation="7" result="b2"/>
      <feMerge><feMergeNode in="b2"/><feMergeNode in="b1"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <filter id="soft" x="-30%" y="-30%" width="160%" height="160%">
      <feGaussianBlur stdDeviation="1.1"/>
    </filter>
    <filter id="irisGlow" x="-100%" y="-100%" width="300%" height="300%">
      <feGaussianBlur in="SourceGraphic" stdDeviation="${f(2 + glow * 4)}" result="g"/>
      <feMerge><feMergeNode in="g"/><feMergeNode in="g"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <radialGradient id="skin" cx="44%" cy="40%" r="64%">
      <stop offset="0%" stop-color="#4a3d68"/>
      <stop offset="45%" stop-color="#342a4f"/>
      <stop offset="80%" stop-color="#231b38"/>
      <stop offset="100%" stop-color="#171126"/>
    </radialGradient>
    <filter id="paint" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="6"/>
    </filter>
    <filter id="paintSoft" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="2.4"/>
    </filter>
    <filter id="grain" x="0" y="0" width="100%" height="100%">
      <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="7" result="noise"/>
      <feColorMatrix in="noise" type="matrix"
        values="0 0 0 0 0.95  0 0 0 0 0.82  0 0 0 0 1  0.4 0 0 0 -0.24" result="tint"/>
      <feComposite in="tint" in2="SourceGraphic" operator="in" result="speckle"/>
      <feMerge><feMergeNode in="SourceGraphic"/><feMergeNode in="speckle"/></feMerge>
    </filter>
    <pattern id="scan" width="4" height="4" patternUnits="userSpaceOnUse">
      <rect width="4" height="1" fill="#f0abfc" opacity="0.05"/>
    </pattern>
    <radialGradient id="shadowBlob" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#07040f" stop-opacity="0.85"/>
      <stop offset="100%" stop-color="#0b0716" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="lightBlob" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#e9d5ff" stop-opacity="0.5"/>
      <stop offset="100%" stop-color="#d8b4fe" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="shadowLid" cx="50%" cy="60%" r="55%">
      <stop offset="0%" stop-color="#a21caf" stop-opacity="0.38"/>
      <stop offset="100%" stop-color="#a21caf" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="irisFill" cx="45%" cy="40%" r="60%">
      <stop offset="0%" stop-color="#fdf4ff"/>
      <stop offset="35%" stop-color="#f0abfc"/>
      <stop offset="75%" stop-color="#c026d3"/>
      <stop offset="100%" stop-color="#581c87"/>
    </radialGradient>
    <radialGradient id="sclera" cx="50%" cy="45%" r="60%">
      <stop offset="0%" stop-color="#e9ddf7" stop-opacity="0.55"/>
      <stop offset="100%" stop-color="#6b5a8a" stop-opacity="0.25"/>
    </radialGradient>
    <linearGradient id="hairGloss" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#f5d0fe" stop-opacity="0"/>
      <stop offset="50%" stop-color="#f5d0fe" stop-opacity="0.35"/>
      <stop offset="100%" stop-color="#f5d0fe" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="neck" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#1a1329"/>
      <stop offset="45%" stop-color="#261d3a"/>
      <stop offset="100%" stop-color="#2a2140"/>
    </linearGradient>
    <linearGradient id="hair" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#3b2a5c"/>
      <stop offset="45%" stop-color="#211634"/>
      <stop offset="100%" stop-color="#0c0816"/>
    </linearGradient>
    <linearGradient id="hairBack" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#241836"/>
      <stop offset="100%" stop-color="#0a0612"/>
    </linearGradient>
    <linearGradient id="jacket" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#261c3d"/>
      <stop offset="100%" stop-color="#0b0814"/>
    </linearGradient>
    <linearGradient id="lapel" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#34275226"/>
      <stop offset="0%" stop-color="#342752"/>
      <stop offset="100%" stop-color="#140e22"/>
    </linearGradient>
    <radialGradient id="cheek" cx="50%" cy="50%" r="50%">
      <stop offset="0%" stop-color="#f0abfc" stop-opacity="0.16"/>
      <stop offset="100%" stop-color="#f0abfc" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="mouthDark" cx="50%" cy="40%" r="70%">
      <stop offset="0%" stop-color="#1a0b22"/>
      <stop offset="100%" stop-color="#07030c"/>
    </radialGradient>
    <linearGradient id="lip" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#8a3f8f"/>
      <stop offset="100%" stop-color="#5b2466"/>
    </linearGradient>
    <clipPath id="faceClip"><path d="${FACE}"/></clipPath>
    <clipPath id="neckClip"><path d="${NECK}"/></clipPath>
  </defs>`;
}

const FACE =
  "M256 110 C 304 110 338 142 340 192 C 342 234 334 268 316 296 " +
  "C 300 322 280 338 256 341 C 232 338 212 322 196 296 C 178 268 170 234 172 192 " +
  "C 174 142 208 110 256 110 Z";

const NECK = "M228 312 C 231 346 229 368 220 388 L 292 388 C 283 368 281 346 284 312 Z";

// Deterministic strand layout, so every frame draws identical hair.
function strands(count, seed, build) {
  let out = "";
  for (let i = 0; i < count; i += 1) out += build(i, (k) => rand(seed + i * 17 + k * 131));
  return out;
}

function hairBack() {
  const sides = strands(34, 11, (i, r) => {
    const left = i % 2 === 0;
    const t = r(1);
    const x0 = left ? 178 - t * 34 : 334 + t * 34;
    const sway = (r(2) - 0.5) * 10;
    const x1 = left ? 140 + t * 20 + sway : 372 - t * 20 + sway;
    const endY = 360 + r(3) * 50;
    const endX = left ? 122 + t * 60 + sway : 390 - t * 60 + sway;
    const path = `M${f(x0)} ${f(140 + r(4) * 40)} C ${f(x1)} ${f(220 + r(5) * 30)} ${f(x1 + (left ? -4 : 4))} ${f(300)} ${f(endX)} ${f(endY)}`;
    return `<path d="${path}" stroke="${r(6) > 0.7 ? NEON_SOFT : "#8b5cf6"}" stroke-width="${f(0.5 + r(7) * 0.9)}" opacity="${f(0.18 + r(8) * 0.3)}"/>`;
  });
  return `
  <path d="M252 52 C 170 52 132 112 134 200 C 142 262 128 318 118 372 C 112 404 140 418 176 410
           C 190 380 190 332 194 292 L 318 292 C 322 332 322 380 336 410 C 372 418 400 404 394 372
           C 384 318 376 262 378 200 C 380 112 334 52 252 52 Z"
        fill="url(#hairBack)" stroke="${NEON_SOFT}" stroke-width="2" filter="url(#neon)" opacity="0.98"/>
  <g fill="none" stroke-linecap="round">${sides}</g>
  <g fill="none" stroke="${NEON_SOFT}" stroke-width="0.8" opacity="0.55" filter="url(#paintSoft)">
    <path d="M122 392 C 118 400 116 410 120 418"/>
    <path d="M392 392 C 398 402 398 410 394 420"/>
    <path d="M132 380 C 126 392 124 402 128 412"/>
  </g>`;
}

function neckAndBody() {
  return `
  <path d="${NECK}" fill="url(#neck)" stroke="${NEON_SOFT}" stroke-width="1" stroke-opacity="0.4"/>
  <g clip-path="url(#neckClip)">
    <ellipse cx="256" cy="322" rx="40" ry="18" fill="url(#shadowBlob)"/>
    <ellipse cx="238" cy="352" rx="5" ry="22" fill="url(#lightBlob)" filter="url(#paintSoft)"/>
    <path d="M240 330 C 242 350 246 368 250 386" stroke="${NEON_SOFT}" stroke-width="0.8" fill="none" opacity="0.3"/>
    <path d="M272 330 C 270 350 266 368 262 386" stroke="${NEON_SOFT}" stroke-width="0.8" fill="none" opacity="0.25"/>
  </g>
  <path d="M60 512 C 70 440 120 410 188 396 C 206 392 216 386 222 376 L 256 426 L 290 376
           C 296 386 306 392 324 396 C 392 410 442 440 452 512 Z"
        fill="url(#jacket)" stroke="${NEON}" stroke-width="2.4" filter="url(#neon)"/>
  <path d="M222 378 C 236 396 246 408 256 426 C 266 408 276 396 290 378 C 280 392 268 402 256 404 C 244 402 232 392 222 378 Z"
        fill="#2c2144" opacity="0.9"/>
  <g fill="none" stroke="${NEON_SOFT}" stroke-width="1" opacity="0.5">
    <path d="M224 384 C 236 392 246 396 256 397 C 266 396 276 392 288 384"/>
    <path d="M200 402 C 214 404 226 400 234 392"/>
    <path d="M312 402 C 298 404 286 400 278 392"/>
  </g>
  <path d="M188 396 C 200 420 214 446 226 476 L 256 426 L 222 378 C 214 388 202 394 188 396 Z" fill="url(#lapel)" stroke="${NEON}" stroke-width="1.2" opacity="0.95"/>
  <path d="M324 396 C 312 420 298 446 286 476 L 256 426 L 290 378 C 298 388 310 394 324 396 Z" fill="url(#lapel)" stroke="${NEON}" stroke-width="1.2" opacity="0.8"/>
  <g fill="none" stroke="${NEON_SOFT}" stroke-width="0.9" stroke-dasharray="2 3" opacity="0.55">
    <path d="M194 402 C 205 424 216 448 226 468"/>
    <path d="M318 402 C 307 424 296 448 286 468"/>
  </g>
  <g fill="none" stroke="#0b0716" stroke-width="3" opacity="0.5" filter="url(#paintSoft)">
    <path d="M150 440 C 166 460 176 486 180 512"/>
    <path d="M362 440 C 346 460 336 486 332 512"/>
  </g>
  <g fill="none" stroke="${NEON_SOFT}" stroke-width="1.1" opacity="0.4">
    <path d="M110 444 C 130 428 156 416 180 410"/>
    <path d="M402 444 C 382 428 356 416 332 410"/>
  </g>
  <g filter="url(#neon)">
    <path d="M232 392 C 240 408 250 414 256 416 C 262 414 272 408 280 392" fill="none" stroke="${IRIS}" stroke-width="0.9" stroke-dasharray="1.5 1.5" opacity="0.8"/>
    <path d="M256 414 L 262 424 L 256 434 L 250 424 Z" fill="${IRIS}" opacity="0.95"/>
    <circle cx="256" cy="424" r="1.6" fill="#fff" opacity="0.8"/>
  </g>`;
}

function face() {
  return `
  <path d="${FACE}" fill="url(#skin)"/>
  <g clip-path="url(#faceClip)">
    <g filter="url(#paint)">
      <path d="M300 120 C 344 150 350 240 318 296 C 302 322 282 338 256 341 C 292 320 320 270 324 210 C 326 170 316 140 300 120 Z" fill="#0d0818" opacity="0.5"/>
      <ellipse cx="226" cy="150" rx="46" ry="22" fill="url(#lightBlob)"/>
      <ellipse cx="203" cy="240" rx="24" ry="12" fill="url(#lightBlob)"/>
      <ellipse cx="312" cy="244" rx="18" ry="10" fill="url(#lightBlob)" opacity="0.5"/>
      <ellipse cx="222" cy="202" rx="32" ry="16" fill="url(#shadowBlob)" opacity="0.8"/>
      <ellipse cx="290" cy="202" rx="32" ry="16" fill="url(#shadowBlob)" opacity="0.9"/>
      <ellipse cx="262" cy="266" rx="14" ry="5" fill="url(#shadowBlob)"/>
      <ellipse cx="256" cy="318" rx="18" ry="6" fill="url(#shadowBlob)" opacity="0.8"/>
      <ellipse cx="252" cy="326" rx="14" ry="7" fill="url(#lightBlob)" opacity="0.7"/>
      <ellipse cx="186" cy="200" rx="10" ry="30" fill="url(#shadowBlob)" opacity="0.7"/>
      <ellipse cx="326" cy="200" rx="10" ry="30" fill="url(#shadowBlob)" opacity="0.9"/>
    </g>
    <ellipse cx="206" cy="258" rx="22" ry="12" fill="url(#cheek)"/>
    <ellipse cx="306" cy="258" rx="22" ry="12" fill="url(#cheek)"/>
    <ellipse cx="254" cy="228" rx="4" ry="22" fill="url(#lightBlob)" filter="url(#paintSoft)"/>
    <ellipse cx="253" cy="256" rx="6" ry="4" fill="url(#lightBlob)" filter="url(#paintSoft)"/>
    <ellipse cx="256" cy="276" rx="6" ry="2.5" fill="url(#lightBlob)" filter="url(#paintSoft)" opacity="0.7"/>
    <rect x="160" y="100" width="200" height="260" fill="url(#scan)"/>
  </g>
  <path d="${FACE}" fill="none" stroke="${NEON}" stroke-width="1.8" filter="url(#neon)"/>
  <path d="M174 176 C 172 232 182 270 198 298 C 212 322 232 336 254 341" fill="none" stroke="#fdf4ff" stroke-width="1.1" opacity="0.55" filter="url(#paintSoft)"/>
  <g fill="none" stroke="${NEON_SOFT}" stroke-linecap="round">
    <path d="M261 212 C 263 230 266 244 267 250" stroke-width="1" opacity="0.4" filter="url(#paintSoft)"/>
    <path d="M247 257 C 245 261 248 264 252 263 C 254 264 258 264 260 263 C 264 264 267 261 265 257" stroke-width="1.3" opacity="0.75"/>
    <path d="M244 254 C 241 251 242 247 246 246" stroke-width="1" opacity="0.45"/>
    <path d="M268 254 C 271 251 270 247 266 246" stroke-width="1" opacity="0.45"/>
    <path d="M252 268 C 252.5 274 253 278 253 281" stroke-width="0.7" opacity="0.14"/>
    <path d="M260 268 C 259.5 274 259 278 259 281" stroke-width="0.7" opacity="0.14"/>
    <path d="M266 236 C 270 244 271 252 268 258" stroke="#07040f" stroke-width="3" opacity="0.35" filter="url(#paintSoft)"/>
  </g>`;
}

function hairFront() {
  const sweep = strands(26, 41, (i, r) => {
    // Bangs sweep from the part (around x 226) across the forehead to the right.
    const t = i / 25;
    const sx = 212 + r(1) * 26;
    const sy = 70 + r(2) * 40;
    const ex = 250 + t * 96 + (r(3) - 0.5) * 12;
    const ey = 130 + t * 60 + r(4) * 12;
    const path = `M${f(sx)} ${f(sy)} C ${f(sx + 30 + t * 30)} ${f(sy + 10 + t * 20)} ${f(ex - 30)} ${f(ey - 26)} ${f(ex)} ${f(ey)}`;
    return `<path d="${path}" stroke="${r(5) > 0.75 ? IRIS : NEON_SOFT}" stroke-width="${f(0.5 + r(6) * 0.9)}" opacity="${f(0.22 + r(7) * 0.35)}"/>`;
  });
  const leftFall = strands(14, 77, (i, r) => {
    const sx = 222 - r(1) * 20;
    const sy = 72 + r(2) * 30;
    const ex = 162 + r(3) * 22;
    const ey = 180 + r(4) * 90;
    const path = `M${f(sx)} ${f(sy)} C ${f(sx - 30)} ${f(sy + 30)} ${f(ex - 8)} ${f(ey - 60)} ${f(ex)} ${f(ey)}`;
    return `<path d="${path}" stroke="${NEON_SOFT}" stroke-width="${f(0.5 + r(5) * 0.8)}" opacity="${f(0.2 + r(6) * 0.3)}"/>`;
  });
  return `
  <path d="M250 56 C 186 56 150 98 148 162 C 150 204 158 240 172 270 C 170 226 176 188 194 160
           C 206 148 216 136 226 120 C 244 150 280 166 318 170 C 332 172 342 182 346 198
           C 352 226 350 250 342 272 C 362 236 370 182 360 134 C 346 86 306 56 250 56 Z"
        fill="url(#hair)" stroke="${NEON}" stroke-width="2.3" filter="url(#neon)"/>
  <g fill="none" stroke-linecap="round">${sweep}${leftFall}</g>
  <path d="M188 104 C 214 78 258 68 300 78 C 318 84 332 96 340 110 C 312 92 272 84 236 92 C 218 96 202 102 188 104 Z"
        fill="url(#hairGloss)" filter="url(#paintSoft)"/>
  <path d="M240 128 C 268 150 300 160 330 164" fill="none" stroke="#fdf4ff" stroke-width="1.4" opacity="0.35" filter="url(#paintSoft)"/>
  <g fill="none" stroke="${NEON_SOFT}" stroke-width="0.6" opacity="0.5">
    <path d="M300 60 C 318 50 336 52 346 60"/>
    <path d="M206 64 C 196 56 184 56 176 62"/>
    <path d="M356 150 C 364 160 368 172 366 186"/>
  </g>`;
}

function eye(cx, cy, side, eyes) {
  const w = 23;
  const open = Math.max(0, Math.min(1, eyes.open));
  const squint = eyes.squint || 0;
  const outer = side === "left" ? -1 : 1;
  const lx = cx - w;
  const rx = cx + w;
  // A slight upward flick at the outer corner.
  const outerY = cy - 3;
  const innerY = cy + 1;
  const [leftY, rightY] = outer < 0 ? [outerY, innerY] : [innerY, outerY];
  if (open <= 0.02) {
    const curve = eyes.closed === "smile" ? -9 : 5;
    return `
    <g filter="url(#neon)">
      <path d="M${lx} ${leftY} Q ${cx} ${cy + curve} ${rx} ${rightY}" fill="none" stroke="${NEON}" stroke-width="2.6" stroke-linecap="round"/>
      ${eyes.closed === "smile" ? "" : lashes(outer < 0 ? lx : rx, outer < 0 ? leftY : rightY, outer)}
    </g>`;
  }
  const top = cy - 12.5 * open;
  const bottom = cy + 8.5 * open * (1 - squint * 0.6);
  const clipId = `eye-${side}`;
  const gx = eyes.gaze.x;
  const gy = eyes.gaze.y;
  const glow = eyes.glow;
  const shape = `M${lx} ${leftY} C ${lx + 8} ${top} ${rx - 8} ${top} ${rx} ${rightY} C ${rx - 8} ${bottom} ${lx + 8} ${bottom} ${lx} ${leftY} Z`;
  return `
  <ellipse cx="${cx}" cy="${f(cy - 9)}" rx="28" ry="12" fill="url(#shadowLid)" filter="url(#paintSoft)"/>
  <clipPath id="${clipId}"><path d="${shape}"/></clipPath>
  <path d="${shape}" fill="url(#sclera)" opacity="${f(0.55 + glow * 0.35)}"/>
  <g clip-path="url(#${clipId})">
    <circle cx="${f(cx + gx)}" cy="${f(cy + gy - 1.5)}" r="11.5" fill="url(#irisFill)" opacity="${f(0.75 + glow * 0.25)}" filter="url(#irisGlow)"/>
    <g stroke="#701a75" stroke-width="0.6" opacity="0.45">${irisFibers(cx + gx, cy + gy - 1.5)}</g>
    <circle cx="${f(cx + gx)}" cy="${f(cy + gy - 1.5)}" r="11" fill="none" stroke="#3b0764" stroke-width="1.6" opacity="0.85"/>
    <circle cx="${f(cx + gx)}" cy="${f(cy + gy - 1.5)}" r="4.6" fill="#12051c"/>
    <circle cx="${f(cx + gx + 3.4)}" cy="${f(cy + gy - 5)}" r="2.2" fill="#ffffff" opacity="0.95"/>
    <circle cx="${f(cx + gx - 3.6)}" cy="${f(cy + gy + 2.6)}" r="1" fill="#ffffff" opacity="0.6"/>
    <path d="M${lx} ${top - 4} C ${lx + 8} ${f(top + 1)} ${rx - 8} ${f(top + 1)} ${rx} ${top - 4} L ${rx} ${f(top + 5)} C ${rx - 8} ${f(top + 6)} ${lx + 8} ${f(top + 6)} ${lx} ${f(top + 5)} Z" fill="#12091d" opacity="0.6"/>
  </g>
  <g filter="url(#neon)" fill="none" stroke-linecap="round">
    <path d="M${lx} ${leftY} C ${lx + 8} ${top} ${rx - 8} ${top} ${rx} ${rightY}" stroke="${NEON}" stroke-width="2.8"/>
    <path d="M${rx - 8} ${bottom} C ${rx - 12} ${bottom + 0.6} ${lx + 12} ${bottom + 0.6} ${lx + 6} ${f(bottom - 1)}" stroke="${NEON_SOFT}" stroke-width="1" opacity="0.6"/>
    ${lashes(outer < 0 ? lx : rx, outer < 0 ? leftY : rightY, outer)}
    ${lidLashes(lx, rx, leftY, rightY, top, outer)}
    ${lowerLashes(lx, rx, bottom, outer, open)}
    <path d="M${lx + 5} ${f(top - 5 + open * 0)} C ${lx + 12} ${f(top - 9)} ${rx - 12} ${f(top - 9)} ${rx - 3} ${f(top - 5)}"
          stroke="${NEON_SOFT}" stroke-width="1" opacity="${f(0.45 * open)}"/>
  </g>`;
}

function irisFibers(x, y) {
  let lines = "";
  for (let i = 0; i < 18; i += 1) {
    const a = (i / 18) * Math.PI * 2;
    lines += `<path d="M${f(x + Math.cos(a) * 5)} ${f(y + Math.sin(a) * 5)} L ${f(x + Math.cos(a + 0.12) * 10.5)} ${f(y + Math.sin(a + 0.12) * 10.5)}"/>`;
  }
  return lines;
}

// Short lashes along the outer half of the upper lid.
function lidLashes(lx, rx, leftY, rightY, top, outer) {
  let out = "";
  for (let i = 0; i < 5; i += 1) {
    const t = 0.55 + i * 0.09;
    const u = outer > 0 ? t : 1 - t;
    const x = lx + (rx - lx) * u;
    const y = (1 - u) * (1 - u) * leftY + 2 * (1 - u) * u * (top - 1) + u * u * rightY;
    const len = 3 + i * 0.8;
    out += `<path d="M${f(x)} ${f(y)} q ${f(outer * len * 0.6)} ${f(-len * 0.6)} ${f(outer * len)} ${f(-len)}" stroke="${NEON}" stroke-width="1"/>`;
  }
  return out;
}

function lowerLashes(lx, rx, bottom, outer, open) {
  let out = "";
  for (let i = 0; i < 3; i += 1) {
    const u = outer > 0 ? 0.62 + i * 0.1 : 0.38 - i * 0.1;
    const x = lx + (rx - lx) * u;
    out += `<path d="M${f(x)} ${f(bottom - 0.5)} l ${f(outer * 1.2)} ${f(2.2 * open)}" stroke="${NEON_SOFT}" stroke-width="0.7" opacity="0.5"/>`;
  }
  return out;
}

// A winged flick of lashes at the outer corner.
function lashes(x, y, outer) {
  return [0, 1, 2]
    .map((i) => {
      const dx = outer * (4 + i * 2.5);
      const dy = -(3 + i * 1.6);
      return `<path d="M${f(x - outer * i * 3)} ${f(y - i * 1.2)} L ${f(x + dx - outer * i * 2)} ${f(y + dy - i)}" stroke="${NEON}" stroke-width="${2.2 - i * 0.4}"/>`;
    })
    .join("");
}

function brow(cx, cy, side, brows) {
  const outer = side === "left" ? -1 : 1;
  const lift = brows.lift[side] || 0;
  const raise = (brows.raise + lift) * 6;
  const frown = brows.frown * 6;
  // A tapered arch: full at the inner end, fine at the tail.
  const inner = { x: cx - outer * 18, y: cy + 2 - raise + frown };
  const peak = { x: cx + outer * 9, y: cy - 12 - raise - frown * 0.2 };
  const tail = { x: cx + outer * 27, y: cy + 3 - raise * 0.6 };
  const shape =
    `M${f(inner.x)} ${f(inner.y - 2.6)} Q ${f(peak.x)} ${f(peak.y - 2)} ${f(tail.x)} ${f(tail.y)}` +
    ` Q ${f(peak.x)} ${f(peak.y + 1.2)} ${f(inner.x)} ${f(inner.y + 2.2)} Z`;
  let hairs = "";
  for (let i = 0; i < 14; i += 1) {
    const t = i / 13;
    const x = (1 - t) * (1 - t) * inner.x + 2 * (1 - t) * t * peak.x + t * t * tail.x;
    const y = (1 - t) * (1 - t) * inner.y + 2 * (1 - t) * t * (peak.y + 1) + t * t * tail.y;
    const len = 5.5 - t * 3;
    const angle = t < 0.2 ? -1.2 : -0.35;
    hairs += `<path d="M${f(x)} ${f(y + 1.2)} l ${f(outer * len * Math.cos(angle))} ${f(len * Math.sin(angle))}" stroke="#fdf4ff" stroke-width="0.7" opacity="0.55"/>`;
  }
  return `<path d="${shape}" fill="${NEON}" opacity="0.8" filter="url(#neon)"/>
    <g stroke-linecap="round">${hairs}</g>`;
}

function mouth(m) {
  const cx = 256;
  const cy = 292;
  const halfW = 26 * m.width * (1 - 0.36 * m.round);
  const lift = m.smile * 5;
  const open = m.open * 22;
  const lx = cx - halfW;
  const rx = cx + halfW;
  const cornerY = cy - lift;
  const roundPull = m.round * 6;
  // The opening: top edge rises a little, bottom drops with the jaw.
  const topEdge = cy - open * 0.2 - m.smile * 1.2;
  const botEdge = cy + open;
  const upperTop = topEdge - 8 - m.round * 1.5;
  const lowerBottom = botEdge + 10 + m.round * 1.5;
  const inner = open > 1.5
    ? `M${f(lx + roundPull * 0.5)} ${f(cornerY)} C ${f(lx + 6 - roundPull)} ${f(topEdge)} ${f(rx - 6 + roundPull)} ${f(topEdge)} ${f(rx - roundPull * 0.5)} ${f(cornerY)}
       C ${f(rx - 4 + roundPull)} ${f(botEdge + 2)} ${f(lx + 4 - roundPull)} ${f(botEdge + 2)} ${f(lx + roundPull * 0.5)} ${f(cornerY)} Z`
    : "";
  const upperLip = `M${f(lx)} ${f(cornerY)} C ${f(lx + halfW * 0.5)} ${f(upperTop + 1)} ${f(cx - 7)} ${f(upperTop - 2)} ${cx - 0.5} ${f(upperTop + 2)}
    C ${cx + 7} ${f(upperTop - 2)} ${f(rx - halfW * 0.5)} ${f(upperTop + 1)} ${f(rx)} ${f(cornerY)}
    C ${f(rx - 8)} ${f(topEdge + 1)} ${f(lx + 8)} ${f(topEdge + 1)} ${f(lx)} ${f(cornerY)} Z`;
  const lowerTopY = m.bite ? topEdge + 2 : botEdge;
  const lowerLip = `M${f(lx + 2)} ${f(cornerY + 0.5)} C ${f(lx + 10)} ${f(lowerTopY + 1)} ${f(rx - 10)} ${f(lowerTopY + 1)} ${f(rx - 2)} ${f(cornerY + 0.5)}
    C ${f(rx - 8)} ${f(lowerBottom)} ${f(lx + 8)} ${f(lowerBottom)} ${f(lx + 2)} ${f(cornerY + 0.5)} Z`;
  let teeth = "";
  if (inner && m.teeth !== "none") {
    const topTeeth = `<rect x="${f(lx)}" y="${f(topEdge - 2)}" width="${f(halfW * 2)}" height="${f(Math.min(7, open * 0.45 + 3))}" fill="#efe6fb" opacity="0.88"/>`;
    const bottomTeeth = m.teeth === "both"
      ? `<rect x="${f(lx)}" y="${f(botEdge - Math.min(6, open * 0.4 + 2.5))}" width="${f(halfW * 2)}" height="7" fill="#e3d6f5" opacity="0.8"/>`
      : "";
    teeth = `<clipPath id="mouthClip"><path d="${inner}"/></clipPath>
      <g clip-path="url(#mouthClip)">${topTeeth}${bottomTeeth}
        <ellipse cx="${cx}" cy="${f(botEdge + 3)}" rx="${f(halfW * 0.55)}" ry="${f(Math.max(2, open * 0.35))}" fill="#7a2f5c" opacity="${open > 10 && m.teeth !== "both" ? 0.7 : 0}"/>
      </g>`;
  } else if (m.bite) {
    teeth = `<rect x="${f(cx - halfW * 0.7)}" y="${f(topEdge - 1)}" width="${f(halfW * 1.4)}" height="4.5" rx="1.5" fill="#efe6fb" opacity="0.85"/>`;
  }
  return `
  <g>
    ${inner ? `<path d="${inner}" fill="url(#mouthDark)"/>` : ""}
    ${teeth}
    <path d="${lowerLip}" fill="url(#lip)" stroke="${NEON}" stroke-width="1.2" filter="url(#neon)"/>
    <path d="${upperLip}" fill="url(#lip)" stroke="${NEON}" stroke-width="1.2" filter="url(#neon)"/>
    <ellipse cx="${cx + 3}" cy="${f(lowerBottom - 4.5)}" rx="${f(halfW * 0.3)}" ry="1.8" fill="#fdf4ff" opacity="0.4" filter="url(#soft)"/>
    <g stroke="#3b0f45" stroke-width="0.5" opacity="0.35">
      ${[-10, -5, 0, 5, 10].map((dx) => `<path d="M${cx + dx} ${f(lowerTopY + 2)} l ${f(dx * 0.05)} ${f(Math.max(2, lowerBottom - lowerTopY - 4))}"/>`).join("")}
    </g>
    <path d="M${cx - 6} ${f(upperTop + 1.5)} Q ${cx} ${f(upperTop + 3.5)} ${cx + 6} ${f(upperTop + 1.5)}" stroke="#fdf4ff" stroke-width="0.8" fill="none" opacity="0.4"/>
    ${m.smile > 0.5 ? `<g stroke="${NEON_SOFT}" stroke-width="1.1" fill="none" opacity="${f((m.smile - 0.5) * 1.2)}">
      <path d="M${f(lx - 5)} ${f(cornerY - 5)} Q ${f(lx - 9)} ${f(cornerY + 2)} ${f(lx - 5)} ${f(cornerY + 8)}"/>
      <path d="M${f(rx + 5)} ${f(cornerY - 5)} Q ${f(rx + 9)} ${f(cornerY + 2)} ${f(rx + 5)} ${f(cornerY + 8)}"/></g>` : ""}
  </g>`;
}

function portrait(expr) {
  const e = merge(NEUTRAL, expr);
  return `<g filter="url(#grain)">
  ${hairBack()}
  ${neckAndBody()}
  ${face()}
  ${eye(222, 206, "left", e.eyes)}
  ${eye(290, 206, "right", e.eyes)}
  ${brow(222, 180, "left", e.brows)}
  ${brow(290, 180, "right", e.brows)}
  ${mouth(e.mouth)}
  ${hairFront()}
  </g>`;
}

function wrap(body, glow = 0.35, extra = "") {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">${defs(glow)}${extra}${body}</svg>`;
}

// ---------------------------------------------------------------------------
// Effects

// Deterministic noise so re-rendering gives identical frames.
function rand(seed) {
  let x = Math.sin(seed * 12.9898) * 43758.5453;
  return x - Math.floor(x);
}

function scanlines(opacity, seed, count = 40) {
  let lines = "";
  for (let i = 0; i < count; i += 1) {
    const y = Math.floor(rand(seed + i) * 512);
    const x = Math.floor(rand(seed + i * 3) * 300);
    const w = 40 + Math.floor(rand(seed + i * 7) * 200);
    lines += `<rect x="${x}" y="${y}" width="${w}" height="${1 + Math.floor(rand(seed + i * 5) * 2)}" fill="${NEON}" opacity="${f(opacity * (0.4 + rand(seed + i * 11) * 0.6))}"/>`;
  }
  return lines;
}

// 13 frames: sparks, then the outline drawing up from the shoulders, then the
// fill arriving, then a settle with a brief bright flash.
function materialize(n) {
  const base = portrait(EXPRESSIONS.base);
  if (n <= 3) return wrap(`<g filter="url(#neon)">${scanlines(0.5 + n * 0.1, n * 17, 12 + n * 10)}</g>`);
  if (n <= 8) {
    const reveal = 512 - ((n - 3) / 5) * 470;
    return wrap(
      `<clipPath id="reveal"><rect x="0" y="${f(reveal)}" width="512" height="512"/></clipPath>
       <g clip-path="url(#reveal)" opacity="${f(0.35 + (n - 3) * 0.1)}" style="filter:saturate(1.6)">${base}</g>
       <g filter="url(#neon)">${scanlines(0.55, n * 29, 24)}</g>
       <rect x="0" y="${f(reveal - 2)}" width="512" height="3" fill="${IRIS}" filter="url(#neon)"/>`,
    );
  }
  const settle = (n - 8) / 5;
  const jitter = n < 12 ? (rand(n) - 0.5) * 6 : 0;
  return wrap(
    `<g transform="translate(${f(jitter)} 0)" opacity="${f(0.7 + settle * 0.3)}">${base}</g>
     ${n < 12 ? `<g filter="url(#neon)">${scanlines(0.35 * (1 - settle), n * 41, 14)}</g>` : ""}
     ${n === 12 ? `<rect width="512" height="512" fill="${IRIS}" opacity="0.08"/>` : ""}`,
    0.35 + settle * 0.4,
  );
}

// 7 frames of horizontal tearing with a color split, calming toward the end.
function glitch(n) {
  const base = portrait(EXPRESSIONS.base);
  const strength = [0.6, 1, 0.8, 1, 0.55, 0.35, 0.15][n - 1];
  let bands = "";
  let clips = "";
  for (let i = 0; i < 7; i += 1) {
    const y = Math.floor(rand(n * 13 + i) * 470);
    const h = 8 + Math.floor(rand(n * 7 + i) * 40);
    const dx = (rand(n * 31 + i) - 0.5) * 60 * strength;
    clips += `<clipPath id="band${i}"><rect x="0" y="${y}" width="512" height="${h}"/></clipPath>`;
    bands += `<g clip-path="url(#band${i})"><g transform="translate(${f(dx)} 0)">${base}</g></g>`;
  }
  const split = 5 * strength;
  return wrap(
    `${clips}
     <g opacity="0.55" style="mix-blend-mode:screen" transform="translate(${f(-split)} 0)"><g style="filter:hue-rotate(-60deg)">${base}</g></g>
     <g opacity="0.55" style="mix-blend-mode:screen" transform="translate(${f(split)} 0)"><g style="filter:hue-rotate(80deg)">${base}</g></g>
     <g opacity="${f(1 - strength * 0.35)}">${base}</g>
     ${bands}
     <g filter="url(#neon)">${scanlines(0.4 * strength, n * 53, 18)}</g>`,
  );
}

export function jessSvg(name) {
  if (Object.hasOwn(EXPRESSIONS, name)) {
    const glow = merge(NEUTRAL, EXPRESSIONS[name]).eyes.glow;
    return wrap(portrait(EXPRESSIONS[name]), glow);
  }
  const effect = /^(materialize|glitch)_(\d{2})$/.exec(name);
  if (effect) {
    const n = Number(effect[2]);
    return effect[1] === "materialize" ? materialize(n) : glitch(n);
  }
  throw new Error(`Unknown Jess frame: ${name}`);
}

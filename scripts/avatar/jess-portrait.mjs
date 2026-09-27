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
    <radialGradient id="skin" cx="50%" cy="38%" r="62%">
      <stop offset="0%" stop-color="#3a3052"/>
      <stop offset="55%" stop-color="#2a2140"/>
      <stop offset="100%" stop-color="#171126"/>
    </radialGradient>
    <linearGradient id="neck" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#1a1329"/>
      <stop offset="45%" stop-color="#261d3a"/>
      <stop offset="100%" stop-color="#2a2140"/>
    </linearGradient>
    <linearGradient id="hair" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#2b1f45"/>
      <stop offset="60%" stop-color="#170f29"/>
      <stop offset="100%" stop-color="#0c0816"/>
    </linearGradient>
    <linearGradient id="jacket" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#1d1630"/>
      <stop offset="100%" stop-color="#0b0814"/>
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
  </defs>`;
}

const FACE =
  "M256 110 C 304 110 338 142 340 192 C 342 234 334 268 316 296 " +
  "C 300 322 280 338 256 341 C 232 338 212 322 196 296 C 178 268 170 234 172 192 " +
  "C 174 142 208 110 256 110 Z";

function hairBack() {
  return `
  <path d="M252 52 C 170 52 132 112 134 200 C 142 262 128 318 118 372 C 112 404 140 418 176 410
           C 190 380 190 332 194 292 L 318 292 C 322 332 322 380 336 410 C 372 418 400 404 394 372
           C 384 318 376 262 378 200 C 380 112 334 52 252 52 Z"
        fill="url(#hair)" stroke="${NEON_SOFT}" stroke-width="2.2" filter="url(#neon)" opacity="0.98"/>
  <g stroke="${NEON_SOFT}" stroke-width="1" fill="none" opacity="0.35">
    <path d="M150 230 C 146 290 140 340 128 384"/>
    <path d="M164 250 C 160 310 158 350 150 400"/>
    <path d="M362 230 C 366 290 372 340 384 384"/>
    <path d="M348 250 C 352 310 354 350 362 400"/>
  </g>`;
}

function neckAndBody() {
  return `
  <path d="M230 316 C 232 346 230 366 222 384 L 290 384 C 282 366 280 346 282 316 Z" fill="url(#neck)"
        stroke="${NEON_SOFT}" stroke-width="1" stroke-opacity="0.35"/>
  <path d="M60 512 C 70 440 120 410 188 396 C 206 392 216 386 222 376 L 256 426 L 290 376
           C 296 386 306 392 324 396 C 392 410 442 440 452 512 Z"
        fill="url(#jacket)" stroke="${NEON}" stroke-width="2.4" filter="url(#neon)"/>
  <path d="M222 378 L 256 426 L 290 378" fill="none" stroke="${NEON}" stroke-width="1.6" opacity="0.8"/>
  <g fill="none" stroke="${NEON_SOFT}" stroke-width="1.4" opacity="0.55">
    <path d="M188 398 C 200 440 214 474 222 512"/>
    <path d="M324 398 C 312 440 298 474 290 512"/>
    <path d="M206 404 C 222 408 238 412 256 426 C 274 412 290 408 306 404"/>
  </g>
  <g filter="url(#neon)">
    <path d="M238 402 C 246 412 266 412 274 402" fill="none" stroke="${NEON}" stroke-width="1.2" opacity="0.8"/>
    <path d="M256 412 L 262 422 L 256 432 L 250 422 Z" fill="${IRIS}" opacity="0.9"/>
  </g>`;
}

function face() {
  return `
  <path d="${FACE}" fill="url(#skin)" stroke="${NEON}" stroke-width="2.4" filter="url(#neon)"/>
  <g clip-path="url(#faceClip)">
    <path d="M300 120 C 340 150 348 240 316 296 C 300 322 282 338 256 341 C 290 320 318 270 322 210 C 324 170 316 140 300 120 Z" fill="#0f0a1a" opacity="0.32"/>
    <ellipse cx="204" cy="258" rx="24" ry="14" fill="url(#cheek)"/>
    <ellipse cx="308" cy="258" rx="24" ry="14" fill="url(#cheek)"/>

    <path d="M186 290 C 206 322 230 338 256 342 C 282 338 306 322 326 290 C 316 330 290 350 256 352 C 222 350 196 330 186 290 Z"
          fill="#0f0a1a" opacity="0.45"/>
  </g>
  <g fill="none" stroke="${NEON_SOFT}" stroke-linecap="round" filter="url(#soft)">
    <path d="M260 212 C 262 230 266 244 267 252" stroke-width="1.2" opacity="0.5"/>
    <path d="M249 259 C 252 262 260 262 263 259" stroke-width="1.4" opacity="0.7"/>
    <path d="M245 256 C 243 253 244 250 247 250" stroke-width="1" opacity="0.45"/>
    <path d="M267 256 C 269 253 268 250 265 250" stroke-width="1" opacity="0.45"/>
  </g>`;
}

function hairFront() {
  return `
  <path d="M250 56 C 186 56 150 98 148 162 C 150 204 158 240 172 270 C 170 226 176 188 194 160
           C 206 148 216 136 226 120 C 244 150 280 166 318 170 C 332 172 342 182 346 198
           C 352 226 350 250 342 272 C 362 236 370 182 360 134 C 346 86 306 56 250 56 Z"
        fill="url(#hair)" stroke="${NEON}" stroke-width="2.3" filter="url(#neon)"/>
  <path d="M196 92 C 222 72 262 66 296 74 C 262 76 232 86 206 104 Z" fill="${IRIS}" opacity="0.12" filter="url(#soft)"/>
  <g fill="none" stroke="${NEON_SOFT}" stroke-width="1.1" opacity="0.5" stroke-linecap="round">
    <path d="M228 78 C 210 94 194 118 184 150"/>
    <path d="M214 86 C 196 108 180 138 172 180"/>
    <path d="M238 96 C 262 128 290 148 324 158"/>
    <path d="M258 84 C 286 110 316 128 342 150"/>
    <path d="M282 76 C 312 92 336 118 348 160"/>
    <path d="M232 112 C 252 136 276 150 306 158"/>
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
  <clipPath id="${clipId}"><path d="${shape}"/></clipPath>
  <path d="${shape}" fill="#d8c4f0" opacity="${f(0.1 + glow * 0.22)}"/>
  <g clip-path="url(#${clipId})">
    <circle cx="${f(cx + gx)}" cy="${f(cy + gy - 1.5)}" r="12" fill="#6d28d9" opacity="0.85"/>
    <circle cx="${f(cx + gx)}" cy="${f(cy + gy - 1.5)}" r="10.6" fill="${IRIS}" opacity="${f(0.55 + glow * 0.45)}" filter="url(#irisGlow)"/>
    <circle cx="${f(cx + gx)}" cy="${f(cy + gy - 1.5)}" r="10.6" fill="none" stroke="#581c87" stroke-width="1.4" opacity="0.8"/>
    <circle cx="${f(cx + gx)}" cy="${f(cy + gy - 1.5)}" r="4.4" fill="#1e0b2e"/>
    <circle cx="${f(cx + gx + 2.6)}" cy="${f(cy + gy - 2.8)}" r="1.8" fill="#ffffff" opacity="0.9"/>
    <path d="M${lx} ${top - 4} C ${lx + 8} ${f(top + 1)} ${rx - 8} ${f(top + 1)} ${rx} ${top - 4} L ${rx} ${f(top + 5)} C ${rx - 8} ${f(top + 6)} ${lx + 8} ${f(top + 6)} ${lx} ${f(top + 5)} Z" fill="#12091d" opacity="0.6"/>
  </g>
  <g filter="url(#neon)" fill="none" stroke-linecap="round">
    <path d="M${lx} ${leftY} C ${lx + 8} ${top} ${rx - 8} ${top} ${rx} ${rightY}" stroke="${NEON}" stroke-width="2.8"/>
    <path d="M${rx - 8} ${bottom} C ${rx - 12} ${bottom + 0.6} ${lx + 12} ${bottom + 0.6} ${lx + 6} ${f(bottom - 1)}" stroke="${NEON_SOFT}" stroke-width="1" opacity="0.6"/>
    ${lashes(outer < 0 ? lx : rx, outer < 0 ? leftY : rightY, outer)}
    <path d="M${lx + 5} ${f(top - 5 + open * 0)} C ${lx + 12} ${f(top - 9)} ${rx - 12} ${f(top - 9)} ${rx - 3} ${f(top - 5)}"
          stroke="${NEON_SOFT}" stroke-width="1" opacity="${f(0.45 * open)}"/>
  </g>`;
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
  return `<path d="${shape}" fill="${NEON}" opacity="0.92" filter="url(#neon)"/>`;
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
    <ellipse cx="${cx + 3}" cy="${f(lowerBottom - 4.5)}" rx="${f(halfW * 0.28)}" ry="1.6" fill="#f5d0fe" opacity="0.28" filter="url(#soft)"/>
    ${m.smile > 0.5 ? `<g stroke="${NEON_SOFT}" stroke-width="1.1" fill="none" opacity="${f((m.smile - 0.5) * 1.2)}">
      <path d="M${f(lx - 5)} ${f(cornerY - 5)} Q ${f(lx - 9)} ${f(cornerY + 2)} ${f(lx - 5)} ${f(cornerY + 8)}"/>
      <path d="M${f(rx + 5)} ${f(cornerY - 5)} Q ${f(rx + 9)} ${f(cornerY + 2)} ${f(rx + 5)} ${f(cornerY + 8)}"/></g>` : ""}
  </g>`;
}

function portrait(expr) {
  const e = merge(NEUTRAL, expr);
  return `
  ${hairBack()}
  ${neckAndBody()}
  ${face()}
  ${eye(222, 206, "left", e.eyes)}
  ${eye(290, 206, "right", e.eyes)}
  ${brow(222, 180, "left", e.brows)}
  ${brow(290, 180, "right", e.brows)}
  ${mouth(e.mouth)}
  ${hairFront()}`;
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

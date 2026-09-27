// Mouth shapes from the words being spoken.
//
// The voice frontend only reports loudness, which says *when* the mouth moves
// but not *how*. The reply text arrives ahead of its audio, so it can supply
// the shape: each syllable becomes one unit -- the consonant that opens it
// (lips closed for m/b/p, teeth on lip for f/v, teeth showing for s/sh/ch)
// and the vowel it rests on. The scene steps through the units on syllable
// onsets in the loudness envelope, or on a clock when there is none.
//
// Spelling is not pronunciation; this only has to look right at speaking
// speed, and a wrong guess is still a plausible mouth.

// Vowel spellings, longest first, and the frame each one rests on.
const NUCLEI = [
  ["augh", "oh"], ["ough", "oh"],
  ["eau", "oh"],
  ["ee", "e_sound"], ["ea", "e_sound"], ["ie", "e_sound"], ["ei", "e_sound"], ["ey", "e_sound"],
  ["ai", "e_sound"], ["ay", "e_sound"],
  ["oo", "oo"], ["ou", "oo"], ["ew", "oo"], ["ue", "oo"], ["ui", "oo"],
  ["oa", "oh"], ["ow", "oh"], ["oi", "oh"], ["oy", "oh"], ["au", "oh"], ["aw", "oh"],
  ["a", "open"], ["e", "e_sound"], ["i", "e_sound"], ["y", "e_sound"], ["o", "oh"], ["u", "oo"],
];

// The quieter form of each resting shape, for soft or clipped syllables.
const SOFT = Object.freeze({ open: "ah_small", e_sound: "halfsmile", oh: "oo", oo: "oo" });

// The consonant just before a vowel decides how the syllable opens.
function onsetFor(consonants) {
  const tail = consonants.slice(-2);
  if (/(sh|ch|zh)$/.test(tail)) return "grit";
  const last = consonants.slice(-1);
  if ("mbp".includes(last)) return "base";
  if ("fv".includes(last) || tail === "ph") return "fv";
  if ("szjx".includes(last)) return "grit";
  if (last === "w") return "oo";
  return null;
}

function syllablesOf(word) {
  const units = [];
  let consonants = "";
  let i = 0;
  while (i < word.length) {
    // "y" opening a word is a consonant ("yes"), elsewhere a vowel ("my").
    const rest = word.slice(i);
    const match = i === 0 && rest[0] === "y" && rest.length > 1 && "aeiou".includes(rest[1])
      ? null
      : NUCLEI.find(([spelling]) => rest.startsWith(spelling));
    if (!match) {
      consonants += word[i];
      i += 1;
      continue;
    }
    const [spelling, frame] = match;
    const end = i + spelling.length;
    // A final "e" after a consonant is silent ("make", "time").
    const silentE = spelling === "e" && end === word.length && units.length > 0 && consonants.length > 0;
    if (!silentE) units.push({ onset: onsetFor(consonants), nucleus: frame });
    consonants = "";
    i = end;
  }
  // A word that ends on lips closing ("him", "stop") or a hiss ("yes").
  const close = onsetFor(consonants);
  if (units.length && (close === "base" || close === "fv" || close === "grit")) {
    units[units.length - 1].coda = close;
  }
  return units;
}

// Syllable units for ``text``, with ``pause`` after sentence-ending punctuation.
export function visemeScript(text) {
  const units = [];
  const tokens = String(text || "").toLowerCase().match(/[a-z']+|[.!?;:]+/g) || [];
  for (const token of tokens) {
    if (/^[.!?;:]+$/.test(token)) {
      if (units.length && !units[units.length - 1].pause) units[units.length - 1].pause = true;
      continue;
    }
    units.push(...syllablesOf(token.replace(/'/g, "")));
  }
  return units;
}

export function softFrame(nucleus) {
  return SOFT[nucleus] || nucleus;
}

// Walks a viseme script as speech plays. Feed it the loudness envelope with
// ``level``; it detects syllable onsets (a rise after a dip) and moves to the
// next unit, showing the unit's opening consonant briefly, then its vowel,
// sized by loudness. ``clock`` drives it at a speaking pace when no envelope
// is arriving.
export class VisemeTrack {
  constructor({ onsetMs = 55, codaMs = 70 } = {}) {
    this.onsetMs = onsetMs;
    this.codaMs = codaMs;
    this.reset([]);
  }

  reset(units) {
    this.units = units;
    this.index = -1;
    this.unitAt = -Infinity;
    this.voiced = false;
    this.peak = 0;
    this.valley = 1;
    this.dipped = false;
    this.lastVoicedAt = -Infinity;
    this.clockNext = 0;
    this.clockClose = Infinity;
  }

  // Keep the position when the same reply grows by a sentence.
  extend(units) {
    this.units = units;
  }

  get current() {
    return this.units[this.index] || null;
  }

  advance(now) {
    this.index += 1;
    this.unitAt = now;
    this.peak = 0;
    this.valley = 1;
    this.dipped = false;
    return this.index < this.units.length;
  }

  // Returns true when this sample started a new syllable.
  level(value, now) {
    this.clockClose = Infinity;
    const on = 0.1;
    const off = 0.06;
    let onset = false;
    if (!this.voiced && value >= on) {
      this.voiced = true;
      onset = true;
    } else if (this.voiced && value < off) {
      this.voiced = false;
    } else if (this.voiced) {
      // Inside continuous voicing, a clear dip then a rise is the next syllable.
      this.peak = Math.max(this.peak, value);
      if (value < this.peak * 0.62) {
        this.dipped = true;
        this.valley = Math.min(this.valley, value);
      } else if (this.dipped && value > this.valley * 1.45 && now - this.unitAt > 90) {
        onset = true;
      }
    }
    if (onset) this.advance(now);
    if (this.voiced) this.lastVoicedAt = now;
    return onset;
  }

  // Syllables at a speaking pace, for when the loudness envelope is missing.
  clock(now) {
    if (now < this.clockNext) return false;
    this.advance(now);
    const unit = this.current;
    const length = 190 + ((this.index * 37) % 60);
    this.voiced = true;
    this.lastVoicedAt = now;
    // The lips meet briefly between syllables, longer after a sentence.
    this.clockClose = now + length - 45;
    this.clockNext = now + length + (unit?.pause ? 260 : 0);
    return true;
  }

  frame(now, loudness) {
    const unit = this.current;
    if (now >= this.clockClose) {
      this.voiced = false;
      this.clockClose = Infinity;
    }
    if (!this.voiced) {
      if (unit?.coda && now - this.lastVoicedAt < this.codaMs) return unit.coda;
      return "base";
    }
    // Past the known text (or none yet): the scene shapes from loudness alone.
    if (!unit) return null;
    if (unit.onset && now - this.unitAt < this.onsetMs) return unit.onset;
    return loudness < 0.3 ? softFrame(unit.nucleus) : unit.nucleus;
  }

}

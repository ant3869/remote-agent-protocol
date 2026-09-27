import assert from "node:assert/strict";
import test from "node:test";

import { VisemeTrack, softFrame, visemeScript } from "../../remote_agent_protocol/web_app/avatar/viseme-script.js";

const nuclei = (text) => visemeScript(text).map((unit) => unit.nucleus);

test("vowel spellings pick the resting mouth shape", () => {
  assert.deepEqual(nuclei("see you soon"), ["e_sound", "oo", "oo"]);
  assert.deepEqual(nuclei("hot cat"), ["oh", "open"]);
  assert.deepEqual(nuclei("make time"), ["open", "e_sound"], "a final e is silent");
  assert.deepEqual(nuclei("yes"), ["e_sound"], "a leading y is a consonant");
});

test("the consonant before a vowel shapes how the syllable opens", () => {
  const [may, five, she] = visemeScript("may five she");
  assert.equal(may.onset, "base");
  assert.equal(five.onset, "fv");
  assert.equal(she.onset, "grit");
  assert.equal(visemeScript("him")[0].coda, "base", "lips close at the end of him");
});

test("sentence ends mark a pause and punctuation adds no syllables", () => {
  const units = visemeScript("Done, sir. Anything else?");
  assert.equal(units.filter((u) => u.pause).length, 2);
  assert.equal(units.every((u) => typeof u.nucleus === "string"), true);
});

test("quiet syllables use the smaller form of the shape", () => {
  assert.equal(softFrame("open"), "ah_small");
  assert.equal(softFrame("oh"), "oo");
});

test("loudness onsets step through the syllables of the reply", () => {
  const track = new VisemeTrack();
  track.reset(visemeScript("me too"));
  let now = 0;
  const feed = (level) => { now += 40; track.level(level, now); return track.frame(now, level); };

  assert.equal(feed(0.02), "base", "silent before speech");
  assert.equal(feed(0.5), "base", "m closes the lips as the first syllable opens");
  feed(0.5);
  assert.equal(feed(0.5), "e_sound");
  assert.equal(feed(0.03), "base", "the lips meet between syllables");
  assert.equal(feed(0.5), "oo");
  assert.equal(track.index, 1);
  feed(0.03);
  feed(0.5);
  assert.equal(track.frame(now, 0.5), null, "past the known text the scene falls back to loudness");
});

test("a dip inside continuous voicing starts the next syllable", () => {
  const track = new VisemeTrack();
  track.reset(visemeScript("hello there"));
  let now = 0;
  for (const level of [0.5, 0.55, 0.3, 0.25, 0.5]) {
    now += 40;
    track.level(level, now);
  }
  assert.equal(track.index, 1);
});

test("without an envelope the clock walks the reply at a speaking pace", () => {
  const track = new VisemeTrack();
  track.reset(visemeScript("okay. fine"));
  const frames = [];
  for (let now = 0; now < 1500; now += 20) {
    track.clock(now);
    frames.push(track.frame(now, 0.5));
  }
  assert.ok(frames.includes("oh"));
  assert.ok(frames.includes("fv"));
  assert.ok(frames.includes("base"), "closures between syllables");
});

import test from "node:test";
import assert from "node:assert/strict";
import { EXPRESSIONS, expressionFor, blendTargets } from "../../remote_agent_protocol/web_app/avatar/expressions.js";

const NEUTRAL_KEYS = Object.keys(EXPRESSIONS.neutral).sort();

test("every expression normalizes to the same key set", () => {
  for (const [name, expression] of Object.entries(EXPRESSIONS)) {
    assert.deepEqual(Object.keys(expression).sort(), NEUTRAL_KEYS, `${name} diverges from neutral schema`);
    for (const value of Object.values(expression)) {
      assert.ok(Number.isFinite(value), `${name} has a non-finite target`);
    }
  }
});

test("required expression names exist", () => {
  const required = [
    "neutral", "attentive", "warm", "pleased", "concerned", "confused",
    "apologetic", "thinking", "focused", "surprised", "error",
    "happy", "excited", "sad", "angry", "calm", "skeptical", "listening", "sleeping",
  ];
  for (const name of required) {
    assert.ok(EXPRESSIONS[name], `missing expression: ${name}`);
  }
});

test("unknown expression returns neutral", () => {
  assert.equal(expressionFor("nonsense"), EXPRESSIONS.neutral);
  assert.equal(expressionFor(undefined), EXPRESSIONS.neutral);
});

test("blendTargets clamps amount and interpolates", () => {
  const overshoot = blendTargets(EXPRESSIONS.neutral, EXPRESSIONS.happy, 4);
  assert.equal(overshoot.mouthCorner, EXPRESSIONS.happy.mouthCorner);
  const negative = blendTargets(EXPRESSIONS.neutral, EXPRESSIONS.happy, -1);
  assert.equal(negative.mouthCorner, 0);
  const half = blendTargets(EXPRESSIONS.neutral, EXPRESSIONS.happy, 0.5);
  assert.ok(Math.abs(half.mouthCorner - EXPRESSIONS.happy.mouthCorner / 2) < 1e-9);
});

test("new schema fields blend and partial overlays fall back to base", () => {
  const blended = blendTargets(EXPRESSIONS.neutral, EXPRESSIONS.excited, 1);
  assert.equal(blended.monocleSpeed, EXPRESSIONS.excited.monocleSpeed);
  assert.equal(blended.glowBias, EXPRESSIONS.excited.glowBias);
  const partial = blendTargets(EXPRESSIONS.happy, { mouthCorner: 0 }, 1);
  assert.equal(partial.mouthCorner, 0);
  assert.equal(partial.cheekRaise, EXPRESSIONS.happy.cheekRaise, "missing keys keep base values");
});

test("skeptical and confused are visually distinct via asymmetry channels", () => {
  assert.ok(EXPRESSIONS.skeptical.browAsymmetry > 0.3);
  assert.ok(EXPRESSIONS.skeptical.mouthAsymmetry > 0.2);
  assert.ok(EXPRESSIONS.confused.headRoll !== 0);
  assert.ok(EXPRESSIONS.sleeping.eyelid > 0.7);
});

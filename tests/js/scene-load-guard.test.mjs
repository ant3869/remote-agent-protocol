import test from "node:test";
import assert from "node:assert/strict";
import { SceneLoadGuard } from "../../remote_agent_protocol/web_app/avatar/scene-load-guard.js";


test("identity change invalidates an in-flight scene before it can install", () => {
  const guard = new SceneLoadGuard("butler:high");
  const butlerLoad = guard.token();

  assert.equal(guard.updateKey("jess:medium"), true);
  assert.equal(guard.accepts(butlerLoad), false);
  assert.equal(guard.accepts(guard.token()), true);
});


test("same scene key does not cause pointless reload", () => {
  const guard = new SceneLoadGuard("butler:high");
  const token = guard.token();

  assert.equal(guard.updateKey("butler:high"), false);
  assert.equal(guard.accepts(token), true);
  guard.invalidate();
  assert.equal(guard.accepts(token), false);
});

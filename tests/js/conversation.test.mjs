import assert from "node:assert/strict";
import test from "node:test";
import conversation from "../../remote_agent_protocol/web_app/conversation.js";

test("partial, final, duplicate, and late events retain one utterance", () => {
  const store = conversation.createStore();
  store.apply({key: "speech:a", id: 1, type: "transcript", text: "One", final: false, speaker_name: "Jess"});
  store.apply({key: "speech:a", id: 2, type: "transcript", text: "One. Two.", final: true});
  assert.equal(store.apply({key: "speech:a", id: 1, type: "transcript", text: "One"}), false);
  assert.equal(store.apply({key: "speech:a", id: 3, type: "transcript", text: "Late", final: false}), false);
  assert.equal(store.rows.size, 1);
  assert.equal(store.rows.get("speech:a").speaker_name, "Jess");
  assert.equal(store.rows.get("speech:a").text, "One. Two.");
});

test("same-harness concurrent tasks stay distinct and terminal state cannot regress", () => {
  const store = conversation.createStore();
  for (const key of ["job:a", "job:b"]) store.apply({key, id: 1, type: "agent_job", agent: "Hermes", status: "running"});
  store.apply({key: "job:a", id: 2, type: "agent_job", status: "done"});
  assert.equal(store.apply({key: "job:a", id: 3, type: "agent_job", status: "running"}), false);
  assert.equal(store.rows.size, 2);
});

test("failure, cancellation, and empty success receive truthful labels", () => {
  assert.deepEqual(conversation.outcome({status: "failed", failure_detail: "Connection refused"}), {status: "Failed", text: "Connection refused"});
  assert.equal(conversation.outcome({status: "cancelled"}).status, "Cancelled");
  assert.equal(conversation.outcome({status: "timeout"}).status, "Timed out");
  assert.equal(conversation.outcome({status: "done"}).status, "Completed without an answer");
});

test("snapshot recovery and clear replace the prior conversation", () => {
  const store = conversation.createStore();
  store.apply({key: "speech:old", id: 1, text: "Old"});
  store.restore({epoch: "new", truncated: true, rows: [{key: "speech:new", id: 20, text: "Recovered"}]});
  assert.equal(store.rows.has("speech:old"), false);
  assert.equal(store.truncated, true);
  store.clear("clear-epoch");
  assert.equal(store.rows.size, 0);
  assert.equal(store.epoch, "clear-epoch");
});

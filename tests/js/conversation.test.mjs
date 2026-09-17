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

test("tool updates retain the most recent explanatory progress", () => {
  const detail = conversation.progress({action: "Running shell command", activity: [
    {text: "Still working"}, {text: "Checking the latest logs for errors."},
    {text: "Running cp_read_tool_result"}, {text: "Running shell command"},
  ]});
  assert.equal(detail.action, "Running shell command");
  assert.equal(detail.thought, "Checking the latest logs for errors.");
});

test("transcript omits narration telemetry and routing duplicates but retains errors and consultations", () => {
  const rows = [
    {type: "routing"}, {type: "sys", text: "Voicing an agent job summary."},
    {type: "agent_job", status: "running"}, {type: "agent_job", status: "failed"},
    {type: "sys", text: "Connection lost"}, {type: "agent_consult", reason: "Quota exceeded"},
    {type: "transcript", role: "user", text: "Ping the agents"},
  ];
  assert.deepEqual(conversation.visibleRows(rows), rows.slice(3));
});

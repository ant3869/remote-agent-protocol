import threading
from collections import deque

import pytest

from remote_agent_protocol.conversation import ConversationEvents, ConversationStore, SpeechText


def publish(store, event, index):
    return store.apply({**event, "id": index})


def test_speaker_snapshot_and_repeated_utterances_have_distinct_identity():
    name = ["Jess"]
    events = ConversationEvents(lambda: name[0])
    first = events.utterance("Hello")
    name[0] = "Butler"
    assert events.stamp(first)["speaker_name"] == "Jess"
    assert events.utterance("Hello")["message_id"] != first["message_id"]


def test_stream_and_playback_fold_without_erasing_text_or_rewinding_delivery():
    store = ConversationStore()
    row = ConversationEvents(lambda: "Hermes", "queued").utterance("First.", final=False)
    publish(store, row, 1)
    publish(
        store,
        {
            "type": "transcript",
            "message_id": row["message_id"],
            "playback_update": True,
            "delivery": "playing",
        },
        2,
    )
    result = publish(store, {**row, "text": "First. Second.", "final": True}, 3)
    assert result["delivery"] == "playing"
    assert result["speaker_name"] == "Hermes"
    assert len(store.rows) == 1
    assert publish(store, {**row, "text": "late partial"}, 4) is None
    result = publish(
        store,
        {
            "type": "transcript",
            "message_id": row["message_id"],
            "playback_update": True,
            "delivery": "interrupted",
        },
        5,
    )
    assert result["text"] == "First. Second."
    assert (
        publish(
            store,
            {
                "type": "transcript",
                "message_id": row["message_id"],
                "playback_update": True,
                "delivery": "played",
            },
            6,
        )
        is None
    )


def test_jobs_keep_identity_and_coalesce_heartbeats_without_hiding_failures():
    store = ConversationStore()
    event = {
        "type": "agent_job",
        "event": "progress",
        "agent": "Hermes",
        "job_id": "a",
        "status": "running",
        "state": "in_progress",
        "action": "Checking",
    }
    publish(store, event, 1)
    publish(store, event, 2)
    publish(store, {**event, "job_id": "b"}, 3)
    assert len(store.rows) == 2
    assert len(store.rows["job:a"]["activity"]) == 1
    finished = publish(
        store,
        {**event, "event": "finished", "status": "failed", "failure_detail": "Connection refused"},
        4,
    )
    assert finished["failure_detail"] == "Connection refused"
    assert publish(store, event, 5) is None


def test_telemetry_does_not_evict_conversation_and_clear_rejects_late_text():
    store = ConversationStore(limit=2)
    events = ConversationEvents(lambda: "Jess")
    speech = events.utterance("Retain me")
    publish(store, speech, 1)
    for index in range(2, 1002):
        publish(store, {"type": "metric", "value": 1}, index)
    assert len(store.rows) == 1
    snapshot = store.snapshot()
    snapshot["rows"][0]["text"] = "Do not mutate storage"
    assert store.snapshot()["rows"][0]["text"] == "Retain me"
    store.clear()
    assert publish(store, {**speech, "text": "Late final"}, 1002) is None
    assert store.snapshot()["rows"] == []
    for index in range(1003, 1006):
        publish(store, events.utterance(str(index)), index)
    assert store.snapshot()["truncated"]
    assert len(store.rows) == 2


def test_consultation_groups_both_sides_under_original_task():
    store = ConversationStore()
    publish(
        store,
        {
            "type": "agent_consult",
            "consultation_id": "consult-1",
            "event": "asked",
            "job_id": "parent",
            "agent": "Codex",
            "peer": "Hermes",
            "question": "Check labels?",
        },
        1,
    )
    row = publish(
        store,
        {
            "type": "agent_consult",
            "consultation_id": "consult-1",
            "event": "answered",
            "job_id": "child",
            "agent": "Hermes",
            "peer": "Codex",
            "answer": "Labels are wrong",
        },
        2,
    )
    assert row["parent_job_id"] == "parent"
    assert set(row["exchanges"]) == {"asked", "answered"}


@pytest.fixture
def web_app():
    from remote_agent_protocol.web_gui import WebVoiceApp

    app = WebVoiceApp.__new__(WebVoiceApp)
    app._conversation = ConversationStore()
    app._conversation_events = ConversationEvents(lambda: "Jess")
    app._event_log = deque(maxlen=4)
    app._event_id = 0
    app._folders = {}
    app._lock = threading.RLock()
    app._status_payload = lambda: {}
    return app


def test_poll_gap_returns_snapshot_and_clear_cannot_replay_old_events(web_app):
    web_app._publish({"type": "transcript", "role": "assistant", "text": "Answer"})
    for _ in range(10):
        web_app._publish({"type": "metric"})
    response = web_app._events_after(1)
    assert response["history_gap"]
    assert response["conversation"]["rows"][0]["text"] == "Answer"
    web_app._clear_conversation()
    assert web_app._events_after(0)["conversation"]["rows"] == []
    assert all(e.get("text") != "Answer" for e in web_app._events_after(0)["events"])


def test_playback_reports_require_matching_session_and_monotonic_sequence(web_app):
    row = web_app._conversation_events.utterance("Full answer", delivery="playback_unconfirmed")
    web_app._publish(row)
    payload = {
        "message_id": row["message_id"],
        "session_id": row["session_id"],
        "sequence": 1,
        "delivery": "playing",
    }
    assert not web_app._ingest_speech({**payload, "session_id": "old-session"})
    assert web_app._ingest_speech(payload)
    assert not web_app._ingest_speech(payload)
    assert web_app._ingest_speech(
        {**payload, "sequence": 2, "delivery": "interrupted", "spoken_text": "Full"}
    )
    assert not web_app._ingest_speech({**payload, "sequence": 3, "delivery": "played"})
    result = web_app._conversation.snapshot()["rows"][0]
    assert result["text"] == "Full answer"
    assert result["spoken_text"] == "Full"


def test_independent_external_speech_requires_current_epoch(web_app):
    event = {
        "message_id": "external-1",
        "source": "external",
        "sequence": 0,
        "delivery": "playing",
        "text": "An external utterance",
        "speaker_name": "Butler",
    }
    assert not web_app._ingest_speech(event)
    assert web_app._ingest_speech({**event, "conversation_epoch": web_app._conversation.epoch})
    assert web_app._conversation.snapshot()["rows"][0]["speaker_name"] == "Butler"


@pytest.mark.parametrize(
    "payload", [None, [], {"message_id": "bad", "sequence": 0, "delivery": []}]
)
def test_malformed_speech_reports_do_not_mutate_conversation(web_app, payload):
    assert not web_app._ingest_speech(payload)
    assert web_app._conversation.snapshot()["rows"] == []


def test_openai_responses_carry_the_same_speech_identity():
    from remote_agent_protocol.openai_bridge import _chat_completion, _stream_chunk

    utterance = ConversationEvents(lambda: "Jess").utterance("Hello")
    chunk = SpeechText("Hello", utterance)
    assert _chat_completion(chunk, "test")["rap"]["message_id"] == utterance["message_id"]
    assert _stream_chunk("id", "test", 0, {"content": chunk})["rap"] == chunk.utterance


def test_clear_rejects_a_reply_that_had_not_emitted_any_text_yet(web_app):
    pending = web_app._conversation_events.utterance("Late answer")
    web_app._clear_conversation()
    web_app._publish(pending)
    assert web_app._conversation.snapshot()["rows"] == []


def test_speech_endpoint_requires_authentication(web_app, monkeypatch):
    import json
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from remote_agent_protocol import config as cfg

    monkeypatch.setattr(cfg, "S2S_BRIDGE_API_KEY", "conversation-test-key")
    row = web_app._conversation_events.utterance("Hello", delivery="playback_unconfirmed")
    web_app._publish(row)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web_app._handler_class())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    body = json.dumps(
        {
            "message_id": row["message_id"],
            "session_id": row["session_id"],
            "sequence": 0,
            "delivery": "playing",
        }
    ).encode()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/speech-events"
        with pytest.raises(urllib.error.HTTPError) as denied:
            urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=3)
        assert denied.value.code == 401
        request = urllib.request.Request(
            url, data=body, headers={"Authorization": "Bearer conversation-test-key"}
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            assert json.load(response) == {"ok": True}
        with pytest.raises(urllib.error.HTTPError) as duplicate:
            urllib.request.urlopen(request, timeout=3)
        assert duplicate.value.code == 409
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

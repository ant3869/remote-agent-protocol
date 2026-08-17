"""Brain-mode voice selection and avatar lip-sync, both owned by the external frontend."""

import asyncio
import json
import queue
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from remote_agent_protocol import brain as brain_module
from remote_agent_protocol import config as cfg
from remote_agent_protocol import web_gui
from remote_agent_protocol.avatar_audio import AvatarAudioEnvelopeHub
from remote_agent_protocol.brain_adapter import BrainSessionAdapter
from remote_agent_protocol.multimodal_prompt import MultimodalPromptBundle
from remote_agent_protocol.personas import PERSONAS
from remote_agent_protocol.web_gui import WebVoiceApp


def _adapter(monkeypatch, voice_file):
    monkeypatch.setattr(cfg, "S2S_VOICE_FILE", str(voice_file))
    return BrainSessionAdapter(PERSONAS[0])


def _queued_announcements(path):
    queue_dir = path.with_suffix(f"{path.suffix}.queue")
    return [
        json.loads(item.read_text(encoding="utf-8"))
        for item in sorted(queue_dir.glob("*.json"))
    ]


def test_set_voice_publishes_selection_for_the_realtime_frontend(monkeypatch, tmp_path):
    voice_file = tmp_path / "s2s_voice.txt"
    adapter = _adapter(monkeypatch, voice_file)

    adapter.set_voice("bm_george")

    assert voice_file.read_text(encoding="utf-8").strip() == "bm_george"


def test_set_voice_ignores_blank_selection(monkeypatch, tmp_path):
    voice_file = tmp_path / "s2s_voice.txt"
    adapter = _adapter(monkeypatch, voice_file)

    adapter.set_voice("   ")

    assert not voice_file.exists()


def test_startup_defaults_republish_the_saved_voice(monkeypatch, tmp_path):
    voice_file = tmp_path / "s2s_voice.txt"
    adapter = _adapter(monkeypatch, voice_file)

    adapter.set_startup_defaults(model="gemma-12b-huihui", voice="bf_emma")

    assert voice_file.read_text(encoding="utf-8").strip() == "bf_emma"


def test_a_non_kokoro_voice_is_not_published(monkeypatch, tmp_path):
    # Personas can carry local-backend ids the frontend's Kokoro cannot speak;
    # publishing one would replace a working voice with a broken one.
    voice_file = tmp_path / "s2s_voice.txt"
    adapter = _adapter(monkeypatch, voice_file)

    adapter.set_voice("voicebox:37fbcc53-71a9-497e-85ae-ba4bde795afd")

    assert not voice_file.exists()


def test_a_non_kokoro_voice_leaves_the_previous_one_in_place(monkeypatch, tmp_path):
    voice_file = tmp_path / "s2s_voice.txt"
    adapter = _adapter(monkeypatch, voice_file)
    adapter.set_voice("bm_george")

    adapter.set_voice("voicebox:37fbcc53-71a9-497e-85ae-ba4bde795afd")

    assert voice_file.read_text(encoding="utf-8").strip() == "bm_george"


def test_a_finished_agent_job_publishes_a_spoken_announcement(monkeypatch, tmp_path):
    announce = tmp_path / "s2s_announce.json"
    monkeypatch.setattr(cfg, "S2S_ANNOUNCE_FILE", str(announce))
    monkeypatch.setattr(cfg, "S2S_VOICE_FILE", str(tmp_path / "voice.txt"))
    events = []
    adapter = BrainSessionAdapter(PERSONAS[0], on_event=events.append)

    adapter._observe_event(
        {
            "type": "agent_job_summary",
            "agent": "code-puppy",
            "job_id": "job-1",
            "status": "finished",
            "result": "All 12 tests pass now.",
            "summary": "tests fixed",
        }
    )

    [data] = _queued_announcements(announce)
    assert data["id"] == "job-1:finished"
    assert data["text"].startswith("[[announce]]")
    assert "code-puppy" in data["text"]
    assert "All 12 tests pass now." in data["text"]
    # The GUI still receives the event unchanged.
    assert [event["type"] for event in events] == ["agent_job_summary"]


def test_a_failed_agent_job_is_still_announced(monkeypatch, tmp_path):
    announce = tmp_path / "s2s_announce.json"
    monkeypatch.setattr(cfg, "S2S_ANNOUNCE_FILE", str(announce))
    monkeypatch.setattr(cfg, "S2S_VOICE_FILE", str(tmp_path / "voice.txt"))
    adapter = BrainSessionAdapter(PERSONAS[0])

    adapter._observe_event(
        {
            "type": "agent_job_summary",
            "agent": "codex",
            "job_id": "job-2",
            "status": "failed",
            "result": "",
            "summary": "provider quota exhausted",
        }
    )

    [data] = _queued_announcements(announce)
    assert "failed" in data["text"]


def test_multiple_finished_jobs_queue_without_overwriting_results(monkeypatch, tmp_path):
    announce = tmp_path / "s2s_announce.json"
    monkeypatch.setattr(cfg, "S2S_ANNOUNCE_FILE", str(announce))
    monkeypatch.setattr(cfg, "S2S_VOICE_FILE", str(tmp_path / "voice.txt"))
    adapter = BrainSessionAdapter(PERSONAS[0])

    jobs = (("job-1", "First substantive answer."), ("job-2", "Second substantive answer."))
    for job_id, result in jobs:
        adapter._observe_event({
            "type": "agent_job_summary",
            "agent": "hermes",
            "job_id": job_id,
            "status": "done",
            "result": result,
            "summary": "completed",
        })

    queued = _queued_announcements(announce)
    assert [item["id"] for item in queued] == ["job-1:done", "job-2:done"]
    assert "First substantive answer." in queued[0]["text"]
    assert "Second substantive answer." in queued[1]["text"]


def test_other_events_do_not_touch_the_announce_file(monkeypatch, tmp_path):
    announce = tmp_path / "s2s_announce.json"
    monkeypatch.setattr(cfg, "S2S_ANNOUNCE_FILE", str(announce))
    monkeypatch.setattr(cfg, "S2S_VOICE_FILE", str(tmp_path / "voice.txt"))
    adapter = BrainSessionAdapter(PERSONAS[0])

    adapter._observe_event({"type": "transcript", "role": "user", "text": "hello"})

    assert not announce.exists()


def test_speak_text_does_not_forge_an_assistant_turn(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(cfg, "S2S_VOICE_FILE", str(tmp_path / "voice.txt"))
    adapter = BrainSessionAdapter(PERSONAS[0], on_event=events.append)

    adapter.speak_text("This is the selected text to speech voice.")

    assert [event["type"] for event in events] == ["sys"]


@pytest.mark.asyncio
async def test_brain_adapter_announces_ready_only_after_start_completes(monkeypatch, tmp_path):
    events = []
    brain_started = asyncio.Event()
    monkeypatch.setattr(cfg, "S2S_VOICE_FILE", str(tmp_path / "voice.txt"))
    # run() sweeps the announcement queue; keep that off the developer's own.
    monkeypatch.setattr(cfg, "S2S_ANNOUNCE_FILE", str(tmp_path / "announce.json"))
    adapter = BrainSessionAdapter(PERSONAS[0], on_event=events.append)

    async def start():
        brain_started.set()

    async def stop():
        return None

    monkeypatch.setattr(adapter._brain, "start", start)
    monkeypatch.setattr(adapter._brain, "stop", stop)
    task = asyncio.create_task(adapter.run())
    await asyncio.wait_for(brain_started.wait(), timeout=1)
    await asyncio.sleep(0)
    try:
        assert events[:2] == [
            {"type": "session", "state": "ready"},
            {"type": "sys", "text": "Brain mode ready; realtime audio is external."},
        ]
    finally:
        adapter.shutdown()
        await asyncio.wait_for(task, timeout=1)


def _envelope_app(speaking=False):
    app = WebVoiceApp.__new__(WebVoiceApp)
    app._avatar_audio = AvatarAudioEnvelopeHub()
    app._avatar_speaking = speaking
    app._lock = threading.RLock()
    app._published = []
    app._publish = app._published.append
    return app


def test_posted_envelope_reaches_avatar_subscribers():
    app = _envelope_app()

    app._ingest_avatar_envelope(
        {"rms": 0.4, "peak": 0.9, "voiced": True, "sample_rate": 24000, "channels": 1}
    )

    _, envelope, _ = app._avatar_audio.wait_after(0, timeout=0.1)
    assert envelope is not None
    assert (envelope.rms, envelope.peak, envelope.voiced) == (0.4, 0.9, True)
    assert app._published == [{"type": "speaking", "value": True}]


def test_silent_envelope_closes_the_mouth():
    app = _envelope_app(speaking=True)

    app._ingest_avatar_envelope({"rms": 0.0, "peak": 0.0, "voiced": False})

    _, envelope, _ = app._avatar_audio.wait_after(0, timeout=0.1)
    assert envelope is not None and envelope.voiced is False
    assert app._published == [{"type": "speaking", "value": False}]


def test_speaking_is_announced_once_per_transition_not_per_frame():
    # Envelopes arrive ~25x/second; one event per frame would flood the bounded
    # event log and evict real transcript history.
    app = _envelope_app()

    for _ in range(50):
        app._ingest_avatar_envelope({"rms": 0.4, "peak": 0.5})
    for _ in range(50):
        app._ingest_avatar_envelope({"rms": 0.0, "peak": 0.0})

    assert app._published == [
        {"type": "speaking", "value": True},
        {"type": "speaking", "value": False},
    ]


def test_envelopes_still_stream_while_speaking_state_holds():
    app = _envelope_app()

    app._ingest_avatar_envelope({"rms": 0.4, "peak": 0.5})
    seq, _, _ = app._avatar_audio.wait_after(0, timeout=0.1)
    app._ingest_avatar_envelope({"rms": 0.6, "peak": 0.7})

    _, envelope, _ = app._avatar_audio.wait_after(seq, timeout=0.1)
    assert envelope is not None and envelope.rms == 0.6


def test_malformed_envelope_is_dropped_rather_than_raising():
    app = _envelope_app()

    app._ingest_avatar_envelope({"rms": "loud"})

    _, envelope, _ = app._avatar_audio.wait_after(0, timeout=0.05)
    assert envelope is None
    assert app._published == []


def test_voice_file_is_swapped_in_atomically(monkeypatch, tmp_path):
    # A poller must never observe a truncated voice id.
    voice_file = tmp_path / "s2s_voice.txt"
    adapter = _adapter(monkeypatch, voice_file)

    adapter.set_voice("bm_daniel")
    adapter.set_voice("bf_lily")

    assert voice_file.read_text(encoding="utf-8").strip() == "bf_lily"
    assert list(tmp_path.glob("*.tmp")) == []


def test_tts_test_button_reports_that_brain_mode_cannot_speak(monkeypatch):
    monkeypatch.setattr(cfg, "RAP_MODE", "brain")
    app = WebVoiceApp.__new__(WebVoiceApp)
    app._status_payload = lambda: {}

    result = app._action_tts_test({})

    assert result["ok"] is False
    assert "realtime frontend" in result["message"]


@pytest.fixture
def envelope_server(monkeypatch):
    """Serve the real handler so the cross-repo POST contract is exercised."""
    monkeypatch.setattr(cfg, "RAP_MODE", "full")
    monkeypatch.setattr(cfg, "S2S_BRIDGE_API_KEY", "test-secret")
    app = WebVoiceApp()
    server = ThreadingHTTPServer(("127.0.0.1", 0), app._handler_class())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield app, server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _post_envelope(port, payload, *, key="test-secret"):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/avatar-envelope",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if key is not None:
        request.add_header("Authorization", f"Bearer {key}")
    return urllib.request.urlopen(request, timeout=5)


def _post_input_state(port, payload, *, key="test-secret"):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/input-state",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if key is not None:
        request.add_header("Authorization", f"Bearer {key}")
    return urllib.request.urlopen(request, timeout=5)


def test_frontend_wake_detection_reaches_dashboard_with_matching_generation(envelope_server):
    app, port = envelope_server
    app._voice_mode = "wake_word"
    app._s2s_mode_generation = 7

    response = _post_input_state(
        port,
        {
            "generation": 7,
            "phase": "wake_word_detected",
            "remaining_secs": 3.0,
            "passive": False,
        },
    )

    assert response.status == 200
    wake = app._status_payload()["wake"]
    assert wake["phase"] == "wake_word_detected"
    assert wake["remaining_secs"] == 3.0
    assert wake["passive"] is False


def test_stale_wake_detection_cannot_overwrite_newer_input_mode(envelope_server):
    app, port = envelope_server
    app._voice_mode = "free_talk"
    app._s2s_mode_generation = 8

    with pytest.raises(urllib.error.HTTPError) as caught:
        _post_input_state(port, {"generation": 7, "phase": "wake_word_detected"})

    assert caught.value.code == 409
    assert app._status_payload()["voiceMode"] == "free_talk"
    assert app._status_payload()["wake"]["phase"] == "idle"


def test_frontend_envelope_post_reaches_the_avatar_hub(envelope_server):
    app, port = envelope_server

    # Exactly the payload scripts/listen_and_play_realtime.py sends.
    response = _post_envelope(
        port,
        {"rms": 0.42, "peak": 0.88, "voiced": True, "sample_rate": 24000, "channels": 1},
    )

    assert response.status == 200
    _, envelope, _ = app._avatar_audio.wait_after(0, timeout=1.0)
    assert envelope is not None
    assert envelope.rms == pytest.approx(0.42)
    assert envelope.sample_rate == 24000


def test_envelope_post_rejects_a_wrong_or_missing_key(envelope_server):
    _, port = envelope_server

    for key in (None, "", "wrong-key"):
        with pytest.raises(urllib.error.HTTPError) as caught:
            _post_envelope(port, {"rms": 0.5}, key=key)
        assert caught.value.code == 401


def test_stack_shutdown_requires_the_bridge_key_and_stops_the_server(monkeypatch):
    monkeypatch.setattr(cfg, "RAP_MODE", "brain")
    monkeypatch.setattr(cfg, "S2S_BRIDGE_API_KEY", "test-secret")
    app = WebVoiceApp()
    server = ThreadingHTTPServer(("127.0.0.1", 0), app._handler_class())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def post(key):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/stack-shutdown",
            data=b"{}",
            headers={"Authorization": f"Bearer {key}"},
            method="POST",
        )
        return urllib.request.urlopen(request, timeout=5)

    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            post("wrong-key")
        assert caught.value.code == 401
        assert thread.is_alive()

        response = post("test-secret")
        assert response.status == 200
        assert json.loads(response.read()) == {"ok": True}
        thread.join(timeout=2)
        assert not thread.is_alive()
    finally:
        server.shutdown()
        server.server_close()


def test_stack_shutdown_runs_the_real_web_app_cleanup_path(monkeypatch):
    monkeypatch.setattr(cfg, "RAP_MODE", "brain")
    monkeypatch.setattr(cfg, "S2S_BRIDGE_PORT", 0)
    monkeypatch.setattr(cfg, "S2S_BRIDGE_API_KEY", "test-secret")
    stopped = threading.Event()
    calls = []

    class FakeBrainSession:
        def build(self):
            calls.append("build")

        async def run(self):
            while not stopped.is_set():
                await asyncio.sleep(0.01)

        def shutdown(self):
            calls.append("session-shutdown")
            stopped.set()

    fake_session = FakeBrainSession()
    monkeypatch.setattr(WebVoiceApp, "_new_session", lambda _self: fake_session)
    monkeypatch.setattr(web_gui.webbrowser, "open", lambda _url: None)
    monkeypatch.setattr(web_gui.process_guard, "install_close_handler", lambda _callback: None)

    real_server = ThreadingHTTPServer
    servers = queue.Queue()

    def server_factory(address, handler):
        server = real_server(address, handler)
        servers.put(server)
        return server

    monkeypatch.setattr(web_gui, "ThreadingHTTPServer", server_factory)
    app = WebVoiceApp()
    app._event_pump = lambda: app._stop.wait()
    app._health_poller = lambda: app._stop.wait()
    app._unload_ollama_models = lambda **_kwargs: calls.append("models-unloaded")
    thread = threading.Thread(target=app.run)
    thread.start()
    server = servers.get(timeout=2)

    request = urllib.request.Request(
        f"http://127.0.0.1:{server.server_address[1]}/api/stack-shutdown",
        data=b"{}",
        headers={"Authorization": "Bearer test-secret"},
        method="POST",
    )
    response = urllib.request.urlopen(request, timeout=5)
    assert response.status == 200
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert "session-shutdown" in calls
    assert "models-unloaded" in calls


def test_persona_switch_carries_its_voice_and_model(monkeypatch, tmp_path):
    # Brain mode has no TTS/LLM client to reconfigure, so a persona swap only
    # lands if its voice and model are pushed through explicitly.
    voice_file = tmp_path / "s2s_voice.txt"
    adapter = _adapter(monkeypatch, voice_file)
    adapter.set_startup_defaults(model="boot-model", voice="af_heart")

    jarvis = next(persona for persona in PERSONAS if persona.name == "Jarvis")
    adapter.set_persona(jarvis)

    assert voice_file.read_text(encoding="utf-8").strip() == jarvis.voice
    assert adapter._brain._model_override == jarvis.model_name(cfg.LLM_MODEL)


def _pending_brain(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "S2S_VOICE_FILE", str(tmp_path / "v.txt"))
    adapter = BrainSessionAdapter(PERSONAS[0])
    brain = adapter._brain
    brain._pending_confirmations["tok-a"] = ("claude", "delete the archive", None, "state-changing")
    brain._pending_confirmations["tok-b"] = ("codex", "list the files", None, "state-changing")
    return adapter, brain


@pytest.mark.asyncio
async def test_button_approval_dispatches_the_named_task_not_the_newest(monkeypatch, tmp_path):
    # Two prompts pending: approving the older one must not run the newer.
    adapter, brain = _pending_brain(monkeypatch, tmp_path)
    started = []

    async def record(agent, task, cwd):
        started.append((agent, task))

    monkeypatch.setattr(brain._bridge, "start", record)

    reply = brain.resolve_confirmation("tok-a", "approve")
    await asyncio.sleep(0)

    assert "claude" in reply
    assert list(brain._pending_confirmations) == ["tok-b"]
    assert len(started) == 1
    assert started[0][0] == "claude"
    assert "delete the archive" in started[0][1]


def test_denial_resolves_only_its_own_confirmation(monkeypatch, tmp_path):
    adapter, brain = _pending_brain(monkeypatch, tmp_path)

    reply = brain.resolve_confirmation("tok-b", "deny")

    assert "codex" in reply
    assert list(brain._pending_confirmations) == ["tok-a"]


def test_resolving_an_unknown_token_is_a_no_op(monkeypatch, tmp_path):
    adapter, brain = _pending_brain(monkeypatch, tmp_path)

    assert brain.resolve_confirmation("tok-missing", "approve") is None
    assert list(brain._pending_confirmations) == ["tok-a", "tok-b"]


def test_spoken_reply_still_answers_the_newest_prompt(monkeypatch, tmp_path):
    # Voice carries no target, so most-recent remains the right convention.
    adapter, brain = _pending_brain(monkeypatch, tmp_path)

    reply = brain._maybe_consume_confirmation("no")

    assert "codex" in reply
    assert list(brain._pending_confirmations) == ["tok-a"]


class _ExplodingSession:
    """Stands in for a brain whose LLM backend is unreachable."""

    def complete_text(self, text, timeout=180.0):
        raise RuntimeError("Cannot connect to host localhost:11434")


def test_brain_failure_answers_with_an_error_instead_of_hanging(envelope_server, monkeypatch):
    # A handler that raises past do_POST sends no response at all, so the
    # realtime frontend blocks until its own timeout and loses the turn.
    app, port = envelope_server
    monkeypatch.setattr(app, "_session", _ExplodingSession())

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer test-secret"},
        method="POST",
    )

    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=10)

    assert caught.value.code == 500
    body = json.loads(caught.value.read())
    assert "11434" in body["error"]["message"]


class _OfflineModelSession:
    """Stands in for a brain whose Ollama host is not accepting connections."""

    def complete_text(self, text, timeout=180.0):
        raise brain_module.LLMUnavailable("http://localhost:11434/v1 is not reachable")

    def stream_text(self, text, timeout=180.0):
        raise brain_module.LLMUnavailable("http://localhost:11434/v1 is not reachable")
        yield ""  # pragma: no cover - generator marker


def test_offline_model_answers_with_a_speakable_reason(envelope_server, monkeypatch):
    # A 500 is silence in a voice frontend. The user should hear what to fix.
    app, port = envelope_server
    monkeypatch.setattr(app, "_session", _OfflineModelSession())

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer test-secret"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.loads(response.read())

    assert response.status == 200
    assert "Ollama" in body["choices"][0]["message"]["content"]


def test_offline_model_closes_a_started_stream_with_the_same_reason(
    envelope_server, monkeypatch
):
    # Headers are already out by then, so the reason has to arrive as content
    # and the stream still has to terminate, or the frontend waits forever.
    app, port = envelope_server
    monkeypatch.setattr(cfg, "S2S_BRIDGE_STREAMING", True)
    monkeypatch.setattr(app, "_session", _OfflineModelSession())

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps(
            {"messages": [{"role": "user", "content": "hi"}], "stream": True}
        ).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer test-secret"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode("utf-8")

    assert "Ollama" in body
    assert body.rstrip().endswith("data: [DONE]")


def test_malformed_envelope_body_does_not_kill_the_connection(envelope_server):
    _, port = envelope_server

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/avatar-envelope",
        data=b"not json at all",
        headers={"Content-Type": "application/json", "Authorization": "Bearer test-secret"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.status == 200


def test_a_failing_status_query_answers_with_an_error(envelope_server, monkeypatch):
    app, port = envelope_server

    def explode():
        raise RuntimeError("status payload blew up")

    monkeypatch.setattr(app, "_status_payload", explode)

    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=10)

    assert caught.value.code == 500


class _RecordingBrain:
    def __init__(self):
        self.calls = []

    async def complete(self, text, *, llm_content=None):
        self.calls.append((text, llm_content))
        return "ok"


def test_typed_prompt_shows_the_request_not_the_scaffold(monkeypatch, tmp_path):
    # The bundle render is for the model. Putting it in the transcript also feeds
    # it to the router and memory, which reason about what the user meant.
    monkeypatch.setattr(cfg, "S2S_VOICE_FILE", str(tmp_path / "v.txt"))
    adapter = BrainSessionAdapter(PERSONAS[0])
    brain = _RecordingBrain()
    adapter._brain = brain
    submitted = []
    monkeypatch.setattr(adapter, "_submit", lambda coro: submitted.append(coro))

    bundle = MultimodalPromptBundle(user_id="u")
    bundle.set_final_instruction("hello")
    adapter.send_multimodal_prompt(bundle)

    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(submitted[0])
    display, llm_content = brain.calls[0]
    assert display == "hello"
    assert "## Instruction to Agent" not in display
    assert "## Instruction to Agent" in llm_content
    assert "## Final User Request\nhello" in llm_content


def test_empty_bundle_sends_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "S2S_VOICE_FILE", str(tmp_path / "v.txt"))
    adapter = BrainSessionAdapter(PERSONAS[0])
    submitted = []
    monkeypatch.setattr(adapter, "_submit", lambda coro: submitted.append(coro))

    adapter.send_multimodal_prompt(MultimodalPromptBundle(user_id="u"))

    assert submitted == []


def test_announcements_from_a_previous_run_are_not_spoken_at_startup(monkeypatch, tmp_path):
    # The frontend deletes each entry as it speaks it, so anything still queued
    # belongs to a session that ended -- narrating it now reports finished work
    # as if it had just landed.
    announce = tmp_path / "announce.json"
    queue_dir = announce.with_suffix(f"{announce.suffix}.queue")
    queue_dir.mkdir(parents=True)
    (queue_dir / "1-job-9_done.json").write_text(
        json.dumps({"id": "job-9:done", "text": "stale"}), encoding="utf-8"
    )
    monkeypatch.setattr(cfg, "S2S_ANNOUNCE_FILE", str(announce))
    adapter = BrainSessionAdapter(PERSONAS[0])

    adapter._discard_stale_announcements()

    assert list(queue_dir.glob("*.json")) == []


def test_announcements_published_by_this_run_survive(monkeypatch, tmp_path):
    announce = tmp_path / "announce.json"
    monkeypatch.setattr(cfg, "S2S_ANNOUNCE_FILE", str(announce))
    adapter = BrainSessionAdapter(PERSONAS[0])
    adapter._discard_stale_announcements()

    adapter._publish_announcement({"job_id": "job-1", "status": "done", "result": "all set"})

    assert len(_queued_announcements(announce)) == 1


def test_a_stream_announces_itself_before_the_model_speaks(envelope_server, monkeypatch):
    # The frontend times its latency budget from the first chunk. Withholding it
    # until the first sentence lands left 54 of 55 recorded turns with no
    # measurable response start at all.
    app, port = envelope_server
    monkeypatch.setattr(cfg, "S2S_BRIDGE_STREAMING", True)

    class SlowSession:
        def complete_text(self, text, timeout=180.0):
            return "late"

        def stream_text(self, text, timeout=180.0):
            yield "First sentence."

    monkeypatch.setattr(app, "_session", SlowSession())
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps({"messages": [{"role": "user", "content": "hi"}], "stream": True}).encode(),
        headers={"Content-Type": "application/json", "Authorization": "Bearer test-secret"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=10) as response:
        chunks = [
            json.loads(line[len("data: ") :])
            for line in response.read().decode().splitlines()
            if line.startswith("data: ") and not line.endswith("[DONE]")
        ]

    # First chunk carries the role and no text; the text follows in its own.
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert chunks[1]["choices"][0]["delta"] == {"content": "First sentence."}
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"

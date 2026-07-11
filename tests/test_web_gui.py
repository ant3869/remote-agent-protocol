import inspect
import json
from pathlib import Path

from remote_agent_protocol import (
    agent_bridge,
    app_state,
    multimodal_prompt,
    persona_config,
    web_gui,
)
from remote_agent_protocol import config as cfg
from remote_agent_protocol.web_gui import WebVoiceApp, _bundle_from_payload

WEB_APP = Path("remote_agent_protocol/web_app")


def test_web_shell_uses_operational_graphite_design_tokens():
    css = (WEB_APP / "styles.css").read_text(encoding="utf-8")

    assert "--surface-app: #09090b" in css
    assert "--surface-panel: #111113" in css
    assert "--status-success: #22c55e" in css
    assert "--status-warning: #eab308" in css
    assert "--status-error: #ef4444" in css
    assert "--accent-agent: #a78bfa" in css
    assert "--accent-action: #60a5fa" in css
    assert "--accent-delegate: #fb923c" in css


def test_web_shell_restores_chat_and_subtle_context_drawer():
    html = (WEB_APP / "index.html").read_text(encoding="utf-8")

    assert "Transcription chat" in html
    assert 'id="contextDrawer" class="context-drawer hidden"' in html
    assert "MLX Whisper Setup" in html
    assert "Memory tree" in html
    assert 'data-view="agents"' in html
    assert 'id="agentsView" class="view"' in html
    assert 'data-view="personas"' in html
    assert 'id="personasView" class="view"' in html
    assert 'id="settingsVoiceModeSelect"' in html
    assert 'id="settingsModelSelect"' in html
    assert 'id="settingsTtsProviderSelect"' in html
    assert 'id="settingsCoquiModelSelect"' in html
    assert 'id="settingsTestVoiceBtn"' in html
    assert 'id="settingsRebootBtn"' in html
    assert 'id="settingsWakeCountdown"' in html
    assert 'id="settingsWakeDetector"' in html


def test_memory_page_uses_real_memory_scopes_and_actions():
    html = (WEB_APP / "index.html").read_text(encoding="utf-8")
    script = (WEB_APP / "app.js").read_text(encoding="utf-8")
    css = (WEB_APP / "styles.css").read_text(encoding="utf-8")

    assert 'data-memory-tab="transcript"' in html
    assert 'data-memory-tab="knowledge"' in html
    assert 'data-memory-tab="pinned"' in html
    assert "Meals" not in html
    assert "Creative and Personal" not in html
    assert 'data-memory-filter="short"' in html
    assert 'data-memory-filter="semantic"' in html
    assert 'data-memory-filter="pinned"' in html
    for marker in [
        "memoryPinInput",
        "memoryPinBtn",
        "memoryDeleteBtn",
        "memoryForgetShortBtn",
        "memoryForgetSemanticBtn",
    ]:
        assert marker in html

    assert "function normalizeMemoryRow" in script
    assert "function isPinnedMemory" in script
    assert 'post("memory_add"' in script
    assert 'post("memory_delete"' in script
    assert 'post("memory_forget_short"' in script
    assert 'post("memory_forget_semantic"' in script
    assert ".memory-actions" in css


def test_web_shell_has_no_obsolete_cyan_first_motif():
    combined = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in (WEB_APP / "index.html", WEB_APP / "styles.css", WEB_APP / "app.js")
    )

    assert "--accent-cyan" not in combined
    assert "#22d3ee" not in combined
    assert "radial-gradient" not in combined


def test_web_shell_has_a_global_command_palette():
    html = (WEB_APP / "index.html").read_text(encoding="utf-8")
    script = (WEB_APP / "app.js").read_text(encoding="utf-8")
    css = (WEB_APP / "styles.css").read_text(encoding="utf-8")

    assert 'id="paletteOpenBtn"' in html
    assert 'id="commandPalette"' in html
    assert "Ctrl K" in html

    assert "function openCommandPalette" in script
    assert "function renderCommandPalette" in script
    assert 'event.key.toLowerCase() === "k"' in script
    for label in [
        "Control Center",
        "Agents",
        "Personas",
        "Memory",
        "Settings",
        "focus message",
        "toggle mic",
        "new chat",
        "refresh memory",
        "export diagnostics",
        "start Ollama",
        "free VRAM",
    ]:
        assert label.lower() in script.lower()

    assert ".command-palette" in css
    assert ".palette-result" in css
    assert "@media (max-width: 760px)" in css
    palette_mobile_rule = css.split("@media (max-width: 760px)", 1)[1]
    assert ".command-palette" in palette_mobile_rule


def test_web_shell_uses_dense_mission_control_structure():
    html = (WEB_APP / "index.html").read_text(encoding="utf-8")

    assert 'class="control-grid"' in html
    assert 'class="activity-panel"' in html
    assert 'class="system-strip"' in html
    assert 'class="assistant-control"' in html
    assert 'class="hero-panel"' not in html
    assert 'class="orb"' not in html


def test_web_shell_maps_live_status_to_semantic_tones():
    script = (WEB_APP / "app.js").read_text(encoding="utf-8")

    assert "function setPillTone" in script
    assert 'setPillTone("sessionPill"' in script
    assert 'setPillTone("ollamaPill"' in script
    assert 'setPillTone("ttsPill"' in script


def test_direct_web_launcher_uses_process_guard(monkeypatch):
    calls = []

    monkeypatch.setattr(
        web_gui.process_guard, "acquire_single_instance_lock", lambda: calls.append("lock") or True
    )
    monkeypatch.setattr(
        web_gui.process_guard, "close_previous_instance", lambda: calls.append("close")
    )
    monkeypatch.setattr(web_gui.process_guard, "write_lock", lambda: calls.append("write"))
    monkeypatch.setattr(web_gui.process_guard, "release_lock", lambda: calls.append("release"))

    class FakeApp:
        def run(self):
            calls.append("run")

    monkeypatch.setattr(web_gui, "WebVoiceApp", FakeApp)

    web_gui.run()

    assert calls == ["lock", "close", "write", "run", "release"]


def test_direct_web_launcher_releases_lock_after_failure(monkeypatch):
    calls = []

    monkeypatch.setattr(
        web_gui.process_guard, "acquire_single_instance_lock", lambda: calls.append("lock") or True
    )
    monkeypatch.setattr(
        web_gui.process_guard, "close_previous_instance", lambda: calls.append("close")
    )
    monkeypatch.setattr(web_gui.process_guard, "write_lock", lambda: calls.append("write"))
    monkeypatch.setattr(web_gui.process_guard, "release_lock", lambda: calls.append("release"))

    class FakeApp:
        def run(self):
            calls.append("run")
            raise RuntimeError("boom")

    monkeypatch.setattr(web_gui, "WebVoiceApp", FakeApp)

    try:
        web_gui.run()
    except RuntimeError:
        pass

    assert calls == ["lock", "close", "write", "run", "release"]


def test_direct_web_launcher_refuses_a_second_instance(monkeypatch):
    calls = []

    monkeypatch.setattr(web_gui.process_guard, "acquire_single_instance_lock", lambda: False)
    monkeypatch.setattr(
        web_gui.process_guard, "close_previous_instance", lambda: calls.append("close")
    )
    monkeypatch.setattr(web_gui.process_guard, "write_lock", lambda: calls.append("write"))

    class FakeApp:
        def run(self):
            calls.append("run")

    monkeypatch.setattr(web_gui, "WebVoiceApp", FakeApp)

    try:
        web_gui.run()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("expected SystemExit")

    # A live sibling's PID must be left alone -- close/write/run never happen.
    assert calls == []


def test_mobile_shell_contains_horizontal_navigation():
    css = (WEB_APP / "styles.css").read_text(encoding="utf-8")

    assert ".app-shell { grid-template-columns: minmax(0, 1fr); }" in css
    assert ".sidebar, .workspace { min-width: 0; width: 100%; }" in css
    assert ".nav-links { display: flex; max-width: 100vw; overflow-x: auto;" in css


def test_payload_builds_multimodal_bundle_for_existing_session_api():
    bundle = _bundle_from_payload(
        {
            "prompt": "Summarize this",
            "notes": "Focus on risks",
            "voiceDraft": "Look at the attached file",
            "attachments": [{"reference": "https://example.test/report", "note": "source"}],
        },
        multimodal_prompt.VOICE_MODE_FREE_TALK,
    )

    assert bundle.voice.transcript == "Look at the attached file"
    assert bundle.text.raw_text == "Focus on risks"
    assert bundle.final_user_instruction == "Summarize this"
    assert bundle.attachments[0].type == multimodal_prompt.ATTACHMENT_LINK
    assert "attachment" in bundle.context_signals


def test_web_shell_uses_backend_transcript_as_single_message_source():
    script = (WEB_APP / "app.js").read_text(encoding="utf-8")
    send_body = script.split("function sendMessage()", 1)[1].split("function delegateMessage()", 1)[
        0
    ]

    assert 'addMessage("user"' not in send_body
    assert "if (state.sending) return" in send_body


def test_agent_lifecycle_is_exposed_in_status_payload():
    app = WebVoiceApp()
    app._fold_event(
        {
            "type": "agent_job",
            "event": "started",
            "job_id": "job-7",
            "agent": "hermes",
            "status": "running",
            "state": "started",
            "action": "Starting",
        }
    )

    status = app._status_payload()

    assert status["activeAgentCount"] == 1
    assert status["agentStates"]["hermes"]["action"] == "Starting"

    app._fold_event(
        {
            "type": "agent_job",
            "event": "finished",
            "job_id": "job-7",
            "agent": "hermes",
            "status": "done",
            "state": "completed",
            "action": "Complete",
        }
    )

    assert app._status_payload()["activeAgentCount"] == 0


def test_stop_app_shuts_session_joins_threads_and_unloads_models(monkeypatch):
    app = WebVoiceApp()
    events = []
    threads = [FakeThread(), FakeThread(), FakeThread()]
    app._session = FakeSession(events)
    app._thread, app._event_thread, app._health_thread = threads
    monkeypatch.setattr(
        web_gui.dashboard, "stop_loaded_models", lambda host: events.append(host) or 2
    )

    app._stop_app()

    assert app._stop.is_set()
    assert events == ["shutdown", cfg.OLLAMA_HOST]
    assert all(thread.joined for thread in threads)
    assert app._thread is None
    assert app._event_thread is None
    assert app._health_thread is None


def test_reboot_session_unloads_models_before_new_session(monkeypatch):
    app = WebVoiceApp()
    events = []
    app._session = FakeSession(events)
    app._thread = FakeThread()
    monkeypatch.setattr(
        web_gui.dashboard, "stop_loaded_models", lambda host: events.append(host) or 1
    )
    monkeypatch.setattr(app, "_new_session", lambda: FakeSession(events))
    monkeypatch.setattr(app, "_start_session_thread", lambda: events.append("start"))

    app._reboot_session()

    assert events == ["shutdown", cfg.OLLAMA_HOST, "muted:True", "start"]


def test_web_memory_actions_call_session_methods():
    app = WebVoiceApp()
    session = FakeMemorySession()
    app._session = session

    assert app._action("memory_add", {"text": "  User likes dashboards.  "})["ok"] is True
    assert app._action("memory_delete", {"id": "mem-7"})["ok"] is True
    assert app._action("memory_forget_short", {})["ok"] is True
    assert app._action("memory_forget_semantic", {})["ok"] is True

    assert session.calls == [
        ("add", "User likes dashboards."),
        ("delete", "mem-7"),
        ("forget_short",),
        ("forget_semantic",),
    ]


def test_web_memory_add_rejects_empty_text():
    app = WebVoiceApp()
    app._session = FakeMemorySession()

    result = app._action("memory_add", {"text": "   "})

    assert result["ok"] is False
    assert "empty" in result["error"]


class FakeSession:
    def __init__(self, events):
        self.events = events

    def shutdown(self):
        self.events.append("shutdown")

    def set_muted(self, muted):
        self.events.append(f"muted:{muted}")


class FakeMemorySession:
    def __init__(self):
        self.calls = []

    def add_semantic_memory(self, text):
        self.calls.append(("add", text))

    def delete_semantic_memory(self, memory_id):
        self.calls.append(("delete", memory_id))

    def forget_short_term_memory(self):
        self.calls.append(("forget_short",))

    def forget_semantic_memory(self):
        self.calls.append(("forget_semantic",))

    def default_agent_backend(self):
        return "code-puppy"

    def agent_backends(self):
        return []


class FakeThread:
    def __init__(self):
        self.joined = False

    def join(self, timeout=None):
        self.joined = True


def test_agents_page_has_trace_history_and_prompt_editor():
    html = (WEB_APP / "index.html").read_text(encoding="utf-8")
    script = (WEB_APP / "app.js").read_text(encoding="utf-8")

    for marker in [
        "agentRoster",
        "agentJobList",
        "agentDetail",
        "agentDetailTask",
        "agentDetailResult",
        "agentDetailNow",
        "agentMoveTimeline",
        "agentMoveCount",
        "agentPromptEditors",
        "agentPromptSaveBtn",
    ]:
        assert marker in html

    assert 'fetch("/api/agents")' in script
    assert "loadAgentsPage" in script
    assert "storeAgentEvent" in script
    assert 'post("agent_prompts_save"' in script


def test_agents_page_explains_agent_moves():
    script = (WEB_APP / "app.js").read_text(encoding="utf-8")
    css = (WEB_APP / "styles.css").read_text(encoding="utf-8")

    assert "function describeAgentMove" in script
    assert "function fallbackAgentMoves" in script
    assert "last_completed_step" in script
    assert "Current move" in script
    assert "Move timeline" in (WEB_APP / "index.html").read_text(encoding="utf-8")
    assert ".agent-move-timeline" in css
    assert ".agent-now-card" in css


def test_agents_payload_preserves_live_output_lines_and_prompts(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "APP_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(cfg, "AGENT_HISTORY_FILE", "")
    app = WebVoiceApp()

    app._publish(
        {
            "type": "agent_job",
            "event": "started",
            "job_id": "job-1",
            "agent": "code-puppy",
            "task": "find logs",
            "status": "running",
        }
    )
    app._publish(
        {
            "type": "agent_job",
            "event": "output",
            "job_id": "job-1",
            "agent": "code-puppy",
            "line": "Calling Search",
            "status": "running",
        }
    )
    app._publish(
        {
            "type": "agent_job",
            "event": "finished",
            "job_id": "job-1",
            "agent": "code-puppy",
            "status": "done",
            "result": "Found the log entries.",
        }
    )

    payload = app._agents_payload()

    assert payload["jobs"][0]["lines"] == ["Calling Search"]
    assert payload["jobs"][0]["result"] == "Found the log entries."
    assert "statusProtocol" in payload["prompts"]
    assert payload["prompts"]["scopePreamble"]["required"] == ["{cwd}"]


def test_agent_prompt_save_persists_and_updates_runtime(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(cfg, "APP_STATE_FILE", str(state_path))
    original = _agent_prompt_snapshot()
    try:
        app = WebVoiceApp()
        result = app._action(
            "agent_prompts_save",
            {
                "prompts": {
                    "scopePreamble": "[Scope {cwd}: stay focused.]",
                    "statusProtocol": "CUSTOM STATUS",
                    "dispatchAck": "Agent {agent} is running: {task}",
                }
            },
        )
        loaded = app_state.load_state(state_path)

        assert result["ok"] is True
        assert loaded.agent_prompts["statusProtocol"] == "CUSTOM STATUS"
        assert cfg.AGENT_SCOPE_PREAMBLE == "[Scope {cwd}: stay focused.]"
        assert agent_bridge.status_protocol() == "CUSTOM STATUS"
        assert app._session._bridge._scope_preamble == "[Scope {cwd}: stay focused.]"
    finally:
        _restore_agent_prompts(original)


def test_agent_prompt_save_requires_template_placeholders(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "APP_STATE_FILE", str(tmp_path / "state.json"))
    app = WebVoiceApp()

    result = app._action(
        "agent_prompts_save",
        {"prompts": {"dispatchAck": "Agent {agent} is running."}},
    )

    assert result["ok"] is False
    assert "{task}" in result["error"]


def test_web_app_cold_start_defaults_to_butler_and_muted(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "APP_STATE_FILE", str(tmp_path / "missing-state.json"))

    app = WebVoiceApp()
    status = app._status_payload()

    assert status["persona"] == "Butler"
    assert status["muted"] is True
    assert app._session._muted is True


def test_agent_finished_event_shows_no_answer_when_result_is_empty():
    script = (WEB_APP / "app.js").read_text(encoding="utf-8")

    assert 'event.result || event.summary || "No answer returned."' in script
    assert 'event.result || "Finished."' not in script


def test_status_poll_does_not_reload_full_session_snapshot():
    app = WebVoiceApp()
    app._session.export_snapshot = lambda: (_ for _ in ()).throw(
        AssertionError("status polling must stay lightweight")
    )

    status = app._status_payload()

    assert status["model"]
    assert status["voice"] == app._voice


def test_wake_event_scores_do_not_poison_event_polling():
    class ScalarScore:
        def item(self):
            return 0.73

    app = WebVoiceApp()
    app._publish({"type": "wake", "state": "awake", "score": ScalarScore()})

    payload = app._events_after(0)

    assert payload["events"][0]["score"] == 0.73
    json.dumps(payload)


def test_status_payload_exposes_wake_word_state():
    app = WebVoiceApp()
    app._voice_mode = "wake_word"
    app._publish(
        {
            "type": "wake",
            "state": "awake",
            "phase": "wake_word_detected",
            "model": "hey_jarvis",
            "model_path": "wake_word/wake_models/hey_jarvis.onnx",
            "persona": "Jarvis",
            "window_secs": 3.0,
            "remaining_secs": 2.5,
            "detector_loaded": True,
            "passive": False,
        }
    )

    wake = app._status_payload()["wake"]

    assert wake["phase"] == "wake_word_detected"
    assert wake["model"] == "hey_jarvis"
    assert wake["model_path"].endswith("hey_jarvis.onnx")
    assert wake["detector_loaded"] is True


def test_poll_connection_errors_are_deduped_and_backed_off():
    script = (WEB_APP / "app.js").read_text(encoding="utf-8")

    assert "connectionLost: false" in script
    assert "if (!state.connectionLost)" in script
    assert "UI connection restored." in script
    assert "state.connectionLost ? 2000 : 450" in script


def test_settings_page_controls_call_existing_actions():
    script = (WEB_APP / "app.js").read_text(encoding="utf-8")

    assert 'settingsPersonaSelect").addEventListener("change"' in script
    assert 'post("persona", { name: event.target.value })' in script
    assert 'settingsVoiceModeSelect").addEventListener("change"' in script
    assert 'post("voice_mode", { mode: event.target.value })' in script
    assert 'settingsModelSelect").addEventListener("change"' in script
    assert 'settingsVoiceSelect").addEventListener("change"' in script
    assert "settingsTtsProviderSelect" in script
    assert 'post("tts"' in script
    assert 'post("tts_test"' in script
    assert 'settingsDiagnosticsBtn").addEventListener("click"' in script
    assert 'settingsRebootBtn").addEventListener("click"' in script


def test_model_and_voice_actions_persist_app_defaults(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(cfg, "APP_STATE_FILE", str(state_path))
    app = WebVoiceApp()

    model_result = app._action("model", {"model": "gemma-test"})
    voice_result = app._action("voice", {"voice": "af_sky"})
    loaded = app_state.load_state(state_path)

    assert model_result["ok"] is True
    assert voice_result["ok"] is True
    assert loaded.model == "gemma-test"
    assert loaded.voice == "af_sky"


def test_tts_action_persists_coqui_defaults(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(cfg, "APP_STATE_FILE", str(state_path))
    app = WebVoiceApp()
    calls = []
    app._session.set_tts = lambda **kwargs: calls.append(kwargs)

    result = app._action(
        "tts",
        {
            "provider": "coqui",
            "model": "tts_models/en/ljspeech/vits",
            "speaker": "speaker-a",
            "language": "en",
            "device": "cpu",
        },
    )
    loaded = app_state.load_state(state_path)

    assert result["ok"] is True
    assert loaded.tts_provider == "coqui"
    assert loaded.coqui_model == "tts_models/en/ljspeech/vits"
    assert loaded.coqui_speaker == "speaker-a"
    assert calls[-1]["voice_backend"] == "coqui"
    assert calls[-1]["tts_options"]["language"] == "en"


def test_tts_provider_payload_includes_coqui():
    app = WebVoiceApp()
    status = app._status_payload()

    assert "coqui" in [row["id"] for row in status["tts"]["providers"]]
    assert "models" in status["tts"]["coqui"]


def test_test_speak_button_calls_session_tts(monkeypatch):
    app = WebVoiceApp()
    spoken = []
    app._session.speak_text = spoken.append

    result = app._action("tts_test", {})

    assert result["ok"] is True
    assert spoken == ["This is the selected text to speech voice."]


def test_saved_model_and_voice_defaults_are_loaded_at_boot(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    app_state.save_state(
        state_path,
        app_state.AppState(
            persona="Jess",
            tool_user="mock",
            voice_mode="wake_word",
            model="gemma-test",
            voice="af_sky",
            tts_provider="coqui",
            coqui_model="tts_models/en/ljspeech/vits",
            coqui_speaker="speaker-a",
            coqui_language="en",
            coqui_device="cpu",
        ),
    )
    monkeypatch.setattr(cfg, "APP_STATE_FILE", str(state_path))

    app = WebVoiceApp()

    assert app._model == "gemma-test"
    assert app._voice == "speaker-a"
    assert app._voice_mode == "wake_word"
    assert app._session._startup_model == "gemma-test"
    assert app._session._startup_voice == "speaker-a"
    assert app._session._startup_tts_backend == "coqui"
    assert app._session._startup_tts_model == "tts_models/en/ljspeech/vits"


def test_tool_user_action_survives_session_rebuild(monkeypatch, tmp_path):
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(cfg, "APP_STATE_FILE", str(state_path))
    app = WebVoiceApp()

    result = app._action("tool_user", {"backend": "mock"})
    rebuilt = app._new_session()

    assert result["status"]["toolUser"] == "mock"
    assert app_state.load_state(state_path).tool_user == "mock"
    assert app._app_state.tool_user == "mock"
    assert rebuilt.default_agent_backend() == "mock"


def test_persona_page_has_full_editor_and_actions():
    html = (WEB_APP / "index.html").read_text(encoding="utf-8")
    script = (WEB_APP / "app.js").read_text(encoding="utf-8")

    for marker in [
        "personaEditName",
        "personaEditDescription",
        "personaEditPrompt",
        "personaEditModel",
        "personaEditVoice",
        "personaEditToolUser",
        "personaMemoryMode",
        "duplicatePersonaBtn",
        "deletePersonaBtn",
        "savePersonaBtn",
    ]:
        assert marker in html

    assert 'post("persona_save"' in script
    assert 'post("persona_create"' in script
    assert 'post("persona_duplicate"' in script
    assert 'post("persona_delete"' in script
    assert 'post("persona", { name: persona.name })' in script


def test_status_payload_exposes_complete_persona_records():
    app = WebVoiceApp()

    persona = app._status_payload()["personas"][0]

    assert persona["name"]
    assert "systemPrompt" in persona
    assert "voiceBackend" in persona
    assert "toolUser" in persona
    assert persona["memory"]["canWrite"] is True
    assert persona["advanced"]["contextHandling"]


def test_persona_save_validates_required_fields():
    app = WebVoiceApp()

    result = app._action(
        "persona_save",
        {"originalName": "Jess", "name": "Jess", "systemPrompt": "", "voice": "af_heart"},
    )

    assert result["ok"] is False
    assert "System prompt" in result["error"]


def test_persona_save_uses_existing_persistence_and_updates_active(monkeypatch):
    app, saved = _web_app_with_persona_store(monkeypatch)
    app._persona = app._persona_by_name("Jess")
    app._model = app._persona.model_name("default")
    app._voice = app._persona.voice

    result = app._action(
        "persona_save",
        {
            "originalName": "Jess",
            "name": "Jess",
            "description": "Updated",
            "systemPrompt": "updated role",
            "model": "gemma-test",
            "voice": "af_sky",
            "voiceBackend": "coqui",
            "voiceModel": "tts_models/en/ljspeech/vits",
            "coquiSpeaker": "speaker-a",
            "coquiLanguage": "en",
            "coquiDevice": "cpu",
            "toolUser": "mock",
        },
    )

    assert result["ok"] is True
    assert saved
    assert saved[-1].personas["Jess"].personality == "updated role"
    assert saved[-1].personas["Jess"].tts_options["speaker"] == "speaker-a"
    assert result["status"]["personaBlurb"] == "Updated"
    assert result["status"]["model"] == "gemma-test"
    assert result["status"]["toolUser"] == "mock"


def test_persona_create_duplicate_and_delete_custom(monkeypatch):
    app, saved = _web_app_with_persona_store(monkeypatch)

    created = app._action("persona_create", {"source": "Jess"})
    assert created["ok"] is True
    created_name = created["status"]["persona"]
    assert created_name.startswith("New Persona")

    duplicated = app._action("persona_duplicate", {"name": created_name})
    assert duplicated["ok"] is True
    duplicate_name = duplicated["status"]["persona"]
    assert duplicate_name.startswith(f"{created_name} Copy")

    deleted = app._action("persona_delete", {"name": duplicate_name})
    assert deleted["ok"] is True
    assert duplicate_name not in [row["name"] for row in deleted["status"]["personas"]]
    assert saved[-1].custom_personas.get(duplicate_name) is None


def test_builtin_persona_delete_resets_instead_of_removing(monkeypatch):
    app, saved = _web_app_with_persona_store(monkeypatch)
    app._persona_config.personas["Jess"] = persona_config.PersonaOverride(
        voice="af_sky", personality="changed"
    )

    result = app._action("persona_delete", {"name": "Jess"})

    assert result["ok"] is True
    assert "Jess" in [row["name"] for row in result["status"]["personas"]]
    assert "Jess" not in saved[-1].personas


def _web_app_with_persona_store(monkeypatch):
    store = persona_config.PersonaConfig()
    saved = []

    def load_config():
        return store

    def save_config(config):
        saved.append(
            persona_config.PersonaConfig(
                personas=dict(config.personas),
                custom_personas=dict(config.custom_personas),
            )
        )

    monkeypatch.setattr(persona_config, "load_config", load_config)
    monkeypatch.setattr(persona_config, "save_config", save_config)
    return WebVoiceApp(), saved


def _agent_prompt_snapshot() -> dict[str, str]:
    return {
        "scopePreamble": cfg.AGENT_SCOPE_PREAMBLE,
        "statusProtocol": agent_bridge.status_protocol(),
        "delegateStyle": cfg.LLM_DELEGATE_STYLE,
        "dispatchAck": cfg.DELEGATION_ACK_PROMPT,
        "update": cfg.AGENT_UPDATE_PROMPT,
        "confirm": cfg.DELEGATION_CONFIRM_PROMPT,
        "confirmApproved": cfg.AGENT_CONFIRM_APPROVED_PROMPT,
        "confirmDenied": cfg.AGENT_CONFIRM_DENIED_PROMPT,
    }


def _restore_agent_prompts(snapshot: dict[str, str]) -> None:
    cfg.AGENT_SCOPE_PREAMBLE = snapshot["scopePreamble"]
    agent_bridge.set_status_protocol(snapshot["statusProtocol"])
    cfg.LLM_DELEGATE_STYLE = snapshot["delegateStyle"]
    cfg.DELEGATION_ACK_PROMPT = snapshot["dispatchAck"]
    cfg.AGENT_UPDATE_PROMPT = snapshot["update"]
    cfg.DELEGATION_CONFIRM_PROMPT = snapshot["confirm"]
    cfg.AGENT_CONFIRM_APPROVED_PROMPT = snapshot["confirmApproved"]
    cfg.AGENT_CONFIRM_DENIED_PROMPT = snapshot["confirmDenied"]


def test_new_session_receives_avatar_audio_callback(monkeypatch):
    captured = {}

    class FakeSession:
        def __init__(self, persona, on_event=None, on_avatar_audio=None):
            captured["callback"] = on_avatar_audio

        def set_manual_prompt_mode(self, value):
            return None

        def set_voice_mode(self, value):
            return None

        def set_muted(self, value):
            return None

        def set_startup_defaults(self, **kwargs):
            return None

        def set_voicebox_warmup_personas(self, personas):
            return None

        def set_default_agent_backend(self, backend):
            return None

    monkeypatch.setattr(web_gui, "VoiceSession", FakeSession)
    app = WebVoiceApp()

    assert captured["callback"] == app._avatar_audio.publish


def test_stop_app_closes_avatar_hub(monkeypatch):
    app = WebVoiceApp()
    closed = []
    monkeypatch.setattr(app._avatar_audio, "close", lambda: closed.append(True))
    monkeypatch.setattr(app, "_stop_session", lambda **kwargs: None)
    monkeypatch.setattr(app, "_join_thread", lambda *args, **kwargs: None)

    app._stop_app()

    assert closed == [True]


def test_avatar_audio_route_calls_streamer():
    app = WebVoiceApp()
    handler_class = app._handler_class()

    assert "/api/avatar-audio" in inspect.getsource(handler_class.do_GET)


def test_avatar_audio_tap_sits_between_tts_and_local_output():
    source = Path("remote_agent_protocol/session.py").read_text(encoding="utf-8")
    output_block = source.split('TranscriptTap(self._on_event, role="assistant")', 1)[1]

    assert output_block.index("self._tts") < output_block.index("AvatarAudioTap(")
    assert output_block.index("AvatarAudioTap(") < output_block.index("transport.output()")


def test_avatar_vendor_and_metadata_files_are_declared():
    metadata = json.loads(
        (WEB_APP / "assets/avatars/butler/metadata.json").read_text(encoding="utf-8")
    )
    version = (WEB_APP / "vendor/three/VERSION").read_text(encoding="utf-8").strip()
    project = Path("pyproject.toml").read_text(encoding="utf-8")

    assert metadata["id"] == "butler"
    assert metadata["model"] is None
    assert metadata["fallback"] == "procedural-butler"
    assert version == "0.180.0"
    assert '"web_app/**/*"' in project
    assert (WEB_APP / "vendor/three/LICENSE").is_file()
    assert (WEB_APP / "vendor/three/three.module.min.js").is_file()
    assert (WEB_APP / "vendor/three/addons/loaders/GLTFLoader.js").is_file()
    assert (WEB_APP / "vendor/three/addons/utils/BufferGeometryUtils.js").is_file()


def test_web_shell_contains_avatar_panel_import_map_and_settings():
    html = (WEB_APP / "index.html").read_text(encoding="utf-8")
    script = (WEB_APP / "app.js").read_text(encoding="utf-8")
    css = (WEB_APP / "styles.css").read_text(encoding="utf-8")

    for marker in [
        'id="avatarPanel"',
        'id="avatarCanvasHost"',
        'id="avatarFallback"',
        'id="avatarPersonaName"',
        'id="avatarStateLabel"',
        'id="avatarEmotionLabel"',
        'id="avatarCollapseBtn"',
        'id="avatarSettingEnabled"',
        'id="avatarSettingQuality"',
        'id="avatarSettingMotion"',
        'id="avatarSettingsSaveBtn"',
    ]:
        assert marker in html
    assert 'type="importmap"' in html
    assert '"three": "/vendor/three/three.module.min.js"' in html
    assert 'src="/avatar/avatar-entry.js"' in html
    assert "function syncAvatarRuntime" in script
    assert "post('avatar_settings'" in script
    assert ".avatar-panel" in css
    assert "@media (prefers-reduced-motion: reduce)" in css


def test_procedural_butler_exposes_required_control_contract():
    source = (WEB_APP / "avatar/procedural-butler.js").read_text(encoding="utf-8")
    scene = (WEB_APP / "avatar/avatar-scene.js").read_text(encoding="utf-8")

    for control in [
        "root",
        "bust",
        "neck",
        "head",
        "jaw",
        "mouthUpper",
        "mouthLower",
        "mouthCornerLeft",
        "mouthCornerRight",
        "cheekLeft",
        "cheekRight",
        "browLeft",
        "browRight",
        "eyeLeft",
        "eyeRight",
        "pupilLeft",
        "pupilRight",
        "lidLeft",
        "lidRight",
    ]:
        assert control in source
    assert "new THREE.WebGLRenderer" in scene
    assert "ResizeObserver" in scene
    assert "renderer.dispose()" in scene
    assert "renderer.forceContextLoss()" in scene


def test_avatar_scene_respects_reduced_motion_and_idle_gate():
    source = (WEB_APP / "avatar/avatar-scene.js").read_text(encoding="utf-8")
    assert "effectiveReducedMotion" in source
    assert "idleMotion" in source
    assert "GazeController" in source
    assert "expressionFor" in source


def test_avatar_scene_has_visibility_context_loss_and_complete_cleanup():
    source = (WEB_APP / "avatar/avatar-scene.js").read_text(encoding="utf-8")
    entry = (WEB_APP / "avatar/avatar-entry.js").read_text(encoding="utf-8")
    panel = (WEB_APP / "avatar/avatar-panel.js").read_text(encoding="utf-8")

    assert "IntersectionObserver" in entry
    assert "webglcontextlost" in source
    assert "webglcontextrestored" in source
    assert "attemptedContextRestore" in source
    assert "aria-label" in panel
    for marker in [
        "observer.disconnect()",
        "stream.dispose()",
        "rig.dispose()",
        "renderer.renderLists.dispose()",
        "renderer.dispose()",
        "renderer.forceContextLoss()",
        "cancelAnimationFrame(animationFrame)",
    ]:
        assert marker in source


def test_package_data_covers_nested_avatar_assets():
    project = Path("pyproject.toml").read_text(encoding="utf-8")
    assert '"web_app/**/*"' in project
    for relative in [
        "avatar/avatar-entry.js",
        "avatar/avatar-scene.js",
        "assets/avatars/butler/metadata.json",
        "vendor/three/three.module.min.js",
        "vendor/three/addons/loaders/GLTFLoader.js",
        "vendor/three/addons/utils/BufferGeometryUtils.js",
    ]:
        assert (WEB_APP / relative).is_file(), relative

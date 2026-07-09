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


def test_web_shell_has_no_obsolete_cyan_first_motif():
    combined = "\n".join(
        path.read_text(encoding="utf-8").lower()
        for path in (WEB_APP / "index.html", WEB_APP / "styles.css", WEB_APP / "app.js")
    )

    assert "--accent-cyan" not in combined
    assert "#22d3ee" not in combined
    assert "radial-gradient" not in combined


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
        web_gui.process_guard, "close_previous_instance", lambda: calls.append("close")
    )
    monkeypatch.setattr(web_gui.process_guard, "write_lock", lambda: calls.append("write"))
    monkeypatch.setattr(web_gui.process_guard, "release_lock", lambda: calls.append("release"))

    class FakeApp:
        def run(self):
            calls.append("run")

    monkeypatch.setattr(web_gui, "WebVoiceApp", FakeApp)

    web_gui.run()

    assert calls == ["close", "write", "run", "release"]


def test_direct_web_launcher_releases_lock_after_failure(monkeypatch):
    calls = []

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

    assert calls == ["close", "write", "run", "release"]


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


class FakeSession:
    def __init__(self, events):
        self.events = events

    def shutdown(self):
        self.events.append("shutdown")

    def set_muted(self, muted):
        self.events.append(f"muted:{muted}")


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

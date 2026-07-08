from pathlib import Path

from remote_agent_protocol import multimodal_prompt
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


def test_status_poll_does_not_reload_full_session_snapshot():
    app = WebVoiceApp()
    app._session.export_snapshot = lambda: (_ for _ in ()).throw(
        AssertionError("status polling must stay lightweight")
    )

    status = app._status_payload()

    assert status["model"]
    assert status["voice"] == app._persona.voice

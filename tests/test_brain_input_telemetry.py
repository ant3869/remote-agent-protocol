"""Brain-mode input controls and realtime telemetry bridges."""

import json
import threading
from pathlib import Path

import pytest

from remote_agent_protocol import app_state, voice_stack, web_gui
from remote_agent_protocol import config as cfg
from remote_agent_protocol.web_gui import WebVoiceApp


@pytest.fixture
def brain_app(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "RAP_MODE", "brain")
    monkeypatch.setattr(cfg, "APP_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(cfg, "S2S_MIC_MUTE_FILE", str(tmp_path / "muted.flag"))
    monkeypatch.setattr(cfg, "S2S_VOICE_MODE_FILE", str(tmp_path / "input-mode.json"), raising=False)
    monkeypatch.setattr(cfg, "S2S_VOICE_MODE_STATUS_FILE", "", raising=False)
    monkeypatch.setattr(cfg, "S2S_VOICE_MODE_ACK_TIMEOUT", 0.0, raising=False)
    monkeypatch.setattr(web_gui.wake_word, "settings_from_config", lambda *_a, **_k: _wake_settings(tmp_path))
    return WebVoiceApp(), tmp_path / "input-mode.json"


def _wake_settings(tmp_path):
    target = web_gui.wake_word.WakeWordTarget(
        "alice", "Alice", 0.61, str(tmp_path / "alice.onnx")
    )
    return web_gui.wake_word.WakeWordSettings(
        enabled=True,
        threshold=0.61,
        active_window_secs=4.5,
        targets=(target,),
    )


def test_brain_mode_publishes_initial_input_mode(brain_app):
    app, mode_file = brain_app

    published = json.loads(mode_file.read_text(encoding="utf-8"))

    assert published == {
        "generation": 1,
        "mode": app._voice_mode,
        "model": "alice",
        "model_path": str(mode_file.parent / "alice.onnx"),
        "threshold": 0.61,
        "active_window_secs": 4.5,
    }


def test_switching_from_free_talk_refreshes_enabled_wake_model(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "RAP_MODE", "brain")
    monkeypatch.setattr(cfg, "APP_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(cfg, "S2S_MIC_MUTE_FILE", str(tmp_path / "muted.flag"))
    monkeypatch.setattr(cfg, "S2S_VOICE_MODE_STATUS_FILE", "", raising=False)
    monkeypatch.setattr(cfg, "S2S_VOICE_MODE_ACK_TIMEOUT", 0.0, raising=False)
    mode_file = tmp_path / "input-mode.json"
    monkeypatch.setattr(cfg, "S2S_VOICE_MODE_FILE", str(mode_file))
    alice = web_gui.wake_word.WakeWordTarget(
        "alice", "Alice", 0.63, str(tmp_path / "Alice.onnx")
    )

    def settings(_cfg, *, enabled):
        return web_gui.wake_word.WakeWordSettings(
            enabled=enabled,
            model="hey_jarvis",
            threshold=0.5,
            active_window_secs=3,
            targets=(alice,) if enabled else (),
        )

    monkeypatch.setattr(web_gui.wake_word, "settings_from_config", settings)
    app = WebVoiceApp()
    assert json.loads(mode_file.read_text(encoding="utf-8"))["model"] == "hey_jarvis"

    result = app._action("voice_mode", {"mode": "wake_word"})
    published = json.loads(mode_file.read_text(encoding="utf-8"))

    assert result["ok"] is True
    assert published["model"] == "alice"
    assert published["model_path"] == str(tmp_path / "Alice.onnx")
    assert published["threshold"] == 0.63


def test_brain_mode_switches_to_wake_word_atomically_and_persists(brain_app):
    app, mode_file = brain_app

    result = app._action("voice_mode", {"mode": "wake_word"})

    assert result["ok"] is True
    assert result["status"]["voiceMode"] == "wake_word"
    assert json.loads(mode_file.read_text(encoding="utf-8"))["mode"] == "wake_word"
    assert not mode_file.with_suffix(".json.tmp").exists()
    assert app_state.load_state(cfg.APP_STATE_FILE).voice_mode == "wake_word"


def test_brain_mode_rejects_push_to_talk_owned_by_missing_external_control(brain_app):
    app, _mode_file = brain_app
    original = app._voice_mode

    result = app._action("voice_mode", {"mode": "push_to_talk"})

    assert result["ok"] is False
    assert app._voice_mode == original
    assert "push to talk" in result["error"].lower()


def test_brain_mode_does_not_claim_mode_changed_when_bridge_write_fails(
    brain_app, monkeypatch
):
    app, _mode_file = brain_app
    original = app._voice_mode
    monkeypatch.setattr(app, "_write_s2s_voice_mode", lambda _mode: None)

    result = app._action("voice_mode", {"mode": "wake_word"})

    assert result["ok"] is False
    assert app._voice_mode == original
    assert "input mode" in result["error"].lower()


def test_publish_folds_external_telemetry_under_state_lock(brain_app, monkeypatch):
    app, _mode_file = brain_app
    owned = []
    original = app._fold_event

    def checked(event):
        owned.append(app._lock._is_owned())
        original(event)

    monkeypatch.setattr(app, "_fold_event", checked)
    app._publish({"type": "turn_timing", "stt": 0.2, "llm": 0.3, "tts": 0.4, "total": 0.9})

    assert owned == [True]


def test_turn_timing_event_updates_all_live_latency_readouts():
    app = WebVoiceApp()

    app._ingest_turn_timing(
        {"stt_s": 0.32, "response_start_s": 0.91, "first_audio_s": 1.27}
    )

    assert app._status_payload()["latency"] == {
        "stt": 0.32,
        "llm": 0.59,
        "tts": 0.36,
        "total": 1.27,
    }
    events = app._events_after(0)["events"]
    assert events[-1]["type"] == "turn_timing"


def test_partial_turn_timing_keeps_real_stt_and_total_without_fake_phase_values():
    app = WebVoiceApp()

    assert app._ingest_turn_timing(
        {"stt_s": 0.3, "response_start_s": None, "first_audio_s": 1.1}
    ) is True

    assert app._status_payload()["latency"] == {
        "stt": 0.3,
        "llm": None,
        "tts": None,
        "total": 1.1,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"stt_s": "wat", "response_start_s": 1.0, "first_audio_s": 2.0},
        {"stt_s": 2.0, "response_start_s": 1.0, "first_audio_s": 3.0},
        {"stt_s": 0.2, "response_start_s": 1.0, "first_audio_s": -1.0},
    ],
)
def test_invalid_turn_timing_is_ignored(payload):
    app = WebVoiceApp()

    assert app._ingest_turn_timing(payload) is False
    assert app._status_payload()["latency"] == {
        "stt": None,
        "llm": None,
        "tts": None,
        "total": None,
    }


def test_brain_mode_transitions_are_serialized_and_await_captured_generation(
    brain_app, monkeypatch
):
    app, _mode_file = brain_app
    events = []
    first_waiting = threading.Event()
    release_first = threading.Event()
    generation = iter([21, 22])

    def write(mode):
        value = next(generation)
        events.append(("write", mode, value))
        return value

    def await_mode(mode, value):
        events.append(("await", mode, value))
        if value == 21:
            first_waiting.set()
            release_first.wait(timeout=2)
        return True, ""

    monkeypatch.setattr(app, "_write_s2s_voice_mode", write)
    monkeypatch.setattr(app, "_await_s2s_voice_mode", await_mode)
    first = threading.Thread(target=app._action, args=("voice_mode", {"mode": "wake_word"}))
    second = threading.Thread(target=app._action, args=("voice_mode", {"mode": "free_talk"}))
    first.start()
    assert first_waiting.wait(timeout=1)
    second.start()
    second.join(timeout=0.05)
    assert second.is_alive()  # blocked behind the transition lock, not interleaving
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert events == [
        ("write", "wake_word", 21),
        ("await", "wake_word", 21),
        ("write", "free_talk", 22),
        ("await", "free_talk", 22),
    ]


def test_brain_mode_rolls_back_when_client_does_not_acknowledge(brain_app, monkeypatch):
    app, mode_file = brain_app
    status_file = mode_file.with_name("mode-status.json")
    monkeypatch.setattr(cfg, "S2S_VOICE_MODE_STATUS_FILE", str(status_file), raising=False)
    monkeypatch.setattr(cfg, "S2S_VOICE_MODE_ACK_TIMEOUT", 0.05, raising=False)
    previous = app._voice_mode
    requested = "wake_word" if previous != "wake_word" else "free_talk"

    result = app._action("voice_mode", {"mode": requested})

    assert result["ok"] is False
    assert "acknowledge" in result["error"].lower()
    assert app._voice_mode == previous
    assert json.loads(mode_file.read_text(encoding="utf-8"))["mode"] == previous


def test_failed_free_talk_request_rolls_back_to_confirmed_wake_word(brain_app, monkeypatch):
    app, _mode_file = brain_app
    app._voice_mode = "wake_word"
    calls = []
    generation = iter([31, 32])
    def write_mode(mode):
        calls.append(("write", mode))
        return next(generation)

    monkeypatch.setattr(app, "_write_s2s_voice_mode", write_mode)

    def await_mode(mode, value):
        calls.append(("await", mode, value))
        return (mode == "wake_word"), "transition rejected"

    monkeypatch.setattr(app, "_await_s2s_voice_mode", await_mode)

    result = app._action("voice_mode", {"mode": "free_talk"})

    assert result["ok"] is False
    assert calls == [
        ("write", "free_talk"),
        ("await", "free_talk", 31),
        ("write", "wake_word"),
        ("await", "wake_word", 32),
    ]
    assert result["status"]["voiceMode"] == "wake_word"
    assert result["status"]["inputControl"]["modeReady"] is True


def test_failed_mode_rollback_is_exposed_as_unconfirmed(brain_app, monkeypatch):
    app, _mode_file = brain_app
    app._voice_mode = "wake_word"
    generation = iter([41, 42])
    monkeypatch.setattr(app, "_write_s2s_voice_mode", lambda _mode: next(generation))
    monkeypatch.setattr(app, "_await_s2s_voice_mode", lambda _mode, _generation: (False, "timeout"))

    result = app._action("voice_mode", {"mode": "free_talk"})

    assert result["ok"] is False
    assert result["status"]["voiceMode"] == "wake_word"
    assert result["status"]["inputControl"]["modeReady"] is False
    assert "rollback" in result["error"].lower()


def test_voice_stack_passes_input_mode_and_live_timing_bridges(monkeypatch, tmp_path):
    mode_file = tmp_path / "input-mode.json"
    monkeypatch.setattr(cfg, "S2S_VOICE_MODE_FILE", str(mode_file), raising=False)
    status_file = tmp_path / "mode-status.json"
    monkeypatch.setattr(cfg, "S2S_VOICE_MODE_STATUS_FILE", str(status_file), raising=False)
    monkeypatch.setattr(cfg, "S2S_BRIDGE_PORT", 9123)
    monkeypatch.setattr(cfg, "S2S_BRIDGE_API_KEY", "secret")

    args = voice_stack.build_stages(tmp_path)[2].args

    assert args[args.index("--external-mode-file") + 1] == str(mode_file.resolve())
    assert args[args.index("--external-mode-status-file") + 1] == str(status_file.resolve())
    assert args[args.index("--turn-timing-url") + 1] == "http://127.0.0.1:9123/api/turn-timing"
    assert args[args.index("--turn-timing-api-key") + 1] == "secret"


def test_brain_ui_keeps_real_input_modes_enabled():
    script = Path("remote_agent_protocol/web_app/app.js").read_text(encoding="utf-8")

    inert_block = script.split("const BRAIN_INERT", 1)[1].split("];", 1)[0]
    assert '"modeBtn"' not in inert_block
    assert 'nextMode(state.status?.voiceMode, state.status?.mode)' in script
    assert 'voiceModeRows(s.mode)' in script

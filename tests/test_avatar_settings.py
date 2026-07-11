import json

from remote_agent_protocol import app_state, web_gui
from remote_agent_protocol.web_gui import WebVoiceApp


def test_avatar_defaults_are_present_for_old_state_files(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"persona": "Jess"}', encoding="utf-8")

    state = app_state.load_state(path)

    assert state.avatar_enabled is True
    assert state.avatar_id == "butler"
    assert state.avatar_quality == "high"
    assert state.avatar_lip_sync is True
    assert state.avatar_gaze is True
    assert state.avatar_idle_motion is True
    assert state.avatar_expression_intensity == 0.62
    assert state.avatar_reduced_motion is None
    assert state.avatar_show_state is True
    assert state.avatar_panel_collapsed is False


def test_avatar_settings_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    state = app_state.normalize_avatar_settings(
        {
            "enabled": False,
            "avatarId": "butler",
            "quality": "low",
            "lipSync": False,
            "gaze": False,
            "idleMotion": False,
            "expressionIntensity": 0.35,
            "reducedMotion": True,
            "showState": False,
            "panelCollapsed": True,
        }
    )
    app_state.save_state(path, state)

    loaded = app_state.load_state(path)

    assert app_state.avatar_settings_payload(loaded) == {
        "enabled": False,
        "avatarId": "butler",
        "quality": "low",
        "lipSync": False,
        "gaze": False,
        "idleMotion": False,
        "expressionIntensity": 0.35,
        "reducedMotion": True,
        "showState": False,
        "panelCollapsed": True,
    }


def test_invalid_avatar_values_normalize_to_safe_defaults():
    state = app_state.normalize_avatar_settings(
        {
            "enabled": "yes",
            "avatarId": "../outside",
            "quality": "ultra",
            "lipSync": 1,
            "expressionIntensity": 8,
            "reducedMotion": "sometimes",
        }
    )

    assert app_state.avatar_settings_payload(state) == {
        "enabled": True,
        "avatarId": "butler",
        "quality": "high",
        "lipSync": True,
        "gaze": True,
        "idleMotion": True,
        "expressionIntensity": 1.0,
        "reducedMotion": None,
        "showState": True,
        "panelCollapsed": False,
    }


def test_status_payload_exposes_avatar_settings():
    app = WebVoiceApp()

    avatar = app._status_payload()["avatar"]

    assert avatar["enabled"] is True
    assert avatar["avatarId"] == "butler"
    assert avatar["quality"] == "high"
    assert avatar["reducedMotion"] is None


def test_avatar_settings_action_normalizes_and_persists(monkeypatch):
    app = WebVoiceApp()
    saved = []
    monkeypatch.setattr(web_gui.app_state, "save_state", lambda path, state: saved.append(state))

    result = app._action(
        "avatar_settings",
        {"settings": {"quality": "low", "expressionIntensity": 0.4, "enabled": False}},
    )

    assert result["ok"] is True
    assert result["status"]["avatar"]["quality"] == "low"
    assert result["status"]["avatar"]["expressionIntensity"] == 0.4
    assert result["status"]["avatar"]["enabled"] is False
    assert saved[-1].avatar_quality == "low"


def test_saved_avatar_state_uses_snake_case_fields(tmp_path):
    path = tmp_path / "state.json"
    app_state.save_state(path, app_state.normalize_avatar_settings({"quality": "medium"}))

    raw = json.loads(path.read_text(encoding="utf-8"))

    assert raw["avatar_quality"] == "medium"
    assert "avatar" not in raw

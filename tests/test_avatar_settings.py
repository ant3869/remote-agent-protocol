import json

from remote_agent_protocol import app_state, web_gui
from remote_agent_protocol.web_gui import WebVoiceApp


def test_avatar_defaults_are_present_for_old_state_files(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"persona": "Jess"}', encoding="utf-8")

    state = app_state.load_state(path)

    assert state.avatar_enabled is True
    assert state.avatar_id == "persona"
    assert state.avatar_quality == "high"
    assert state.avatar_lip_sync is True
    assert state.avatar_gaze is True
    assert state.avatar_idle_motion is True
    assert state.avatar_expression_intensity == 0.62
    assert state.avatar_reduced_motion is None
    assert state.avatar_show_state is True
    assert state.avatar_panel_collapsed is False


def test_a_butler_choice_saved_before_there_was_a_choice_now_matches_the_persona(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"persona": "Jess", "avatar_id": "butler"}', encoding="utf-8")

    migrated = app_state.load_state(path)
    app_state.save_state(
        path, app_state.normalize_avatar_settings({"avatarId": "butler"}, migrated)
    )

    assert migrated.avatar_id == "persona"
    assert app_state.load_state(path).avatar_id == "butler", "a choice made now is kept"


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
        "avatarId": "persona",
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
    assert avatar["avatarId"] == "persona"
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


def test_ui_layout_defaults_to_stage_and_survives_a_round_trip(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"persona": "Jess"}', encoding="utf-8")
    assert app_state.load_state(path).ui_layout == "stage"

    path.write_text('{"ui_layout": "sideways"}', encoding="utf-8")
    assert app_state.load_state(path).ui_layout == "stage"

    app_state.save_state(path, app_state.AppState(ui_layout="console"))
    assert app_state.load_state(path).ui_layout == "console"


def test_set_ui_layout_action_validates_persists_and_reports(monkeypatch):
    app = WebVoiceApp()
    saved = []
    monkeypatch.setattr(web_gui.app_state, "save_state", lambda path, state: saved.append(state))

    result = app._action("set_ui_layout", {"mode": "console"})
    bad = app._action("set_ui_layout", {"mode": "sideways"})

    assert result["ok"] is True
    assert result["status"]["uiLayout"] == "console"
    assert saved[-1].ui_layout == "console"
    assert bad["ok"] is False
    assert bad["status"]["uiLayout"] == "console"


def test_stage_layout_markup_is_wired():
    from pathlib import Path

    web = Path(web_gui.__file__).with_name("web_app")
    html = (web / "index.html").read_text(encoding="utf-8")

    assert 'data-layout="stage"' in html
    assert 'id="avatarSlotRail"' in html and 'id="avatarSlotStage"' in html
    assert 'data-layout-mode="stage"' in html and 'data-layout-mode="console"' in html
    assert html.index("/layout-v4.css") < html.index("/layout-stage.css")
    rail = html.index('id="avatarSlotRail"')
    assert rail < html.index('id="avatarPanel"') < html.index('id="avatarSlotStage"')
    assert (web / "layout-stage.css").is_file()

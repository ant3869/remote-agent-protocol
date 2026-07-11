from __future__ import annotations

import subprocess
from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


root = Path.cwd()
app_state_path = root / "remote_agent_protocol/app_state.py"
web_gui_path = root / "remote_agent_protocol/web_gui.py"
test_path = root / "tests/test_avatar_settings.py"

app_state = app_state_path.read_text(encoding="utf-8")
app_state = replace_once(
    app_state,
    "import os\nfrom dataclasses import asdict, dataclass, field\n",
    "import os\nimport re\nfrom collections.abc import Mapping\nfrom dataclasses import asdict, dataclass, field, replace\n",
    "app_state imports",
)
app_state = replace_once(
    app_state,
    "    agent_prompts: dict[str, str] = field(default_factory=dict)\n\n\ndef load_state",
    """    agent_prompts: dict[str, str] = field(default_factory=dict)
    avatar_enabled: bool = True
    avatar_id: str = \"butler\"
    avatar_quality: str = \"high\"
    avatar_lip_sync: bool = True
    avatar_gaze: bool = True
    avatar_idle_motion: bool = True
    avatar_expression_intensity: float = 0.62
    avatar_reduced_motion: bool | None = None
    avatar_show_state: bool = True
    avatar_panel_collapsed: bool = False


AVATAR_QUALITIES = frozenset({\"low\", \"medium\", \"high\"})
_AVATAR_ID_RE = re.compile(r\"^[a-z0-9][a-z0-9_-]{0,63}$\")


def _pick(raw: Mapping[str, object], snake: str, camel: str, default: object) -> object:
    if snake in raw:
        return raw[snake]
    if camel in raw:
        return raw[camel]
    return default


def _bool_or(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _tri_bool_or(value: object, default: bool | None) -> bool | None:
    return value if value is None or isinstance(value, bool) else default


def _avatar_id_or(value: object, default: str) -> str:
    return value if isinstance(value, str) and _AVATAR_ID_RE.fullmatch(value) else default


def _quality_or(value: object, default: str) -> str:
    return value if isinstance(value, str) and value in AVATAR_QUALITIES else default


def _intensity_or(value: object, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return max(0.0, min(1.0, float(value)))


def normalize_avatar_settings(
    raw: Mapping[str, object] | object,
    base: AppState | None = None,
) -> AppState:
    \"\"\"Normalize browser or persisted avatar settings against an existing state.\"\"\"
    current = base or AppState()
    values = raw if isinstance(raw, Mapping) else {}
    return replace(
        current,
        avatar_enabled=_bool_or(
            _pick(values, \"avatar_enabled\", \"enabled\", current.avatar_enabled),
            current.avatar_enabled,
        ),
        avatar_id=_avatar_id_or(
            _pick(values, \"avatar_id\", \"avatarId\", current.avatar_id),
            current.avatar_id,
        ),
        avatar_quality=_quality_or(
            _pick(values, \"avatar_quality\", \"quality\", current.avatar_quality),
            current.avatar_quality,
        ),
        avatar_lip_sync=_bool_or(
            _pick(values, \"avatar_lip_sync\", \"lipSync\", current.avatar_lip_sync),
            current.avatar_lip_sync,
        ),
        avatar_gaze=_bool_or(
            _pick(values, \"avatar_gaze\", \"gaze\", current.avatar_gaze),
            current.avatar_gaze,
        ),
        avatar_idle_motion=_bool_or(
            _pick(values, \"avatar_idle_motion\", \"idleMotion\", current.avatar_idle_motion),
            current.avatar_idle_motion,
        ),
        avatar_expression_intensity=_intensity_or(
            _pick(
                values,
                \"avatar_expression_intensity\",
                \"expressionIntensity\",
                current.avatar_expression_intensity,
            ),
            current.avatar_expression_intensity,
        ),
        avatar_reduced_motion=_tri_bool_or(
            _pick(
                values,
                \"avatar_reduced_motion\",
                \"reducedMotion\",
                current.avatar_reduced_motion,
            ),
            current.avatar_reduced_motion,
        ),
        avatar_show_state=_bool_or(
            _pick(values, \"avatar_show_state\", \"showState\", current.avatar_show_state),
            current.avatar_show_state,
        ),
        avatar_panel_collapsed=_bool_or(
            _pick(
                values,
                \"avatar_panel_collapsed\",
                \"panelCollapsed\",
                current.avatar_panel_collapsed,
            ),
            current.avatar_panel_collapsed,
        ),
    )


def avatar_settings_payload(state: AppState) -> dict[str, object]:
    \"\"\"Return the camelCase avatar settings contract consumed by the browser.\"\"\"
    return {
        \"enabled\": state.avatar_enabled,
        \"avatarId\": state.avatar_id,
        \"quality\": state.avatar_quality,
        \"lipSync\": state.avatar_lip_sync,
        \"gaze\": state.avatar_gaze,
        \"idleMotion\": state.avatar_idle_motion,
        \"expressionIntensity\": state.avatar_expression_intensity,
        \"reducedMotion\": state.avatar_reduced_motion,
        \"showState\": state.avatar_show_state,
        \"panelCollapsed\": state.avatar_panel_collapsed,
    }


def load_state""",
    "app_state fields and helpers",
)
app_state = replace_once(
    app_state,
    "    return AppState(\n        persona=raw.get(\"persona\") if isinstance(raw.get(\"persona\"), str) else None,",
    "    state = AppState(\n        persona=raw.get(\"persona\") if isinstance(raw.get(\"persona\"), str) else None,",
    "load_state state assignment",
)
app_state = replace_once(
    app_state,
    "        },\n    )\n\n\ndef save_state",
    "        },\n    )\n    return normalize_avatar_settings(raw, state)\n\n\ndef save_state",
    "load_state avatar normalization",
)
app_state_path.write_text(app_state, encoding="utf-8")

web_gui = web_gui_path.read_text(encoding="utf-8")
web_gui = replace_once(
    web_gui,
    "            agent_prompts=self._app_state.agent_prompts,\n        )",
    """            agent_prompts=self._app_state.agent_prompts,
            avatar_enabled=self._app_state.avatar_enabled,
            avatar_id=self._app_state.avatar_id,
            avatar_quality=self._app_state.avatar_quality,
            avatar_lip_sync=self._app_state.avatar_lip_sync,
            avatar_gaze=self._app_state.avatar_gaze,
            avatar_idle_motion=self._app_state.avatar_idle_motion,
            avatar_expression_intensity=self._app_state.avatar_expression_intensity,
            avatar_reduced_motion=self._app_state.avatar_reduced_motion,
            avatar_show_state=self._app_state.avatar_show_state,
            avatar_panel_collapsed=self._app_state.avatar_panel_collapsed,
        )""",
    "web state persistence",
)
web_gui = replace_once(
    web_gui,
    '            "toolUser": self._session.default_agent_backend(),\n            "personas": self._personas_payload(),',
    '            "toolUser": self._session.default_agent_backend(),\n            "avatar": app_state.avatar_settings_payload(self._app_state),\n            "personas": self._personas_payload(),',
    "status avatar payload",
)
web_gui = replace_once(
    web_gui,
    "        elif name == \"ptt\":\n            self._session.set_push_to_talk(bool(payload.get(\"active\")))",
    """        elif name == \"avatar_settings\":
            self._app_state = app_state.normalize_avatar_settings(
                payload.get(\"settings\"), self._app_state
            )
            self._save_state()
        elif name == \"ptt\":
            self._session.set_push_to_talk(bool(payload.get(\"active\")))""",
    "avatar settings action",
)
web_gui_path.write_text(web_gui, encoding="utf-8")

test_path.write_text(
    '''import json

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
''',
    encoding="utf-8",
)

subprocess.run(
    ["python", "-m", "ruff", "format", str(app_state_path), str(web_gui_path), str(test_path)],
    check=True,
)
subprocess.run(
    [
        "python",
        "-m",
        "pytest",
        "tests/test_app_state.py",
        "tests/test_web_gui.py",
        "tests/test_avatar_settings.py",
        "-q",
        "--disable-warnings",
        "--maxfail=1",
    ],
    check=True,
)

Path(__file__).unlink()
subprocess.run(
    [
        "git",
        "add",
        "remote_agent_protocol/app_state.py",
        "remote_agent_protocol/web_gui.py",
        "tests/test_avatar_settings.py",
        ".github/avatar_tasks/task1.py",
    ],
    check=True,
)
subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
subprocess.run(
    ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
    check=True,
)
subprocess.run(["git", "commit", "-m", "feat(avatar): persist companion settings"], check=True)
subprocess.run(["git", "push", "origin", "HEAD:feature/animated-butler-avatar"], check=True)
print("TASK 1 DONE: avatar settings tests passed and implementation committed")

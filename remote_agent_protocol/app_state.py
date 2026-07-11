"""Remember the operator's app defaults across restarts.

Deliberately tiny: this is UI state, not persona configuration. Persona
*definitions* live in personas.py / persona_overrides.json; this file records
which runtime picks should come back on the next boot.

Best-effort throughout: a missing or corrupt state file just means defaults.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from loguru import logger

from remote_agent_protocol import multimodal_prompt


@dataclass
class AppState:
    """Last-used picks restored at boot."""

    persona: str | None = None
    tool_user: str | None = None
    voice_mode: str = multimodal_prompt.DEFAULT_VOICE_MODE
    model: str | None = None
    voice: str | None = None
    tts_provider: str | None = None
    coqui_model: str | None = None
    coqui_speaker: str | None = None
    coqui_language: str | None = None
    coqui_device: str | None = None
    agent_prompts: dict[str, str] = field(default_factory=dict)
    avatar_enabled: bool = True
    avatar_id: str = "butler"
    avatar_quality: str = "high"
    avatar_lip_sync: bool = True
    avatar_gaze: bool = True
    avatar_idle_motion: bool = True
    avatar_expression_intensity: float = 0.62
    avatar_reduced_motion: bool | None = None
    avatar_show_state: bool = True
    avatar_panel_collapsed: bool = False


AVATAR_QUALITIES = frozenset({"low", "medium", "high"})
_AVATAR_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


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
    """Normalize browser or persisted avatar settings against an existing state."""
    current = base or AppState()
    values = raw if isinstance(raw, Mapping) else {}
    return replace(
        current,
        avatar_enabled=_bool_or(
            _pick(values, "avatar_enabled", "enabled", current.avatar_enabled),
            current.avatar_enabled,
        ),
        avatar_id=_avatar_id_or(
            _pick(values, "avatar_id", "avatarId", current.avatar_id),
            current.avatar_id,
        ),
        avatar_quality=_quality_or(
            _pick(values, "avatar_quality", "quality", current.avatar_quality),
            current.avatar_quality,
        ),
        avatar_lip_sync=_bool_or(
            _pick(values, "avatar_lip_sync", "lipSync", current.avatar_lip_sync),
            current.avatar_lip_sync,
        ),
        avatar_gaze=_bool_or(
            _pick(values, "avatar_gaze", "gaze", current.avatar_gaze),
            current.avatar_gaze,
        ),
        avatar_idle_motion=_bool_or(
            _pick(values, "avatar_idle_motion", "idleMotion", current.avatar_idle_motion),
            current.avatar_idle_motion,
        ),
        avatar_expression_intensity=_intensity_or(
            _pick(
                values,
                "avatar_expression_intensity",
                "expressionIntensity",
                current.avatar_expression_intensity,
            ),
            current.avatar_expression_intensity,
        ),
        avatar_reduced_motion=_tri_bool_or(
            _pick(
                values,
                "avatar_reduced_motion",
                "reducedMotion",
                current.avatar_reduced_motion,
            ),
            current.avatar_reduced_motion,
        ),
        avatar_show_state=_bool_or(
            _pick(values, "avatar_show_state", "showState", current.avatar_show_state),
            current.avatar_show_state,
        ),
        avatar_panel_collapsed=_bool_or(
            _pick(
                values,
                "avatar_panel_collapsed",
                "panelCollapsed",
                current.avatar_panel_collapsed,
            ),
            current.avatar_panel_collapsed,
        ),
    )


def avatar_settings_payload(state: AppState) -> dict[str, object]:
    """Return the camelCase avatar settings contract consumed by the browser."""
    return {
        "enabled": state.avatar_enabled,
        "avatarId": state.avatar_id,
        "quality": state.avatar_quality,
        "lipSync": state.avatar_lip_sync,
        "gaze": state.avatar_gaze,
        "idleMotion": state.avatar_idle_motion,
        "expressionIntensity": state.avatar_expression_intensity,
        "reducedMotion": state.avatar_reduced_motion,
        "showState": state.avatar_show_state,
        "panelCollapsed": state.avatar_panel_collapsed,
    }


def load_state(path: str | Path) -> AppState:
    """Read saved state; empty path (persistence disabled) or bad file -> defaults."""
    if not str(path):
        return AppState()
    p = Path(path)
    if not p.exists():
        return AppState()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Couldn't read app state {p} ({e}) -- using defaults.")
        return AppState()
    if not isinstance(raw, dict):
        return AppState()
    agent_prompts = raw.get("agent_prompts")
    if not isinstance(agent_prompts, dict):
        agent_prompts = {}
    state = AppState(
        persona=raw.get("persona") if isinstance(raw.get("persona"), str) else None,
        tool_user=raw.get("tool_user") if isinstance(raw.get("tool_user"), str) else None,
        voice_mode=multimodal_prompt.normalize_voice_mode(raw.get("voice_mode")),
        model=raw.get("model") if isinstance(raw.get("model"), str) else None,
        voice=raw.get("voice") if isinstance(raw.get("voice"), str) else None,
        tts_provider=raw.get("tts_provider") if isinstance(raw.get("tts_provider"), str) else None,
        coqui_model=raw.get("coqui_model") if isinstance(raw.get("coqui_model"), str) else None,
        coqui_speaker=raw.get("coqui_speaker")
        if isinstance(raw.get("coqui_speaker"), str)
        else None,
        coqui_language=raw.get("coqui_language")
        if isinstance(raw.get("coqui_language"), str)
        else None,
        coqui_device=raw.get("coqui_device") if isinstance(raw.get("coqui_device"), str) else None,
        agent_prompts={
            str(key): value for key, value in agent_prompts.items() if isinstance(value, str)
        },
    )
    return normalize_avatar_settings(raw, state)


def save_state(path: str | Path, state: AppState) -> None:
    """Persist state atomically; a write failure is logged, never raised."""
    if not str(path):
        return
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as e:
        logger.warning(f"Couldn't save app state to {p}: {e}")
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def resolve_persona_name(saved: str | None, available: list[str], default: str) -> str:
    """Pick the boot persona: the saved one if it still exists, else the default.

    A persona renamed or removed since the last run must not break boot, and
    the configured default itself may be stale -- fall through to the first
    available name as the last resort.
    """
    if saved in available:
        return saved
    if default in available:
        return default
    return available[0] if available else default

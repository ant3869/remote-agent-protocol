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
from dataclasses import asdict, dataclass, field
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
    return AppState(
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

"""Remember the operator's last picks (persona, tool user) across restarts.

Deliberately tiny: this is UI state, not configuration. Persona *definitions*
live in personas.py / persona_overrides.json; this file only records which one
was active so the app boots as the character you actually use. Ad-hoc voice and
model picks stay session-scoped on purpose -- pinning those is what the config
panel's "Save persona" is for.

Best-effort throughout: a missing or corrupt state file just means defaults.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from loguru import logger

from remote_agent_protocol import multimodal_prompt


@dataclass
class AppState:
    """Last-used picks restored at boot."""

    persona: str | None = None
    tool_user: str | None = None
    voice_mode: str = multimodal_prompt.DEFAULT_VOICE_MODE


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
    return AppState(
        persona=raw.get("persona") if isinstance(raw.get("persona"), str) else None,
        tool_user=raw.get("tool_user") if isinstance(raw.get("tool_user"), str) else None,
        voice_mode=multimodal_prompt.normalize_voice_mode(raw.get("voice_mode")),
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

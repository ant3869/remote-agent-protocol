"""JSON-backed persona overrides.

Built-in personas stay in code; user edits live in `data/persona_overrides.json`
(see `config/persona_overrides.example.json` for the format) so a
GUI save does not rewrite Python files like a tiny arsonist.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from remote_agent_protocol import config as cfg
from remote_agent_protocol import voicebox
from remote_agent_protocol.personas import Persona

CONFIG_PATH = Path(cfg.DATA_DIR) / "persona_overrides.json"


@dataclass
class PersonaOverride:
    """Sparse per-persona field overrides; None/empty means keep the built-in."""

    voice: str | None = None
    voice_backend: str | None = None
    voice_model: str | None = None
    personality: str | None = None
    blurb: str | None = None
    model: str | None = None
    tool_user: str | None = None


@dataclass
class PersonaConfig:
    """All persona overrides, keyed by persona name."""

    personas: dict[str, PersonaOverride] = field(default_factory=dict)


def load_config(path: str | Path = CONFIG_PATH) -> PersonaConfig:
    """Load persona overrides from disk; missing file means no overrides."""
    path = Path(path)
    if not path.exists():
        return PersonaConfig()
    raw = json.loads(path.read_text(encoding="utf-8"))
    overrides = {
        name: PersonaOverride(**values)
        for name, values in raw.get("personas", {}).items()
        if isinstance(values, dict)
    }
    return PersonaConfig(personas=overrides)


def save_config(config: PersonaConfig, path: str | Path = CONFIG_PATH) -> None:
    """Write overrides as pretty JSON, dropping empty fields."""
    path = Path(path)
    data = {
        "personas": {
            name: {k: v for k, v in asdict(override).items() if v not in (None, "")}
            for name, override in config.personas.items()
        }
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def apply_override(base: Persona, override: PersonaOverride | None) -> Persona:
    """Return ``base`` with any non-empty override fields applied."""
    if override is None:
        return base
    return replace(
        base,
        voice=override.voice or base.voice,
        voice_backend=override.voice_backend or base.voice_backend,
        voice_model=override.voice_model or base.voice_model,
        personality=override.personality or base.personality,
        blurb=override.blurb or base.blurb,
        model=override.model if override.model not in (None, "") else base.model,
        tool_user=override.tool_user if override.tool_user not in (None, "") else base.tool_user,
    )


def effective_personas(bases: list[Persona], config: PersonaConfig) -> list[Persona]:
    """Built-in personas with user overrides applied, original order kept."""
    return [apply_override(base, config.personas.get(base.name)) for base in bases]


def effective_by_name(name: str, bases: list[Persona], config: PersonaConfig) -> Persona:
    """Effective persona by name, falling back to the first persona."""
    for persona in effective_personas(bases, config):
        if persona.name == name:
            return persona
    return effective_personas(bases, config)[0]


def override_from_persona(persona: Persona) -> PersonaOverride:
    """Snapshot a full persona as an override record (used by GUI saves)."""
    return PersonaOverride(
        voice=persona.voice,
        voice_backend=persona.voice_backend,
        voice_model=persona.voice_model,
        personality=persona.personality,
        blurb=persona.blurb,
        model=persona.model,
        tool_user=persona.tool_user,
    )


def voicebox_personas(bases: list[Persona], config: PersonaConfig) -> list[Persona]:
    """Return effective personas that need the Voicebox backend."""
    return [
        persona
        for persona in effective_personas(bases, config)
        if persona.voice_backend == "voicebox" or voicebox.is_voicebox_ref(persona.voice)
    ]


def valid_tool_user(value: str | None) -> bool:
    """True if ``value`` is empty or names a configured agent backend."""
    return value in (None, "") or value in cfg.AGENT_BACKENDS

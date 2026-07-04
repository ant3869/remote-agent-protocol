"""TTS backend factory for Jess.

Most personas use local Kokoro. PersonaTTSService routes per-persona voices so a
single running pipeline can speak Kokoro or Voicebox profiles without rebuilding.
Cartesia support remains available for explicit global backend use.
"""

import os
from pathlib import Path
from uuid import UUID

from pipecat.services.cartesia.tts import (
    CartesiaHttpTTSService,
    CartesiaTTSService,
    GenerationConfig,
)
from pipecat.services.tts_service import TextAggregationMode
from remote_agent_protocol import config as cfg
from remote_agent_protocol import voicebox
from remote_agent_protocol.persona_tts import PersonaTTSService


def load_env_value(name: str, env_path: str | Path = ".env") -> str | None:
    """Tiny .env reader: enough for KEY=value, no dependency required."""
    value = os.getenv(name)
    if value:
        return value
    path = Path(env_path)
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw = stripped.split("=", 1)
        if key.strip() == name:
            return raw.strip().strip('"').strip("'")
    return None


def is_uuid(value: str) -> bool:
    """Return True if value looks like a provider UUID (Cartesia voice id)."""
    try:
        UUID(value)
        return True
    except (TypeError, ValueError):
        return False


def create_tts(
    persona_voice: str, voice_model: str | None = None, voice_backend: str | None = None
):
    """Create the configured TTS service for a persona voice."""
    backend = (voice_backend or cfg.TTS_BACKEND).lower().strip()
    if backend in {"kokoro", "voicebox"} or voicebox.is_voicebox_ref(persona_voice):
        return PersonaTTSService(
            settings=PersonaTTSService.Settings(
                voice=persona_voice,
                model=voice_model,
                language="en-us",
                voice_backend="voicebox" if voicebox.is_voicebox_ref(persona_voice) else backend,
            )
        )

    if backend == "cartesia":
        api_key = load_env_value("CARTESIA_API_KEY")
        if not api_key:
            raise RuntimeError("TTS_BACKEND='cartesia' but CARTESIA_API_KEY is missing from .env")
        settings = CartesiaTTSService.Settings(
            model=cfg.CARTESIA_MODEL,
            voice=cfg.CARTESIA_VOICE_ID,
            generation_config=GenerationConfig(
                speed=cfg.CARTESIA_SPEED, emotion=cfg.CARTESIA_EMOTION
            ),
        )
        if cfg.CARTESIA_TRANSPORT.lower().strip() == "websocket":
            return CartesiaTTSService(
                api_key=api_key,
                sample_rate=cfg.CARTESIA_SAMPLE_RATE,
                max_buffer_delay_ms=cfg.CARTESIA_MAX_BUFFER_DELAY_MS,
                text_aggregation_mode=TextAggregationMode.TOKEN,
                settings=settings,
            )
        return CartesiaHttpTTSService(
            api_key=api_key, sample_rate=cfg.CARTESIA_SAMPLE_RATE, settings=settings
        )

    raise ValueError(f"Unknown TTS_BACKEND: {cfg.TTS_BACKEND!r}")


def voice_switch_supported(voice: str) -> bool:
    """Whether live voice switching can accept this voice under current backend."""
    if cfg.TTS_BACKEND.lower().strip() in {"kokoro", "voicebox"}:
        return True
    return is_uuid(voice)

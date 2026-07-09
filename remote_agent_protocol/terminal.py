r"""Remote Agent Protocol -- 100% local terminal mode.

Stack (defaults, all configurable via .env / jess/config.py):
  STT       - Whisper (faster-whisper) or Moonshine (ONNX, CPU)
  LLM       - Ollama (OpenAI-compat endpoint)
  TTS       - Kokoro (ONNX, CPU), Voicebox, or Cartesia
  Transport - local mic + speakers (PyAudio)

All the real work lives in remote_agent_protocol.session.VoiceSession, shared with the GUI.
This file is just the terminal entry point: pick the default persona, run.

Run:
  .venv\Scripts\python -m remote_agent_protocol.terminal    (or double-click start_terminal.bat)

Want the pretty control panel with live voice/persona switching instead?
  .venv\Scripts\python -m remote_agent_protocol             (or double-click start_gui.bat)
"""

import asyncio

from loguru import logger

from remote_agent_protocol import config as cfg
from remote_agent_protocol import dashboard, logging_setup, persona_config, personas, process_guard
from remote_agent_protocol.session import VoiceSession

# Readable, filtered logging (drops giant context dumps; see logging_setup.py).
logging_setup.setup_logging(cfg.DEBUG_MODE)


async def main() -> None:
    """Boot the default persona and run the voice session until exit."""
    config = persona_config.load_config()
    persona = persona_config.effective_by_name(cfg.DEFAULT_PERSONA_NAME, personas.PERSONAS, config)
    session = VoiceSession(persona)
    session.set_voicebox_warmup_personas(
        persona_config.voicebox_personas(personas.PERSONAS, config)
    )
    session.build()
    await session.run()


if __name__ == "__main__":
    process_guard.close_previous_instance()
    process_guard.write_lock()
    try:
        asyncio.run(main())
    finally:
        try:
            count = dashboard.stop_loaded_models(cfg.OLLAMA_HOST)
            logger.info(f"Unloaded {count} Ollama model(s).")
        except Exception as exc:
            logger.warning(f"Could not unload models: {exc}")
        process_guard.release_lock()

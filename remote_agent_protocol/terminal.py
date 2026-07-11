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
import sys
import threading

from loguru import logger

from remote_agent_protocol import config as cfg
from remote_agent_protocol import dashboard, logging_setup, persona_config, personas, process_guard
from remote_agent_protocol.session import VoiceSession

# Readable, filtered logging (drops giant context dumps; see logging_setup.py).
logging_setup.setup_logging(cfg.DEBUG_MODE)


async def main(session: VoiceSession) -> None:
    """Run the voice session until exit."""
    await session.run()


if __name__ == "__main__":
    if not process_guard.acquire_single_instance_lock():
        print("Remote Agent Protocol is already running.")
        sys.exit(1)
    process_guard.close_previous_instance()
    process_guard.write_lock()

    _config = persona_config.load_config()
    _persona = persona_config.effective_by_name(cfg.DEFAULT_PERSONA_NAME, personas.PERSONAS, _config)
    _session = VoiceSession(_persona)
    _session.set_voicebox_warmup_personas(
        persona_config.voicebox_personas(personas.PERSONAS, _config)
    )
    _session.build()

    # Built before installing the handler so a close during build() (unlikely,
    # but build() does I/O) can't reach a session that isn't ready to shut down.
    _cleanup_done = threading.Event()

    def _on_console_close() -> None:
        # Closing the console window sends CTRL_CLOSE_EVENT, which CPython
        # does not turn into KeyboardInterrupt the way it does Ctrl+C; left
        # unhandled, Windows force-kills this process a few seconds later,
        # skipping the finally block below entirely and leaving the session's
        # delegated agent subprocesses and the Voicebox server running.
        # session.shutdown() is documented safe to call from any thread; wait
        # here (on Windows' own console-control thread) so Windows doesn't
        # kill mid-cleanup.
        _session.shutdown()
        _cleanup_done.wait(timeout=10.0)

    process_guard.install_close_handler(_on_console_close)

    try:
        asyncio.run(main(_session))
    finally:
        _cleanup_done.set()
        try:
            count = dashboard.stop_loaded_models(cfg.OLLAMA_HOST)
            logger.info(f"Unloaded {count} Ollama model(s).")
        except Exception as exc:
            logger.warning(f"Could not unload models: {exc}")
        process_guard.release_lock()

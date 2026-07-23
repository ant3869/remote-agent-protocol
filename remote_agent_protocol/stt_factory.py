"""STT factory -- build the speech-to-text service from config.

Keeps STT selection in ONE place (DRY) so session.py doesn't care which engine
is active. Two engines:

  * "whisper"   -- faster-whisper (OpenAI Whisper). Far more accurate than
                   Moonshine, and on your GPU the big models are still snappy.
                   This is the "amazing" quality you saw in Voicebox.
  * "moonshine" -- the original tiny ONNX model. Fastest, lowest accuracy,
                   pure CPU. Kept as a fallback / for low-resource runs.

Whisper on Windows needs the CUDA DLLs on the search path (see cuda_dlls.py),
which is why we import that first and lazily import the Whisper service only
when actually needed.
"""

from loguru import logger

from remote_agent_protocol import config as cfg

# Friendly names -> what faster-whisper actually loads. Anything not listed is
# passed through verbatim, so you can point WHISPER_MODEL at any HF/CT2 repo.
_MODEL_MAP = {
    "tiny": "tiny",
    "base": "base",
    "small": "small",
    "medium": "medium",
    "large": "large-v3",
    "large-v3": "large-v3",
    "large-v3-turbo": "deepdml/faster-whisper-large-v3-turbo-ct2",
    "turbo": "deepdml/faster-whisper-large-v3-turbo-ct2",
    "distil-large": "Systran/faster-distil-whisper-large-v2",
    "distil-medium-en": "Systran/faster-distil-whisper-medium.en",
}


def create_stt():
    """Return an STT service instance based on config.STT_ENGINE."""
    engine = cfg.STT_ENGINE.lower()

    if engine == "moonshine":
        from pipecat.services.moonshine.stt import MoonshineSTTService

        logger.info("STT engine: Moonshine (tiny, CPU)")
        return MoonshineSTTService()

    if engine != "whisper":
        logger.warning(f"Unknown STT_ENGINE '{engine}', defaulting to whisper")

    return _create_whisper()


def _create_whisper():
    from remote_agent_protocol import (
        cuda_dlls,  # registers CUDA DLL dirs BEFORE faster_whisper imports
    )

    device = cfg.WHISPER_DEVICE
    compute = cfg.WHISPER_COMPUTE_TYPE

    # Fall back to CPU if the GPU isn't usable, instead of crashing mid-sentence.
    if device in ("cuda", "auto") and not cuda_dlls.cuda_available():
        logger.warning("Whisper: CUDA not available -> falling back to CPU (int8)")
        device, compute = "cpu", "int8"

    from pipecat.services.whisper.stt import WhisperSTTService

    model = _MODEL_MAP.get(cfg.WHISPER_MODEL, cfg.WHISPER_MODEL)
    logger.info(f"STT engine: Whisper '{cfg.WHISPER_MODEL}' on {device} ({compute})")
    return WhisperSTTService(
        device=device,
        compute_type=compute,
        settings=WhisperSTTService.Settings(model=model),
    )

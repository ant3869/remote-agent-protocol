"""Coqui TTS provider integration."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger

from remote_agent_protocol import config as cfg

COQUI_PROVIDER_ID = "coqui"
_MODELS_CACHE: tuple[float, list[CoquiModelInfo]] | None = None
_LAST_ERROR = ""
_LAST_LOADED_MODEL = ""
_LAST_DEVICE = cfg.COQUI_TTS_DEVICE
_LAST_SPEAKERS: list[str] = []
_LAST_LANGUAGES: list[str] = []


@dataclass(frozen=True)
class CoquiAvailability:
    """Runtime availability for the Coqui Python API."""

    available: bool
    label: str
    error: str = ""
    source: str = ""


@dataclass(frozen=True)
class CoquiModelInfo:
    """One Coqui model row suitable for settings UI pickers."""

    id: str
    label: str
    model_type: str
    language: str
    dataset: str
    model_name: str
    installed: bool = False
    supports_speakers: bool | None = None
    supports_languages: bool | None = None

    def as_payload(self) -> dict[str, Any]:
        """Return a JSON-friendly model row."""
        return asdict(self)


@dataclass(frozen=True)
class CoquiSynthesisResult:
    """PCM audio returned from Coqui synthesis."""

    pcm: bytes
    sample_rate: int
    model: str
    speaker: str
    language: str
    device: str
    elapsed_secs: float


def availability() -> CoquiAvailability:
    """Check whether ``from TTS.api import TTS`` works."""
    try:
        tts_class = _import_tts_class()
    except Exception as exc:
        return CoquiAvailability(False, "Coqui missing", str(exc), _source_dir_label())
    source = getattr(sys.modules.get(tts_class.__module__), "__file__", "") or _source_dir_label()
    return CoquiAvailability(True, "Coqui installed", source=source)


def list_models(*, refresh: bool = False) -> list[CoquiModelInfo]:
    """List Coqui models without downloading or loading them."""
    global _MODELS_CACHE
    now = time.monotonic()
    if (
        not refresh
        and _MODELS_CACHE is not None
        and now - _MODELS_CACHE[0] < cfg.COQUI_TTS_MODEL_CACHE_SECS
    ):
        return _MODELS_CACHE[1]
    models = _read_models_file()
    _MODELS_CACHE = (now, models)
    logger.info(f"Coqui model list refreshed: {len(models)} model(s)")
    return models


def status_payload(selected_model: str | None = None, *, refresh: bool = False) -> dict[str, Any]:
    """Return Coqui state for the web setup/status panes."""
    avail = availability()
    selected = selected_model or cfg.COQUI_TTS_MODEL
    models = list_models(refresh=refresh)
    model = next((row for row in models if row.id == selected), None)
    return {
        "provider": COQUI_PROVIDER_ID,
        "available": avail.available,
        "label": avail.label,
        "error": _LAST_ERROR or avail.error,
        "source": avail.source,
        "selectedModel": selected,
        "loadedModel": _LAST_LOADED_MODEL,
        "loaded": bool(_LAST_LOADED_MODEL and _LAST_LOADED_MODEL == selected),
        "device": _LAST_DEVICE,
        "speakers": _LAST_SPEAKERS,
        "languages": _LAST_LANGUAGES,
        "model": model.as_payload() if model else None,
        "models": [row.as_payload() for row in models],
    }


class CoquiTTSProvider:
    """Small provider facade around Coqui's blocking Python API."""

    id = COQUI_PROVIDER_ID
    label = "Coqui"

    def __init__(self) -> None:
        """Initialize an unloaded provider."""
        self._tts = None
        self._model_id = ""
        self._speaker_names: list[str] = []
        self._language_names: list[str] = []
        self._device = cfg.COQUI_TTS_DEVICE

    async def is_available(self) -> bool:
        """Return True when the Coqui API can be imported."""
        return availability().available

    async def list_models(self) -> list[CoquiModelInfo]:
        """Return cached model discovery rows."""
        return list_models()

    async def load_model(self, model_id: str, options: dict[str, Any] | None = None) -> None:
        """Load the selected Coqui model, downloading it if Coqui needs to."""
        await asyncio.to_thread(self._load_model_sync, model_id, options or {})

    async def synthesize(
        self, text: str, options: dict[str, Any] | None = None
    ) -> CoquiSynthesisResult:
        """Synthesize text and return mono int16 PCM."""
        return await asyncio.to_thread(self._synthesize_sync, text, options or {})

    async def unload(self) -> None:
        """Drop the loaded model reference."""
        self._tts = None
        self._model_id = ""
        self._speaker_names = []
        self._language_names = []

    @property
    def speakers(self) -> list[str]:
        """Speaker names exposed by the loaded model."""
        return self._speaker_names

    @property
    def languages(self) -> list[str]:
        """Language names exposed by the loaded model."""
        return self._language_names

    def _load_model_sync(self, model_id: str, options: dict[str, Any]) -> None:
        global _LAST_DEVICE, _LAST_ERROR, _LAST_LANGUAGES, _LAST_LOADED_MODEL, _LAST_SPEAKERS
        model = (model_id or cfg.COQUI_TTS_MODEL).strip()
        device = str(options.get("device") or cfg.COQUI_TTS_DEVICE).strip().lower()
        if self._tts is not None and self._model_id == model and self._device == device:
            return
        started = time.perf_counter()
        try:
            tts_class = _import_tts_class()
            gpu = device == "cuda"
            tts = tts_class(model_name=model, progress_bar=False, gpu=gpu)
            if device in {"cuda", "mps"} and hasattr(tts, "to"):
                tts.to(device)
            self._tts = tts
            self._model_id = model
            self._device = device
            self._speaker_names = list(getattr(tts, "speakers", None) or [])
            self._language_names = list(getattr(tts, "languages", None) or [])
            _LAST_LOADED_MODEL = model
            _LAST_DEVICE = device
            _LAST_SPEAKERS = self._speaker_names
            _LAST_LANGUAGES = self._language_names
            _LAST_ERROR = ""
            logger.info(
                f"Coqui model loaded: {model} on {device} ({time.perf_counter() - started:.2f}s)"
            )
        except Exception as exc:
            _LAST_ERROR = str(exc)
            logger.warning(f"Coqui model load failed for {model}: {exc}")
            raise

    def _synthesize_sync(self, text: str, options: dict[str, Any]) -> CoquiSynthesisResult:
        global _LAST_ERROR
        model = str(options.get("model") or cfg.COQUI_TTS_MODEL)
        self._load_model_sync(model, options)
        assert self._tts is not None
        speaker = str(options.get("speaker") or cfg.COQUI_TTS_SPEAKER or "").strip()
        language = str(options.get("language") or cfg.COQUI_TTS_LANGUAGE or "").strip()
        if not speaker and self._speaker_names:
            speaker = self._speaker_names[0]
        if not language and self._language_names:
            language = self._language_names[0]
        started = time.perf_counter()
        try:
            wav = self._tts.tts(
                text=text,
                speaker=speaker or None,
                language=language or None,
                split_sentences=True,
            )
            sample_rate = int(
                getattr(getattr(self._tts, "synthesizer", None), "output_sample_rate", 0)
                or cfg.COQUI_TTS_SAMPLE_RATE
            )
            pcm = _float_wav_to_pcm(wav)
            elapsed = time.perf_counter() - started
            _LAST_ERROR = ""
            logger.info(
                f"Coqui synthesis completed: model={model}, speaker={speaker or '-'}, "
                f"language={language or '-'}, {len(pcm)} bytes in {elapsed:.2f}s"
            )
            return CoquiSynthesisResult(
                pcm=pcm,
                sample_rate=sample_rate,
                model=model,
                speaker=speaker,
                language=language,
                device=self._device,
                elapsed_secs=elapsed,
            )
        except Exception as exc:
            _LAST_ERROR = str(exc)
            logger.warning(f"Coqui synthesis failed: {exc}")
            raise


def _float_wav_to_pcm(wav: Any) -> bytes:
    arr = np.asarray(wav, dtype=np.float32)
    if arr.ndim > 1:
        arr = arr.mean(axis=1)
    return (np.clip(arr, -1.0, 1.0) * 32767).astype(np.int16).tobytes()


def _import_tts_class():
    try:
        return importlib.import_module("TTS.api").TTS
    except Exception as first_error:
        source = Path(cfg.COQUI_TTS_SOURCE_DIR)
        if not (source / "TTS" / "api.py").exists():
            raise first_error
        if str(source) not in sys.path:
            sys.path.insert(0, str(source))
        if "TTS" in sys.modules and not getattr(sys.modules["TTS"], "__file__", None):
            sys.modules.pop("TTS", None)
        return importlib.import_module("TTS.api").TTS


def _read_models_file() -> list[CoquiModelInfo]:
    path = _models_file_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"Coqui model list unavailable: {exc}")
        return []
    rows: list[CoquiModelInfo] = []
    for model_type, languages in raw.items():
        if not isinstance(languages, dict):
            continue
        for language, datasets in languages.items():
            if not isinstance(datasets, dict):
                continue
            for dataset, models in datasets.items():
                if not isinstance(models, dict):
                    continue
                for model_name in models:
                    model_id = f"{model_type}/{language}/{dataset}/{model_name}"
                    rows.append(
                        CoquiModelInfo(
                            id=model_id,
                            label=model_id,
                            model_type=model_type,
                            language=language,
                            dataset=dataset,
                            model_name=model_name,
                            installed=_downloaded_model_dir(model_id).exists(),
                        )
                    )
    return rows


def _models_file_path() -> Path:
    source_file = Path(cfg.COQUI_TTS_SOURCE_DIR) / "TTS" / ".models.json"
    if source_file.exists():
        return source_file
    try:
        tts_class = _import_tts_class()
        return Path(tts_class.get_models_file_path())
    except Exception:
        return source_file


def _downloaded_model_dir(model_id: str) -> Path:
    return _coqui_data_dir() / model_id.replace("/", "--")


def _coqui_data_dir() -> Path:
    if os.environ.get("TTS_HOME"):
        return Path(os.environ["TTS_HOME"]).expanduser() / "tts"
    if os.environ.get("XDG_DATA_HOME"):
        return Path(os.environ["XDG_DATA_HOME"]).expanduser() / "tts"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "tts"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "tts"
    return Path.home() / ".local" / "share" / "tts"


def _source_dir_label() -> str:
    return str(Path(cfg.COQUI_TTS_SOURCE_DIR))

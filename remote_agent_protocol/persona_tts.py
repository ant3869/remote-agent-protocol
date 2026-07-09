"""Routing TTS service for persona-bound voice providers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path

import aiohttp
import numpy as np
from kokoro_onnx import Kokoro
from loguru import logger

from pipecat.audio.utils import create_stream_resampler
from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame
from pipecat.services.kokoro.tts import (
    KOKORO_CACHE_DIR,
    _ensure_model_files,
    language_to_kokoro_language,
)
from pipecat.services.settings import TTSSettings, assert_given
from pipecat.services.tts_service import TTSService
from pipecat.transcriptions.language import Language
from pipecat.utils.tracing.service_decorators import traced_tts
from remote_agent_protocol import config as cfg
from remote_agent_protocol import coqui_tts, voicebox, voices


@dataclass
class PersonaTTSSettings(TTSSettings):
    """TTS settings plus the persona-level backend/personality switches."""

    voice_backend: str = "kokoro"
    personality: bool = False
    extra: dict = field(default_factory=dict)


class PersonaTTSService(TTSService):
    """One Pipecat TTS processor that can speak Kokoro, Voicebox, or Coqui voices."""

    Settings = PersonaTTSSettings
    _settings: Settings

    def __init__(
        self,
        *,
        settings: Settings,
        sample_rate: int | None = None,
        kokoro_model_path: str | None = None,
        kokoro_voices_path: str | None = None,
    ):
        """Initialize the service.

        Args:
            settings: Voice/backend settings (``PersonaTTSSettings``).
            sample_rate: Output rate; defaults to the Voicebox rate.
            kokoro_model_path: Optional explicit Kokoro ONNX model path.
            kokoro_voices_path: Optional explicit Kokoro voices file path.
        """
        super().__init__(
            sample_rate=sample_rate or cfg.VOICEBOX_SAMPLE_RATE,
            push_start_frame=True,
            push_stop_frames=True,
            settings=settings,
        )
        model_file = (
            Path(kokoro_model_path) if kokoro_model_path else KOKORO_CACHE_DIR / "kokoro-v1.0.onnx"
        )
        voices = (
            Path(kokoro_voices_path) if kokoro_voices_path else KOKORO_CACHE_DIR / "voices-v1.0.bin"
        )
        _ensure_model_files(model_file, voices)
        self._kokoro = Kokoro(str(model_file), str(voices))
        self._resampler = create_stream_resampler()
        self._http: aiohttp.ClientSession | None = None
        self._voicebox_ready = False
        self._voicebox_model_fallbacks: dict[str, str] = {}
        self._coqui = coqui_tts.CoquiTTSProvider()

    def can_generate_metrics(self) -> bool:
        """This service reports TTFB and usage metrics."""
        return True

    def language_to_service_language(self, language: Language) -> str:
        """Map a Pipecat language to Kokoro's language code."""
        return language_to_kokoro_language(language)

    def voicebox_model_for_request(self, requested: str | None) -> str:
        """Resolve the Voicebox model, honoring learned fallbacks."""
        model = requested or cfg.VOICEBOX_DEFAULT_MODEL
        return self._voicebox_model_fallbacks.get(model, model)

    def remember_voicebox_model_fallback(self, requested: str, fallback: str) -> None:
        """Record that ``requested`` is unavailable and ``fallback`` works."""
        self._voicebox_model_fallbacks[requested] = fallback

    async def stop(self, frame):
        """Close the HTTP session, then run the base stop handling."""
        await self._close_http_session()
        await super().stop(frame)

    async def cancel(self, frame):
        """Close the HTTP session, then run the base cancel handling."""
        await self._close_http_session()
        await super().cancel(frame)

    async def warm_voicebox_for(
        self, voice_ref: str, model: str | None, text: str = "warmup"
    ) -> None:
        """Start Voicebox and preload a profile/model without changing active persona."""
        if not voicebox.is_voicebox_ref(voice_ref):
            return
        async for frame in self._run_voicebox(
            text, "voicebox-warmup", voice_ref=voice_ref, model=model
        ):
            if isinstance(frame, ErrorFrame):
                logger.warning(f"Voicebox warmup failed: {frame.error}")
            break

    @traced_tts
    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        """Synthesize ``text`` with whichever backend the active voice needs."""
        if self._uses_voicebox():
            async for frame in self._run_voicebox(text, context_id):
                yield frame
            return
        if self._uses_coqui():
            async for frame in self._run_coqui(text, context_id):
                yield frame
            return
        async for frame in self._run_kokoro(text, context_id):
            yield frame

    def _uses_voicebox(self) -> bool:
        backend = assert_given(self._settings.voice_backend) or "kokoro"
        voice = assert_given(self._settings.voice) or ""
        return backend == "voicebox" or voicebox.is_voicebox_ref(voice)

    def _uses_coqui(self) -> bool:
        backend = assert_given(self._settings.voice_backend) or "kokoro"
        return backend == coqui_tts.COQUI_PROVIDER_ID

    async def _http_session(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession()
        return self._http

    async def _close_http_session(self) -> None:
        if self._http is not None and not self._http.closed:
            await self._http.close()
        self._http = None

    async def _run_kokoro(
        self, text: str, context_id: str, *, voice_override: str | None = None
    ) -> AsyncGenerator[Frame | None, None]:
        logger.debug(f"{self}: Generating Kokoro TTS [{text}]")
        try:
            await self.start_tts_usage_metrics(text)
            voice = voice_override or assert_given(self._settings.voice)
            if voice is None:
                raise ValueError("Kokoro TTS voice must be specified")
            stream = self._kokoro.create_stream(text, voice=voice, lang="en-us", speed=1.0)
            async for samples, sample_rate in stream:
                await self.stop_ttfb_metrics()
                audio_int16 = (samples * 32767).astype(np.int16).tobytes()
                audio_data = await self._resampler.resample(
                    audio_int16, sample_rate, self.sample_rate
                )
                yield TTSAudioRawFrame(
                    audio=audio_data,
                    sample_rate=self.sample_rate,
                    num_channels=1,
                    context_id=context_id,
                )
        except Exception as exc:
            yield ErrorFrame(error=f"Kokoro TTS failed: {exc}")
        finally:
            await self.stop_ttfb_metrics()

    def _fallback_kokoro_voice(self) -> str:
        extra = assert_given(self._settings.extra) or {}
        candidates = (
            str(extra.get("fallback_voice") or ""),
            str(assert_given(self._settings.voice) or ""),
            "af_heart",
        )
        return next((voice for voice in candidates if voices.is_valid(voice)), "af_heart")

    async def _ensure_voicebox_ready(self) -> None:
        if self._voicebox_ready:
            return
        voicebox.start_server_once()
        session = await self._http_session()
        for _ in range(60):
            try:
                async with session.get(f"{voicebox.base_url()}/health") as response:
                    if response.status == 200:
                        self._voicebox_ready = True
                        return
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(0.5)
        raise TimeoutError("Voicebox server did not become healthy in time")

    async def _run_voicebox(
        self,
        text: str,
        context_id: str,
        *,
        voice_ref: str | None = None,
        model: str | None = None,
    ) -> AsyncGenerator[Frame | None, None]:
        logger.debug(f"{self}: Generating Voicebox TTS [{text}]")
        voice_ref = voice_ref or assert_given(self._settings.voice) or ""
        profile_id = voicebox.profile_id_from_ref(voice_ref)
        if not profile_id:
            yield ErrorFrame(
                error=f"Voicebox voice must look like voicebox:<profile_id>, got {voice_ref!r}"
            )
            return
        requested_model = model or assert_given(self._settings.model) or cfg.VOICEBOX_DEFAULT_MODEL
        payload = {
            "profile_id": profile_id,
            "text": text,
            "language": "en",
            "engine": cfg.VOICEBOX_DEFAULT_ENGINE,
            "model_size": self.voicebox_model_for_request(requested_model),
            "personality": bool(assert_given(self._settings.personality)),
        }
        try:
            await self.start_tts_usage_metrics(text)
            await self._ensure_voicebox_ready()
            async for chunk, rate in self._post_voicebox_stream(payload, requested_model):
                await self.stop_ttfb_metrics()
                yield TTSAudioRawFrame(
                    audio=chunk, sample_rate=rate, num_channels=1, context_id=context_id
                )
        except Exception as exc:
            yield ErrorFrame(error=f"Voicebox TTS failed: {exc}")
        finally:
            await self.stop_ttfb_metrics()

    async def _post_voicebox_stream(
        self, payload: dict, requested_model: str
    ) -> AsyncGenerator[tuple[bytes, int], None]:
        session = await self._http_session()
        async with session.post(f"{voicebox.base_url()}/generate/stream", json=payload) as response:
            if response.status != 200:
                error_text = await response.text()
                if (
                    "not downloaded" not in error_text
                    or payload["model_size"] == cfg.VOICEBOX_DEFAULT_MODEL
                ):
                    raise RuntimeError(f"Voicebox TTS error {response.status}: {error_text}")
                self.remember_voicebox_model_fallback(requested_model, cfg.VOICEBOX_DEFAULT_MODEL)
                fallback = dict(payload, model_size=cfg.VOICEBOX_DEFAULT_MODEL)
                async for chunk in self._post_voicebox_stream(fallback, requested_model):
                    yield chunk
                return
            parser = voicebox.WavStreamParser()
            async for data in response.content.iter_chunked(8192):
                for pcm, rate in parser.feed(data):
                    yield pcm, rate
            for pcm, rate in parser.finish():
                yield pcm, rate

    async def _run_coqui(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        logger.debug(f"{self}: Generating Coqui TTS [{text}]")
        requested_model = assert_given(self._settings.model) or cfg.COQUI_TTS_MODEL
        extra = assert_given(self._settings.extra) or {}
        options = {
            "model": requested_model,
            "speaker": extra.get("speaker") or assert_given(self._settings.voice) or "",
            "language": extra.get("language") or assert_given(self._settings.language) or "",
            "device": extra.get("device") or cfg.COQUI_TTS_DEVICE,
        }
        try:
            await self.start_tts_usage_metrics(text)
            result = await self._coqui.synthesize(text, options)
            await self.stop_ttfb_metrics()
            audio_data = await self._resampler.resample(
                result.pcm, result.sample_rate, self.sample_rate
            )
            yield TTSAudioRawFrame(
                audio=audio_data,
                sample_rate=self.sample_rate,
                num_channels=1,
                context_id=context_id,
            )
        except Exception as exc:
            fallback_voice = self._fallback_kokoro_voice()
            logger.warning(
                f"Coqui TTS failed ({exc}); falling back to Kokoro voice {fallback_voice}"
            )
            async for frame in self._run_kokoro(text, context_id, voice_override=fallback_voice):
                yield frame
        finally:
            await self.stop_ttfb_metrics()

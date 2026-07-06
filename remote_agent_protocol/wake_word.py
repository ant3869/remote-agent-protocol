"""Wake-word gate for the live audio path, plus configuration/preflight helpers.

When enabled, ``WakeWordGate`` sits between the input transport and STT and
drops microphone audio until the wake phrase is heard (openwakeword, fully
local). After a trigger the mic stays open for a configurable window, and every
finished bot reply refreshes the window so follow-ups don't need re-waking.

The gate degrades gracefully: if the engine or its model can't load, it passes
all audio through (always-listening) and says so via an event, so a broken
optional dependency can never mute the app.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from loguru import logger

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    StartFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

# openwakeword's native input format: 80 ms of 16 kHz mono int16 samples.
SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1280
_CHUNK_BYTES = CHUNK_SAMPLES * 2


@dataclass(frozen=True)
class WakeWordTarget:
    """One detector model mapped to the persona it activates."""

    model: str
    persona: str
    threshold: float


@dataclass(frozen=True)
class WakeWordSettings:
    """Operator-facing wake-word configuration (see config.py)."""

    enabled: bool
    engine: str = "openwakeword"
    model: str = "hey_jarvis"
    threshold: float = 0.5
    active_window_secs: float = 12.0
    targets: tuple[WakeWordTarget, ...] = ()
    switch_timeout_secs: float = 0.5

    @property
    def effective_targets(self) -> tuple[WakeWordTarget, ...]:
        """Configured multi-model targets, or the legacy single model."""
        return self.targets or (WakeWordTarget(self.model, "", self.threshold),)


@dataclass(frozen=True)
class WakeWordStatus:
    """Preflight result: whether the configured engine can actually run."""

    enabled: bool
    ready: bool
    message: str


def discover_local_models() -> set[str]:
    """Return canonical wake model names already installed on this machine."""
    spec = importlib.util.find_spec("openwakeword")
    if spec is None or not spec.submodule_search_locations:
        return set()
    model_dir = Path(next(iter(spec.submodule_search_locations))) / "resources" / "models"
    if not model_dir.is_dir():
        return set()
    names = set()
    for path in model_dir.glob("*.onnx"):
        if path.stem.startswith("hey_"):
            names.add(re.sub(r"_v\d+(?:\.\d+)*$", "", path.stem))
    return names


def resolve_targets(
    mapping: dict[str, str],
    *,
    available_models: set[str],
    persona_names: set[str],
    threshold: float,
) -> tuple[WakeWordTarget, ...]:
    """Resolve explicit mappings or match installed model names to personas."""
    if mapping:
        pairs = mapping.items()
    else:
        personas_by_key = {name.lower().replace(" ", "_"): name for name in persona_names}
        pairs = (
            (model, personas_by_key[model.removeprefix("hey_").lower()])
            for model in sorted(available_models)
            if model.removeprefix("hey_").lower() in personas_by_key
        )
    targets = []
    for model, persona in pairs:
        if model not in available_models:
            logger.warning(f"Wake model '{model}' is not installed; skipping persona '{persona}'")
        elif persona not in persona_names:
            logger.warning(f"Wake model '{model}' names unknown persona '{persona}'; skipping")
        else:
            targets.append(WakeWordTarget(model, persona, threshold))
    return tuple(targets)


def settings_from_config(cfg) -> WakeWordSettings:
    """Build WakeWordSettings from the app config module."""
    targets = ()
    if cfg.WAKE_WORD_ENABLED:
        from remote_agent_protocol import personas

        targets = resolve_targets(
            getattr(cfg, "WAKE_WORD_PERSONAS", {}),
            available_models=discover_local_models(),
            persona_names=set(personas.names()),
            threshold=cfg.WAKE_WORD_THRESHOLD,
        )
        if not targets:
            targets = (
                WakeWordTarget(
                    cfg.WAKE_WORD_MODEL,
                    getattr(cfg, "DEFAULT_PERSONA_NAME", ""),
                    cfg.WAKE_WORD_THRESHOLD,
                ),
            )
    return WakeWordSettings(
        enabled=cfg.WAKE_WORD_ENABLED,
        engine=cfg.WAKE_WORD_ENGINE,
        model=cfg.WAKE_WORD_MODEL,
        threshold=cfg.WAKE_WORD_THRESHOLD,
        active_window_secs=cfg.WAKE_WORD_ACTIVE_WINDOW_SECS,
        targets=targets,
        switch_timeout_secs=getattr(cfg, "WAKE_WORD_SWITCH_TIMEOUT_SECS", 0.5),
    )


def preflight(settings: WakeWordSettings) -> WakeWordStatus:
    """Return whether the configured wake-word engine is usable."""
    if not settings.enabled:
        return WakeWordStatus(enabled=False, ready=True, message="wake word disabled")
    if settings.engine != "openwakeword":
        return WakeWordStatus(
            enabled=True,
            ready=False,
            message=f"unsupported wake word engine: {settings.engine}",
        )
    if importlib.util.find_spec("openwakeword") is None:
        return WakeWordStatus(
            enabled=True,
            ready=False,
            message="openwakeword is not installed; run pip install openwakeword==0.6.0",
        )
    return WakeWordStatus(
        enabled=True,
        ready=True,
        message=(
            "openwakeword ready "
            f"({', '.join(target.model for target in settings.effective_targets)}) "
            "-- mic gated until wake phrase"
        ),
    )


def _openwakeword_detector(settings: WakeWordSettings):
    """Load the real openwakeword model, downloading it on first use."""
    from openwakeword.model import Model

    def build() -> Model:
        return Model(
            wakeword_models=[target.model for target in settings.effective_targets],
            inference_framework="onnx",
        )

    try:
        return build()
    except Exception:
        # 0.6.x ships without model files; fetch just what we need (one time).
        models = [target.model for target in settings.effective_targets]
        logger.info(f"Downloading openwakeword models: {', '.join(models)}")
        from openwakeword.utils import download_models

        download_models(model_names=models)
        return build()


class WakeWordGate(FrameProcessor):
    """Drop mic audio until the wake phrase is heard; re-arm after a quiet window.

    States (reported via ``on_event`` as ``{"type": "wake", "state": ...}``):

    - ``armed``: audio is dropped and fed to the detector.
    - ``awake``: audio passes through; expires ``active_window_secs`` after the
      last trigger or bot reply.
    - ``bypass``: the detector could not be built (or audio isn't 16 kHz mono);
      all audio passes through, exactly like wake word off.

    A finished bot reply (``BotStoppedSpeakingFrame``) always opens/refreshes
    the window: when Jess just said something -- including a background agent
    question -- the user must be able to answer without re-waking her.

    The window is also VAD-aware: while the user is mid-utterance (between the
    broadcast ``UserStartedSpeakingFrame`` / ``UserStoppedSpeakingFrame``) it
    never lapses, and each finished user turn refreshes it -- so a long
    sentence can't be cut off and a flowing conversation stays open.
    """

    def __init__(
        self,
        settings: WakeWordSettings,
        *,
        detector_factory: Callable[[WakeWordSettings], object] | None = None,
        on_event: Callable[[dict], None] | None = None,
        on_persona: Callable[[str], object] | None = None,
        **kwargs,
    ):
        """Initialize the gate.

        Args:
            settings: Wake-word configuration (model, threshold, window).
            detector_factory: Builds the detector from settings; defaults to
                the real openwakeword model. Injectable for tests.
            on_event: Callback receiving ``{"type": "wake", ...}`` state events.
            on_persona: Sync or async callback that applies the detected persona.
            **kwargs: Additional arguments passed to FrameProcessor.
        """
        super().__init__(**kwargs)
        self._settings = settings
        self._detector_factory = detector_factory or _openwakeword_detector
        self._on_event = on_event
        self._on_persona = on_persona
        self._detector = None
        self._bypass = False
        self._awake_until = 0.0
        self._user_speaking = False
        self._buffer = bytearray()
        self._warned_format = False
        self._active_target: WakeWordTarget | None = None

    @property
    def awake(self) -> bool:
        """Whether the mic window is currently open."""
        return time.monotonic() < self._awake_until

    def _emit(
        self,
        state: str,
        target: WakeWordTarget | None = None,
        *,
        score: float | None = None,
        error: str = "",
    ) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(
                {
                    "type": "wake",
                    "state": state,
                    "model": target.model if target else self._settings.model,
                    "persona": target.persona if target else "",
                    "score": score,
                    "error": error,
                    "window_secs": self._settings.active_window_secs,
                }
            )
        except Exception as exc:
            logger.warning(f"WakeWordGate on_event raised: {exc}")

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Gate input audio; pass every other frame through untouched."""
        await super().process_frame(frame, direction)
        if isinstance(frame, StartFrame):
            await self._setup_detector()
            await self.push_frame(frame, direction)
        elif isinstance(frame, BotStoppedSpeakingFrame):
            if not self._bypass:
                self._open_window()
            await self.push_frame(frame, direction)
        elif isinstance(frame, BotStartedSpeakingFrame):
            self._user_speaking = False  # safety valve if a VAD stop went missing
            await self.push_frame(frame, direction)
        elif isinstance(frame, UserStartedSpeakingFrame):
            self._user_speaking = True
            await self.push_frame(frame, direction)
        elif isinstance(frame, UserStoppedSpeakingFrame):
            self._user_speaking = False
            if not self._bypass and self._awake_until:
                self._open_window()  # each finished turn earns a fresh window
            await self.push_frame(frame, direction)
        elif isinstance(frame, InputAudioRawFrame):
            if self._bypass or not self._gateable(frame):
                await self.push_frame(frame, direction)
            elif self._user_speaking:  # never cut off mid-utterance
                await self.push_frame(frame, direction)
            else:
                if self._awake_until:  # window just lapsed -> re-arm once
                    if not self.awake:
                        self._awake_until = 0.0
                        self._rearm()
                detected = self._listen(frame.audio)
                if detected is not None:
                    target, score = detected
                    if await self._activate(target, score):
                        logger.info(
                            f"Wake word '{target.model}' detected -> {target.persona or '-'}"
                        )
                elif self.awake:
                    await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)

    async def _setup_detector(self) -> None:
        try:
            self._detector = await asyncio.to_thread(self._detector_factory, self._settings)
        except Exception as exc:
            self._bypass = True
            logger.warning(f"Wake word engine unavailable ({exc}); staying always-listening")
            self._emit("bypass")
            return
        self._emit("armed")
        models = ", ".join(target.model for target in self._settings.effective_targets)
        logger.info(f"Wake word gate armed: say one of [{models}] to open the mic")

    def _gateable(self, frame: InputAudioRawFrame) -> bool:
        """Only gate the format the detector understands; otherwise pass audio."""
        if frame.sample_rate == SAMPLE_RATE and frame.num_channels == 1:
            return True
        if not self._warned_format:
            self._warned_format = True
            logger.warning(
                f"Wake word gate bypassed: needs {SAMPLE_RATE} Hz mono, got "
                f"{frame.sample_rate} Hz x{frame.num_channels}"
            )
        return False

    async def _activate(self, target: WakeWordTarget, score: float) -> bool:
        """Apply a target persona before opening the microphone window."""
        try:
            if target.persona and self._on_persona is not None:
                result = self._on_persona(target.persona)
                if inspect.isawaitable(result):
                    await asyncio.wait_for(result, self._settings.switch_timeout_secs)
        except Exception as exc:
            logger.warning(f"Wake persona switch failed ({exc}); staying armed")
            self._emit("switch_failed", target, score=score, error=str(exc))
            return False
        self._active_target = target
        self._open_window(target, score=score)
        return True

    def _open_window(
        self, target: WakeWordTarget | None = None, *, score: float | None = None
    ) -> None:
        was_awake = self.awake
        self._awake_until = time.monotonic() + self._settings.active_window_secs
        if not was_awake:
            self._emit("awake", target or self._active_target, score=score)

    def _rearm(self) -> None:
        self._buffer.clear()
        reset = getattr(self._detector, "reset", None)
        if callable(reset):
            reset()
        self._emit("armed")

    def _listen(self, audio: bytes) -> tuple[WakeWordTarget, float] | None:
        """Feed dropped audio to the detector in its native 80 ms chunks."""
        self._buffer.extend(audio)
        while len(self._buffer) >= _CHUNK_BYTES:
            chunk = np.frombuffer(bytes(self._buffer[:_CHUNK_BYTES]), dtype=np.int16)
            del self._buffer[:_CHUNK_BYTES]
            try:
                scores: Mapping[str, float] = self._detector.predict(chunk)
            except Exception as exc:
                self._bypass = True
                logger.warning(f"Wake word detector failed ({exc}); disabling gate")
                self._emit("bypass")
                return None
            candidates = []
            for target in self._settings.effective_targets:
                score = next(
                    (
                        value
                        for model, value in scores.items()
                        if model == target.model
                        or re.sub(r"_v\d+(?:\.\d+)*$", "", model) == target.model
                    ),
                    0.0,
                )
                if score >= target.threshold:
                    candidates.append((score, target))
            if candidates:
                score, target = max(candidates, key=lambda item: item[0])
                self._buffer.clear()
                reset = getattr(self._detector, "reset", None)
                if callable(reset):
                    reset()
                return target, score
        return None

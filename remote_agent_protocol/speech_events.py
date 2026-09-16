"""Ordered speech boundaries for text and local output delivery correlation."""

from dataclasses import dataclass, field

from pipecat.frames.frames import (
    CancelFrame,
    DataFrame,
    ErrorFrame,
    Frame,
    InterruptionFrame,
    OutputAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


@dataclass
class SpeechBoundaryFrame(DataFrame):
    """An utterance boundary serialized alongside TTS audio through the transport.

    Parameters:
        utterance: Stable attribution and message metadata.
        ending: Whether all audio for this utterance has been queued before this frame.
    """

    utterance: dict = field(default_factory=dict)
    ending: bool = False


class SpeechPlaybackTap(FrameProcessor):
    """Report delivery only after frames pass the output transport."""

    def __init__(self, on_event, pending_speech: dict | None = None, **kwargs):
        """Observe ordered boundaries without guessing identity from voice settings."""
        super().__init__(**kwargs)
        self._on_event = on_event
        self._active: dict[str, tuple[dict, bool]] = {}
        self._pending_speech = pending_speech if pending_speech is not None else {}

    def _report(self, utterance: dict, delivery: str) -> None:
        self._on_event(
            {
                "type": "transcript",
                "message_id": utterance["message_id"],
                "session_id": utterance.get("session_id"),
                "playback_update": True,
                "delivery": delivery,
            }
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Track audio submitted to the device; preserve interruption/failure state."""
        await super().process_frame(frame, direction)
        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, SpeechBoundaryFrame):
                ident = frame.utterance["message_id"]
                if frame.ending:
                    active = self._active.pop(ident, None)
                    self._pending_speech.pop(ident, None)
                    if active:
                        self._report(active[0], "played" if active[1] else "playback_unconfirmed")
                else:
                    self._active[ident] = (frame.utterance, False)
                return  # Application markers do not belong in LLM aggregation.
            if isinstance(frame, OutputAudioRawFrame):
                for ident, (utterance, heard) in list(self._active.items())[-1:]:
                    if not heard:
                        self._report(utterance, "playing")
                        self._active[ident] = (utterance, True)
            elif isinstance(frame, (InterruptionFrame, CancelFrame, ErrorFrame)):
                delivery = "failed" if isinstance(frame, ErrorFrame) else "interrupted"
                for utterance, _ in self._active.values():
                    self._report(utterance, delivery)
                self._active.clear()
                self._pending_speech.clear()
        await self.push_frame(frame, direction)

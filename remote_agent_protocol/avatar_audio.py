"""Bounded avatar mouth telemetry derived from local TTS PCM."""

from __future__ import annotations

import json
import math
import struct
import threading
import time
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class AvatarAudioEnvelope:
    """A normalized latest-value TTS audio envelope."""

    rms: float
    peak: float
    voiced: bool
    sample_rate: int
    channels: int
    timestamp: float


def compute_pcm16_envelope(
    audio: bytes,
    sample_rate: int,
    channels: int,
    *,
    timestamp: float | None = None,
    silence_threshold: float = 0.012,
) -> AvatarAudioEnvelope:
    """Calculate normalized RMS and peak from little-endian signed 16-bit PCM."""
    normalized_sample_rate = max(0, int(sample_rate))
    normalized_channels = max(1, int(channels))
    observed_at = time.time() if timestamp is None else float(timestamp)
    usable = len(audio) - (len(audio) % 2)
    if usable <= 0:
        return AvatarAudioEnvelope(
            0.0,
            0.0,
            False,
            normalized_sample_rate,
            normalized_channels,
            observed_at,
        )

    count = usable // 2
    samples = struct.unpack(f"<{count}h", audio[:usable])
    peak_raw = max(abs(sample) for sample in samples)
    rms_raw = math.sqrt(sum(sample * sample for sample in samples) / count)
    rms = max(0.0, min(1.0, rms_raw / 32768.0))
    peak = max(0.0, min(1.0, peak_raw / 32768.0))
    threshold = max(0.0, min(1.0, float(silence_threshold)))
    return AvatarAudioEnvelope(
        rms=rms,
        peak=peak,
        voiced=rms >= threshold,
        sample_rate=normalized_sample_rate,
        channels=normalized_channels,
        timestamp=observed_at,
    )


class AvatarAudioEnvelopeHub:
    """Store one latest envelope and wake bounded SSE consumers."""

    def __init__(self) -> None:
        """Initialize an empty, open hub."""
        self._condition = threading.Condition()
        self._seq = 0
        self._latest: AvatarAudioEnvelope | None = None
        self._closed = False

    def publish(self, envelope: AvatarAudioEnvelope) -> None:
        """Replace the latest envelope and wake waiting clients."""
        with self._condition:
            if self._closed:
                return
            self._seq += 1
            self._latest = envelope
            self._condition.notify_all()

    def wait_after(
        self, sequence: int, *, timeout: float
    ) -> tuple[int, AvatarAudioEnvelope | None, bool]:
        """Wait for a sequence newer than ``sequence`` or for hub closure."""
        with self._condition:
            if not self._closed and self._seq <= sequence:
                self._condition.wait_for(
                    lambda: self._closed or self._seq > sequence,
                    timeout=max(0.0, float(timeout)),
                )
            latest = self._latest if self._seq > sequence else None
            return self._seq, latest, self._closed

    def close(self) -> None:
        """Close the hub and wake every waiting client."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()


def sse_data(sequence: int, envelope: AvatarAudioEnvelope) -> bytes:
    """Serialize one envelope as a server-sent event without raw audio."""
    data = json.dumps(
        {"seq": int(sequence), **asdict(envelope)},
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"id: {int(sequence)}\nevent: envelope\ndata: {data}\n\n".encode()

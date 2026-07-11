import json
import math
import struct

import pytest

from remote_agent_protocol.avatar_audio import (
    AvatarAudioEnvelope,
    AvatarAudioEnvelopeHub,
    compute_pcm16_envelope,
    sse_data,
)


def pcm16(*samples: int) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


def test_silence_produces_closed_mouth_envelope():
    envelope = compute_pcm16_envelope(b"\x00\x00" * 80, 16000, 1, timestamp=5.0)

    assert envelope.rms == 0.0
    assert envelope.peak == 0.0
    assert envelope.voiced is False
    assert envelope.timestamp == 5.0


def test_pcm_envelope_is_normalized_and_bounded():
    envelope = compute_pcm16_envelope(
        pcm16(32767, -32768, 16384, -16384), 24000, 1, timestamp=7.0
    )

    expected_rms = math.sqrt((32767**2 + 32768**2 + 16384**2 + 16384**2) / 4) / 32768
    assert envelope.rms == pytest.approx(expected_rms)
    assert envelope.peak == 1.0
    assert envelope.voiced is True


def test_empty_and_odd_length_pcm_are_safe():
    empty = compute_pcm16_envelope(b"", 0, 0, timestamp=0.0)
    odd = compute_pcm16_envelope(b"\xff", 16000, 1, timestamp=1.0)

    assert empty == AvatarAudioEnvelope(0.0, 0.0, False, 0, 1, 0.0)
    assert odd.rms == 0.0
    assert odd.voiced is False


def test_hub_keeps_only_latest_value_and_wakes_waiters():
    hub = AvatarAudioEnvelopeHub()
    first = AvatarAudioEnvelope(0.1, 0.2, True, 16000, 1, 1.0)
    second = AvatarAudioEnvelope(0.3, 0.4, True, 16000, 1, 2.0)

    hub.publish(first)
    hub.publish(second)
    seq, latest, closed = hub.wait_after(0, timeout=0)

    assert seq == 2
    assert latest is second
    assert closed is False


def test_wait_without_new_value_returns_no_envelope():
    hub = AvatarAudioEnvelopeHub()
    hub.publish(AvatarAudioEnvelope(0.1, 0.2, True, 16000, 1, 1.0))

    assert hub.wait_after(1, timeout=0) == (1, None, False)


def test_closed_hub_returns_immediately_and_ignores_publish():
    hub = AvatarAudioEnvelopeHub()
    hub.close()
    hub.publish(AvatarAudioEnvelope(0.1, 0.2, True, 16000, 1, 1.0))

    assert hub.wait_after(0, timeout=1) == (0, None, True)


def test_sse_serialization_contains_only_envelope_data():
    payload = sse_data(4, AvatarAudioEnvelope(0.2, 0.5, True, 16000, 1, 9.5))
    lines = payload.decode("utf-8").splitlines()
    body = json.loads(lines[2].removeprefix("data: "))

    assert payload.startswith(b"id: 4\nevent: envelope\ndata: ")
    assert body == {
        "seq": 4,
        "rms": 0.2,
        "peak": 0.5,
        "voiced": True,
        "sample_rate": 16000,
        "channels": 1,
        "timestamp": 9.5,
    }
    assert b"audio" not in payload
    assert payload.endswith(b"\n\n")

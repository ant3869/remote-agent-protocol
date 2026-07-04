import unittest

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    LLMFullResponseEndFrame,
    LLMTextFrame,
    LLMUpdateSettingsFrame,
    MetricsFrame,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import TTFBMetricsData
from pipecat.tests.utils import run_test
from remote_agent_protocol.session_processors import DelegationTap, MicGate, TranscriptTap


def audio_frame():
    return InputAudioRawFrame(audio=b"\x00" * 320, sample_rate=16000, num_channels=1)


class MicGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_open_gate_passes_audio(self):
        await run_test(
            MicGate(),
            frames_to_send=[audio_frame()],
            expected_down_frames=[InputAudioRawFrame],
        )

    async def test_muted_at_construction_drops_audio(self):
        # Regression: a session rebuilt while the GUI shows MUTED must come up
        # muted, not hot.
        await run_test(
            MicGate(muted=True),
            frames_to_send=[audio_frame()],
            expected_down_frames=[],
        )

    async def test_audio_is_dropped_while_bot_is_speaking(self):
        await run_test(
            MicGate(),
            frames_to_send=[
                BotStartedSpeakingFrame(),
                audio_frame(),  # dropped: bot talking, avoids speaker bleed
                BotStoppedSpeakingFrame(),
                audio_frame(),  # passes again
            ],
            expected_down_frames=[
                BotStartedSpeakingFrame,
                BotStoppedSpeakingFrame,
                InputAudioRawFrame,
            ],
        )


class DelegationTapTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_context_refresh_precedes_user_transcript(self):
        refresh = LLMUpdateSettingsFrame(settings={"system_instruction": "fresh"})
        frames, _ = await run_test(
            DelegationTap(lambda *_: "", lambda _: None, context_refresh=lambda: refresh),
            frames_to_send=[
                TranscriptionFrame(text="What time is it?", user_id="u", timestamp="t")
            ],
            expected_down_frames=[LLMUpdateSettingsFrame, TranscriptionFrame],
        )

        self.assertIs(frames[0], refresh)


class TranscriptTapRoleTests(unittest.IsolatedAsyncioTestCase):
    """Each tap role owns a disjoint slice of events so nothing fires twice."""

    async def test_user_tap_reports_only_user_transcripts(self):
        events: list[dict] = []
        tap = TranscriptTap(events.append, role="user")

        await run_test(
            tap,
            frames_to_send=[
                TranscriptionFrame(text=" hello there ", user_id="u", timestamp="t"),
                BotStartedSpeakingFrame(),
                MetricsFrame(data=[TTFBMetricsData(processor="WhisperSTTService#0", value=0.4)]),
            ],
        )

        self.assertEqual(events, [{"type": "transcript", "role": "user", "text": "hello there"}])

    async def test_assistant_tap_aggregates_llm_text_only(self):
        events: list[dict] = []
        tap = TranscriptTap(events.append, role="assistant")

        await run_test(
            tap,
            frames_to_send=[
                LLMTextFrame(text="Hey "),
                LLMTextFrame(text="you."),
                LLMFullResponseEndFrame(),
                BotStartedSpeakingFrame(),  # telemetry's job, not this tap's
            ],
        )

        self.assertEqual(events, [{"type": "transcript", "role": "assistant", "text": "Hey you."}])

    async def test_telemetry_tap_owns_metrics_speaking_and_turns(self):
        events: list[dict] = []
        tap = TranscriptTap(events.append, role="telemetry")

        await run_test(
            tap,
            frames_to_send=[
                UserStoppedSpeakingFrame(),
                MetricsFrame(data=[TTFBMetricsData(processor="WhisperSTTService#0", value=0.4)]),
                BotStartedSpeakingFrame(),
                BotStoppedSpeakingFrame(),
                TranscriptionFrame(text="ignored here", user_id="u", timestamp="t"),
            ],
        )

        self.assertEqual(
            events,
            [
                {"type": "turn", "event": "user_stopped"},
                {"type": "metric", "bucket": "stt", "kind": "ttfb", "value": 0.4},
                {"type": "speaking", "value": True},
                {"type": "turn", "event": "bot_started"},
                {"type": "speaking", "value": False},
            ],
        )


if __name__ == "__main__":
    unittest.main()

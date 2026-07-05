import unittest

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    LLMUpdateSettingsFrame,
    MetricsFrame,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import TTFBMetricsData
from pipecat.tests.utils import run_test
from remote_agent_protocol.session_processors import (
    DelegationTap,
    LLMDelegateTap,
    MicGate,
    TranscriptTap,
    is_placeholder_task,
    looks_like_delegation_promise,
)


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

    async def test_async_resolver_is_awaited(self):
        # The intent router resolves over the network, so resolve may be async.
        async def resolve(_text):
            return ("code-puppy", "get the forecast")

        frames, _ = await run_test(
            DelegationTap(lambda agent, task: f"[ack {agent}: {task}]", resolve),
            frames_to_send=[
                TranscriptionFrame(text="what's the weather", user_id="u", timestamp="t")
            ],
            expected_down_frames=[TranscriptionFrame],
        )

        self.assertEqual(frames[0].text, "[ack code-puppy: get the forecast]")

    async def test_async_model_control_short_circuits_delegation(self):
        delegated = []

        async def control(_text):
            return "[model switched to OpenAI GPT-5.5]"

        frames, _ = await run_test(
            DelegationTap(
                lambda agent, task: delegated.append((agent, task)) or "unused",
                lambda _text: None,
                control_check=control,
            ),
            frames_to_send=[
                TranscriptionFrame(text="change to OpenAI", user_id="u", timestamp="t")
            ],
            expected_down_frames=[TranscriptionFrame],
        )

        self.assertEqual(frames[0].text, "[model switched to OpenAI GPT-5.5]")
        self.assertEqual(delegated, [])


class DelegationPromiseTests(unittest.TestCase):
    """Detect replies that claim agent work is happening (real transcript lines)."""

    def test_fabricated_promises_are_detected(self):
        for text in (
            "I shall summon the Bat Computer immediately to identify the source, sir.",
            "I'll have the Bat Computer verify the contemporary standard.",
            "I am dispatching the results of the diagnostic back to you via code-puppy.",
            "Allow me to access the coordinates; the Bat Computer is working on it.",
            "I shall check the recent sporting reports from the Bat Computer, sir.",
        ):
            with self.subTest(text=text[:40]):
                self.assertTrue(looks_like_delegation_promise(text))

    def test_plain_conversation_is_not_a_promise(self):
        for text in (
            "It is precisely two-fifteen, sir; a rather precise time.",
            "Naturally, sir, you are embodying the very spirit of the Dark Knight.",
            "Welcome back, sir; I trust the patrol was not unduly taxing.",
        ):
            with self.subTest(text=text[:40]):
                self.assertFalse(looks_like_delegation_promise(text))


class LLMDelegateTapTests(unittest.IsolatedAsyncioTestCase):
    async def _run_response(self, chunks: list[str]):
        dispatched: list[str] = []
        responses: list[tuple[str, bool]] = []
        tap = LLMDelegateTap(
            dispatched.append,
            on_response=lambda text, sent: responses.append((text, sent)),
        )
        frames = (
            [LLMFullResponseStartFrame()]
            + [LLMTextFrame(text=c) for c in chunks]
            + [LLMFullResponseEndFrame()]
        )
        await run_test(tap, frames_to_send=frames)
        return dispatched, responses

    async def test_marker_is_dispatched_and_response_reports_it(self):
        dispatched, responses = await self._run_response(
            ["Sending it to my agent now. ", "[[delegate: get weather for Bentonville]]"]
        )

        self.assertEqual(dispatched, ["get weather for Bentonville"])
        self.assertEqual(len(responses), 1)
        self.assertTrue(responses[0][1])

    async def test_markerless_response_reports_nothing_dispatched(self):
        dispatched, responses = await self._run_response(
            ["I shall summon the Bat Computer ", "immediately, sir."]
        )

        self.assertEqual(dispatched, [])
        self.assertEqual(responses, [("I shall summon the Bat Computer immediately, sir.", False)])

    async def test_placeholder_marker_is_ignored_not_dispatched(self):
        # Regression: nudged small models copy the prompt example verbatim and
        # emit [[delegate: clear task description]] -- that must never launch a job.
        dispatched, responses = await self._run_response(
            ["On it, sir. ", "[[delegate: clear task description]]"]
        )

        self.assertEqual(dispatched, [])
        self.assertFalse(responses[0][1])

    def test_placeholder_task_detection(self):
        self.assertTrue(is_placeholder_task("clear task description"))
        self.assertTrue(is_placeholder_task("Clear task description."))
        self.assertTrue(is_placeholder_task("..."))
        self.assertFalse(is_placeholder_task("get weather for Bentonville"))


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

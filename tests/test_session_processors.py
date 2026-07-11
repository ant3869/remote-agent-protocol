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
    TTSAudioRawFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import TTFBMetricsData
from pipecat.tests.utils import run_test
from remote_agent_protocol import multimodal_prompt
from remote_agent_protocol.session_processors import (
    AvatarAudioTap,
    DelegationTap,
    LLMDelegateTap,
    ManualPromptDraftTap,
    MicGate,
    STTNoiseFilter,
    TranscriptTap,
    is_placeholder_task,
    is_stt_hallucination,
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

    async def test_input_disabled_drops_audio_for_push_to_talk_idle(self):
        gate = MicGate()
        gate.input_enabled = False

        await run_test(
            gate,
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


class STTNoiseFilterTests(unittest.IsolatedAsyncioTestCase):
    def _transcript(self, text):
        return TranscriptionFrame(text=text, user_id="u", timestamp="t")

    def test_known_hallucinations_are_recognized(self):
        for text in ("Thank you.", "thank you", "Thanks for watching!", "  You  ", "Bye."):
            self.assertTrue(is_stt_hallucination(text), text)

    def test_real_utterances_are_kept(self):
        for text in ("thank you for opening the file", "open the steam app", "what time is it"):
            self.assertFalse(is_stt_hallucination(text), text)

    async def test_filter_drops_hallucination_but_passes_real_speech(self):
        await run_test(
            STTNoiseFilter(),
            frames_to_send=[self._transcript("Thank you."), self._transcript("open the file")],
            expected_down_frames=[TranscriptionFrame],  # only the real one survives
        )


class ManualPromptDraftTapTests(unittest.IsolatedAsyncioTestCase):
    async def test_manual_mode_holds_transcript_as_draft(self):
        events = []
        tap = ManualPromptDraftTap(lambda: True, lambda text, intent: events.append((text, intent)))

        await run_test(
            tap,
            frames_to_send=[
                TranscriptionFrame(text="look at the highlighted part", user_id="u", timestamp="t")
            ],
            expected_down_frames=[],
        )

        self.assertEqual(events, [("look at the highlighted part", "")])

    async def test_send_it_now_is_draft_intent_not_llm_text(self):
        events = []
        tap = ManualPromptDraftTap(lambda: True, lambda text, intent: events.append((text, intent)))

        await run_test(
            tap,
            frames_to_send=[TranscriptionFrame(text="send it now", user_id="u", timestamp="t")],
            expected_down_frames=[],
        )

        self.assertEqual(events, [("send it now", "send")])

    async def test_disabled_mode_passes_transcript_through(self):
        tap = ManualPromptDraftTap(lambda: False, lambda *_: None)

        await run_test(
            tap,
            frames_to_send=[TranscriptionFrame(text="hello", user_id="u", timestamp="t")],
            expected_down_frames=[TranscriptionFrame],
        )

    async def test_context_aware_mode_passes_normal_free_talk(self):
        events = []
        tap = ManualPromptDraftTap(
            lambda text: bool(multimodal_prompt.context_signals(text)),
            lambda text, intent: events.append((text, intent)),
        )

        await run_test(
            tap,
            frames_to_send=[TranscriptionFrame(text="hello there", user_id="u", timestamp="t")],
            expected_down_frames=[TranscriptionFrame],
        )

        self.assertEqual(events, [])

    async def test_context_aware_mode_holds_context_reference(self):
        events = []
        tap = ManualPromptDraftTap(
            lambda text: bool(multimodal_prompt.context_signals(text)),
            lambda text, intent: events.append((text, intent)),
        )

        await run_test(
            tap,
            frames_to_send=[
                TranscriptionFrame(text="look at this screenshot", user_id="u", timestamp="t")
            ],
            expected_down_frames=[],
        )

        self.assertEqual(events, [("look at this screenshot", "")])


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
            "I am having code-puppy update the Python GUI to change its color to blue, sir.",
            "I am having code-puppy update the Python GUI to include the number 5, sir.",
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


class AvatarAudioTapTests(unittest.IsolatedAsyncioTestCase):
    async def test_tts_audio_passes_through_unchanged_and_emits_envelope(self):
        events = []
        frame = TTSAudioRawFrame(audio=b"\xff\x7f" * 80, sample_rate=24000, num_channels=1)
        tap = AvatarAudioTap(
            events.append,
            publish_interval_secs=0.05,
            clock=lambda: 1.0,
            wall_clock=lambda: 10.0,
        )

        frames, _ = await run_test(tap, frames_to_send=[frame])

        self.assertIs(frames[0], frame)
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0].voiced)
        self.assertEqual(events[0].timestamp, 10.0)

    async def test_rate_limit_drops_intermediate_envelopes(self):
        events = []
        ticks = iter([0.0, 0.01, 0.06])
        tap = AvatarAudioTap(events.append, clock=lambda: next(ticks), wall_clock=lambda: 20.0)
        frames = [
            TTSAudioRawFrame(audio=b"\x00\x20" * 40, sample_rate=24000, num_channels=1)
            for _ in range(3)
        ]

        await run_test(tap, frames_to_send=frames)

        self.assertEqual(len(events), 2)

    async def test_callback_failure_does_not_block_audio(self):
        frame = TTSAudioRawFrame(audio=b"\x00\x20" * 40, sample_rate=24000, num_channels=1)

        def fail(_envelope):
            raise RuntimeError("telemetry unavailable")

        frames, _ = await run_test(AvatarAudioTap(fail), frames_to_send=[frame])

        self.assertIs(frames[0], frame)


class TranscriptTapUserStartTests(unittest.IsolatedAsyncioTestCase):
    async def test_telemetry_tap_reports_user_started(self):
        events = []

        await run_test(
            TranscriptTap(events.append, role="telemetry"),
            frames_to_send=[UserStartedSpeakingFrame(), UserStoppedSpeakingFrame()],
        )

        self.assertEqual(
            events,
            [
                {"type": "turn", "event": "user_started"},
                {"type": "turn", "event": "user_stopped"},
            ],
        )


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

    async def test_assistant_tap_mirrors_injected_tts_speech(self):
        # Agent status updates (finished/continuing/handoff) are injected as
        # TTSSpeakFrames that bypass the LLM; they must still reach the
        # transcript or they are spoken aloud but never shown (regression).
        events: list[dict] = []
        tap = TranscriptTap(events.append, role="assistant")

        await run_test(
            tap,
            frames_to_send=[TTSSpeakFrame(text="  hermes-yolo finished: found two alerts.  ")],
        )

        self.assertEqual(
            events,
            [
                {
                    "type": "transcript",
                    "role": "assistant",
                    "text": "hermes-yolo finished: found two alerts.",
                }
            ],
        )

    async def test_user_and_telemetry_taps_ignore_injected_tts(self):
        # Only the assistant tap owns injected speech; the others must not
        # double-report it.
        for role in ("user", "telemetry"):
            events: list[dict] = []
            await run_test(
                TranscriptTap(events.append, role=role),
                frames_to_send=[TTSSpeakFrame(text="agent update")],
            )
            self.assertEqual(events, [], f"role {role} should ignore TTSSpeakFrame")

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

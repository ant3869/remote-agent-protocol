import pytest

from pipecat.frames.frames import (
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
    TTSSpeakFrame,
)
from pipecat.tests.utils import SleepFrame, run_test
from remote_agent_protocol.conversation import ConversationEvents
from remote_agent_protocol.session_processors import TranscriptTap
from remote_agent_protocol.speech_events import SpeechBoundaryFrame, SpeechPlaybackTap


@pytest.mark.asyncio
async def test_streamed_text_is_visible_before_final_and_keeps_starting_persona():
    events = []
    source = ConversationEvents(lambda: "Jess", "queued")
    await run_test(
        TranscriptTap(events.append, "assistant", conversation=source),
        frames_to_send=[
            LLMFullResponseStartFrame(),
            LLMTextFrame("One."),
            LLMTextFrame(" Two."),
            LLMFullResponseEndFrame(),
        ],
    )
    assert [e["text"] for e in events] == ["One.", "One. Two.", "One. Two."]
    assert len({e["message_id"] for e in events}) == 1
    assert [e["final"] for e in events] == [False, False, True]


@pytest.mark.asyncio
async def test_injected_harness_metadata_survives_persona_fallback():
    events = []
    source = ConversationEvents(lambda: "Jess", "queued")
    frame = TTSSpeakFrame("Result from Hermes")
    frame.metadata["conversation"] = source.utterance(
        speaker_name="Hermes", role="agent", job_id="job-1"
    )
    await run_test(
        TranscriptTap(events.append, "assistant", conversation=source), frames_to_send=[frame]
    )
    assert len(events) == 1
    assert events[0]["speaker_name"] == "Hermes"
    assert events[0]["job_id"] == "job-1"


@pytest.mark.asyncio
async def test_playback_boundaries_confirm_only_audio_that_passed_transport():
    events = []
    source = ConversationEvents(lambda: "Jess", "queued")
    utterance = source.utterance("Hello")
    await run_test(
        SpeechPlaybackTap(events.append),
        frames_to_send=[
            SpeechBoundaryFrame(utterance=utterance),
            OutputAudioRawFrame(audio=b"\0" * 320, sample_rate=16000, num_channels=1),
            SpeechBoundaryFrame(utterance=utterance, ending=True),
        ],
    )
    assert [event["delivery"] for event in events] == ["playing", "played"]
    assert all(event["message_id"] == utterance["message_id"] for event in events)


@pytest.mark.asyncio
async def test_missing_audio_never_claims_played():
    events = []
    utterance = ConversationEvents(lambda: "Jess").utterance("No audio")
    await run_test(
        SpeechPlaybackTap(events.append),
        frames_to_send=[
            SpeechBoundaryFrame(utterance=utterance),
            SpeechBoundaryFrame(utterance=utterance, ending=True),
        ],
    )
    assert events[-1]["delivery"] == "playback_unconfirmed"


@pytest.mark.asyncio
async def test_interrupt_keeps_partial_text_and_next_turn_has_a_fresh_buffer():
    events = []
    source = ConversationEvents(lambda: "Jess", "queued")
    await run_test(
        TranscriptTap(events.append, "assistant", conversation=source),
        frames_to_send=[
            LLMFullResponseStartFrame(),
            LLMTextFrame("First unfinished"),
            SleepFrame(sleep=0.05),
            InterruptionFrame(),
            SleepFrame(sleep=0.05),
            LLMFullResponseStartFrame(),
            LLMTextFrame("New reply."),
            LLMFullResponseEndFrame(),
        ],
    )
    finals = [event for event in events if event.get("final")]
    assert finals[0]["text"] == "First unfinished"
    assert finals[0]["delivery"] == "interrupted"
    assert finals[-1]["text"] == "New reply."
    assert finals[0]["message_id"] != finals[-1]["message_id"]


@pytest.mark.asyncio
async def test_text_precedes_serialized_audio_for_consecutive_harness_utterances():
    from pipecat.frames.frames import TTSAudioRawFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.services.settings import TTSSettings
    from pipecat.services.tts_service import TTSService
    from pipecat.transports.base_output import BaseOutputTransport
    from pipecat.transports.base_transport import TransportParams

    events = []
    written = []
    source = ConversationEvents(lambda: "Jess", "queued")

    class SyntheticSpeech(TTSService):
        def __init__(self):
            super().__init__(
                push_start_frame=True,
                push_stop_frames=True,
                sample_rate=16000,
                settings=TTSSettings(model=None, voice=None, language=None),
            )

        async def run_tts(self, text, context_id):
            yield TTSAudioRawFrame(
                audio=b"\0\1" * 6400, sample_rate=16000, num_channels=1, context_id=context_id
            )

    class RecordingOutput(BaseOutputTransport):
        async def start(self, frame):
            await super().start(frame)
            await self.set_transport_ready(frame)

        async def write_audio_frame(self, frame):
            # The actual transport queue reaches the output only after visible text.
            assert any(event.get("text") for event in events)
            written.append(frame)
            return True

    speech = []
    for name in ("Hermes", "Codex"):
        frame = TTSSpeakFrame(f"Hello from {name}.")
        frame.metadata["conversation"] = source.utterance(speaker_name=name, role="agent")
        speech.append(frame)
    await run_test(
        Pipeline(
            [
                TranscriptTap(events.append, "assistant", conversation=source),
                SyntheticSpeech(),
                RecordingOutput(
                    TransportParams(audio_out_enabled=True, audio_out_end_silence_secs=0)
                ),
                SpeechPlaybackTap(events.append),
            ]
        ),
        frames_to_send=speech,
    )
    assert written
    for frame in speech:
        ident = frame.metadata["conversation"]["message_id"]
        utterance_events = [event for event in events if event.get("message_id") == ident]
        assert utterance_events[0]["speaker_name"] == frame.metadata["conversation"]["speaker_name"]
        assert [event["delivery"] for event in utterance_events] == ["queued", "playing", "played"]

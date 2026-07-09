import asyncio
import unittest
from unittest.mock import AsyncMock

from pipecat.frames.frames import TTSAudioRawFrame
from remote_agent_protocol import persona_tts, tts_factory


class PersonaTTSTests(unittest.TestCase):
    def test_factory_returns_routing_tts_for_kokoro_or_voicebox_personas(self):
        service = tts_factory.create_tts("af_heart")
        self.assertIsInstance(service, persona_tts.PersonaTTSService)

    def test_persona_tts_settings_carry_backend_and_model(self):
        settings = persona_tts.PersonaTTSService.Settings(
            voice="voicebox:profile-1",
            voice_backend="voicebox",
            model="0.6B",
            extra={"speaker": "speaker-a", "language": "en"},
        )

        self.assertEqual(settings.voice, "voicebox:profile-1")
        self.assertEqual(settings.voice_backend, "voicebox")
        self.assertEqual(settings.model, "0.6B")
        self.assertEqual(settings.extra["speaker"], "speaker-a")

    def test_factory_returns_routing_tts_for_coqui(self):
        service = tts_factory.create_tts(
            "speaker-a",
            voice_model="tts_models/en/ljspeech/vits",
            voice_backend="coqui",
            tts_options={"speaker": "speaker-a", "language": "en"},
        )

        self.assertIsInstance(service, persona_tts.PersonaTTSService)
        self.assertEqual(service._settings.voice_backend, "coqui")
        self.assertEqual(service._settings.extra["language"], "en")

    def test_voicebox_model_fallback_is_cached(self):
        service = tts_factory.create_tts(
            "voicebox:profile-1", voice_model="0.6B", voice_backend="voicebox"
        )

        self.assertEqual(service.voicebox_model_for_request("0.6B"), "0.6B")
        service.remember_voicebox_model_fallback("0.6B", "1.7B")

        self.assertEqual(service.voicebox_model_for_request("0.6B"), "1.7B")

    def test_coqui_failure_falls_back_to_kokoro_voice(self):
        async def fake_kokoro(text, context_id, *, voice_override=None):
            used_voice.append(voice_override)
            yield TTSAudioRawFrame(
                audio=b"pcm", sample_rate=24000, num_channels=1, context_id=context_id
            )

        async def collect():
            return [frame async for frame in service._run_coqui("hello", "ctx")]

        used_voice: list[str | None] = []
        service = tts_factory.create_tts(
            "",
            voice_model="tts_models/en/ljspeech/vits",
            voice_backend="coqui",
            tts_options={"fallback_voice": "bm_george"},
        )
        service._coqui.synthesize = AsyncMock(side_effect=RuntimeError("No module named 'torch'"))
        service._run_kokoro = fake_kokoro

        frames = asyncio.run(collect())

        self.assertEqual(used_voice, ["bm_george"])
        self.assertEqual(frames[0].audio, b"pcm")


if __name__ == "__main__":
    unittest.main()

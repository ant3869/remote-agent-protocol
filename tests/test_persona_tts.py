import unittest

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
        )

        self.assertEqual(settings.voice, "voicebox:profile-1")
        self.assertEqual(settings.voice_backend, "voicebox")
        self.assertEqual(settings.model, "0.6B")

    def test_voicebox_model_fallback_is_cached(self):
        service = tts_factory.create_tts(
            "voicebox:profile-1", voice_model="0.6B", voice_backend="voicebox"
        )

        self.assertEqual(service.voicebox_model_for_request("0.6B"), "0.6B")
        service.remember_voicebox_model_fallback("0.6B", "1.7B")

        self.assertEqual(service.voicebox_model_for_request("0.6B"), "1.7B")


if __name__ == "__main__":
    unittest.main()

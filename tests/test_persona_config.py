import tempfile
import unittest
from pathlib import Path

from remote_agent_protocol import persona_config, personas


class PersonaConfigTests(unittest.TestCase):
    def test_save_load_and_apply_persona_override(self):
        base = personas.Persona(
            name="Jess",
            voice="af_heart",
            personality="base personality",
            blurb="base blurb",
            model=None,
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "personas.json"
            config = persona_config.PersonaConfig(
                personas={
                    "Jess": persona_config.PersonaOverride(
                        voice="voicebox:profile-1",
                        voice_backend="voicebox",
                        voice_model="1.7B",
                        tts_options={"speaker": "speaker-a", "language": "en"},
                        personality="custom personality",
                        model="gemma-12b",
                        tool_user="code-puppy",
                    )
                }
            )
            persona_config.save_config(config, path)

            loaded = persona_config.load_config(path)
            effective = persona_config.apply_override(base, loaded.personas["Jess"])

            self.assertEqual(effective.voice, "voicebox:profile-1")
            self.assertEqual(effective.voice_backend, "voicebox")
            self.assertEqual(effective.voice_model, "1.7B")
            self.assertEqual(effective.tts_options, {"speaker": "speaker-a", "language": "en"})
            self.assertEqual(effective.personality, "custom personality")
            self.assertEqual(effective.model, "gemma-12b")
            self.assertEqual(effective.tool_user, "code-puppy")

    def test_defaults_produce_builtin_personas(self):
        config = persona_config.PersonaConfig()
        effective = persona_config.effective_personas(personas.PERSONAS, config)

        self.assertEqual([p.name for p in effective], personas.names())
        self.assertEqual(effective[0].name, "Jess")

    def test_voicebox_personas_finds_overridden_voicebox_ref(self):
        base = personas.Persona(name="Gremlin", voice="af_heart", personality="p", blurb="b")
        config = persona_config.PersonaConfig(
            personas={"Gremlin": persona_config.PersonaOverride(voice="voicebox:p1")}
        )

        matches = persona_config.voicebox_personas([base], config)

        self.assertEqual([p.name for p in matches], ["Gremlin"])

    def test_custom_personas_round_trip_after_builtins(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "personas.json"
            config = persona_config.PersonaConfig(
                custom_personas={
                    "Nova": persona_config.PersonaOverride(
                        voice="af_sky",
                        personality="custom role",
                        blurb="custom blurb",
                        model="gemma-custom",
                        tool_user="mock",
                    )
                }
            )

            persona_config.save_config(config, path)
            loaded = persona_config.load_config(path)
            effective = persona_config.effective_personas(personas.PERSONAS, loaded)

            self.assertEqual(effective[-1].name, "Nova")
            self.assertEqual(effective[-1].personality, "custom role")
            self.assertEqual(effective[-1].tool_user, "mock")


if __name__ == "__main__":
    unittest.main()

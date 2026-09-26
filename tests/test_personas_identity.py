"""The persona's name is pinned in its system prompt."""

from remote_agent_protocol import personas


def test_every_builtin_prompt_pins_the_characters_own_name():
    expected = {"Jess": "Jess", "Jarvis": "Jarvis", "Butler": "Bartholomew", "Zen": "Zen"}
    for persona in personas.PERSONAS:
        if persona.name in expected:
            assert f"Your name is {expected[persona.name]}." in persona.system_prompt


def test_a_copied_persona_keeps_the_name_its_personality_gives():
    source = personas.by_name("Jess")
    copy = personas.Persona(name="Jess Copy", voice=source.voice, personality=source.personality)

    assert copy.spoken_name == "Jess"


def test_an_unnamed_personality_falls_back_to_the_display_name():
    persona = personas.Persona(name="Nova", voice="af_sky", personality="You are a calm guide.")

    assert persona.spoken_name == "Nova"
    assert "never adopt it" in persona.system_prompt
    assert persona.system_prompt.endswith(personas.SPEAK_STYLE)

import dataclasses
import unittest
from datetime import datetime
from unittest.mock import patch

from remote_agent_protocol import config, personas
from remote_agent_protocol import session as session_mod
from remote_agent_protocol.session_processors import MicGate


def persona_with_tool_user(tool_user: str | None):
    return dataclasses.replace(personas.DEFAULT_PERSONA, tool_user=tool_user)


class DefaultBackendFromPersonaTests(unittest.TestCase):
    """The session, not each frontend, owns applying a persona's tool user."""

    def test_persona_tool_user_becomes_default_backend(self):
        session = session_mod.VoiceSession(persona_with_tool_user("mock"))

        self.assertEqual(session.default_agent_backend(), "mock")

    def test_unknown_tool_user_keeps_configured_default(self):
        session = session_mod.VoiceSession(persona_with_tool_user("not-a-backend"))

        self.assertEqual(session.default_agent_backend(), config.AGENT_DEFAULT_BACKEND)

    def test_set_persona_switches_default_backend(self):
        session = session_mod.VoiceSession(personas.DEFAULT_PERSONA)

        # No running loop: the TTS/LLM part of the swap is skipped, but the
        # synchronous tool-user part must still land.
        session.set_persona(persona_with_tool_user("code-puppy"))

        self.assertEqual(session.default_agent_backend(), "code-puppy")


class MutePersistenceTests(unittest.TestCase):
    def test_mute_before_build_is_remembered(self):
        session = session_mod.VoiceSession(personas.DEFAULT_PERSONA)

        session.set_muted(True)  # gate doesn't exist yet

        self.assertTrue(session._muted)

    def test_mute_applies_to_live_gate(self):
        session = session_mod.VoiceSession(personas.DEFAULT_PERSONA)
        session._gate = MicGate()

        session.set_muted(True)
        self.assertTrue(session._gate.muted)

        session.set_muted(False)
        self.assertFalse(session._gate.muted)


class RuntimeContextTests(unittest.TestCase):
    def test_system_instruction_reads_the_clock_each_time(self):
        voice_session = session_mod.VoiceSession(personas.DEFAULT_PERSONA)

        with patch.object(session_mod, "datetime") as clock:
            clock.now.side_effect = [datetime(2026, 7, 4, 13, 58), datetime(2026, 7, 4, 13, 59)]
            first = voice_session._system_instruction()
            second = voice_session._system_instruction()

        self.assertIn("Saturday, July 04, 2026, 01:58 PM", first)
        self.assertIn("Saturday, July 04, 2026, 01:59 PM", second)


if __name__ == "__main__":
    unittest.main()

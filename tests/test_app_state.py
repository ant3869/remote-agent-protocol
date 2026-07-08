import tempfile
import unittest
from pathlib import Path

from remote_agent_protocol import app_state


class AppStateFileTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "state.json"

    def tearDown(self):
        self._dir.cleanup()

    def test_roundtrip(self):
        app_state.save_state(
            self.path,
            app_state.AppState(
                persona="Butler",
                tool_user="mock",
                voice_mode="push_to_talk",
            ),
        )

        loaded = app_state.load_state(self.path)

        self.assertEqual(loaded.persona, "Butler")
        self.assertEqual(loaded.tool_user, "mock")
        self.assertEqual(loaded.voice_mode, "push_to_talk")

    def test_save_leaves_no_temp_file(self):
        app_state.save_state(self.path, app_state.AppState(persona="Jess"))

        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_missing_file_gives_defaults(self):
        state = app_state.load_state(self.path)

        self.assertIsNone(state.persona)
        self.assertIsNone(state.tool_user)

    def test_corrupt_file_gives_defaults(self):
        self.path.write_text("{ nope", encoding="utf-8")

        state = app_state.load_state(self.path)

        self.assertIsNone(state.persona)

    def test_non_string_values_are_ignored(self):
        self.path.write_text('{"persona": 7, "tool_user": ["x"]}', encoding="utf-8")

        state = app_state.load_state(self.path)

        self.assertIsNone(state.persona)
        self.assertIsNone(state.tool_user)
        self.assertEqual(state.voice_mode, "free_talk")

    def test_unknown_voice_mode_falls_back_to_free_talk(self):
        self.path.write_text('{"voice_mode": "banana"}', encoding="utf-8")

        state = app_state.load_state(self.path)

        self.assertEqual(state.voice_mode, "free_talk")

    def test_empty_path_disables_persistence(self):
        self.assertEqual(app_state.load_state(""), app_state.AppState())
        app_state.save_state("", app_state.AppState(persona="Jess"))  # no-op, no crash


class ResolvePersonaNameTests(unittest.TestCase):
    NAMES = ["Jess", "Butler", "Zen"]

    def test_saved_name_wins_when_it_still_exists(self):
        self.assertEqual(app_state.resolve_persona_name("Butler", self.NAMES, "Jess"), "Butler")

    def test_unknown_saved_name_falls_back_to_default(self):
        self.assertEqual(app_state.resolve_persona_name("Ghost", self.NAMES, "Jess"), "Jess")

    def test_no_saved_name_uses_default(self):
        self.assertEqual(app_state.resolve_persona_name(None, self.NAMES, "Jess"), "Jess")

    def test_stale_default_falls_back_to_first_available(self):
        self.assertEqual(app_state.resolve_persona_name(None, self.NAMES, "Gone"), "Jess")


if __name__ == "__main__":
    unittest.main()

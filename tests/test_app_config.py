import json
import unittest
from unittest import mock

from remote_agent_protocol import config, ollama_models


class AgentConfigTests(unittest.TestCase):
    def test_vad_accepts_short_low_gain_speech_onsets(self):
        self.assertEqual(config.VAD_CONFIDENCE, 0.6)
        self.assertEqual(config.VAD_START_SECS, 0.1)
        self.assertEqual(config.VAD_MIN_VOLUME, 0.6)

    def test_ollama_clients_share_the_configured_host(self):
        self.assertEqual(config.OLLAMA_BASE_URL, f"{config.OLLAMA_HOST}/v1")

    def test_agent_jobs_have_a_bounded_default_runtime(self):
        self.assertGreater(config.AGENT_JOB_TIMEOUT_SECS, 0)

    def test_code_puppy_jobs_start_from_a_clean_session(self):
        # --quick-resume shares a session pool with the human's own interactive
        # runs, so a delegated task arrives mid-conversation in whatever was last
        # discussed there and gets answered in that context.
        self.assertEqual(
            config.AGENT_BACKENDS["code-puppy"],
            ["code-puppy", "-p", "{task}"],
        )

    def test_the_wake_word_leaves_time_to_actually_start_talking(self):
        # At 3s the window closed before the recognizer reported speech, so the
        # phrase was heard and the sentence after it was dropped.
        self.assertGreaterEqual(config.WAKE_WORD_ACTIVE_WINDOW_SECS, 8)

    def test_a_question_can_be_answered_without_saying_the_wake_word_again(self):
        self.assertGreater(config.WAKE_WORD_FOLLOW_UP_SECS, config.WAKE_WORD_ACTIVE_WINDOW_SECS)

    def test_hermes_uses_persistent_single_query_sessions(self):
        self.assertEqual(
            config.AGENT_BACKENDS["hermes"],
            ["hermes", "chat", "-q", "{task}"],
        )
        self.assertEqual(
            config.AGENT_BACKENDS["hermes-yolo"],
            ["hermes", "chat", "--yolo", "-q", "{task}"],
        )

    def test_installing_new_software_requires_confirmation(self):
        # "install a skill called agent-reach" ran straight to a live pip
        # install with no spoken confirmation (jess_runtime.log 2026-07-05
        # 13:59/14:06) because only "uninstall" was destructive, not its
        # opposite -- both mutate the system and can run arbitrary code.
        self.assertIn("install", config.AGENT_DESTRUCTIVE_WORDS)

    def test_parses_remote_agent_commands(self):
        raw = '{"openclaw":["remote-agent","laptop","openclaw","{task}"]}'

        self.assertEqual(
            config._parse_command_map(raw, "TEST"),
            {"openclaw": ["remote-agent", "laptop", "openclaw", "{task}"]},
        )

    def test_rejects_shell_command_strings(self):
        with self.assertRaisesRegex(ValueError, "non-empty string arrays"):
            config._parse_command_map('{"openclaw":"openclaw run"}', "TEST")


if __name__ == "__main__":
    unittest.main()


class OllamaPreloadTests(unittest.TestCase):
    """The reply model is made resident before a turn needs it."""

    def test_preload_asks_ollama_to_load_and_hold_the_model(self):
        seen = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b"{}"

        def fake_urlopen(request, timeout=None):
            seen["url"] = request.full_url
            seen["body"] = json.loads(request.data)
            seen["timeout"] = timeout
            return Response()

        with mock.patch.object(ollama_models.urllib.request, "urlopen", fake_urlopen):
            self.assertTrue(ollama_models.preload("http://localhost:11434", "gemma-12b", "5m"))

        self.assertTrue(seen["url"].endswith("/api/generate"))
        # An empty prompt loads the weights and generates nothing.
        self.assertEqual(seen["body"]["prompt"], "")
        self.assertEqual(seen["body"]["keep_alive"], "5m")
        # A cold load off disk takes far longer than any per-turn budget.
        self.assertGreaterEqual(seen["timeout"], 60)

    def test_preload_without_a_model_is_a_no_op(self):
        self.assertFalse(ollama_models.preload("http://localhost:11434", "", "5m"))

    def test_an_unreachable_ollama_is_reported_not_raised(self):
        with mock.patch.object(
            ollama_models.urllib.request, "urlopen", side_effect=OSError("refused")
        ):
            self.assertFalse(ollama_models.preload("http://localhost:11434", "gemma-12b", "5m"))

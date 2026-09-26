import importlib
import json
import os
import unittest
from unittest import mock

import loguru

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

    def test_agent_default_model_targets_is_empty_unless_configured(self):
        # AGENT_DEFAULT_MODEL_TARGETS_JSON is unset in the test environment --
        # this must change no behavior by default.
        self.assertEqual(config.AGENT_DEFAULT_MODEL_TARGETS, {})

    def test_agent_default_model_targets_json_parses_agent_to_provider_map(self):
        parsed = config._parse_string_map(
            '{"code-puppy":"openai"}', "AGENT_DEFAULT_MODEL_TARGETS_JSON"
        )
        self.assertEqual(parsed, {"code-puppy": "openai"})

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
            ["hermes", "chat", "--query-file", "{task_file}"],
        )

    def test_openclaw_uses_the_configured_gateway_agent(self):
        command = config.AGENT_BACKENDS["openclaw"]
        self.assertEqual(
            command,
            [
                "openclaw",
                "agent",
                "--agent",
                config.OPENCLAW_AGENT_ID,
                "--message-file",
                "{task_file}",
            ],
        )
        self.assertNotIn("exec", command)
        self.assertNotIn("--state-dir", command)

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


class CloudReasoningEffortConfigTests(unittest.TestCase):
    """CLOUD_LLM_REASONING_EFFORT: empty means "don't send it"."""

    def test_empty_is_accepted_without_a_warning(self):
        with mock.patch.object(loguru.logger, "warning") as mock_warning:
            self.assertEqual(config._validate_cloud_reasoning_effort(""), "")
        mock_warning.assert_not_called()

    def test_each_known_value_is_kept(self):
        for value in ("none", "minimal", "low", "medium", "high"):
            with self.subTest(value=value):
                self.assertEqual(config._validate_cloud_reasoning_effort(value), value)

    def test_an_invalid_value_is_ignored_and_warned_once(self):
        with mock.patch.object(loguru.logger, "warning") as mock_warning:
            result = config._validate_cloud_reasoning_effort("maximum-overdrive")
        self.assertEqual(result, "")
        mock_warning.assert_called_once()

    def test_env_wiring_normalizes_case_and_whitespace(self):
        # An end-to-end check that the module-level assignment actually runs
        # the raw env value through _env() -> strip().lower() -> validation.
        with mock.patch.dict(os.environ, {"CLOUD_LLM_REASONING_EFFORT": "  MEDIUM  "}):
            reloaded = importlib.reload(config)
        try:
            self.assertEqual(reloaded.CLOUD_LLM_REASONING_EFFORT, "medium")
        finally:
            importlib.reload(config)  # restore the ambient environment for other tests


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

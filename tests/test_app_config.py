import unittest

from remote_agent_protocol import config


class AgentConfigTests(unittest.TestCase):
    def test_vad_accepts_short_low_gain_speech_onsets(self):
        self.assertEqual(config.VAD_CONFIDENCE, 0.6)
        self.assertEqual(config.VAD_START_SECS, 0.1)
        self.assertEqual(config.VAD_MIN_VOLUME, 0.6)

    def test_ollama_clients_share_the_configured_host(self):
        self.assertEqual(config.OLLAMA_BASE_URL, f"{config.OLLAMA_HOST}/v1")

    def test_agent_jobs_have_a_bounded_default_runtime(self):
        self.assertGreater(config.AGENT_JOB_TIMEOUT_SECS, 0)

    def test_code_puppy_resumes_the_workspace_session(self):
        self.assertEqual(
            config.AGENT_BACKENDS["code-puppy"],
            ["code-puppy", "--quick-resume", "-p", "{task}"],
        )

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

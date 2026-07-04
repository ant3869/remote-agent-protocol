import unittest

from remote_agent_protocol import config


class AgentConfigTests(unittest.TestCase):
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

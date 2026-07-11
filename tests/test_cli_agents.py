import unittest
from unittest.mock import MagicMock, patch

from remote_agent_protocol.cli_agents import ClaudeCodeCliBackend, CodexCliBackend


class TestCliAgents(unittest.TestCase):
    @patch("shutil.which")
    def test_codex_is_available(self, mock_which):
        mock_which.return_value = "/usr/local/bin/codex"
        agent = CodexCliBackend()
        self.assertTrue(agent.is_available())

    @patch("shutil.which")
    def test_claude_code_is_available(self, mock_which):
        mock_which.return_value = None
        agent = ClaudeCodeCliBackend()
        self.assertFalse(agent.is_available())

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_codex_get_status_success(self, mock_run, mock_which):
        mock_which.return_value = "/usr/local/bin/codex"

        mock_proc_ver = MagicMock()
        mock_proc_ver.returncode = 0
        mock_proc_ver.stdout = "codex-cli 0.144.1\n"

        mock_proc_auth = MagicMock()
        mock_proc_auth.returncode = 0
        mock_proc_auth.stdout = "ping\n"
        mock_proc_auth.stderr = ""

        mock_run.side_effect = [mock_proc_ver, mock_proc_auth]

        agent = CodexCliBackend()
        status = agent.get_status()

        self.assertTrue(status.available)
        self.assertEqual(status.version, "codex-cli 0.144.1")
        self.assertTrue(status.auth_ok)
        self.assertEqual(status.executable_path, "/usr/local/bin/codex")
        self.assertIsNone(status.error)

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_claude_code_get_status_auth_fail(self, mock_run, mock_which):
        mock_which.return_value = "/usr/local/bin/claude"

        mock_proc_ver = MagicMock()
        mock_proc_ver.returncode = 0
        mock_proc_ver.stdout = "claude 1.2.3\n"

        mock_proc_auth = MagicMock()
        mock_proc_auth.returncode = 1
        mock_proc_auth.stdout = "unauthorized request\n"
        mock_proc_auth.stderr = ""

        mock_run.side_effect = [mock_proc_ver, mock_proc_auth]

        agent = ClaudeCodeCliBackend()
        status = agent.get_status()

        self.assertTrue(status.available)
        self.assertEqual(status.version, "claude 1.2.3")
        self.assertFalse(status.auth_ok)
        self.assertEqual(status.executable_path, "/usr/local/bin/claude")
        self.assertIn("Not authenticated", status.error)


if __name__ == "__main__":
    unittest.main()

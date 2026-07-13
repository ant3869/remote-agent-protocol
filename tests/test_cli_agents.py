import unittest
from unittest.mock import MagicMock, patch

from remote_agent_protocol.cli_agents import (
    ClaudeCodeCliBackend,
    CodePuppyCliBackend,
    CodexCliBackend,
    HermesCliBackend,
    get_all_cli_agents,
)


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

    @patch("shutil.which")
    def test_hermes_is_available(self, mock_which):
        mock_which.return_value = r"C:\hermes\hermes.exe"
        self.assertTrue(HermesCliBackend().is_available())

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_hermes_get_status_never_probes_auth(self, mock_run, mock_which):
        # Hermes resumes a shared on-disk session per agent name; a status
        # check must never touch that session the way a real delegated turn
        # would, so only --version is ever run (one subprocess call, not two).
        mock_which.return_value = r"C:\hermes\hermes.exe"
        mock_proc = MagicMock(returncode=0, stdout="Hermes Agent v0.18.2\n", stderr="")
        mock_run.return_value = mock_proc

        status = HermesCliBackend().get_status()

        self.assertTrue(status.available)
        self.assertEqual(status.version, "Hermes Agent v0.18.2")
        self.assertIsNone(status.auth_ok)
        self.assertIsNone(status.error)
        mock_run.assert_called_once()

    @patch("shutil.which")
    def test_code_puppy_is_available(self, mock_which):
        mock_which.return_value = None
        self.assertFalse(CodePuppyCliBackend().is_available())

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_code_puppy_get_status_success(self, mock_run, mock_which):
        mock_which.return_value = r"C:\tools\code-puppy.exe"
        mock_run.return_value = MagicMock(returncode=0, stdout="0.0.614\n", stderr="")

        status = CodePuppyCliBackend().get_status()

        self.assertTrue(status.available)
        self.assertEqual(status.version, "0.0.614")
        self.assertEqual(status.executable_path, r"C:\tools\code-puppy.exe")

    @patch("shutil.which", return_value=None)
    def test_missing_executable_reports_unavailable_not_an_exception(self, _mock_which):
        status = HermesCliBackend().get_status()
        self.assertFalse(status.available)
        self.assertIn("not found in PATH", status.error)

    def test_get_all_cli_agents_includes_all_four_backends(self):
        ids = {agent.id for agent in get_all_cli_agents()}
        self.assertEqual(ids, {"codex", "claude-code", "hermes", "code-puppy"})


if __name__ == "__main__":
    unittest.main()

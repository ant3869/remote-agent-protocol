"""Tests for the centralized backend-executable resolution helper.

Covers the Windows CreateProcess quirk documented in
docs/notes/2026-09-25-harness-audit.md: a bare command name only gets
``.exe`` auto-appended by a shell-less launch, never ``.cmd``/``.bat``, so an
npm-installed shim has to be resolved to its real path first. All tests mock
``shutil.which``/``os.name``/``os.environ`` so they run identically on every
platform, per the isolation instructions for this task.
"""

import os
import unittest
from unittest import mock

from remote_agent_protocol import subprocess_resolution as sr


class ResolveExecutableTests(unittest.TestCase):
    def test_resolves_bare_name_via_which(self):
        with mock.patch.object(sr.shutil, "which", return_value=r"C:\npm\openclaw.CMD"):
            self.assertEqual(sr.resolve_executable("openclaw"), r"C:\npm\openclaw.CMD")

    def test_falls_back_to_original_name_when_not_found(self):
        with mock.patch.object(sr.shutil, "which", return_value=None):
            self.assertEqual(sr.resolve_executable("codex"), "codex")


class FindShadowExecutableTests(unittest.TestCase):
    """Detect a same-named .exe elsewhere on PATH than the resolved shim.

    Uses a real temp filesystem (two directories, each with a same-named
    file) so the PATH-walking logic itself is exercised, not just mocked
    away -- only ``os.name`` and ``PATH``/``PATHEXT`` are injected.
    """

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        import pathlib

        self.npm_dir = pathlib.Path(self._tmp.name) / "npm"
        self.local_bin_dir = pathlib.Path(self._tmp.name) / "local_bin"
        self.npm_dir.mkdir()
        self.local_bin_dir.mkdir()

    def _touch(self, path):
        path.write_text("", encoding="utf-8")

    def test_detects_exe_elsewhere_on_path_when_resolved_is_a_shim(self):
        cmd_path = self.npm_dir / "openclaw.CMD"
        exe_path = self.local_bin_dir / "openclaw.exe"
        self._touch(cmd_path)
        self._touch(exe_path)
        path_env = os.pathsep.join([str(self.npm_dir), str(self.local_bin_dir)])
        with mock.patch.object(sr.shutil, "which", return_value=str(cmd_path)):
            shadow = sr.find_shadow_executable(
                "openclaw", path_env=path_env, is_windows=True
            )
        self.assertIsNotNone(shadow)
        self.assertEqual(shadow.resolved, str(cmd_path))
        self.assertEqual(shadow.shadow, str(exe_path))

    def test_returns_none_when_no_other_exe_exists(self):
        cmd_path = self.npm_dir / "codex.CMD"
        self._touch(cmd_path)
        path_env = str(self.npm_dir)
        with mock.patch.object(sr.shutil, "which", return_value=str(cmd_path)):
            shadow = sr.find_shadow_executable("codex", path_env=path_env, is_windows=True)
        self.assertIsNone(shadow)

    def test_returns_none_when_resolved_is_already_the_exe(self):
        exe_path = self.local_bin_dir / "claude.exe"
        self._touch(exe_path)
        path_env = str(self.local_bin_dir)
        with mock.patch.object(sr.shutil, "which", return_value=str(exe_path)):
            shadow = sr.find_shadow_executable("claude", path_env=path_env, is_windows=True)
        self.assertIsNone(shadow)

    def test_returns_none_when_nothing_resolves(self):
        with mock.patch.object(sr.shutil, "which", return_value=None):
            shadow = sr.find_shadow_executable("ghost", path_env="", is_windows=True)
        self.assertIsNone(shadow)

    def test_returns_none_on_non_windows_regardless_of_path_contents(self):
        cmd_path = self.npm_dir / "openclaw"
        exe_path = self.local_bin_dir / "openclaw.exe"
        self._touch(cmd_path)
        self._touch(exe_path)
        path_env = os.pathsep.join([str(self.npm_dir), str(self.local_bin_dir)])
        with mock.patch.object(sr.shutil, "which", return_value=str(cmd_path)):
            shadow = sr.find_shadow_executable("openclaw", path_env=path_env, is_windows=False)
        self.assertIsNone(shadow)


class BackendArgvSafetyTests(unittest.TestCase):
    """Task text must never reach argv for a backend launched through cmd.exe.

    Windows' CreateProcess hands a .cmd/.bat target to cmd.exe /c, which
    re-parses the trailing command line using its OWN metacharacter rules
    (&, |, ^, %VAR%, redirection). A backend whose resolved executable is a
    shell script therefore MUST carry task text out-of-band (a prompt file
    or stdin, per config.py's {task_file}/{task_stdin} placeholders), never
    as a literal {task} argv token -- spoken/typed text containing those
    characters would otherwise be reinterpreted by cmd.exe instead of passed
    through verbatim. hermes, openclaw, and codex resolve to .CMD shims on
    this project's Windows target (docs/notes/2026-09-25-harness-audit.md)
    and are exactly the backends this guards.
    """

    _SHELL_SHIM_BACKENDS = ("hermes", "openclaw", "codex")

    def test_shell_shim_backends_never_place_task_text_in_argv(self):
        from remote_agent_protocol import config as cfg

        for name in self._SHELL_SHIM_BACKENDS:
            template = cfg.AGENT_BACKENDS.get(name)
            if template is None:
                continue  # not configured on this install; nothing to guard
            with self.subTest(backend=name):
                self.assertNotIn(
                    "{task}",
                    template,
                    f"{name}'s template embeds raw task text in argv, but it "
                    "resolves to a .cmd shim on Windows -- use {task_file} or "
                    "{task_stdin} instead",
                )
                self.assertTrue(
                    any("{task_file}" in part or "{task_stdin}" in part for part in template),
                    f"{name}'s template carries the task via neither "
                    "{task_file} nor {task_stdin}",
                )


if __name__ == "__main__":
    unittest.main()

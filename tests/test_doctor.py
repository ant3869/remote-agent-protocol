import json
import unittest
from unittest import mock
from urllib.error import URLError

from remote_agent_protocol import config as cfg
from remote_agent_protocol import doctor


class FormatAndExitPolicyTests(unittest.TestCase):
    def test_exit_code_is_zero_with_no_failures(self):
        results = [doctor.CheckResult("a", "ok", "fine"), doctor.CheckResult("b", "warn", "meh")]
        self.assertEqual(doctor.exit_code(results), 0)

    def test_exit_code_is_one_with_any_failure(self):
        results = [doctor.CheckResult("a", "ok", "fine"), doctor.CheckResult("b", "fail", "broken")]
        self.assertEqual(doctor.exit_code(results), 1)

    def test_format_results_includes_summary_counts(self):
        results = [
            doctor.CheckResult("a", "ok", "fine"),
            doctor.CheckResult("b", "warn", "meh"),
            doctor.CheckResult("c", "fail", "broken"),
        ]
        text = doctor.format_results(results)
        self.assertIn("[OK  ] a: fine", text)
        self.assertIn("[WARN] b: meh", text)
        self.assertIn("[FAIL] c: broken", text)
        self.assertIn("1 ok, 1 warn, 1 fail", text)


class CheckPythonTests(unittest.TestCase):
    def test_current_interpreter_passes(self):
        result = doctor.check_python()
        self.assertEqual(result.status, "ok")

    def test_below_minimum_fails(self):
        with mock.patch.object(doctor.sys, "version_info", (3, 10, 0)):
            result = doctor.check_python()
        self.assertEqual(result.status, "fail")


class CheckOllamaTests(unittest.TestCase):
    def _mock_urlopen(self, tags_models):
        def opener(url, timeout=None):
            payload = {"models": [{"name": n} for n in tags_models]}
            cm = mock.MagicMock()
            cm.__enter__.return_value = cm
            cm.read.return_value = json.dumps(payload).encode()
            return cm

        return opener

    def test_reachable_and_registered(self):
        with (
            mock.patch.object(cfg, "LLM_MODEL", "gemma-12b"),
            mock.patch.object(cfg, "INTENT_ROUTER_ENABLED", False),
            mock.patch(
                "remote_agent_protocol.doctor.json.load",
                return_value={"models": [{"name": "gemma-12b:latest"}]},
            ),
            mock.patch("remote_agent_protocol.doctor.urllib.request.urlopen"),
        ):
            results = doctor.check_ollama()

        statuses = {r.name: r.status for r in results}
        self.assertEqual(statuses["ollama-server"], "ok")
        self.assertEqual(statuses["ollama-chat-model"], "ok")

    def test_unreachable_fails_server_and_model_checks(self):
        with mock.patch(
            "remote_agent_protocol.doctor.urllib.request.urlopen", side_effect=URLError("down")
        ):
            results = doctor.check_ollama()

        statuses = {r.name: r.status for r in results}
        self.assertEqual(statuses["ollama-server"], "fail")
        self.assertEqual(statuses["ollama-chat-model"], "fail")

    def test_reachable_but_configured_model_missing(self):
        with (
            mock.patch.object(cfg, "LLM_MODEL", "does-not-exist"),
            mock.patch.object(cfg, "INTENT_ROUTER_ENABLED", False),
            mock.patch(
                "remote_agent_protocol.doctor.json.load",
                return_value={"models": [{"name": "something-else:latest"}]},
            ),
            mock.patch("remote_agent_protocol.doctor.urllib.request.urlopen"),
        ):
            results = doctor.check_ollama()

        statuses = {r.name: r.status for r in results}
        self.assertEqual(statuses["ollama-server"], "ok")
        self.assertEqual(statuses["ollama-chat-model"], "fail")
        self.assertIn("does-not-exist", results[1].message)

    def test_intent_model_checked_only_when_router_enabled(self):
        with (
            mock.patch.object(cfg, "INTENT_ROUTER_ENABLED", True),
            mock.patch.object(cfg, "INTENT_MODEL", "qwen2.5:3b"),
            mock.patch(
                "remote_agent_protocol.doctor.json.load",
                return_value={"models": [{"name": "qwen2.5:3b"}]},
            ),
            mock.patch("remote_agent_protocol.doctor.urllib.request.urlopen"),
        ):
            results = doctor.check_ollama()

        names = [r.name for r in results]
        self.assertIn("ollama-intent-model", names)


class CheckTtsBackendTests(unittest.TestCase):
    def test_kokoro_is_always_ok(self):
        with mock.patch.object(cfg, "TTS_BACKEND", "kokoro"):
            result = doctor.check_tts_backend()
        self.assertEqual(result.status, "ok")

    def test_cartesia_without_key_fails(self):
        with (
            mock.patch.object(cfg, "TTS_BACKEND", "cartesia"),
            mock.patch("remote_agent_protocol.doctor._env_file_value", return_value=None),
        ):
            result = doctor.check_tts_backend()
        self.assertEqual(result.status, "fail")


class CheckModuleAvailabilityTests(unittest.TestCase):
    def test_stt_module_installed(self):
        with (
            mock.patch.object(cfg, "STT_ENGINE", "whisper"),
            mock.patch(
                "remote_agent_protocol.doctor.importlib.util.find_spec", return_value=object()
            ),
        ):
            result = doctor.check_stt_module()
        self.assertEqual(result.status, "ok")

    def test_stt_module_missing(self):
        with (
            mock.patch.object(cfg, "STT_ENGINE", "whisper"),
            mock.patch("remote_agent_protocol.doctor.importlib.util.find_spec", return_value=None),
        ):
            result = doctor.check_stt_module()
        self.assertEqual(result.status, "fail")

    def test_tts_module_covers_kokoro_voicebox_and_coqui(self):
        for backend in ("kokoro", "voicebox", "coqui"):
            with (
                mock.patch.object(cfg, "TTS_BACKEND", backend),
                mock.patch(
                    "remote_agent_protocol.doctor.importlib.util.find_spec", return_value=None
                ),
            ):
                result = doctor.check_tts_module()
            self.assertEqual(result.status, "fail", backend)
            self.assertIn("kokoro_onnx", result.message)

    def test_tts_module_cartesia_needs_no_package(self):
        with mock.patch.object(cfg, "TTS_BACKEND", "cartesia"):
            result = doctor.check_tts_module()
        self.assertEqual(result.status, "ok")


class CheckWakeWordModuleTests(unittest.TestCase):
    def test_disabled_wake_word_is_skipped_entirely(self):
        with mock.patch.object(cfg, "WAKE_WORD_ENABLED", False):
            result = doctor.check_wake_word_module()
        self.assertIsNone(result)

    def test_enabled_but_missing_package_fails(self):
        with (
            mock.patch.object(cfg, "WAKE_WORD_ENABLED", True),
            mock.patch.object(cfg, "WAKE_WORD_ENGINE", "openwakeword"),
            mock.patch("remote_agent_protocol.doctor.importlib.util.find_spec", return_value=None),
        ):
            result = doctor.check_wake_word_module()
        self.assertEqual(result.status, "fail")

    def test_enabled_and_installed_is_ok(self):
        with (
            mock.patch.object(cfg, "WAKE_WORD_ENABLED", True),
            mock.patch.object(cfg, "WAKE_WORD_ENGINE", "openwakeword"),
            mock.patch(
                "remote_agent_protocol.doctor.importlib.util.find_spec", return_value=object()
            ),
        ):
            result = doctor.check_wake_word_module()
        self.assertEqual(result.status, "ok")


class CheckAudioDevicesTests(unittest.TestCase):
    def test_no_explicit_indices_returns_nothing(self):
        with (
            mock.patch.object(cfg, "MIC_DEVICE_INDEX", None),
            mock.patch.object(cfg, "SPEAKER_DEVICE_INDEX", None),
        ):
            self.assertEqual(doctor.check_audio_devices(), [])

    def test_bad_explicit_device_index_fails(self):
        with (
            mock.patch.object(cfg, "MIC_DEVICE_INDEX", 99),
            mock.patch.object(cfg, "SPEAKER_DEVICE_INDEX", None),
            mock.patch(
                "remote_agent_protocol.doctor._audio_device_names", return_value={0: "Built-in Mic"}
            ),
        ):
            results = doctor.check_audio_devices()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "fail")
        self.assertIn("99", results[0].message)

    def test_valid_explicit_device_index_passes(self):
        with (
            mock.patch.object(cfg, "MIC_DEVICE_INDEX", 0),
            mock.patch.object(cfg, "SPEAKER_DEVICE_INDEX", None),
            mock.patch(
                "remote_agent_protocol.doctor._audio_device_names", return_value={0: "Built-in Mic"}
            ),
        ):
            results = doctor.check_audio_devices()

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, "ok")


class CheckAgentBackendsTests(unittest.TestCase):
    def test_python_placeholder_is_always_ok(self):
        with mock.patch.object(cfg, "AGENT_BACKENDS", {"mock": ["{python}", "-u", "{task}"]}):
            results = doctor.check_agent_backends()
        self.assertEqual(results[0].status, "ok")

    def test_missing_executable_on_path_fails(self):
        with (
            mock.patch.object(cfg, "AGENT_BACKENDS", {"hermes": ["hermes", "chat", "{task}"]}),
            mock.patch("remote_agent_protocol.doctor.shutil.which", return_value=None),
        ):
            results = doctor.check_agent_backends()
        self.assertEqual(results[0].status, "fail")

    def test_executable_found_on_path_is_ok(self):
        with (
            mock.patch.object(cfg, "AGENT_BACKENDS", {"hermes": ["hermes", "chat", "{task}"]}),
            mock.patch(
                "remote_agent_protocol.doctor.shutil.which", return_value=r"C:\tools\hermes.exe"
            ),
        ):
            results = doctor.check_agent_backends()
        self.assertEqual(results[0].status, "ok")

    def test_never_executes_the_backend_command(self):
        with (
            mock.patch.object(cfg, "AGENT_BACKENDS", {"hermes": ["hermes", "chat", "{task}"]}),
            mock.patch(
                "remote_agent_protocol.doctor.shutil.which", return_value=r"C:\tools\hermes.exe"
            ),
            mock.patch("subprocess.run") as run,
            mock.patch("subprocess.Popen") as popen,
        ):
            doctor.check_agent_backends()

        run.assert_not_called()
        popen.assert_not_called()


class RunChecksTests(unittest.TestCase):
    def test_one_crashing_check_does_not_hide_the_rest(self):
        boom = mock.Mock(side_effect=RuntimeError("boom"), __name__="boom_check")
        fine = mock.Mock(return_value=doctor.CheckResult("fine", "ok", "all good"))
        with mock.patch.object(doctor, "_CHECKS", (boom, fine)):
            results = doctor.run_checks()

        statuses = {r.name: r.status for r in results}
        self.assertEqual(statuses["boom_check"], "fail")
        self.assertEqual(statuses["fine"], "ok")

    def test_full_run_never_installs_downloads_or_launches_anything(self):
        with (
            mock.patch("subprocess.run") as run,
            mock.patch("subprocess.Popen") as popen,
            mock.patch(
                "remote_agent_protocol.doctor.urllib.request.urlopen", side_effect=URLError("down")
            ),
        ):
            doctor.run_checks()

        run.assert_not_called()
        popen.assert_not_called()

    def test_fully_healthy_configuration_exits_zero(self):
        healthy = (
            lambda: doctor.CheckResult("a", "ok", "fine"),
            lambda: doctor.CheckResult("b", "ok", "fine"),
        )
        with mock.patch.object(doctor, "_CHECKS", healthy):
            self.assertEqual(doctor.exit_code(doctor.run_checks()), 0)

    def test_any_failure_exits_one(self):
        mixed = (
            lambda: doctor.CheckResult("a", "ok", "fine"),
            lambda: doctor.CheckResult("b", "fail", "broken"),
        )
        with mock.patch.object(doctor, "_CHECKS", mixed):
            self.assertEqual(doctor.exit_code(doctor.run_checks()), 1)


class MainEntryPointTests(unittest.TestCase):
    def test_help_exits_cleanly(self):
        with self.assertRaises(SystemExit) as ctx:
            doctor.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)


if __name__ == "__main__":
    unittest.main()

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from remote_agent_protocol import dashboard


class DashboardTests(unittest.TestCase):
    def test_classifies_pipecat_processors_into_dashboard_buckets(self):
        self.assertEqual(dashboard.metric_bucket("WhisperSTTService#0"), "stt")
        self.assertEqual(dashboard.metric_bucket("MoonshineSTTService#0"), "stt")
        self.assertEqual(dashboard.metric_bucket("OpenAILLMService#0"), "llm")
        self.assertEqual(dashboard.metric_bucket("KokoroTTSService#0"), "tts")
        self.assertIsNone(dashboard.metric_bucket("SomeRandomProcessor#0"))

    def test_metric_event_turns_timing_metrics_into_plain_events(self):
        metric = SimpleNamespace(processor="WhisperSTTService#0", value=0.410)

        event = dashboard.metric_event(metric, kind="processing")

        self.assertEqual(
            event,
            {"type": "metric", "bucket": "stt", "kind": "processing", "value": 0.410},
        )

    def test_metric_event_ignores_unknown_processors(self):
        metric = SimpleNamespace(processor="Random#0", value=1.23)

        self.assertIsNone(dashboard.metric_event(metric, kind="processing"))

    def test_latency_state_tracks_total_from_user_stop_to_bot_start(self):
        state = dashboard.LatencyState()

        state.mark_user_turn_complete(100.0)
        state.update("stt", "processing", 0.41)
        state.update("llm", "ttfb", 0.52)
        state.mark_bot_started(101.75)

        self.assertEqual(state.values["stt"], 0.41)
        self.assertEqual(state.values["llm"], 0.52)
        self.assertEqual(state.values["total"], 1.75)

    def test_formats_latency_dashboard_line(self):
        state = dashboard.LatencyState()
        state.update("stt", "processing", 0.410)
        state.update("llm", "ttfb", 0.523)
        state.update("tts", "processing", 0.691)
        state.values["total"] = 1.812

        self.assertEqual(
            dashboard.format_latency_line(state),
            "STT 0.41s | LLM 0.52s | TTS 0.69s | Total 1.81s",
        )

    def test_ollama_health_from_payloads(self):
        tags = {"models": [{"name": "gemma:latest"}, {"name": "nomic-embed-text:latest"}]}
        ps = {"models": [{"name": "gemma:latest"}]}

        health = dashboard.OllamaHealth.from_payloads(tags, ps)

        self.assertTrue(health.ok)
        self.assertEqual(health.model_count, 2)
        self.assertEqual(health.loaded_count, 1)
        self.assertEqual(health.label, "Ollama OK (1 loaded / 2 installed)")

    def test_ollama_health_down_label(self):
        health = dashboard.OllamaHealth.down("nope")

        self.assertFalse(health.ok)
        self.assertEqual(health.label, "Ollama DOWN")


class OllamaPathTests(unittest.TestCase):
    """The install location must come from the environment, never a literal user path."""

    def test_install_dir_follows_localappdata(self):
        with mock.patch.dict("os.environ", {"LOCALAPPDATA": r"X:\SomeUser\AppData\Local"}):
            self.assertEqual(
                dashboard.ollama_install_dir(),
                Path(r"X:\SomeUser\AppData\Local") / "Programs" / "Ollama",
            )

    def test_app_path_is_none_when_not_installed(self):
        with tempfile.TemporaryDirectory() as empty:
            with mock.patch.dict("os.environ", {"LOCALAPPDATA": empty}):
                self.assertIsNone(dashboard.ollama_app_path())

    def test_cli_path_prefers_path_lookup(self):
        with mock.patch(
            "remote_agent_protocol.dashboard.shutil.which", return_value=r"C:\tools\ollama.exe"
        ):
            self.assertEqual(dashboard.ollama_cli_path(), r"C:\tools\ollama.exe")

    def test_cli_path_falls_back_to_install_dir(self):
        with tempfile.TemporaryDirectory() as base:
            exe = Path(base) / "Programs" / "Ollama" / "ollama.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"")
            with mock.patch("remote_agent_protocol.dashboard.shutil.which", return_value=None):
                with mock.patch.dict("os.environ", {"LOCALAPPDATA": base}):
                    self.assertEqual(dashboard.ollama_cli_path(), str(exe))

    def test_start_ollama_app_raises_a_clear_error_when_missing(self):
        with tempfile.TemporaryDirectory() as empty:
            with mock.patch.dict("os.environ", {"LOCALAPPDATA": empty}):
                with self.assertRaisesRegex(RuntimeError, "Ollama app not found"):
                    dashboard.start_ollama_app()


class VramStatusTests(unittest.TestCase):
    def test_unavailable_when_nvidia_smi_missing(self):
        with mock.patch("remote_agent_protocol.dashboard.shutil.which", return_value=None):
            status = dashboard.vram_status()
        self.assertFalse(status.available)
        self.assertIn("not found", status.error)

    def test_parses_csv_output_from_the_first_gpu(self):
        completed = SimpleNamespace(
            returncode=0, stdout="8192, 16384, 42\n", stderr=""
        )
        with (
            mock.patch(
                "remote_agent_protocol.dashboard.shutil.which",
                return_value=r"C:\nvidia-smi.exe",
            ),
            mock.patch(
                "remote_agent_protocol.dashboard.subprocess.run", return_value=completed
            ),
        ):
            status = dashboard.vram_status()
        self.assertTrue(status.available)
        self.assertEqual(status.used_mb, 8192)
        self.assertEqual(status.total_mb, 16384)
        self.assertEqual(status.gpu_util_percent, 42)
        self.assertEqual(status.percent, 50.0)
        self.assertEqual(status.label, "8.0 / 16.0 GB (50%)")

    def test_only_the_first_line_is_used_on_a_multi_gpu_box(self):
        completed = SimpleNamespace(
            returncode=0, stdout="1024, 8192, 10\n2048, 8192, 20\n", stderr=""
        )
        with (
            mock.patch(
                "remote_agent_protocol.dashboard.shutil.which",
                return_value=r"C:\nvidia-smi.exe",
            ),
            mock.patch(
                "remote_agent_protocol.dashboard.subprocess.run", return_value=completed
            ),
        ):
            status = dashboard.vram_status()
        self.assertEqual(status.used_mb, 1024)

    def test_unavailable_on_nonzero_return_code(self):
        completed = SimpleNamespace(returncode=1, stdout="", stderr="driver not loaded")
        with (
            mock.patch(
                "remote_agent_protocol.dashboard.shutil.which",
                return_value=r"C:\nvidia-smi.exe",
            ),
            mock.patch(
                "remote_agent_protocol.dashboard.subprocess.run", return_value=completed
            ),
        ):
            status = dashboard.vram_status()
        self.assertFalse(status.available)
        self.assertEqual(status.error, "driver not loaded")

    def test_unavailable_when_subprocess_raises(self):
        with (
            mock.patch(
                "remote_agent_protocol.dashboard.shutil.which",
                return_value=r"C:\nvidia-smi.exe",
            ),
            mock.patch(
                "remote_agent_protocol.dashboard.subprocess.run",
                side_effect=OSError("boom"),
            ),
        ):
            status = dashboard.vram_status()
        self.assertFalse(status.available)
        self.assertIn("boom", status.error)

    def test_no_gpu_label(self):
        status = dashboard.VramHealth(available=False)
        self.assertEqual(status.label, "No GPU")


if __name__ == "__main__":
    unittest.main()

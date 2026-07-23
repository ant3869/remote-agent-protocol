import unittest
from datetime import datetime

from remote_agent_protocol import diagnostics


def sample_report():
    return diagnostics.build_report(
        session_snapshot={
            "persona": "Jess",
            "model": "gemma-e4b-max",
            "voice": "af_heart",
            "voice_backend": "kokoro",
            "default_agent_backend": "hermes",
            "agent_backends": ["hermes", "mock"],
            "short_term_memory": ["You: hi", "Jess: hey"],
        },
        tts_backend="kokoro",
        ollama={"ok": True, "label": "Ollama OK"},
        tts={"ok": False, "label": "Voicebox DOWN"},
        latency_line="STT 0.10s | LLM 0.40s | TTS 0.20s | Total 0.70s",
        jobs=[{"status": "done", "secs": 2.0, "agent": "mock", "task": "build"}],
        devices=[{"index": 1, "name": "Mic", "in": 2, "out": 0}],
        now=datetime(2026, 7, 3, 12, 0, 0),
    )


class DiagnosticsTests(unittest.TestCase):
    def test_build_report_is_stable_with_fixed_now(self):
        report = sample_report()
        self.assertEqual(report["generated_at"], "2026-07-03T12:00:00")
        self.assertEqual(report["tts_backend"], "kokoro")

    def test_format_warns_about_conversation_content(self):
        text = diagnostics.format_report(sample_report())
        self.assertIn("review before sharing", text)

    def test_format_includes_health_session_and_jobs(self):
        text = diagnostics.format_report(sample_report())
        self.assertIn("Ollama OK", text)
        self.assertIn("Voicebox DOWN", text)
        self.assertIn("PROBLEM", text)  # tts.ok is False
        self.assertIn("persona        : Jess", text)
        self.assertIn("mock: build", text)
        self.assertIn("Mic", text)
        self.assertIn("You: hi", text)

    def test_format_handles_empty_sections(self):
        report = diagnostics.build_report(
            session_snapshot={},
            tts_backend="kokoro",
            ollama={"ok": True, "label": "OK"},
            tts={"ok": True, "label": "OK"},
            latency_line="--",
            jobs=[],
            devices=[],
            now=datetime(2026, 1, 1),
        )
        text = diagnostics.format_report(report)
        self.assertIn("(none)", text)
        self.assertIn("(PyAudio unavailable)", text)
        self.assertIn("(empty)", text)


if __name__ == "__main__":
    unittest.main()

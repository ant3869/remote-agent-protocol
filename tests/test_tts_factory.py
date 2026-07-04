import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from remote_agent_protocol import tts_factory


class TTSFactoryTests(unittest.TestCase):
    def test_load_env_value_reads_dotenv_without_printing_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("# hi\nCARTESIA_API_KEY='secret-value'\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(
                    tts_factory.load_env_value("CARTESIA_API_KEY", path), "secret-value"
                )

    def test_environment_wins_over_dotenv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("CARTESIA_API_KEY=file-secret\n", encoding="utf-8")
            with patch.dict(os.environ, {"CARTESIA_API_KEY": "env-secret"}, clear=True):
                self.assertEqual(tts_factory.load_env_value("CARTESIA_API_KEY", path), "env-secret")

    def test_is_uuid(self):
        self.assertTrue(tts_factory.is_uuid("db6b0ed5-d5d3-463d-ae85-518a07d3c2b4"))
        self.assertFalse(tts_factory.is_uuid("af_heart"))


if __name__ == "__main__":
    unittest.main()

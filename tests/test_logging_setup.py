import re
import sys
import tempfile
import unittest
from pathlib import Path

from loguru import logger

from remote_agent_protocol import logging_setup


class RuntimeLogTests(unittest.TestCase):
    def test_setup_logging_writes_bounded_runtime_log(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime.log"
            try:
                logging_setup.setup_logging(False, log_path=path)
                logger.info("agent status persisted")
                text = path.read_text(encoding="utf-8")
            finally:
                logger.remove()
                logger.add(sys.stderr)

        self.assertIn("agent status persisted", text)
        self.assertRegex(text, r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}[+-]\d{2}:\d{2}")


if __name__ == "__main__":
    unittest.main()

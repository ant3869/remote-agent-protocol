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

    def test_default_log_path_is_test_only_under_pytest(self):
        # Regression: gui.py/terminal.py/web_gui.py call setup_logging() at
        # bare module import time. Merely importing one of those from a test
        # file used to point loguru's global sink at the real runtime log for
        # the rest of that pytest process -- every other test's log output
        # then landed there too, interleaved with real conversation history.
        self.assertTrue(logging_setup._running_under_pytest())
        self.assertNotEqual(logging_setup._TEST_LOG_PATH, logging_setup._DEFAULT_LOG_PATH)

        try:
            logging_setup.setup_logging(False)  # log_path defaults to None
            logger.info("should land in the test log, never the real one")
        finally:
            logger.remove()
            logger.add(sys.stderr)

        self.assertTrue(logging_setup._TEST_LOG_PATH.exists())
        self.assertIn(
            "should land in the test log",
            logging_setup._TEST_LOG_PATH.read_text(encoding="utf-8"),
        )
        if logging_setup._DEFAULT_LOG_PATH.exists():
            self.assertNotIn(
                "should land in the test log",
                logging_setup._DEFAULT_LOG_PATH.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()

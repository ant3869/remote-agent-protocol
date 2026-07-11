"""Readable terminal and bounded file logging for the local Jess demo.

Pipecat is wired with loguru and is *extremely* chatty at DEBUG level. The
single noisiest offender is the LLM service dumping the ENTIRE conversation
context on every turn -- "Generating chat from context [ ...50 messages... ]" --
which buries the genuinely useful one-line events (TTFB, transcriptions, turn
state) under a screenful of JSON.

This module installs compact stderr and rotating-file sinks that:
  * drops those wall-of-text dumps outright (we don't need the whole prompt
    echoed back at us in a live voice session),
  * defensively truncates any other runaway line,
  * prints a compact, aligned `time | LEVEL | source | message` format that's
    actually scannable.

Flip ``DEBUG_MODE`` in config.py to choose verbosity; this just makes whatever
level you pick readable instead of a firehose.
"""

import sys
from pathlib import Path

from loguru import logger

# Substrings that mark a line as "a full-context dump we never want to read".
# The event itself is implied by the metrics lines around it, so we drop it
# entirely rather than truncate.
_NOISY_DUMPS = ("Generating chat from context",)

# Nothing readable is ever this long. Anything bigger gets clipped so a single
# stray blob can't blow up your scrollback again.
_MAX_LINE = 280


def _readable_filter(record) -> bool:
    """Return False for the megabyte-class dump lines so loguru skips them."""
    message = record["message"]
    return not any(marker in message for marker in _NOISY_DUMPS)


def _formatter(record) -> str:
    """Build a compact format string and stash derived fields on the record."""
    # "pipecat.processors.aggregators.llm_response_universal" -> "llm_response_universal"
    record["extra"]["source"] = record["name"].rsplit(".", 1)[-1]

    message = record["message"]
    if len(message) > _MAX_LINE:
        overflow = len(message) - _MAX_LINE
        message = f"{message[:_MAX_LINE].rstrip()} …(+{overflow} chars)"
    record["extra"]["body"] = message
    record["extra"]["timestamp"] = record["time"].isoformat(timespec="milliseconds")

    return (
        "<green>{extra[timestamp]}</green> │ "
        "<level>{level: <5}</level> │ "
        "<cyan>{extra[source]: <26}</cyan> │ "
        "<level>{extra[body]}</level>\n{exception}"
    )


_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DEFAULT_LOG_PATH = _DATA_DIR / "jess_runtime.log"
# gui.py, terminal.py, and web_gui.py each call setup_logging() at bare module
# import time, so merely importing one of them from a test file -- not running
# it -- used to point loguru's global sink at the real runtime log for the
# rest of that pytest process. Every other test's log calls landed there too,
# interleaving synthetic fixture data (repeated "job-1".."job-32" bursts,
# deliberately-triggered failures) with real conversation history, which read
# as the app "piling answers on top of each other" when reviewed afterward.
_TEST_LOG_PATH = _DATA_DIR / "test_runtime.log"


def _running_under_pytest() -> bool:
    """True for the whole pytest process, from collection through the last test."""
    return "pytest" in sys.modules


def setup_logging(debug: bool, log_path: str | Path | None = None) -> None:
    """Replace loguru's default sink with our readable, filtered one.

    Args:
        debug: When True, show the full DEBUG pipeline flow (minus the noise).
            When False, stay quiet at WARNING for normal use.
        log_path: Bounded rotating runtime-log destination. Defaults to the
            real runtime log, or a separate test-only log when running under
            pytest -- pass an explicit path to override either way.
    """
    if log_path is None:
        log_path = _TEST_LOG_PATH if _running_under_pytest() else _DEFAULT_LOG_PATH
    logger.remove()
    logger.add(
        sys.stderr,
        level="DEBUG" if debug else "WARNING",
        format=_formatter,
        filter=_readable_filter,
        colorize=True,
    )
    logger.add(
        log_path,
        level="DEBUG" if debug else "INFO",
        format=_formatter,
        filter=_readable_filter,
        colorize=False,
        encoding="utf-8",
        rotation="2 MB",
        retention=3,
    )

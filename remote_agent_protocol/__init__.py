"""Remote Agent Protocol -- local-first desktop voice switchboard built on Pipecat.

The application package: GUI, voice session, agent bridge, memory, personas,
and configuration. The vendored Pipecat framework lives in ``src/pipecat`` and
is used as a library; application code stays in this package.
"""

from pathlib import Path

try:
    __version__ = (
        (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip()
    )
except OSError:
    __version__ = "0.0.0+unknown"

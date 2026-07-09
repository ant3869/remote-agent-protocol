"""Single-instance guard -- close a leftover process from a previous run.

Why this exists: the app spawns agent-backend subprocesses (hermes,
code-puppy, ...) as descendants of its own process (agent_bridge.py). A clean
exit reaps them via AgentBridge.shutdown(), but a run that crashes or gets
killed outside that path (task manager, a window-manager force-close, a power
loss) leaves both the old app process and any agent children it spawned
running in the background -- competing for the mic and holding API/websocket
ports open. On every launch, the app looks for the PID it recorded last time
and kills its whole process tree before doing anything else, then records its
own PID for next time.

Windows-only for now: process identity/termination is done via PowerShell CIM
queries and ``taskkill /T`` (tree kill), since that's the only platform this
app currently ships on. A no-op elsewhere rather than a half-working guess.
"""

import os
import subprocess
import sys
from pathlib import Path

from loguru import logger

from remote_agent_protocol import config as cfg

_LOCK_FILE = cfg.DATA_DIR / "jess.pid"
# Substring that must appear in a candidate PID's command line before we kill
# it -- guards against PID reuse handing us an unrelated process to murder.
_IDENTITY_MARKER = "remote_agent_protocol"


def _read_lock(lock_file: Path) -> int | None:
    try:
        return int(lock_file.read_text().strip())
    except (OSError, ValueError):
        return None


def _command_line(pid: int) -> str:
    """Best-effort command line for ``pid``; empty string if it's gone or unreadable."""
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip()


def close_previous_instance(lock_file: Path = _LOCK_FILE) -> None:
    """Kill the leftover process (and its subprocess tree) from the last run, if any."""
    if sys.platform != "win32":
        return
    pid = _read_lock(lock_file)
    if pid is None:
        return
    if pid == os.getpid():
        return
    if _IDENTITY_MARKER not in _command_line(pid):
        return  # dead, or the PID was recycled by an unrelated process
    logger.warning(f"Closing leftover {_IDENTITY_MARKER} process from a previous run: PID {pid}")
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"Could not close leftover process {pid}: {e}")


def write_lock(lock_file: Path = _LOCK_FILE) -> None:
    """Record this process's PID so the next launch can find it if this one crashes."""
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(str(os.getpid()))


def release_lock(lock_file: Path = _LOCK_FILE) -> None:
    """Clear the lock on a clean shutdown, so the next launch has nothing to close."""
    pid = _read_lock(lock_file)
    if pid is not None and pid != os.getpid():
        return
    try:
        lock_file.unlink()
    except FileNotFoundError:
        pass

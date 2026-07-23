"""Single-instance guard -- refuse a second launch, close a crash leftover.

Why this exists: the app spawns agent-backend subprocesses (hermes,
code-puppy, ...) as descendants of its own process (agent_bridge.py). A clean
exit reaps them via AgentBridge.shutdown(), but a run that crashes or gets
killed outside that path (task manager, a window-manager force-close, a power
loss) leaves both the old app process and any agent children it spawned
running in the background -- competing for the mic and holding API/websocket
ports open. On every launch, the app looks for the PID it recorded last time
and kills its whole process tree before doing anything else, then records its
own PID for next time.

That PID-file check is advisory, not atomic: two launches close enough
together (e.g. a double-invoked start script) both read the file before
either has written its own PID, so both pass the check and end up running
side by side -- and worse, the second one's close_previous_instance() then
kills the FIRST one's still-healthy PID, since nothing distinguishes a
crash leftover from a sibling that is simply still starting up
(jess_runtime.log 2026-07-10 23:04:30: two full app instances launched the
same second, both binding the lifecycle websocket port). acquire_single_
instance_lock() closes that gap with a named OS mutex, which the kernel
guarantees only one process can ever hold; call it FIRST, before touching
the PID file at all, so a genuine live sibling is left alone.

Windows-only for now: process identity/termination is done via PowerShell CIM
queries and ``taskkill /T`` (tree kill), since that's the only platform this
app currently ships on. A no-op elsewhere rather than a half-working guess.
"""

import ctypes
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from remote_agent_protocol import config as cfg

_LOCK_FILE = cfg.DATA_DIR / "jess.pid"
# Substring that must appear in a candidate PID's command line before we kill
# it -- guards against PID reuse handing us an unrelated process to murder.
_IDENTITY_MARKER = "remote_agent_protocol"

# Machine-wide (not per-session) so two logins can't both run the app either.
_MUTEX_NAME = "Global\\RemoteAgentProtocolSingleInstance"
_ERROR_ALREADY_EXISTS = 183
# The mutex handle must outlive this function or Windows releases it
# immediately; kept here so exactly one module-global owns it per process.
_mutex_handle: int | None = None


def acquire_single_instance_lock(name: str = _MUTEX_NAME) -> bool:
    """Atomically claim this machine's one Remote Agent Protocol slot.

    Returns False if another instance already holds it -- the caller should
    exit without touching the PID file, since that instance is genuinely
    alive, not a crash leftover. Returns True once this process owns the
    lock; the OS releases it automatically on exit or crash, so there is no
    stale state to clean up on the next launch. No-op (always True) off
    Windows, matching close_previous_instance().
    """
    global _mutex_handle
    if sys.platform != "win32":
        return True
    handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True  # couldn't ask the OS; don't block startup on it
    if ctypes.windll.kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        ctypes.windll.kernel32.CloseHandle(handle)
        return False
    _mutex_handle = handle
    return True


# Console control events (wincon.h). CTRL_C_EVENT/CTRL_BREAK_EVENT are
# already delivered to Python as KeyboardInterrupt by CPython's own handler
# (registered ahead of ours), so this app's normal Ctrl+C path is untouched;
# these are the events Python does NOT translate on its own.
_CTRL_CLOSE_EVENT = 2
_CTRL_LOGOFF_EVENT = 5
_CTRL_SHUTDOWN_EVENT = 6
_FORWARDED_CTRL_EVENTS = {_CTRL_CLOSE_EVENT, _CTRL_LOGOFF_EVENT, _CTRL_SHUTDOWN_EVENT}
# Must outlive the process (same reason as _mutex_handle above): ctypes frees
# a callback the moment nothing references it, and Windows calling into a
# freed callback crashes the process instead of raising a catchable error.
_console_handler_ref = None


def install_close_handler(on_close: Callable[[], None]) -> None:
    """Run ``on_close`` on window close, logoff, or shutdown -- not just Ctrl+C.

    Why this exists: start_gui.bat's own instructions say "Close the window
    to quit", but clicking that X sends CTRL_CLOSE_EVENT, which CPython does
    not turn into KeyboardInterrupt the way it does Ctrl+C. Left unhandled,
    Windows gives the process a few seconds and then force-terminates it --
    skipping WebVoiceApp.run()'s ``finally`` entirely, so the voice session,
    its delegated agent subprocesses, and the Voicebox server never get torn
    down and are left running until the next launch's close_previous_
    instance() reaps them. Wiring this event to the same graceful-shutdown
    path used for Ctrl+C closes that gap at the moment of closing, not one
    launch later.

    ``on_close`` runs on a dedicated OS-created thread (never the main
    thread), so it is safe -- and expected -- for it to block until cleanup
    actually finishes; Windows only force-kills if it never returns. No-op
    off Windows.
    """
    if sys.platform != "win32":
        return

    def _handler(ctrl_type: int) -> bool:
        if ctrl_type not in _FORWARDED_CTRL_EVENTS:
            return False  # not ours; let the next handler (or default) run
        try:
            on_close()
        except Exception:
            logger.exception("Console close handler failed")
        return True

    global _console_handler_ref
    handler_routine = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
    _console_handler_ref = handler_routine(_handler)
    if not ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_handler_ref, True):
        logger.warning("Could not install console close handler")


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
            # OS-locale default codec crashes subprocess's internal reader
            # thread on any non-cp1252 byte (see cli_agents.py); a command
            # line can carry arbitrary task text, so pin utf-8 defensively.
            encoding="utf-8",
            errors="replace",
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

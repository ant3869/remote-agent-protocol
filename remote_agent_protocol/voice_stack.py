r"""One-command launcher for brain mode plus its external realtime frontend.

Brain mode is three processes across two checkouts: this GUI serving the brain
endpoint, the frontend's server owning STT/TTS, and the frontend's client owning
the microphone and speakers. They must start in that order -- the server dials
the brain, and the client dials the server -- so this module starts each one only
once the previous is actually answering, rather than sleeping and hoping.

Run it with ``python -m remote_agent_protocol.voice_stack`` or
``scripts\\start_voice.bat``.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect as websocket_connect

from remote_agent_protocol import config as cfg

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Stage:
    """One child process and the check that proves it is ready for the next."""

    name: str
    args: list[str]
    cwd: Path
    ready: Callable[[], bool] | None = None
    ready_timeout: float = 180.0
    stop: Callable[[], bool] | None = None
    ready_file: Path | None = None


def realtime_ready(websocket_url: str, pool_url: str) -> Callable[[], bool]:
    """Require one Realtime handshake, then wait until its pool slot is idle."""
    handshake_complete = False

    def probe() -> bool:
        nonlocal handshake_complete
        try:
            if not handshake_complete:
                with websocket_connect(
                    websocket_url, open_timeout=2.0, close_timeout=1.0
                ) as connection:
                    payload = json.loads(connection.recv(timeout=2.0))
                    if not isinstance(payload, dict) or payload.get("type") != "session.created":
                        return False
                handshake_complete = True
            with urllib.request.urlopen(pool_url, timeout=2.0) as response:
                pool = json.loads(response.read())
            units = pool.get("units", [])
            return pool.get("in_use") == 0 and bool(units) and all(
                unit.get("state") == "idle" for unit in units
            )
        except (OSError, TimeoutError, ValueError, json.JSONDecodeError, WebSocketException):
            return False

    return probe


def http_ok(url: str) -> Callable[[], bool]:
    """Build a readiness probe for an HTTP endpoint that should answer 2xx."""

    def probe() -> bool:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, OSError, TimeoutError):
            return False

    return probe


def http_post_ok(url: str, api_key: str) -> Callable[[], bool]:
    """Build an authenticated soft-shutdown request."""

    def request_shutdown() -> bool:
        request = urllib.request.Request(
            url,
            data=b"{}",
            headers={"Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=2.0) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, OSError, TimeoutError):
            return False

    return request_shutdown


def port_open(host: str, port: int) -> Callable[[], bool]:
    """Build a readiness probe for a TCP listener."""

    def probe() -> bool:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            return False

    return probe


def free_loopback_port() -> int:
    """Ask Windows for one currently free loopback TCP port."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def select_stack_ports() -> tuple[int, int]:
    """Allocate distinct ephemeral ports for the brain and Realtime server."""
    bridge_port = free_loopback_port()
    ws_port = free_loopback_port()
    while ws_port == bridge_port:
        ws_port = free_loopback_port()
    return bridge_port, ws_port


def file_ready(path: Path) -> Callable[[], bool]:
    """Build a readiness probe for an atomic PID-tagged handshake file."""

    def probe() -> bool:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload.get("ready") is True and int(payload.get("pid", 0)) > 0
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False

    return probe


def resolve_s2s_home() -> Path | None:
    """Locate the frontend checkout, falling back to a sibling directory."""
    if cfg.S2S_HOME:
        candidate = Path(cfg.S2S_HOME).expanduser()
        return candidate if candidate.is_dir() else None
    sibling = REPO_ROOT.parent / "speech-to-speech"
    return sibling if sibling.is_dir() else None


def missing_frontend_pieces(s2s_home: Path) -> list[str]:
    """Return the launchers/venv the frontend checkout is missing, if any."""
    required = ("run-rap-brain.cmd", "run-realtime-client.cmd", ".venv")
    return [name for name in required if not (s2s_home / name).exists()]


def occupied_ports(
    bridge_port: int | None = None, ws_port: int | None = None
) -> list[tuple[str, int]]:
    """Return selected stack ports that something is already listening on.

    Readiness is probed by connecting, which any process holding the port
    satisfies -- including a leftover one. Without this check a second launch
    looks healthy while its own server spends a minute loading models and then
    dies on bind, and the client quietly attaches to the stale server instead.
    """
    wanted = (
        ("RAP brain/GUI", cfg.S2S_BRIDGE_PORT if bridge_port is None else bridge_port),
        ("speech-to-speech server", cfg.S2S_WS_PORT if ws_port is None else ws_port),
    )
    return [(name, port) for name, port in wanted if port_open("127.0.0.1", port)()]


def missing_kokoro_requirements(s2s_home: Path) -> list[str]:
    """Return the Kokoro imports the frontend's venv cannot satisfy.

    Only checked when its launcher actually selects Kokoro. Worth checking at all
    because the failure is badly misreported downstream: the TTS handler wraps
    pipeline construction in ``except ImportError`` and blames a missing kokoro
    package, when the real gap is usually spaCy's ``en_core_web_sm`` -- which
    spaCy installs by shelling out to pip, something a uv venv does not have.
    """
    launcher = s2s_home / "run-rap-brain.cmd"
    try:
        if "--tts kokoro" not in launcher.read_text(encoding="utf-8"):
            return []
    except OSError:
        return []

    python = s2s_home / ".venv/Scripts/python.exe"
    if not python.exists():
        return []
    probe = (
        "import importlib.util as u;"
        "print('kokoro' if u.find_spec('kokoro') is None else '', end=' ');"
        "print('en_core_web_sm' if u.find_spec('en_core_web_sm') is None else '', end='')"
    )
    try:
        result = subprocess.run(
            [str(python), "-c", probe], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return result.stdout.split()


def build_stages(
    s2s_home: Path,
    *,
    bridge_port: int | None = None,
    ws_port: int | None = None,
    bridge_api_key: str | None = None,
) -> list[Stage]:
    """Describe the three processes in dependency order."""
    bridge_port = cfg.S2S_BRIDGE_PORT if bridge_port is None else bridge_port
    ws_port = cfg.S2S_WS_PORT if ws_port is None else ws_port
    bridge_api_key = bridge_api_key or cfg.S2S_BRIDGE_API_KEY
    client_ready_file = (cfg.DATA_DIR / "s2s_client.ready").resolve()
    return [
        Stage(
            name="RAP brain + GUI",
            args=[sys.executable, "-u", "-m", "remote_agent_protocol"],
            cwd=REPO_ROOT,
            ready=http_ok(f"http://127.0.0.1:{bridge_port}/health"),
            stop=http_post_ok(
                f"http://127.0.0.1:{bridge_port}/api/stack-shutdown",
                bridge_api_key,
            ),
        ),
        Stage(
            name="speech-to-speech server",
            # The sibling launcher forwards %*, and its parser keeps the last
            # duplicate value. RAP therefore owns the network boundary and
            # brain settings instead of trusting stale literals in another repo.
            args=[
                str(s2s_home / "run-rap-brain.cmd"),
                "--ws_host",
                "127.0.0.1",
                "--ws_port",
                str(ws_port),
                "--responses_api_base_url",
                f"http://127.0.0.1:{bridge_port}/v1",
                "--responses_api_api_key",
                bridge_api_key,
                "--model_name",
                cfg.S2S_BRIDGE_MODEL,
                # Speak after ONE finished sentence; the upstream default of 3
                # makes the butler feel like he is reading prepared remarks.
                "--stream_batch_sentences",
                "1",
            ],
            cwd=s2s_home,
            # A TCP listener may be stale or unrelated. Require the protocol's
            # session.created event before launching the physical audio client.
            ready=realtime_ready(
                f"ws://127.0.0.1:{ws_port}/v1/realtime",
                f"http://127.0.0.1:{ws_port}/v1/pool",
            ),
            ready_timeout=600.0,
        ),
        Stage(
            # The launcher knows the real paths and port, so pass them rather
            # than trusting the .cmd's hardcoded copies. Its argparse takes the
            # last value, so these win over whatever the script already sets.
            name="speech-to-speech client",
            args=[
                str(s2s_home / "run-realtime-client.cmd"),
                "--host",
                "127.0.0.1",
                "--port",
                str(ws_port),
                "--external-mute-file",
                str(Path(cfg.S2S_MIC_MUTE_FILE).resolve()),
                "--external-voice-file",
                str(Path(cfg.S2S_VOICE_FILE).resolve()),
                "--external-mode-file",
                str(Path(cfg.S2S_VOICE_MODE_FILE).resolve()),
                "--external-mode-status-file",
                str(Path(cfg.S2S_VOICE_MODE_STATUS_FILE).resolve()),
                "--avatar-envelope-url",
                f"http://127.0.0.1:{bridge_port}/api/avatar-envelope",
                "--avatar-envelope-api-key",
                bridge_api_key,
                "--announce-file",
                str(Path(cfg.S2S_ANNOUNCE_FILE).resolve()),
                "--timing-file",
                str((cfg.DATA_DIR / "s2s_turn_timings.jsonl").resolve()),
                "--turn-timing-url",
                f"http://127.0.0.1:{bridge_port}/api/turn-timing",
                "--turn-timing-api-key",
                bridge_api_key,
                "--input-state-url",
                f"http://127.0.0.1:{bridge_port}/api/input-state",
                "--input-state-api-key",
                bridge_api_key,
                "--ready-file",
                str(client_ready_file),
            ],
            cwd=s2s_home,
            ready=file_ready(client_ready_file),
            ready_timeout=60.0,
            ready_file=client_ready_file,
        ),
    ]


def child_env(
    *,
    bridge_port: int | None = None,
    ws_port: int | None = None,
    bridge_api_key: str | None = None,
) -> dict[str, str]:
    """Environment for children, including launcher-selected dynamic ports."""
    env = dict(os.environ)
    # Forcing it here means the stack works without hand-editing .env, and a
    # stale RAP_MODE=full cannot silently start a second local audio pipeline.
    env["RAP_MODE"] = "brain"
    if bridge_port is not None:
        env["S2S_BRIDGE_PORT"] = str(bridge_port)
    if ws_port is not None:
        env["S2S_WS_PORT"] = str(ws_port)
    if bridge_api_key is not None:
        env["S2S_BRIDGE_API_KEY"] = bridge_api_key
    return env


def stage_log_path(stage_name: str) -> Path:
    """Per-stage log file under the repo's ``logs`` directory.

    Children used to write only to their own console windows, which made every
    field report a copy-paste exercise. Files make the last run inspectable.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", stage_name.lower()).strip("-")
    return REPO_ROOT / "logs" / f"{slug}.log"


def _spawn(stage: Stage, env: dict[str, str]) -> subprocess.Popen:
    if stage.ready_file is not None:
        stage.ready_file.unlink(missing_ok=True)
        stage.ready_file.with_suffix(stage.ready_file.suffix + ".tmp").unlink(missing_ok=True)
    log_path = stage_log_path(stage.name)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Truncate per run: "the last run's log" is the useful artifact, and the
    # console window it replaces never accumulated history either.
    log_handle = log_path.open("wb")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        stage.args,
        cwd=str(stage.cwd),
        env=env,
        creationflags=flags,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    log_handle.close()
    return process


def _await_ready(stage: Stage, process: subprocess.Popen) -> bool:
    if stage.ready is None:
        return True
    deadline = time.monotonic() + stage.ready_timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            logger.error(f"{stage.name} exited with code {process.returncode} before becoming ready")
            return False
        if stage.ready():
            if process.poll() is None:
                return True
            logger.error(f"{stage.name} exited while publishing readiness")
            return False
        time.sleep(0.5)
    logger.error(f"{stage.name} did not become ready within {stage.ready_timeout:.0f}s")
    return False


def _force_process_tree(process: subprocess.Popen) -> None:
    """Stop a process and, on Windows, every descendant of launcher wrappers."""
    pid = getattr(process, "pid", None)
    if sys.platform == "win32" and pid is not None:
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=15,
            )
            if result.returncode == 0:
                return
        except (OSError, subprocess.SubprocessError):
            pass
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def _shutdown(started: list[tuple[Stage, subprocess.Popen]]) -> None:
    for stage, process in reversed(started):
        if process.poll() is not None:
            continue
        logger.info(f"Stopping {stage.name}")
        if stage.stop is not None:
            try:
                if stage.stop():
                    process.wait(timeout=10)
                    continue
            except (OSError, subprocess.SubprocessError):
                logger.warning(f"Soft shutdown failed for {stage.name}; forcing its process tree")
        _force_process_tree(process)


def run_stack() -> int:
    """Start every stage in order, then supervise until one exits."""
    s2s_home = resolve_s2s_home()
    if s2s_home is None:
        logger.error(
            "Cannot find the speech-to-speech checkout. Clone it next to this repo, "
            "or set S2S_HOME in .env to its path."
        )
        return 2
    missing = missing_frontend_pieces(s2s_home)
    if missing:
        logger.error(
            f"{s2s_home} is missing {', '.join(missing)}. "
            "Install the frontend's venv and launchers before starting the voice stack."
        )
        return 2
    if "kokoro" in (gaps := missing_kokoro_requirements(s2s_home)):
        logger.error(
            "The frontend selects Kokoro TTS but its venv has no kokoro package. "
            "Run: uv sync --extra kokoro --inexact"
        )
        return 2
    if "en_core_web_sm" in gaps:
        logger.error(
            "Kokoro needs spaCy's en_core_web_sm and it is not installed. Its own "
            "downloader fails silently in a uv venv, and the TTS then misreports "
            "this as a missing kokoro package. Install it explicitly:\n"
            "  uv pip install --python .venv\\Scripts\\python.exe "
            "https://github.com/explosion/spacy-models/releases/download/"
            "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
        )
        return 2

    bridge_port, ws_port = select_stack_ports()
    logger.info(f"Selected dynamic ports: brain/GUI {bridge_port}, Realtime {ws_port}")
    if busy := occupied_ports(bridge_port, ws_port):
        listed = ", ".join(f"{name} (port {port})" for name, port in busy)
        logger.error(
            f"Already listening: {listed}.\n"
            "The voice stack looks like it is already up -- close those windows and try again.\n"
            "Starting a second copy would load models for a minute, fail to bind, and leave the\n"
            "client talking to the old server."
        )
        return 2

    bridge_api_key = secrets.token_urlsafe(32)
    env = child_env(
        bridge_port=bridge_port,
        ws_port=ws_port,
        bridge_api_key=bridge_api_key,
    )
    started: list[tuple[Stage, subprocess.Popen]] = []
    try:
        for stage in build_stages(
            s2s_home,
            bridge_port=bridge_port,
            ws_port=ws_port,
            bridge_api_key=bridge_api_key,
        ):
            logger.info(f"Starting {stage.name}")
            process = _spawn(stage, env)
            started.append((stage, process))
            if not _await_ready(stage, process):
                return 1
            logger.info(f"{stage.name} is ready")

        logger.info("Voice stack is up. Close this window or press Ctrl+C to stop everything.")
        while True:
            for stage, process in started:
                if process.poll() is not None:
                    logger.warning(f"{stage.name} exited with code {process.returncode}; shutting down")
                    return process.returncode or 0
            time.sleep(1.0)
    except KeyboardInterrupt:
        logger.info("Interrupted; shutting down the voice stack")
        return 0
    finally:
        _shutdown(started)


def main() -> None:
    """Entry point for ``python -m remote_agent_protocol.voice_stack``."""
    raise SystemExit(run_stack())


if __name__ == "__main__":
    main()

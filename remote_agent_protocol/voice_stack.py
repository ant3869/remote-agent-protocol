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
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect as websocket_connect

from remote_agent_protocol import config as cfg
from remote_agent_protocol import process_guard
from remote_agent_protocol.doctor import model_registered, ollama_tags

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


def process_is_running(pid: int) -> bool:
    """Return whether ``pid`` still identifies a live process."""
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0 and str(pid) in result.stdout
        os.kill(pid, 0)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def file_ready(path: Path) -> Callable[[], bool]:
    """Build a readiness probe for an atomic, live-PID-tagged handshake file."""

    def probe() -> bool:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            pid = int(payload.get("pid", 0))
            return payload.get("ready") is True and process_is_running(pid)
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

    Called with no arguments this reports the *configured* ports, which is what
    the doctor wants to know: something already answering on `S2S_BRIDGE_PORT`
    is a standalone brain bridge, and it will collide with the GUI's own
    endpoint. The launcher itself no longer asks -- it picks ephemeral ports, so
    a port check there can only ever say "free".
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


def local_ollama_address() -> str | None:
    """``host:port`` to bind a local Ollama on, or None if it lives elsewhere.

    A remote ``OLLAMA_HOST`` is somebody else's server; starting one here would
    bind a different machine's address and never satisfy the probe.
    """
    parsed = urllib.parse.urlsplit(cfg.OLLAMA_HOST)
    hostname = parsed.hostname or ""
    if hostname not in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return None
    return f"{hostname}:{parsed.port or 11434}"


def ollama_executable() -> str | None:
    """Path to the ``ollama`` binary, including the spot its installer uses.

    The Windows installer drops it under LOCALAPPDATA and only adds it to PATH
    for shells started afterwards, so PATH alone misses a fresh install.
    """
    found = shutil.which("ollama")
    if found:
        return found
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidate = Path(local_app_data) / "Programs" / "Ollama" / "ollama.exe"
        if candidate.exists():
            return str(candidate)
    return None


def start_ollama(address: str, timeout: float = 90.0) -> bool:
    """Serve Ollama on ``address`` and wait until it answers.

    Left running when the stack stops: Ollama is a machine-wide service other
    parts of RAP talk to, and killing it would evict the models it just spent
    the launch loading.
    """
    executable = ollama_executable()
    if executable is None:
        return False
    log_path = REPO_ROOT / "logs" / "ollama.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, OLLAMA_HOST=address)
    try:
        with log_path.open("wb") as log_handle:
            subprocess.Popen(
                [executable, "serve"],
                env=env,
                # No console of its own, and detached so closing the launcher
                # window does not take the server down with it.
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
            )
    except OSError:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ollama_tags(cfg.OLLAMA_HOST, 2.0) is not None:
            return True
        time.sleep(0.5)
    return False


def ensure_llm_backend() -> str | None:
    """Start Ollama if needed; explain why the brain has no model if it can't.

    The brain answers the frontend over an OpenAI-compatible endpoint that is
    really Ollama behind a proxy, so a stopped Ollama surfaces only a minute
    into the launch -- as an HTTP 500 raised inside the frontend's LLM warmup,
    which then exits and takes the whole stack down.
    """
    tags = ollama_tags(cfg.OLLAMA_HOST, 2.0)
    if tags is None:
        address = local_ollama_address()
        if address is None:
            return (
                f"Ollama is not answering at {cfg.OLLAMA_HOST}, and that host is not this "
                "machine, so the stack cannot start it. Bring it up there, or point "
                "OLLAMA_HOST at a local server."
            )
        logger.info(f"Ollama is not running; starting it on {address}")
        if not start_ollama(address):
            return (
                f"Could not start Ollama on {address}, and the brain has nothing to think "
                f"with. Check {REPO_ROOT / 'logs' / 'ollama.log'}, or install Ollama if the "
                "'ollama' command is missing."
            )
        logger.info("Ollama is up")
        tags = ollama_tags(cfg.OLLAMA_HOST, 2.0) or []
    if not model_registered(cfg.LLM_MODEL, tags):
        return (
            f"Ollama has no model named '{cfg.LLM_MODEL}'. Register it -- see "
            "remote_agent_protocol\\models\\README.md -- or point LLM_MODEL in .env at a "
            "model 'ollama list' shows."
        )
    return None


def audio_devices(s2s_home: Path) -> dict | None:
    """PortAudio's device table as the frontend's venv sees it, or None.

    Asked of that venv rather than this one so the indices are the ones the
    client will actually open.
    """
    python = s2s_home / ".venv/Scripts/python.exe"
    if not python.exists():
        return None
    probe = (
        "import json, sounddevice as sd;"
        "print(json.dumps({'default': list(sd.default.device), 'devices': ["
        "{'index': i, 'name': d['name'], 'inputs': d['max_input_channels'],"
        " 'outputs': d['max_output_channels']}"
        " for i, d in enumerate(sd.query_devices())]}))"
    )
    try:
        result = subprocess.run(
            [str(python), "-c", probe], capture_output=True, text=True, timeout=60
        )
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        return None


def resolve_audio_device(table: dict, wanted: str, *, capture: bool) -> int | None:
    """Index for a configured device name or index; None if there is no match.

    An empty setting means the system default, which is the right answer far
    more often than a remembered index is.
    """
    wanted = wanted.strip()
    channels = "inputs" if capture else "outputs"
    if wanted.isdigit():
        index = int(wanted)
        return next(
            (
                index
                for device in table.get("devices", [])
                if device.get("index") == index and device.get(channels, 0) > 0
            ),
            None,
        )
    if wanted:
        needle = wanted.casefold()
        for device in table.get("devices", []):
            if device.get(channels, 0) > 0 and needle in device.get("name", "").casefold():
                return int(device["index"])
        return None
    default = (table.get("default") or [None, None])[0 if capture else 1]
    return int(default) if isinstance(default, int) and default >= 0 else None


def device_name(table: dict, index: int) -> str:
    """Human-readable name for a device index, for the launcher's log."""
    for device in table.get("devices", []):
        if device.get("index") == index:
            return str(device.get("name", "")).strip()
    return f"index {index}"


def select_audio_devices(s2s_home: Path) -> tuple[int | None, int | None]:
    """Microphone and speaker indices to hand the client, either may be None.

    None leaves the frontend's launcher to decide, which is the right fallback
    when the device table cannot be read but a poor default for the microphone:
    a stale index there is silent rather than noisy, so it is worth naming the
    device this stack expects and logging what it picked.
    """
    table = audio_devices(s2s_home)
    if table is None:
        logger.warning("Could not read the audio devices; the frontend's own choice stands")
        return None, None

    microphone = resolve_audio_device(table, cfg.S2S_INPUT_DEVICE, capture=True)
    if microphone is None:
        logger.warning(
            f"No microphone matches S2S_INPUT_DEVICE={cfg.S2S_INPUT_DEVICE!r}; "
            "the frontend's own choice stands"
        )
    else:
        logger.info(f"Microphone: {device_name(table, microphone)} (index {microphone})")

    # Only ever set on request: an unwanted microphone is silence, but an
    # unwanted speaker moves audio that is currently playing where it should.
    speakers = None
    if cfg.S2S_OUTPUT_DEVICE.strip():
        speakers = resolve_audio_device(table, cfg.S2S_OUTPUT_DEVICE, capture=False)
        if speakers is None:
            logger.warning(
                f"No speakers match S2S_OUTPUT_DEVICE={cfg.S2S_OUTPUT_DEVICE!r}; "
                "the frontend's own choice stands"
            )
        else:
            logger.info(f"Speakers: {device_name(table, speakers)} (index {speakers})")
    return microphone, speakers


def build_stages(
    s2s_home: Path,
    *,
    bridge_port: int | None = None,
    ws_port: int | None = None,
    bridge_api_key: str | None = None,
    input_device: int | None = None,
    output_device: int | None = None,
) -> list[Stage]:
    """Describe the three processes in dependency order."""
    bridge_port = cfg.S2S_BRIDGE_PORT if bridge_port is None else bridge_port
    ws_port = cfg.S2S_WS_PORT if ws_port is None else ws_port
    bridge_api_key = bridge_api_key or cfg.S2S_BRIDGE_API_KEY
    client_ready_file = (cfg.DATA_DIR / "s2s_client.ready").resolve()
    device_args: list[str] = []
    if input_device is not None:
        device_args += ["--input-device", str(input_device)]
    if output_device is not None:
        device_args += ["--output-device", str(output_device)]
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
                "--external-mute-status-file",
                str(Path(cfg.S2S_MIC_MUTE_STATUS_FILE).resolve()),
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
                *device_args,
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


def log_excerpt(stage_name: str, lines: int = 12) -> str:
    """The tail of a stage's log, formatted for the launcher console.

    A failing child says nothing here on its own -- its output goes to a file in
    a windowless process -- so a launch failure would otherwise report only an
    exit code and leave the real traceback for the reader to go find.
    """
    path = stage_log_path(stage_name)
    try:
        tail = path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    except OSError:
        return f"No log to show; expected it at {path}"
    body = "\n".join(f"  {line}" for line in tail if line.strip())
    return f"Last lines of {path}:\n{body}" if body else f"Its log at {path} is empty"


def _await_ready(stage: Stage, process: subprocess.Popen) -> bool:
    if stage.ready is None:
        return True
    deadline = time.monotonic() + stage.ready_timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            logger.error(
                f"{stage.name} exited with code {process.returncode} before becoming ready\n"
                f"{log_excerpt(stage.name)}"
            )
            return False
        if stage.ready():
            if process.poll() is None:
                return True
            logger.error(f"{stage.name} exited while publishing readiness\n{log_excerpt(stage.name)}")
            return False
        time.sleep(0.5)
    logger.error(
        f"{stage.name} did not become ready within {stage.ready_timeout:.0f}s\n"
        f"{log_excerpt(stage.name)}"
    )
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
    if (backend_problem := ensure_llm_backend()) is not None:
        logger.error(backend_problem)
        return 2

    # Ports cannot answer "is it already running?" now that each launch picks
    # ephemeral ones -- a fresh port is free by construction. The app's own
    # single-instance lock can, and it also catches a GUI started on its own.
    # A held lock is usually a crashed run's process still sitting there, which
    # the app has always cleaned up on its own next launch; do that here rather
    # than refusing a launch nobody else is using.
    if process_guard.instance_is_running():
        logger.warning("The single-instance lock is held; closing whatever still holds it")
        if not process_guard.reclaim_instance_slot():
            logger.error(
                "Remote Agent Protocol is already running and could not be closed from here.\n"
                "Close its windows and try again. Starting a second copy would spend a minute "
                "loading models before the app refused the duplicate, leaving the audio client "
                "talking to the first one."
            )
            return 2
        logger.info("Reclaimed the slot from a leftover run; continuing")

    bridge_port, ws_port = select_stack_ports()
    logger.info(f"Selected dynamic ports: brain/GUI {bridge_port}, Realtime {ws_port}")

    input_device, output_device = select_audio_devices(s2s_home)
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
            input_device=input_device,
            output_device=output_device,
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

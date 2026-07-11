"""Read-only startup doctor.

One deterministic pass over what a voice session needs before it can run
cleanly: Python version, Ollama reachability and configured models, the
selected TTS backend, STT/TTS/wake-word Python packages, configured audio
device indices, and agent backend executables.

Every check here is diagnosis only. Nothing here installs a package,
downloads a model, launches a service, or edits configuration. Each check
catches its own expected I/O/config errors -- one broken check must not hide
the rest. Imports are deliberately narrow (config, dashboard, stdlib) so this
module can run even when a model runtime (kokoro_onnx, faster_whisper,
openwakeword, ...) is missing or broken; that's exactly the case it needs to
report on.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError

from remote_agent_protocol import config as cfg
from remote_agent_protocol import dashboard

_MIN_PYTHON = (3, 11)

# STT_ENGINE -> the Python package pipecat's STT service imports for it.
_STT_MODULES = {"whisper": "faster_whisper", "moonshine": "moonshine_voice"}

# TTS_BACKEND -> the Python package required. kokoro/voicebox/coqui all route
# through PersonaTTSService, which imports kokoro_onnx unconditionally at
# module load regardless of which of the three is actually selected.
_TTS_MODULES = {"kokoro": "kokoro_onnx", "voicebox": "kokoro_onnx", "coqui": "kokoro_onnx"}


@dataclass(frozen=True)
class CheckResult:
    """One diagnostic outcome: ok, warn (non-fatal), or fail."""

    name: str
    status: str
    message: str


def format_results(results: list[CheckResult]) -> str:
    """One line per result plus a final ok/warn/fail summary line."""
    lines = [f"[{r.status.upper():4}] {r.name}: {r.message}" for r in results]
    oks = sum(1 for r in results if r.status == "ok")
    warns = sum(1 for r in results if r.status == "warn")
    fails = sum(1 for r in results if r.status == "fail")
    lines.append(f"\n{oks} ok, {warns} warn, {fails} fail")
    return "\n".join(lines)


def exit_code(results: list[CheckResult]) -> int:
    """0 when nothing failed, 1 when any check failed. Warnings don't fail."""
    return 1 if any(r.status == "fail" for r in results) else 0


def check_python() -> CheckResult:
    """The interpreter running this meets the project's minimum version."""
    version = platform.python_version()
    if sys.version_info[:2] >= _MIN_PYTHON:
        return CheckResult("python", "ok", f"Python {version}")
    required = ".".join(str(part) for part in _MIN_PYTHON)
    return CheckResult("python", "fail", f"Python {version} < {required} required")


def _ollama_tags(host: str, timeout: float) -> list[str] | None:
    """Raw model names from /api/tags, or None if unreachable.

    Deliberately bypasses ollama_models.available(), which substitutes a
    static fallback list on failure -- the doctor must never treat that
    fallback as evidence a model is actually registered.
    """
    url = host.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.load(resp)
    except (URLError, OSError, ValueError):
        return None
    return [m.get("name", "") for m in data.get("models", []) if m.get("name")]


def _model_registered(name: str, tags: list[str]) -> bool:
    """True if `name` matches a tag exactly or with an implicit ':latest'."""
    bare_tags = {t.split(":", 1)[0] for t in tags}
    bare_name = name.split(":", 1)[0]
    return name in tags or bare_name in bare_tags


def check_ollama(timeout: float = 2.0) -> list[CheckResult]:
    """Ollama reachability plus the configured chat and intent models."""
    tags = _ollama_tags(cfg.OLLAMA_HOST, timeout)
    if tags is None:
        results = [
            CheckResult("ollama-server", "fail", f"unreachable at {cfg.OLLAMA_HOST}"),
            CheckResult("ollama-chat-model", "fail", "cannot verify: Ollama unreachable"),
        ]
        if cfg.INTENT_ROUTER_ENABLED:
            results.append(
                CheckResult("ollama-intent-model", "fail", "cannot verify: Ollama unreachable")
            )
        return results

    results = [CheckResult("ollama-server", "ok", f"reachable at {cfg.OLLAMA_HOST}")]
    if _model_registered(cfg.LLM_MODEL, tags):
        results.append(CheckResult("ollama-chat-model", "ok", f"'{cfg.LLM_MODEL}' registered"))
    else:
        results.append(
            CheckResult(
                "ollama-chat-model",
                "fail",
                f"'{cfg.LLM_MODEL}' not registered; run 'ollama pull {cfg.LLM_MODEL}'",
            )
        )
    if cfg.INTENT_ROUTER_ENABLED:
        if _model_registered(cfg.INTENT_MODEL, tags):
            results.append(
                CheckResult("ollama-intent-model", "ok", f"'{cfg.INTENT_MODEL}' registered")
            )
        else:
            results.append(
                CheckResult(
                    "ollama-intent-model",
                    "fail",
                    f"'{cfg.INTENT_MODEL}' not registered; run 'ollama pull {cfg.INTENT_MODEL}'",
                )
            )
    return results


def _env_file_value(name: str, env_path: str = ".env") -> str | None:
    """Tiny local .env reader (stdlib only, no third-party dependency).

    Mirrors tts_factory.load_env_value's behavior without importing
    tts_factory, which transitively imports kokoro_onnx at module load --
    exactly the model-runtime import this module avoids.
    """
    value = os.environ.get(name)
    if value:
        return value
    path = Path(env_path)
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw = stripped.split("=", 1)
        if key.strip() == name:
            return raw.strip().strip('"').strip("'")
    return None


def check_tts_backend(timeout: float = 0.75) -> CheckResult:
    """Whether the configured TTS backend is reachable / has what it needs."""
    has_key = bool(_env_file_value("CARTESIA_API_KEY"))
    health = dashboard.tts_health(
        cfg.TTS_BACKEND,
        voicebox_url=cfg.VOICEBOX_BASE_URL,
        has_cartesia_key=has_key,
        timeout=timeout,
    )
    return CheckResult("tts-backend", "ok" if health.ok else "fail", health.label)


def check_stt_module() -> CheckResult:
    """The Python package the configured STT engine needs is installed."""
    engine = cfg.STT_ENGINE.lower().strip()
    module = _STT_MODULES.get(engine)
    if module is None:
        return CheckResult("stt-module", "warn", f"unknown STT_ENGINE '{engine}'")
    if importlib.util.find_spec(module) is not None:
        return CheckResult("stt-module", "ok", f"{engine} -> '{module}' installed")
    return CheckResult("stt-module", "fail", f"{engine} needs '{module}'; not installed")


def check_tts_module() -> CheckResult:
    """The Python package the configured TTS backend needs is installed."""
    backend = cfg.TTS_BACKEND.lower().strip()
    module = _TTS_MODULES.get(backend)
    if module is None:
        return CheckResult("tts-module", "ok", f"{backend} needs no local Python package")
    if importlib.util.find_spec(module) is not None:
        return CheckResult("tts-module", "ok", f"{backend} -> '{module}' installed")
    return CheckResult("tts-module", "fail", f"{backend} needs '{module}'; not installed")


def check_wake_word_module() -> CheckResult | None:
    """The wake-word engine's package, only when wake word is enabled."""
    if not cfg.WAKE_WORD_ENABLED:
        return None
    engine = cfg.WAKE_WORD_ENGINE.lower().strip()
    if engine != "openwakeword":
        return CheckResult("wake-word-module", "warn", f"unknown WAKE_WORD_ENGINE '{engine}'")
    if importlib.util.find_spec("openwakeword") is not None:
        return CheckResult("wake-word-module", "ok", "openwakeword installed")
    return CheckResult(
        "wake-word-module", "fail", "openwakeword not installed; run 'pip install openwakeword'"
    )


def _audio_device_names() -> dict[int, str]:
    """Map device index to name for every enumerable audio device; {} if PyAudio can't load."""
    try:
        import pyaudio
    except Exception:
        return {}
    pa = pyaudio.PyAudio()
    try:
        return {
            i: pa.get_device_info_by_index(i).get("name", "?") for i in range(pa.get_device_count())
        }
    finally:
        pa.terminate()


def check_audio_devices() -> list[CheckResult]:
    """Explicitly configured mic/speaker indices point at a real device."""
    mic, speaker = cfg.MIC_DEVICE_INDEX, cfg.SPEAKER_DEVICE_INDEX
    if mic is None and speaker is None:
        return []
    devices = _audio_device_names()
    results = []
    for label, index in (("mic-device", mic), ("speaker-device", speaker)):
        if index is None:
            continue
        if index in devices:
            results.append(CheckResult(label, "ok", f"device {index}: {devices[index]}"))
        else:
            results.append(CheckResult(label, "fail", f"device index {index} does not exist"))
    return results


def _agent_executable_status(cmd: list[str]) -> tuple[str, str]:
    if not cmd:
        return "fail", "empty command"
    token = cmd[0]
    if token == "{python}":
        return "ok", "uses the current Python interpreter"
    if os.path.isabs(token):
        if os.path.exists(token):
            return "ok", f"found at {token}"
        return "fail", f"not found: {token}"
    found = shutil.which(token)
    if found:
        return "ok", f"found at {found}"
    return "fail", f"'{token}' not found on PATH"


def check_agent_backends() -> list[CheckResult]:
    """Each configured agent backend's executable is locatable, never run."""
    results = []
    for name, cmd in cfg.AGENT_BACKENDS.items():
        status, message = _agent_executable_status(cmd)
        results.append(CheckResult(f"agent-backend:{name}", status, message))
    return results


# Each entry returns a CheckResult, a list[CheckResult], or None (skipped).
_CHECKS = (
    check_python,
    check_ollama,
    check_tts_backend,
    check_stt_module,
    check_tts_module,
    check_wake_word_module,
    check_audio_devices,
    check_agent_backends,
)


def run_checks() -> list[CheckResult]:
    """Run every check, isolating failures so one broken check can't hide the rest."""
    results: list[CheckResult] = []
    for check in _CHECKS:
        try:
            outcome = check()
        except Exception as exc:  # a check must never take the whole doctor down
            results.append(CheckResult(check.__name__, "fail", f"check crashed: {exc}"))
            continue
        if outcome is None:
            continue
        if isinstance(outcome, list):
            results.extend(outcome)
        else:
            results.append(outcome)
    return results


def main(argv: list[str] | None = None) -> int:
    """Run all checks, print the report, and return the process exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m remote_agent_protocol.doctor",
        description=(
            "Read-only startup checks for Remote Agent Protocol: Python version, "
            "Ollama and configured models, TTS backend, STT/TTS/wake-word packages, "
            "configured audio devices, and agent backend executables. Never "
            "installs, downloads, launches, or edits configuration."
        ),
    )
    parser.parse_args(argv)
    results = run_checks()
    print(format_results(results))
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main())

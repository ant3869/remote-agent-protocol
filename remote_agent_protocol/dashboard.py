"""Small dashboard helpers for Jess's GUI.

Pure-ish functions live here so the GUI doesn't become a haunted ball of Tk
callbacks. This module owns:
  * classifying Pipecat metric processors into STT / LLM / TTS buckets
  * formatting latency values for the status dashboard
  * querying/formatting Ollama health
  * a couple of process-control helpers for Ollama / loaded models

Keep this file boring. Boring telemetry code is good telemetry code.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError


def ollama_install_dir() -> Path:
    """Ollama's default per-user install directory on Windows."""
    local = os.environ.get("LOCALAPPDATA", "")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "Programs" / "Ollama"


def ollama_app_path() -> str | None:
    """Path to the Ollama tray app, or None if it isn't installed there."""
    candidate = ollama_install_dir() / "ollama app.exe"
    return str(candidate) if candidate.exists() else None


def ollama_cli_path() -> str | None:
    """Path to the ollama CLI: PATH first, then the default install dir."""
    found = shutil.which("ollama")
    if found:
        return found
    candidate = ollama_install_dir() / "ollama.exe"
    return str(candidate) if candidate.exists() else None


def metric_bucket(processor: str) -> str | None:
    """Map a Pipecat processor name to the dashboard bucket it belongs to."""
    p = processor.lower()
    if "stt" in p or "whisper" in p or "moonshine" in p:
        return "stt"
    if "llm" in p or "openai" in p:
        return "llm"
    if "tts" in p or "kokoro" in p:
        return "tts"
    return None


def metric_event(metric, kind: str) -> dict | None:
    """Convert a Pipecat metric data object into a plain GUI event."""
    bucket = metric_bucket(getattr(metric, "processor", ""))
    if bucket is None:
        return None
    value = getattr(metric, "value", None)
    if not isinstance(value, (int, float)):
        return None
    return {"type": "metric", "bucket": bucket, "kind": kind, "value": float(value)}


@dataclass
class LatencyState:
    """Latest timings shown by the GUI dashboard."""

    values: dict[str, float | None] = field(
        default_factory=lambda: {"stt": None, "llm": None, "tts": None, "total": None}
    )
    _user_turn_complete_at: float | None = None

    def update(self, bucket: str, kind: str, value: float) -> None:
        """Update one timing bucket from a metric event."""
        if kind in ("processing", "ttfb") and bucket in self.values:
            self.values[bucket] = value

    def mark_user_turn_complete(self, now: float | None = None) -> None:
        """Mark when the user finished talking, for total perceived latency."""
        self._user_turn_complete_at = time.monotonic() if now is None else now

    def mark_bot_started(self, now: float | None = None) -> None:
        """Mark when audio output starts; computes total if we have a start."""
        if self._user_turn_complete_at is None:
            return
        t = time.monotonic() if now is None else now
        self.values["total"] = max(0.0, t - self._user_turn_complete_at)
        self._user_turn_complete_at = None


def _fmt(value: float | None) -> str:
    return "--" if value is None else f"{value:.2f}s"


def format_latency_line(state: LatencyState) -> str:
    """One compact human-readable latency dashboard line."""
    v = state.values
    return (
        f"STT {_fmt(v['stt'])} | LLM {_fmt(v['llm'])} | "
        f"TTS {_fmt(v['tts'])} | Total {_fmt(v['total'])}"
    )


@dataclass(frozen=True)
class OllamaHealth:
    """Snapshot of the local Ollama server's reachability and model counts."""

    ok: bool
    model_count: int = 0
    loaded_count: int = 0
    error: str = ""

    @property
    def label(self) -> str:
        """Compact status-chip text for the GUI."""
        if not self.ok:
            return "Ollama DOWN"
        return f"Ollama OK ({self.loaded_count} loaded / {self.model_count} installed)"

    @classmethod
    def from_payloads(cls, tags: dict, ps: dict) -> OllamaHealth:
        """Build a healthy snapshot from /api/tags and /api/ps responses."""
        return cls(
            ok=True,
            model_count=len(tags.get("models", [])),
            loaded_count=len(ps.get("models", [])),
        )

    @classmethod
    def down(cls, error: str) -> OllamaHealth:
        """Build an unreachable-server snapshot."""
        return cls(ok=False, error=error)


def _get_json(url: str, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


def ollama_health(host: str, timeout: float = 0.75) -> OllamaHealth:
    """Query Ollama /api/tags + /api/ps and return a tiny health snapshot."""
    base = host.rstrip("/")
    try:
        tags = _get_json(base + "/api/tags", timeout)
        ps = _get_json(base + "/api/ps", timeout)
        return OllamaHealth.from_payloads(tags, ps)
    except (OSError, URLError, ValueError) as e:
        return OllamaHealth.down(str(e))


@dataclass(frozen=True)
class TTSHealth:
    """Whether the configured TTS backend can currently speak."""

    ok: bool
    label: str


def _endpoint_alive(url: str, timeout: float) -> bool:
    """True if the URL's host answers at all -- even a 404 means it's up."""
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except HTTPError:
        return True  # the server responded; a non-200 still means it's alive
    except (OSError, URLError, ValueError):
        return False


def tts_health(
    backend: str,
    *,
    voicebox_url: str = "",
    has_cartesia_key: bool = False,
    timeout: float = 0.75,
) -> TTSHealth:
    """A tiny health snapshot for the active TTS backend.

    Kokoro runs in-process (always fine); Voicebox is a local REST server we can
    ping; Cartesia is cloud, so we can only report whether an API key is present.
    """
    b = (backend or "").lower().strip()
    if b == "kokoro":
        return TTSHealth(True, "Kokoro OK (local)")
    if b == "voicebox":
        if not voicebox_url:
            return TTSHealth(False, "Voicebox: no URL")
        alive = _endpoint_alive(voicebox_url, timeout)
        return TTSHealth(alive, "Voicebox OK" if alive else "Voicebox DOWN")
    if b == "cartesia":
        return TTSHealth(
            has_cartesia_key,
            "Cartesia (key set)" if has_cartesia_key else "Cartesia: NO KEY",
        )
    return TTSHealth(True, f"{backend} ?")


def start_ollama_app() -> None:
    """Start the persistent Ollama tray app.

    Raises:
        RuntimeError: If the tray app isn't at Ollama's default install path.
    """
    app = ollama_app_path()
    if app is None:
        raise RuntimeError(f"Ollama app not found under {ollama_install_dir()}")
    subprocess.Popen([app], close_fds=True)


def stop_loaded_models(host: str, timeout: float = 1.0) -> int:
    """Unload every currently loaded Ollama model. Returns count requested.

    Raises:
        RuntimeError: If the ollama CLI can't be located.
    """
    cli = ollama_cli_path()
    if cli is None:
        raise RuntimeError("ollama CLI not found (not on PATH or default install dir)")
    ps = _get_json(host.rstrip("/") + "/api/ps", timeout)
    names = [m.get("name") for m in ps.get("models", []) if m.get("name")]
    for name in names:
        subprocess.run([cli, "stop", name], capture_output=True, text=True, timeout=15)
    return len(names)

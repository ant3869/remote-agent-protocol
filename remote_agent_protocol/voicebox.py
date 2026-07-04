"""Voicebox app integration helpers.

Read profiles from Voicebox's SQLite DB and speak through its REST backend when
that backend is running. DB reads are read-only; no sneaky writes, because rude.
"""

from __future__ import annotations

import io
import sqlite3
import struct
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

from remote_agent_protocol import config as cfg

VOICEBOX_PREFIX = "voicebox:"
DEFAULT_DATA_DIR = Path.home() / "AppData" / "Roaming" / "sh.voicebox.app"
DEFAULT_DB = DEFAULT_DATA_DIR / "voicebox.db"
_SERVER_PROC: subprocess.Popen | None = None


@dataclass(frozen=True)
class VoiceboxProfile:
    """One voice profile row from Voicebox's SQLite database."""

    id: str
    name: str
    description: str = ""
    language: str = "en"
    voice_type: str = "cloned"
    preset_engine: str | None = None
    preset_voice_id: str | None = None
    default_engine: str | None = None
    personality: str | None = None

    @property
    def voice_ref(self) -> str:
        """Stable ``voicebox:<id>`` reference used in persona voice fields."""
        return f"{VOICEBOX_PREFIX}{self.id}"

    @property
    def label(self) -> str:
        """Human-readable picker label."""
        kind = "cloned" if self.voice_type == "cloned" else self.voice_type
        return f"Voicebox {kind} - {self.name}"


def is_voicebox_ref(value: str) -> bool:
    """True if ``value`` is a ``voicebox:<id>`` voice reference."""
    return value.startswith(VOICEBOX_PREFIX)


def profile_id_from_ref(value: str) -> str:
    """Profile id from a ``voicebox:`` reference, or empty string."""
    return value[len(VOICEBOX_PREFIX) :] if is_voicebox_ref(value) else ""


def backend_for_voice(voice: str, fallback: str) -> str:
    """Infer provider backend from a voice ref, otherwise keep fallback."""
    return "voicebox" if is_voicebox_ref(voice) else fallback


def wav_bytes_to_pcm(data: bytes) -> tuple[bytes, int]:
    """Extract mono PCM bytes and sample rate from a WAV response."""
    with wave.open(io.BytesIO(data), "rb") as wav:
        channels = wav.getnchannels()
        if channels != 1:
            raise ValueError(f"Voicebox returned {channels} channels; expected mono")
        if wav.getsampwidth() != 2:
            raise ValueError(
                f"Voicebox returned {wav.getsampwidth() * 8}-bit audio; expected 16-bit PCM"
            )
        return wav.readframes(wav.getnframes()), wav.getframerate()


class WavStreamParser:
    """Incrementally strip a WAV header and emit aligned PCM chunks."""

    def __init__(self):
        """Initialize an empty parser awaiting the WAV header."""
        self._buffer = bytearray()
        self._header_done = False
        self._sample_rate = 0
        self._block_align = 2

    def feed(self, data: bytes) -> list[tuple[bytes, int]]:
        """Consume streamed bytes; return aligned (pcm, sample_rate) chunks."""
        self._buffer.extend(data)
        if not self._header_done:
            parsed = self._try_parse_header()
            if not parsed:
                return []
        return self._drain(aligned=True)

    def finish(self) -> list[tuple[bytes, int]]:
        """Flush any trailing PCM once the stream has ended."""
        if not self._header_done:
            raise ValueError("Incomplete WAV stream")
        return self._drain(aligned=False)

    def _try_parse_header(self) -> bool:
        data = bytes(self._buffer)
        if len(data) < 44:
            return False
        if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            raise ValueError("Voicebox did not return a WAV stream")
        offset = 12
        fmt_seen = False
        while offset + 8 <= len(data):
            chunk_id = data[offset : offset + 4]
            chunk_size = struct.unpack_from("<I", data, offset + 4)[0]
            chunk_start = offset + 8
            chunk_end = chunk_start + chunk_size
            if chunk_end > len(data):
                return False
            if chunk_id == b"fmt ":
                audio_format, channels, rate, _byte_rate, align, bits = struct.unpack_from(
                    "<HHIIHH", data, chunk_start
                )
                if audio_format != 1 or channels != 1 or bits != 16:
                    raise ValueError("Voicebox WAV must be mono 16-bit PCM")
                self._sample_rate = rate
                self._block_align = align
                fmt_seen = True
            elif chunk_id == b"data":
                if not fmt_seen:
                    raise ValueError("Voicebox WAV data chunk appeared before fmt chunk")
                del self._buffer[:chunk_start]
                self._header_done = True
                return True
            offset = chunk_end + (chunk_size % 2)
        return False

    def _drain(self, *, aligned: bool) -> list[tuple[bytes, int]]:
        size = len(self._buffer)
        if aligned:
            size -= size % self._block_align
        if size <= 0:
            return []
        chunk = bytes(self._buffer[:size])
        del self._buffer[:size]
        return [(chunk, self._sample_rate)]


def load_profiles(db_path: str | Path = DEFAULT_DB) -> list[VoiceboxProfile]:
    """Read all Voicebox profiles (read-only); missing DB means none."""
    path = Path(db_path)
    if not path.exists():
        return []
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "select id, name, coalesce(description,'' ) as description, "
            "coalesce(language,'en') as language, coalesce(voice_type,'cloned') as voice_type, "
            "preset_engine, preset_voice_id, default_engine, personality "
            "from profiles order by voice_type, name"
        ).fetchall()
    finally:
        con.close()
    return [VoiceboxProfile(**dict(row)) for row in rows]


def labelled_profiles(db_path: str | Path = DEFAULT_DB) -> list[tuple[str, str]]:
    """(label, voice_ref) pairs for GUI voice pickers."""
    return [(profile.label, profile.voice_ref) for profile in load_profiles(db_path)]


def base_url() -> str:
    """Configured Voicebox REST endpoint without a trailing slash."""
    return cfg.VOICEBOX_BASE_URL.rstrip("/")


def server_command() -> list[str]:
    """Voicebox server command for the configured local endpoint."""
    url = base_url()
    host_port = url.removeprefix("http://").removeprefix("https://")
    host, _, port = host_port.partition(":")
    return [
        cfg.VOICEBOX_SERVER_EXE,
        "--host",
        host or "127.0.0.1",
        "--port",
        port or "18080",
        "--data-dir",
        cfg.VOICEBOX_DATA_DIR,
    ]


def start_server_once() -> None:
    """Start Voicebox server if this process has not already started it."""
    global _SERVER_PROC
    if _SERVER_PROC is not None and _SERVER_PROC.poll() is None:
        return
    exe = Path(cfg.VOICEBOX_SERVER_EXE)
    if not exe.exists():
        raise FileNotFoundError(f"Voicebox server not found: {exe}")
    _SERVER_PROC = subprocess.Popen(
        server_command(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )

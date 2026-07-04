"""Diagnostics bundle -- a shareable snapshot of what Jess is running.

When audio devices, models, or agent backends misbehave, "export" collects the
relevant state into one text file you can read or attach to a bug report. The
build/format functions are pure and deterministic (pass ``now`` for stable
output); only writing touches the disk, best-effort.
"""

from __future__ import annotations

import platform
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def audio_devices() -> list[dict[str, Any]]:
    """Enumerate input/output devices via PyAudio; [] if it can't load."""
    try:
        import pyaudio
    except Exception:
        return []
    pa = pyaudio.PyAudio()
    try:
        devices: list[dict[str, Any]] = []
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            devices.append(
                {
                    "index": i,
                    "name": info.get("name", "?"),
                    "in": int(info.get("maxInputChannels", 0)),
                    "out": int(info.get("maxOutputChannels", 0)),
                }
            )
        return devices
    finally:
        pa.terminate()


def build_report(
    *,
    session_snapshot: dict[str, Any],
    tts_backend: str,
    ollama: dict[str, Any],
    tts: dict[str, Any],
    latency_line: str,
    jobs: list[dict[str, Any]],
    devices: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Assemble the diagnostics report dict from already-gathered pieces."""
    return {
        "generated_at": (now or datetime.now()).isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "tts_backend": tts_backend,
        "ollama": ollama,
        "tts": tts,
        "latency": latency_line,
        "session": session_snapshot,
        "audio_devices": devices or [],
        "recent_jobs": jobs,
    }


def _health_line(name: str, health: dict[str, Any]) -> str:
    mark = "OK" if health.get("ok") else "PROBLEM"
    return f"{name:<8} [{mark}] {health.get('label', '?')}"


def format_report(report: dict[str, Any]) -> str:
    """Render the report as a human-readable, copy-pasteable text block."""
    session = report.get("session", {})
    lines: list[str] = [
        "=== Remote Agent Protocol diagnostics ===",
        "NOTE: includes conversation excerpts and agent task text -- review before sharing.",
        f"generated : {report.get('generated_at', '?')}",
        f"platform  : {report.get('platform', '?')}  (python {report.get('python', '?')})",
        "",
        "-- health --",
        _health_line("ollama", report.get("ollama", {})),
        _health_line("tts", report.get("tts", {})),
        f"latency  {report.get('latency', '--')}",
        "",
        "-- session --",
        f"persona        : {session.get('persona', '?')}",
        f"model          : {session.get('model', '?')}",
        f"voice          : {session.get('voice', '?')} ({session.get('voice_backend', '?')})",
        f"tts backend    : {report.get('tts_backend', '?')}",
        f"default agent  : {session.get('default_agent_backend', '?')}",
        f"agent backends : {', '.join(session.get('agent_backends', []))}",
        "",
        "-- audio devices --",
    ]
    for dev in report.get("audio_devices", []):
        io = []
        if dev.get("in"):
            io.append(f"in x{dev['in']}")
        if dev.get("out"):
            io.append(f"out x{dev['out']}")
        lines.append(f"  [{dev.get('index'):>2}] {dev.get('name', '?')}  ({', '.join(io)})")
    if not report.get("audio_devices"):
        lines.append("  (PyAudio unavailable)")

    lines += ["", "-- recent agent jobs --"]
    jobs = report.get("recent_jobs", [])
    if not jobs:
        lines.append("  (none)")
    for job in jobs:
        secs = job.get("secs")
        took = f" {secs}s" if secs is not None else ""
        lines.append(
            f"  [{job.get('status', '?')}]{took} {job.get('agent', '?')}: {job.get('task', '')}"
        )

    lines += ["", "-- short-term memory --"]
    rows = session.get("short_term_memory", [])
    if not rows:
        lines.append("  (empty)")
    lines += [f"  {row}" for row in rows]

    return "\n".join(lines) + "\n"


def write_bundle(
    directory: str | Path,
    report: dict[str, Any],
    *,
    now: datetime | None = None,
) -> Path:
    """Write the formatted report to a timestamped file; returns its path."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    path = Path(directory) / f"jess-diagnostics-{stamp}.txt"
    path.write_text(format_report(report), encoding="utf-8")
    return path

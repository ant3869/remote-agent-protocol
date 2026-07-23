"""Persist finished agent jobs so the Agents panel survives a restart.

Tiny and pure on purpose: the live jobs still belong to AgentBridge; this only
appends a bounded, on-disk tail of what has already finished, and reads it back
when the GUI opens. Best-effort throughout -- a flaky disk must never take down
the voice loop.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from pathlib import Path
from typing import Any

_APPEND_LOCK = threading.Lock()
_MAX_PERSISTED_LINES = 50  # keep the on-disk log tail small


def job_to_row(job: Any) -> dict[str, Any]:
    """Flatten an AgentJob into the JSON-serialisable row we persist."""
    return {
        "job_id": job.job_id,
        "agent": job.agent,
        "machine": job.machine,
        "task": job.task,
        "status": job.status,
        "secs": job.secs,
        "state": job.state,
        "action": job.action,
        "tool": job.tool,
        "step": job.step,
        "step_total": job.step_total,
        "last_completed_step": job.last_completed_step,
        "summary": job.summary,
        "result": getattr(job, "result", ""),
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "failure_kind": job.failure_kind,
        "failure_detail": job.failure_detail,
        "model_label": job.model_label,
        "host_modified": getattr(job, "host_modified", False),
        "lines": list(job.lines)[-_MAX_PERSISTED_LINES:],
    }


def load_history(path: str | Path, limit: int = 100) -> list[dict[str, Any]]:
    """Return the persisted job rows (last ``limit``), or [] if unreadable."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    rows = [row for row in data if isinstance(row, dict)]
    return rows[-limit:] if limit > 0 else rows


def clear_history(path: str | Path) -> bool:
    """Delete the persisted history file. Missing file counts as success.

    Returns False on an OSError so the caller can tell the user deletion
    actually failed, rather than silently claiming it worked.
    """
    with _APPEND_LOCK:
        p = Path(path)
        try:
            p.unlink(missing_ok=True)
        except OSError:
            return False
        return True


def append_job(path: str | Path, row: dict[str, Any], limit: int = 100) -> None:
    """Append one job row, trimming the file to the last ``limit`` rows.

    Written via temp file + swap so a crash mid-write can't corrupt history.
    """
    with _APPEND_LOCK:
        history = load_history(path, 0)
        history.append(row)
        if limit > 0 and len(history) > limit:
            history = history[-limit:]
        p = Path(path)
        tmp = p.with_name(p.name + ".tmp")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, p)
        except OSError:
            with contextlib.suppress(OSError):  # persistence is a nicety, never a hard dependency
                tmp.unlink(missing_ok=True)

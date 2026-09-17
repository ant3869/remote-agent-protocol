"""Typed names for public control-plane lifecycle events."""
# ruff: noqa: D102

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

PROBE_STARTED = "control_probe_started"
PROBE_SUCCEEDED = "control_probe_succeeded"
PROBE_FAILED = "control_probe_failed"
STATUS_CHANGED = "control_status_changed"
LAUNCH_STARTED = "control_launch_started"
LAUNCH_READY = "control_launch_ready"
LAUNCH_FAILED = "control_launch_failed"
JOB_DISPATCHED = "control_job_dispatched"
JOB_PROGRESS_CHANGED = "control_job_progress_changed"
CANCELLATION_REQUESTED = "control_cancellation_requested"
CANCELLATION_COMPLETED = "control_cancellation_completed"
REDIRECTION_COMPLETED = "control_redirection_completed"


@dataclass(frozen=True, slots=True)
class ControlPlaneEvent:
    """An allowlisted event suitable for transcript and lifecycle projection."""

    event: str
    agent_id: str
    job_id: str = ""
    detail: str = ""
    at: datetime = field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "type": "agent_control",
            "event": self.event,
            "agent": self.agent_id,
            "job_id": self.job_id,
            "detail": self.detail[:500],
            "at": self.at.isoformat(),
            "data": self.data,
        }

"""Typed names for public conversation-hub lifecycle events."""
# ruff: noqa: D102

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

FLOOR_CHANGED = "conversation_floor_changed"
CHANNEL_CREATED = "conversation_channel_created"
CHANNEL_RESTORED = "conversation_channel_restored"
CHANNEL_ARCHIVED = "conversation_channel_archived"
CHANNEL_REOPENED = "conversation_channel_reopened"
SESSION_BOUND = "conversation_session_bound"
SESSION_RESUMED = "conversation_session_resumed"
SESSION_ROTATED = "conversation_session_rotated"
SESSION_RESET = "conversation_session_reset"
CONTEXT_ASSEMBLED = "conversation_context_assembled"
MEMORY_PROPOSED = "conversation_memory_proposed"
MEMORY_PROMOTED = "conversation_memory_promoted"
MEMORY_SUPERSEDED = "conversation_memory_superseded"
MEMORY_FORGOTTEN = "conversation_memory_forgotten"
BUTLER_HANDOFF_STARTED = "conversation_butler_handoff_started"
BUTLER_HANDOFF_COMPLETED = "conversation_butler_handoff_completed"
TASK_ASSIGNED = "conversation_task_assigned"
TASK_REASSIGNED = "conversation_task_reassigned"
RESULT_AVAILABLE = "conversation_result_available"
BUTLER_INTERVENTION_STARTED = "conversation_butler_intervention_started"
BUTLER_INTERVENTION_RESOLVED = "conversation_butler_intervention_resolved"


@dataclass(frozen=True, slots=True)
class ConversationEvent:
    """An allowlisted event suitable for transcript and lifecycle projection.

    ``data`` must never carry hidden prompts, memory values, context contents,
    credentials, or raw harness output; callers keep it to identifiers and
    classification only.
    """

    event: str
    channel_id: str
    task_id: str = ""
    detail: str = ""
    at: datetime = field(default_factory=lambda: datetime.now(UTC))
    data: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "type": "agent_conversation",
            "event": self.event,
            "channel": self.channel_id,
            "task_id": self.task_id,
            "detail": self.detail[:500],
            "at": self.at.isoformat(),
            "data": self.data,
        }

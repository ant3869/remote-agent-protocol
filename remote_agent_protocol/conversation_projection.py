"""Merge the durable conversation hub and the live per-process transcript.

Two independent transcript systems exist and neither talks to the other:
``AgentConversationHub`` (durable, no concept of voice/playback state) and
``ConversationStore`` (live, in-memory, no concept of channel/task/result).
This module is the single place that reconciles them into one read-only
unified view, plus the small pure helpers for channel summaries and memory
detail that back the Task 9 web routes.

Every hub read here operates on an already-detached :class:`HubSnapshot` (or
a single already-copied ``ScopedMemory``) gathered on the hub's own event-loop
thread via :func:`gather_hub_snapshot`/:func:`gather_memory` -- never on the
hub's live, mutable dicts directly, which would be a cross-thread hazard when
read from the synchronous HTTP handler thread.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from remote_agent_protocol.conversation_hub.models import (
    AgentChannel,
    ConversationTurn,
    MemoryScope,
    MemoryStatus,
    ScopedMemory,
)
from remote_agent_protocol.conversation_hub.service import AgentConversationHub

_STATUS_LABELS = {
    MemoryStatus.SUPERSEDED: "Superseded; excluded from context.",
    MemoryStatus.FORGOTTEN: "Forgotten; correction history cleared.",
}


@dataclass(frozen=True)
class HubSnapshot:
    """An already-detached copy of everything the projection needs from the hub."""

    channels: tuple[AgentChannel, ...]
    turns: tuple[ConversationTurn, ...]
    available: bool = True


async def gather_hub_snapshot(hub: AgentConversationHub | None) -> HubSnapshot:
    """Copy everything the projection needs off the hub, on its own loop thread."""
    if hub is None:
        return HubSnapshot(channels=(), turns=(), available=False)
    channels = hub.channels()
    turns = tuple(turn for channel in channels for turn in hub.turns(channel.channel_id))
    return HubSnapshot(channels=channels, turns=turns, available=True)


async def gather_memory(hub: AgentConversationHub | None, memory_id: str) -> ScopedMemory | None:
    """Return one detached memory record, or None if the hub or id is unknown."""
    if hub is None:
        return None
    try:
        return hub.memory(memory_id)
    except KeyError:
        return None


def channel_summaries(snapshot: HubSnapshot) -> list[dict]:
    """Describe every known channel for the channel-filter control and action buttons."""
    return [
        {
            "channel_id": channel.channel_id,
            "agent_id": channel.agent_id,
            "chapter_id": channel.chapter_id,
            "summary": channel.summary,
            "active_task_ids": list(channel.active_task_ids),
            "archived": channel.archived_at is not None,
            "archived_at": channel.archived_at.isoformat() if channel.archived_at else None,
            "created_at": channel.created_at.isoformat(),
            "updated_at": channel.updated_at.isoformat(),
            # Butler is never a harness backend (AGENT_BACKENDS never includes
            # it), so "reset session" would only ever fail for its channel --
            # the UI must not offer an action guaranteed to 409.
            "resettable": channel.channel_id != "coordinator:butler",
        }
        for channel in snapshot.channels
    ]


def _normalized_source_channel_id(source_agent: str | None) -> str:
    """Map a live row's source_agent to a channel_id, matching session.py's own rule."""
    if not source_agent:
        return "coordinator:butler"
    # A remote host prefix ("laptop:hermes") is keyed by machine, not agent --
    # normalize it the same way session.py:_harness_voice does, rather than
    # importing the hub's private _channel_id_for.
    return f"agent:{source_agent.rsplit(':', 1)[-1]}"


def _live_entry(row: dict) -> dict:
    return {
        "entry_id": row["key"],
        "channel_id": _normalized_source_channel_id(row.get("source_agent")),
        "task_id": None,
        "attempt_id": row.get("job_id"),
        "speaker_id": row.get("speaker_id") or row.get("speaker_name"),
        "speaker_role": row.get("role"),
        "voice": row.get("speaker_name"),
        "playback_state": row.get("delivery"),
        "result_kind": None,
        "full_text": row.get("text", ""),
        "spoken_text": row.get("spoken_text"),
        "occurred_at": row.get("occurred_at"),
        "source": "live",
    }


def _hub_entry(turn: ConversationTurn) -> dict:
    return {
        "entry_id": turn.turn_id,
        "channel_id": turn.channel_id,
        "task_id": turn.task_id,
        "attempt_id": turn.metadata.get("attempt_id"),
        "speaker_id": turn.speaker_id,
        "speaker_role": turn.speaker_role,
        "voice": None,
        "playback_state": None,
        "result_kind": turn.result_kind.value if turn.result_kind else None,
        "full_text": turn.full_text,
        "spoken_text": turn.spoken_text,
        "occurred_at": turn.created_at.isoformat(),
        "source": "hub",
    }


def unified_history(
    snapshot: HubSnapshot,
    live_snapshot: dict,
    *,
    channel_id: str | None = None,
    query: str = "",
) -> list[dict]:
    """Merge hub turns and live transcript rows into one chronological view."""
    entries: dict[str, dict] = {turn.turn_id: _hub_entry(turn) for turn in snapshot.turns}
    # (speaker_id, attempt_id) -> hub entries sharing that pair. Not unique in
    # general (a job's start/progress/consult/result narrations all share it),
    # so this only narrows *candidates* -- merging still requires the exact
    # spoken_text match below.
    by_agent_attempt: dict[tuple[str, str], list[dict]] = {}
    for entry in entries.values():
        if entry["attempt_id"] is not None:
            by_agent_attempt.setdefault((entry["speaker_id"], entry["attempt_id"]), []).append(
                entry
            )

    extra: list[dict] = []
    for row in live_snapshot.get("rows", []):
        if row.get("type") != "transcript":
            continue
        if row.get("role") == "user" and snapshot.available:
            # The hub's own user turn (already in `entries`) covers this; there
            # is no reliable correlation key between the two, so suppress the
            # live duplicate rather than show both or guess at one.
            continue
        source_agent = row.get("source_agent")
        job_id = row.get("job_id")
        merged = False
        if source_agent and job_id:
            # A remote host prefix ("laptop:hermes") is keyed by machine, not
            # agent -- strip it the same way _harness_voice/channel inference
            # do, so a matching hub turn (keyed by the bare agent id) is found.
            short_agent = source_agent.rsplit(":", 1)[-1]
            for candidate in by_agent_attempt.get((short_agent, job_id), ()):
                if candidate["source"] == "hub" and row.get("text") == (
                    candidate["spoken_text"] or candidate["full_text"]
                ):
                    candidate["voice"] = row.get("speaker_name")
                    candidate["playback_state"] = row.get("delivery")
                    candidate["source"] = "hub+live"
                    merged = True
                    break
        if not merged:
            extra.append(_live_entry(row))

    merged_entries = [*entries.values(), *extra]
    merged_entries.sort(key=lambda entry: datetime.fromisoformat(entry["occurred_at"]))

    if channel_id:
        merged_entries = [entry for entry in merged_entries if entry["channel_id"] == channel_id]
    if query:
        needle = query.lower()
        merged_entries = [
            entry
            for entry in merged_entries
            if needle in (entry["full_text"] or "").lower()
            or needle in (entry["spoken_text"] or "").lower()
        ]
    return merged_entries


def _memory_eligibility(memory: ScopedMemory) -> str:
    label = _STATUS_LABELS.get(memory.status)
    if label is not None:
        return label
    if memory.scope is MemoryScope.SHARED:
        return "Eligible for every channel (shared scope)."
    if memory.scope is MemoryScope.TASK:
        target = memory.task_id or "its task"
        return f"Eligible for {target} (task scope)."
    if memory.scope is MemoryScope.PROJECT:
        target = memory.project_id or "its project"
        return f"Eligible for {target} (project scope)."
    target = memory.channel_id or "its channel"
    return f"Eligible for {target} (channel-scoped)."


def memory_detail(memory: ScopedMemory | None) -> dict[str, Any] | None:
    """Describe one memory record for the memory-detail panel."""
    if memory is None:
        return None
    return {
        "memory_id": memory.memory_id,
        "scope": memory.scope.value,
        "confidence": memory.confidence.value,
        "status": memory.status.value,
        "subject": memory.subject,
        "value": memory.value,
        "source_turn_ids": list(memory.source_turn_ids),
        "observed_at": memory.observed_at.isoformat(),
        "supersedes": memory.supersedes,
        "channel_id": memory.channel_id,
        "task_id": memory.task_id,
        "project_id": memory.project_id,
        "eligibility": _memory_eligibility(memory),
    }

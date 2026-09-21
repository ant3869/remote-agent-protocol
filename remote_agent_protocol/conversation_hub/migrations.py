"""Bring a loaded conversation store up to current shape at startup.

Runs once per process, between ``ConversationStore.load()`` and
``AgentConversationHub.restore()``. Every step is idempotent -- a no-op once
its target state already exists -- so calling ``migrate()`` twice against the
same store produces identical output the second time. Persists exactly once,
at the end, so a mid-migration crash can never leave the store half migrated.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from remote_agent_protocol import config as cfg
from remote_agent_protocol import job_store, memory

from .models import (
    AgentChannel,
    ConversationTurn,
    FloorState,
    MemoryStatus,
    ResultKind,
    ScopedMemory,
    TaskReference,
)
from .store import ConversationLoadResult, ConversationStore

_JOB_FAILURE_STATUSES = frozenset({"failed", "cancelled"})
_CANONICAL_RESULT_KINDS = frozenset(
    {ResultKind.SUCCESS, ResultKind.PARTIAL, ResultKind.BLOCKED, ResultKind.FAILURE}
)


def migrate(
    result: ConversationLoadResult,
    *,
    store: ConversationStore,
    agent_ids: Iterable[str],
    now: datetime,
) -> ConversationLoadResult:
    """Return the migrated state, having already persisted it once."""
    if result.error is not None:
        _preserve_corrupt_file(store.path, now)
        result = ConversationLoadResult()

    agent_id_set = {"butler", *agent_ids}

    channels = _ensure_channels(result.channels, agent_id_set, now)
    turns, metadata = _import_legacy_coordinator_history(result.turns, result.metadata, now)
    task_references = _bind_unbound_jobs(result.task_references, agent_id_set, now)
    task_references, channels, floor_state = _resolve_stale_tasks(
        task_references, channels, result.floor_state, now
    )
    turns = _apply_channel_retention(turns, result.memories, task_references)

    migrated = ConversationLoadResult(
        channels=channels,
        turns=turns,
        memories=result.memories,
        bindings=result.bindings,
        task_references=task_references,
        floor_state=floor_state,
        metadata=metadata,
    )
    store.save(
        channels=migrated.channels,
        turns=migrated.turns,
        memories=migrated.memories,
        bindings=migrated.bindings,
        task_references=migrated.task_references,
        floor_state=migrated.floor_state,
        metadata=migrated.metadata,
    )
    return migrated


def _preserve_corrupt_file(path: Path, now: datetime) -> None:
    """Copy an unreadable/incompatible store aside before anything can overwrite it."""
    if not path.exists():
        return
    stamp = now.strftime("%Y%m%dT%H%M%S")
    backup = path.with_name(f"{path.name}.corrupt-{stamp}")
    suffix = 0
    while backup.exists():
        suffix += 1
        backup = path.with_name(f"{path.name}.corrupt-{stamp}-{suffix}")
    try:
        backup.write_bytes(path.read_bytes())
        logger.warning(f"Preserved an unreadable conversation store as {backup}")
    except OSError as exc:
        logger.warning(f"Could not preserve corrupt conversation store {path}: {exc}")


def _ensure_channels(
    channels: list[AgentChannel], agent_ids: Iterable[str], now: datetime
) -> list[AgentChannel]:
    """Create a stable channel for every configured agent that lacks one yet."""
    result = list(channels)
    existing_channel_ids = {channel.channel_id for channel in result}
    for agent_id in sorted(set(agent_ids)):
        channel_id = "coordinator:butler" if agent_id.lower() == "butler" else f"agent:{agent_id}"
        if channel_id in existing_channel_ids:
            continue
        result.append(AgentChannel.new(agent_id, now))
        existing_channel_ids.add(channel_id)
    return result


def _import_legacy_coordinator_history(
    turns: list[ConversationTurn], metadata: dict[str, Any], now: datetime
) -> tuple[list[ConversationTurn], dict[str, Any]]:
    """Copy eligible pre-hub chat history into Butler's channel, exactly once.

    ``jess_memory.json`` is not deprecated by this -- BrainSession keeps
    reading it for its own live prompt budget. This only gives Task 9's
    conversation-history panel something older than the hub itself.
    """
    metadata = dict(metadata)
    if metadata.get("legacy_import_done"):
        return turns, metadata
    messages = memory.load_memory(cfg.MEMORY_FILE, 0)
    eligible = memory.strip_ephemeral(
        messages,
        system_prefixes=(cfg.MEM0_MEMORY_HEADER,),
        drop_contents=(cfg.KICKOFF_RETURNING, cfg.KICKOFF_FIRST),
        drop_prefixes=cfg.EPHEMERAL_PROMPT_PREFIXES,
    )
    turns = list(turns)
    for message in eligible:
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        content = str(message.get("content", "")).strip()
        if not content:
            continue
        turns.append(
            ConversationTurn.new(
                channel_id="coordinator:butler",
                speaker_id="ant" if role == "user" else "butler",
                speaker_role=role,
                full_text=content,
                now=now,
                metadata={"origin": "legacy_import"},
            )
        )
    metadata["legacy_import_done"] = True
    return turns, metadata


def _bind_unbound_jobs(
    task_references: list[TaskReference], agent_ids: Iterable[str], now: datetime
) -> list[TaskReference]:
    """Backfill a durable task reference for a finished job the hub never saw.

    Job IDs are a per-process counter that restarts with every launch, so the
    same short ID recurs across unrelated runs inside the retained history --
    a duplicated ID is treated as ambiguous and left unbound, per the spec.
    """
    if not cfg.AGENT_HISTORY_FILE:
        return task_references
    rows = job_store.load_history(cfg.AGENT_HISTORY_FILE, 0)
    agent_id_set = set(agent_ids)
    existing_task_ids = {task.task_id for task in task_references}

    job_id_counts: dict[str, int] = {}
    for row in rows:
        job_id = row.get("job_id")
        if isinstance(job_id, str) and job_id:
            job_id_counts[job_id] = job_id_counts.get(job_id, 0) + 1

    result = list(task_references)
    for row in rows:
        job_id = row.get("job_id")
        if not isinstance(job_id, str) or not job_id or job_id_counts.get(job_id, 0) != 1:
            continue
        agent = row.get("agent")
        if not isinstance(agent, str) or agent not in agent_id_set:
            continue
        task_id = f"legacy-job:{job_id}"
        if task_id in existing_task_ids:
            continue
        status = row.get("status")
        if status == "done" and not row.get("failure_kind"):
            mapped_status = "done"
        elif status == "done" or status in _JOB_FAILURE_STATUSES:
            mapped_status = "failed"
        else:
            # running/waiting/blocked/unrecognized: job_store only ever
            # persists finished jobs, so this is defensive, not expected.
            continue
        result.append(
            TaskReference(
                task_id=task_id,
                channel_id=f"agent:{agent}",
                agent_id=agent,
                status=mapped_status,
                created_at=_parse_job_timestamp(row.get("started_at"), now),
                updated_at=_parse_job_timestamp(row.get("finished_at"), now),
            )
        )
        existing_task_ids.add(task_id)
    return result


def _parse_job_timestamp(value: object, fallback: datetime) -> datetime:
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return fallback
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            return parsed
    return fallback


def _resolve_stale_tasks(
    task_references: list[TaskReference],
    channels: list[AgentChannel],
    floor_state: FloorState | None,
    now: datetime,
) -> tuple[list[TaskReference], list[AgentChannel], FloorState | None]:
    """Mark a task nothing can ever complete again, and stop routing to it.

    ``attempt_id`` must be cleared, not just the status: the next process's
    job-ID counter restarts from 1, and _task_by_job_id matches an incoming
    bridge event to a durable task by attempt_id -- a left-over attempt_id
    would let an unrelated future job silently reactivate this dead task.
    """
    interrupted_ids: set[str] = set()
    new_tasks: list[TaskReference] = []
    for task in task_references:
        if task.status == "active":
            interrupted_ids.add(task.task_id)
            new_tasks.append(replace(task, status="interrupted", attempt_id=None, updated_at=now))
        else:
            new_tasks.append(task)

    if not interrupted_ids:
        return task_references, channels, floor_state

    new_channels = [
        replace(
            channel,
            active_task_ids=[
                task_id for task_id in channel.active_task_ids if task_id not in interrupted_ids
            ],
        )
        if any(task_id in interrupted_ids for task_id in channel.active_task_ids)
        else channel
        for channel in channels
    ]

    new_floor_state = floor_state
    if floor_state is not None:
        remaining_owners = {
            task_id: owner
            for task_id, owner in floor_state.task_owner_by_id.items()
            if task_id not in interrupted_ids
        }
        if remaining_owners != floor_state.task_owner_by_id:
            new_floor_state = replace(
                floor_state, task_owner_by_id=remaining_owners, updated_at=now
            )

    return new_tasks, new_channels, new_floor_state


def _apply_channel_retention(
    turns: list[ConversationTurn],
    memories: list[ScopedMemory],
    task_references: list[TaskReference],
) -> list[ConversationTurn]:
    """Trim each channel toward its retention caps, protected turns exempt."""
    protected_turn_ids: set[str] = set()
    for record in memories:
        if record.status == MemoryStatus.ACTIVE:
            protected_turn_ids.update(record.source_turn_ids)
    active_task_ids = {task.task_id for task in task_references if task.status == "active"}

    def is_protected(turn: ConversationTurn) -> bool:
        if turn.turn_id in protected_turn_ids:
            return True
        if turn.task_id is not None and turn.task_id in active_task_ids:
            return True
        if turn.metadata.get("artifact_refs") or turn.metadata.get("attempt_id"):
            return True
        return turn.result_kind in _CANONICAL_RESULT_KINDS

    by_channel: dict[str, list[ConversationTurn]] = {}
    for turn in turns:
        by_channel.setdefault(turn.channel_id, []).append(turn)

    keep_ids: set[str] = set()
    for channel_turns in by_channel.values():
        trimmed = _trim_progress(channel_turns, cfg.CONVERSATION_PROGRESS_RETENTION, is_protected)
        trimmed = _trim_oldest(trimmed, cfg.CONVERSATION_CHANNEL_TURN_RETENTION, is_protected)
        keep_ids.update(turn.turn_id for turn in trimmed)

    return [turn for turn in turns if turn.turn_id in keep_ids]


def _trim_progress(
    turns: list[ConversationTurn], limit: int, is_protected: Any
) -> list[ConversationTurn]:
    """Cap unprotected PROGRESS-kind turns at ``limit``, oldest dropped first."""
    progress = [
        (index, turn) for index, turn in enumerate(turns) if turn.result_kind == ResultKind.PROGRESS
    ]
    if len(progress) <= limit:
        return turns
    excess = len(progress) - limit
    drop: set[int] = set()
    dropped = 0
    for index, turn in progress:
        if dropped >= excess:
            break
        if is_protected(turn):
            continue
        drop.add(index)
        dropped += 1
    return [turn for index, turn in enumerate(turns) if index not in drop]


def _trim_oldest(
    turns: list[ConversationTurn], limit: int, is_protected: Any
) -> list[ConversationTurn]:
    """Cap unprotected turns at ``limit``, oldest dropped first."""
    if len(turns) <= limit:
        return turns
    excess = len(turns) - limit
    kept: list[ConversationTurn] = []
    dropped = 0
    for turn in turns:
        if dropped < excess and not is_protected(turn):
            dropped += 1
            continue
        kept.append(turn)
    return kept

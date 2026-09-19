"""The AgentConversationHub: one continuous conversational room over Tasks 1-5.

Owns logical channels, floor and task ownership, scoped memory, bounded
context assembly, RAP-managed session bindings, and durable persistence.
``AgentBridge`` retains subprocess ownership through the Task 4 session
adapters; this module owns conversational identity, channel context, and
result attribution. Full voice mode and Brain mode share one instance.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from remote_agent_protocol.control_plane.adapters.base import ConversationSessionAdapter
from remote_agent_protocol.control_plane.registry import AgentRegistry

from .context import ContextAssembler, ContextRequest, LiveObservation, TaskContext
from .events import (
    BUTLER_HANDOFF_COMPLETED,
    BUTLER_HANDOFF_STARTED,
    BUTLER_INTERVENTION_STARTED,
    CHANNEL_ARCHIVED,
    CHANNEL_CREATED,
    CHANNEL_REOPENED,
    CHANNEL_RESTORED,
    CONTEXT_ASSEMBLED,
    FLOOR_CHANGED,
    MEMORY_FORGOTTEN,
    RESULT_AVAILABLE,
    SESSION_BOUND,
    SESSION_RESET,
    TASK_ASSIGNED,
    TASK_REASSIGNED,
    ConversationEvent,
)
from .floor import BUTLER_ID, FloorDecision, FloorManager, TurnRoutingInput
from .memory import MemoryRepository
from .models import (
    AgentChannel,
    ConversationTurn,
    FloorState,
    ResultKind,
    ScopedMemory,
    SessionBinding,
    TaskReference,
)
from .selection import AgentSelector, CapabilityRequirement
from .sessions import SessionBindingManager
from .store import ConversationLoadResult, ConversationStore

EventListener = Callable[[dict[str, Any]], None]

# Task 7 owns the versioned, formal communication-contract envelope
# (COMMUNICATION_CONTRACT_VERSION/AgentResultEnvelope). The hub needs a
# contract string before then, so this copy matches the design doc exactly
# and should be replaced by an import from results.py once Task 7 lands.
COMMUNICATION_CONTRACT_VERSION = 1
COMMUNICATION_CONTRACT = (
    "Speak to Ant like a trusted teammate: natural, direct, and brief, but complete. "
    "Lead with the outcome. Include every important result, decision, warning, failure, "
    "and next step. Omit internal tool chatter unless asked. Keep operational progress "
    "separate from the final answer. If blocked, ask one clear question. Never impersonate Butler."
)

_NO_DISPATCH_KINDS = frozenset(
    {
        "return_to_butler",
        "acknowledgment",
        "clarification",
        "unavailable",
        "completion",
        "completion_unowned",
    }
)
_NON_TERMINAL_JOB_STATUSES = frozenset({"running", "waiting", "blocked"})


def _channel_id_for(agent_id: str) -> str:
    """Return the stable channel ID for a short agent ID, including Butler."""
    return "coordinator:butler" if agent_id.lower() == BUTLER_ID else f"agent:{agent_id}"


def _agent_id_from_channel(channel_id: str | None) -> str | None:
    """Recover the short agent ID from a stable channel ID, if one is set."""
    if channel_id is None:
        return None
    if channel_id == "coordinator:butler":
        return BUTLER_ID
    return channel_id.removeprefix("agent:")


@dataclass(frozen=True)
class ConversationTurnRequest:
    """One inbound turn from voice or the typed composer, already text-normalized."""

    text: str
    source: str
    explicit_agent_id: str | None
    correlation_id: str
    created_at: datetime

    def __post_init__(self) -> None:
        """Require identifiable, timestamped input for every routed turn."""
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if not self.text.strip():
            raise ValueError("text must be non-empty")
        if not self.source.strip():
            raise ValueError("source must be non-empty")


@dataclass(frozen=True)
class TurnDisposition:
    """Where a turn landed: which channel, target, task, and routing rationale."""

    channel_id: str
    target_id: str
    task_id: str | None
    floor_decision: FloorDecision
    spoken_acknowledgment: str | None


class AgentConversationHub:
    """The application service for conversational continuity across all channels."""

    def __init__(
        self,
        *,
        store: ConversationStore,
        floor_manager: FloorManager,
        memories: MemoryRepository,
        context_assembler: ContextAssembler,
        selector: AgentSelector,
        adapters: Mapping[str, ConversationSessionAdapter],
        registry: AgentRegistry,
        communication_contract: str = COMMUNICATION_CONTRACT,
        on_event: EventListener | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        """Construct an empty hub; call ``restore`` to hydrate durable state."""
        self._store = store
        self._floor_manager = floor_manager
        self._memories = memories
        self._context_assembler = context_assembler
        self._selector = selector
        self._adapters = dict(adapters)
        self._registry = registry
        self._contract = communication_contract
        self._on_event = on_event
        self._now = now
        self._channels: dict[str, AgentChannel] = {}
        self._turns: list[ConversationTurn] = []
        self._tasks: dict[str, TaskReference] = {}
        self._floor_state = FloorState.new(now=self._now())
        self._sessions = SessionBindingManager(persist=self._persist_binding)
        self._lock = asyncio.Lock()

    @property
    def floor_state(self) -> FloorState:
        """Return the current independent front-door/floor/task-owner state."""
        return self._floor_state

    def channel(self, channel_id: str) -> AgentChannel | None:
        """Return a known channel by its stable ID, if one has been created."""
        return self._channels.get(channel_id)

    def task(self, task_id: str) -> TaskReference | None:
        """Return a known durable task reference, if one has been created."""
        return self._tasks.get(task_id)

    def turns(self, channel_id: str) -> tuple[ConversationTurn, ...]:
        """Return the persisted turns for one channel, oldest first."""
        return tuple(turn for turn in self._turns if turn.channel_id == channel_id)

    def restore(self, result: ConversationLoadResult) -> None:
        """Hydrate logical state from the durable store at application start.

        Live control-plane observations are untouched here and remain stale
        until Milestone 1 re-verifies them.
        """
        self._channels = {channel.channel_id: channel for channel in result.channels}
        self._turns = list(result.turns)
        self._tasks = {task.task_id: task for task in result.task_references}
        self._floor_state = result.floor_state or FloorState.new(now=self._now())
        self._memories = MemoryRepository(result.memories, policy=self._memories.policy)
        self._context_assembler = ContextAssembler(self._memories, self._context_assembler.budget)
        self._sessions = SessionBindingManager(
            persist=self._persist_binding, bindings=result.bindings
        )
        for channel_id in self._channels:
            self._emit(CHANNEL_RESTORED, channel_id, detail="Restored; live status remains stale.")

    # -- Turn routing -----------------------------------------------------

    async def handle_turn(self, request: ConversationTurnRequest) -> TurnDisposition:
        """Resolve floor, select/verify a target, and dispatch or narrate."""
        async with self._lock:
            current_floor_channel = self._floor_state.floor_channel_id
            current_floor_agent = _agent_id_from_channel(current_floor_channel) or BUTLER_ID

            if request.explicit_agent_id:
                decision = self._direct_decision(request.explicit_agent_id, current_floor_agent)
            else:
                routing_input = TurnRoutingInput(
                    text=request.text,
                    now=request.created_at,
                    current_floor=current_floor_channel,
                    last_speaker_id=self._floor_state.last_speaker_id,
                    current_task_id=self._current_task_id_for(current_floor_agent),
                    active_tasks=tuple(self._tasks.values()),
                    available_agent_ids=frozenset(self._adapters),
                    verified_agent_ids=frozenset(self._adapters),
                )
                decision = self._floor_manager.resolve(routing_input)

            if decision.kind in _NO_DISPATCH_KINDS:
                return self._respond_without_dispatch(request, decision)

            target_id = decision.target_id
            if decision.requires_butler_selection:
                selected = await self._select_agent()
                if selected is None:
                    return self._respond_without_dispatch(
                        request,
                        decision,
                        spoken="I don't currently have a verified agent for that.",
                    )
                target_id = selected

            return await self._dispatch(request, decision, target_id)

    def _direct_decision(self, explicit_agent_id: str, current_floor_agent: str) -> FloorDecision:
        """Resolve a caller-supplied explicit target the same way a parsed name would."""
        target_id = explicit_agent_id.strip()
        if target_id.lower() == BUTLER_ID:
            return FloorDecision(
                kind="return_to_butler",
                target_id=BUTLER_ID,
                task_id=None,
                requires_clarification=False,
                requires_butler_selection=False,
                reason_code="explicit_butler",
                next_floor_id=BUTLER_ID,
            )
        if target_id not in self._adapters:
            return FloorDecision(
                kind="unavailable",
                target_id=BUTLER_ID,
                task_id=None,
                requires_clarification=False,
                requires_butler_selection=False,
                reason_code="named_agent_unavailable",
                next_floor_id=current_floor_agent,
                spoken_text=f"{target_id} is not a configured agent.",
            )
        task_id = self._sole_active_task_id(target_id)
        switched = target_id != current_floor_agent and task_id is not None
        return FloorDecision(
            kind="direct",
            target_id=target_id,
            task_id=task_id,
            requires_clarification=False,
            requires_butler_selection=False,
            reason_code="explicit_agent_task_switch" if switched else "explicit_agent",
            next_floor_id=target_id,
        )

    def _current_task_id_for(self, agent_id: str) -> str | None:
        """Return the sole active task the current floor holder owns, if any."""
        candidates = [
            task_id
            for task_id, owner in self._floor_state.task_owner_by_id.items()
            if owner == agent_id and (task := self._tasks.get(task_id)) and task.status == "active"
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda task_id: self._tasks[task_id].updated_at)

    def _sole_active_task_id(self, agent_id: str) -> str | None:
        """Return an unambiguous active task for direct addressing, else None."""
        candidates = [
            task_id
            for task_id, owner in self._floor_state.task_owner_by_id.items()
            if owner == agent_id and (task := self._tasks.get(task_id)) and task.status == "active"
        ]
        return candidates[0] if len(candidates) == 1 else None

    async def _select_agent(self) -> str | None:
        """Use live control-plane evidence to pick a qualified candidate."""
        snapshots = await self._registry.list()
        decision = self._selector.select(CapabilityRequirement(), snapshots)
        return decision.agent_id

    async def _dispatch(
        self, request: ConversationTurnRequest, decision: FloorDecision, target_id: str
    ) -> TurnDisposition:
        """Bind a channel-owned session, assemble context, and dispatch through it."""
        now = request.created_at
        channel, created = self._get_or_create_channel(target_id)
        if created:
            self._emit(CHANNEL_CREATED, channel.channel_id, detail="First channel for this agent.")

        task_id = decision.task_id or f"task_{uuid4().hex}"
        existing_task = self._tasks.get(task_id)
        task_context = self._task_context(task_id)

        user_turn = ConversationTurn.new(
            channel_id=channel.channel_id,
            speaker_id=request.source,
            speaker_role="user",
            full_text=request.text,
            now=now,
            task_id=task_id,
        )
        self._turns.append(user_turn)

        recent_turns = tuple(turn for turn in self._turns if turn.channel_id == channel.channel_id)
        live_state = await self._live_observations(channel.channel_id, target_id)
        context_request = ContextRequest(
            channel_id=channel.channel_id,
            current_request=request.text,
            communication_contract=self._contract,
            task_id=task_id,
            active_task=task_context,
            recent_turns=recent_turns,
            live_state=live_state,
            previous_channel_id=self._floor_state.floor_channel_id,
        )
        try:
            package = self._context_assembler.assemble(context_request)
        except ValueError as exc:
            return self._butler_intervention(
                request, decision, task_id, f"Context could not be assembled: {exc}"
            )
        self._emit(
            CONTEXT_ASSEMBLED,
            channel.channel_id,
            task_id=task_id,
            data={"omitted_sections": list(package.omitted_sections)},
        )

        adapter = self._adapters.get(target_id)
        if adapter is None:
            return self._butler_intervention(
                request, decision, task_id, f"{target_id} has no configured session adapter."
            )

        if decision.requires_butler_selection:
            self._emit(
                BUTLER_HANDOFF_STARTED,
                "coordinator:butler",
                task_id=task_id,
                detail=f"Selected {target_id}.",
            )

        try:
            handle = await self._sessions.dispatch(channel.channel_id, adapter, package)
        except Exception as exc:  # noqa: BLE001 - normalized into a Butler intervention
            return self._butler_intervention(request, decision, task_id, f"Dispatch failed: {exc}")

        self._emit(
            SESSION_BOUND, channel.channel_id, task_id=task_id, detail="Session bound for dispatch."
        )
        if decision.requires_butler_selection:
            self._emit(
                BUTLER_HANDOFF_COMPLETED,
                channel.channel_id,
                task_id=task_id,
                detail=f"{target_id} is handling this.",
            )

        reassigned = existing_task is not None and existing_task.agent_id != target_id
        self._tasks[task_id] = TaskReference(
            task_id=task_id,
            channel_id=channel.channel_id,
            agent_id=target_id,
            status="active",
            created_at=existing_task.created_at if existing_task else now,
            updated_at=now,
            attempt_id=handle.job_id,
            artifact_refs=list(existing_task.artifact_refs) if existing_task else [],
        )
        active_ids = list(dict.fromkeys([*channel.active_task_ids, task_id]))
        self._channels[channel.channel_id] = replace(
            channel, active_task_ids=active_ids, updated_at=now
        )
        self._floor_state = replace(
            self._floor_state,
            floor_channel_id=channel.channel_id,
            task_owner_by_id={**self._floor_state.task_owner_by_id, task_id: target_id},
            updated_at=now,
        )
        self._emit(
            TASK_REASSIGNED if reassigned else TASK_ASSIGNED,
            channel.channel_id,
            task_id=task_id,
            data={"agent_id": target_id},
        )
        self._save()
        return TurnDisposition(
            channel_id=channel.channel_id,
            target_id=target_id,
            task_id=task_id,
            floor_decision=decision,
            spoken_acknowledgment=None,
        )

    def _respond_without_dispatch(
        self,
        request: ConversationTurnRequest,
        decision: FloorDecision,
        *,
        spoken: str | None = None,
    ) -> TurnDisposition:
        """Record the turn and move the floor without starting any new work."""
        channel, created = self._get_or_create_channel(decision.target_id)
        if created:
            self._emit(CHANNEL_CREATED, channel.channel_id, detail="First channel for this agent.")
        turn = ConversationTurn.new(
            channel_id=channel.channel_id,
            speaker_id=request.source,
            speaker_role="user",
            full_text=request.text,
            now=request.created_at,
            task_id=decision.task_id,
        )
        self._turns.append(turn)
        next_floor = decision.next_floor_id or decision.target_id
        self._floor_state = replace(
            self._floor_state,
            floor_channel_id=_channel_id_for(next_floor),
            updated_at=request.created_at,
        )
        self._emit(
            FLOOR_CHANGED,
            channel.channel_id,
            detail=decision.reason_code,
            data={"kind": decision.kind},
        )
        self._save()
        return TurnDisposition(
            channel_id=channel.channel_id,
            target_id=decision.target_id,
            task_id=decision.task_id,
            floor_decision=decision,
            spoken_acknowledgment=spoken if spoken is not None else decision.spoken_text,
        )

    def _butler_intervention(
        self,
        request: ConversationTurnRequest,
        decision: FloorDecision,
        task_id: str | None,
        detail: str,
    ) -> TurnDisposition:
        """Transfer narration to Butler when dispatch cannot safely proceed."""
        self._emit(
            BUTLER_INTERVENTION_STARTED, "coordinator:butler", task_id=task_id or "", detail=detail
        )
        channel, created = self._get_or_create_channel(BUTLER_ID)
        if created:
            self._emit(CHANNEL_CREATED, channel.channel_id, detail="First channel for this agent.")
        self._floor_state = replace(
            self._floor_state, floor_channel_id=channel.channel_id, updated_at=request.created_at
        )
        self._save()
        return TurnDisposition(
            channel_id=channel.channel_id,
            target_id=BUTLER_ID,
            task_id=task_id,
            floor_decision=decision,
            spoken_acknowledgment=detail,
        )

    def _task_context(self, task_id: str) -> TaskContext | None:
        """Reconstruct a short task description from its originating user turn."""
        origin = next(
            (
                turn
                for turn in self._turns
                if turn.task_id == task_id and turn.speaker_role == "user"
            ),
            None,
        )
        return TaskContext(task_id=task_id, text=origin.full_text) if origin else None

    async def _live_observations(
        self, channel_id: str, agent_id: str
    ) -> tuple[LiveObservation, ...]:
        """Summarize the target's current control-plane evidence for this channel."""
        snapshot = await self._registry.get(agent_id)
        if snapshot is None:
            return ()
        observation = snapshot.observation
        detail = (
            f"{agent_id}: presence={observation.presence.value} "
            f"activity={observation.activity.value} health={observation.health.value}"
        )
        if observation.current_work is not None and observation.current_work.summary:
            detail += f"; current work: {observation.current_work.summary}"
        return (
            LiveObservation(
                text=detail, observed_at=observation.observed_at, channel_id=channel_id
            ),
        )

    # -- Background job completion -----------------------------------------

    async def handle_job_event(self, event: Mapping[str, Any]) -> None:
        """Record RAP-owned job lifecycle without ever stealing the active floor."""
        if event.get("type") != "agent_job":
            return
        job_id = str(event.get("job_id") or "")
        if not job_id:
            return
        async with self._lock:
            task = self._task_by_job_id(job_id)
            if task is None:
                return
            status = str(event.get("status", ""))
            now = self._now()
            if status in _NON_TERMINAL_JOB_STATUSES:
                if task.status != "active":
                    self._tasks[task.task_id] = replace(task, status="active", updated_at=now)
                    self._save()
                return

            failure_kind = str(event.get("failure_kind") or "")
            if status == "failed" or failure_kind:
                result_kind, new_status = ResultKind.FAILURE, "failed"
            elif status == "done":
                result_kind, new_status = ResultKind.SUCCESS, "done"
            else:
                return

            agent_id = task.agent_id
            text = str(
                event.get("result") or event.get("failure_detail") or event.get("summary") or ""
            )
            routing = self._floor_manager.resolve(
                TurnRoutingInput(
                    text="",
                    now=now,
                    current_floor=self._floor_state.floor_channel_id,
                    last_speaker_id=self._floor_state.last_speaker_id,
                    active_tasks=tuple(self._tasks.values()),
                    completion_task_id=task.task_id,
                )
            )
            channel, created = self._get_or_create_channel(agent_id)
            if created:
                self._emit(
                    CHANNEL_CREATED, channel.channel_id, detail="First channel for this agent."
                )
            turn = ConversationTurn.new(
                channel_id=channel.channel_id,
                speaker_id=agent_id,
                speaker_role="agent",
                full_text=text or "(no result text was provided)",
                now=now,
                task_id=task.task_id,
                result_kind=result_kind,
                contract_version=channel.communication_contract_version,
            )
            self._turns.append(turn)
            self._tasks[task.task_id] = replace(task, status=new_status, updated_at=now)
            remaining_active = [tid for tid in channel.active_task_ids if tid != task.task_id]
            self._channels[channel.channel_id] = replace(
                channel, active_task_ids=remaining_active, updated_at=now
            )
            preserved_floor = (
                routing.next_floor_id
                or _agent_id_from_channel(self._floor_state.floor_channel_id)
                or BUTLER_ID
            )
            self._floor_state = replace(
                self._floor_state,
                floor_channel_id=_channel_id_for(preserved_floor),
                last_speaker_id=agent_id,
                updated_at=now,
            )
            self._save()
            self._emit(
                RESULT_AVAILABLE,
                channel.channel_id,
                task_id=task.task_id,
                data={"result_kind": result_kind.value},
            )
            if result_kind is ResultKind.FAILURE:
                self._emit(
                    BUTLER_INTERVENTION_STARTED,
                    "coordinator:butler",
                    task_id=task.task_id,
                    detail=text or "Agent work failed.",
                )

    def _task_by_job_id(self, job_id: str) -> TaskReference | None:
        """Resolve the durable task owning a RAP bridge job attempt."""
        return next((task for task in self._tasks.values() if task.attempt_id == job_id), None)

    # -- Conversation controls ----------------------------------------------

    async def reset_session(self, channel_id: str) -> SessionBinding:
        """Replace only the physical harness session; the logical channel survives."""
        async with self._lock:
            agent_id = _agent_id_from_channel(channel_id)
            adapter = agent_id and self._adapters.get(agent_id)
            if adapter is None:
                raise ValueError(f"No configured session adapter for {channel_id}")
            binding = await self._sessions.rotate(
                channel_id, adapter, reason="user_requested_reset"
            )
            self._emit(SESSION_RESET, channel_id, detail="Session reset by explicit request.")
            self._save()
            return binding

    async def new_chapter(self, channel_id: str) -> AgentChannel:
        """Preserve durable memory while closing the current conversational topic."""
        async with self._lock:
            channel = self._channels.get(channel_id)
            if channel is None:
                raise ValueError(f"Unknown channel: {channel_id}")
            now = self._now()
            updated = replace(
                channel,
                chapter_id=channel.chapter_id + 1,
                summary="",
                active_task_ids=[],
                updated_at=now,
            )
            self._channels[channel_id] = updated
            self._save()
            self._emit(
                CHANNEL_REOPENED, channel_id, detail=f"Started chapter {updated.chapter_id}."
            )
            return updated

    async def archive_channel(self, channel_id: str) -> AgentChannel:
        """Mark an inactive channel archived without deleting its transcript."""
        async with self._lock:
            channel = self._channels.get(channel_id)
            if channel is None:
                raise ValueError(f"Unknown channel: {channel_id}")
            now = self._now()
            updated = replace(channel, archived_at=now, updated_at=now)
            self._channels[channel_id] = updated
            self._save()
            self._emit(CHANNEL_ARCHIVED, channel_id)
            return updated

    async def forget_memory(self, memory_id: str) -> ScopedMemory:
        """Exclude a memory from future context assembly, leaving a tombstone."""
        async with self._lock:
            forgotten = self._memories.forget(memory_id)
            self._save()
            self._emit(
                MEMORY_FORGOTTEN,
                forgotten.channel_id or "coordinator:butler",
                data={"memory_id": memory_id},
            )
            return forgotten

    # -- Internals ------------------------------------------------------------

    def _get_or_create_channel(self, agent_id: str) -> tuple[AgentChannel, bool]:
        channel_id = _channel_id_for(agent_id)
        existing = self._channels.get(channel_id)
        if existing is not None:
            return existing, False
        channel = AgentChannel.new(agent_id=agent_id, now=self._now())
        self._channels[channel_id] = channel
        return channel, True

    def _persist_binding(self, binding: SessionBinding) -> None:
        self._save()

    def _save(self) -> None:
        self._store.save(
            channels=list(self._channels.values()),
            turns=list(self._turns),
            memories=self._memories.snapshot(),
            bindings=list(self._sessions.bindings),
            task_references=list(self._tasks.values()),
            floor_state=self._floor_state,
        )

    def _emit(
        self,
        event: str,
        channel_id: str,
        *,
        task_id: str = "",
        detail: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        if self._on_event is not None:
            self._on_event(
                ConversationEvent(
                    event, channel_id, task_id=task_id, detail=detail, data=data or {}
                ).to_payload()
            )

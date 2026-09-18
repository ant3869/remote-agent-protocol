"""Deterministic conversation-floor and task-ownership resolution.

The resolver deliberately does not mutate durable state.  Its caller persists
the returned ``next_floor_id`` alongside the task ownership records, keeping a
background result from changing who receives the user's next turn.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from remote_agent_protocol.conversation_hub.models import TaskReference
from remote_agent_protocol.voice_commands import is_smalltalk, parse_delegation

BUTLER_ID = "butler"
_ACTIVE_TASK_STATUSES = frozenset(
    {"active", "blocked", "in_progress", "pending", "queued", "running"}
)
_DEICTIC_REFERENCE = re.compile(r"\b(?:it|that|this)\b", re.IGNORECASE)
_FOLLOW_UP = re.compile(
    r"^(?:"
    r"what about|and\b|also\b|continue\b|please continue\b|"
    r"can you\b|could you\b|would you\b|"
    r"why\b|how\b|more\b|tell me more\b"
    r")",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TurnRoutingInput:
    """Evidence needed to route one user turn or an asynchronous completion.

    ``available_agent_ids`` is ``None`` only when availability has not been
    supplied yet.  An empty set is meaningful evidence that no harness agent
    is currently available.  ``verified_agent_ids`` controls the alternatives
    Butler may offer after a named agent is unavailable.
    """

    text: str
    now: datetime
    current_floor: str | None = None
    last_speaker_id: str | None = None
    current_task_id: str | None = None
    active_tasks: tuple[TaskReference, ...] = ()
    completion_task_id: str | None = None
    available_agent_ids: frozenset[str] | None = None
    verified_agent_ids: frozenset[str] = frozenset()
    unavailability_evidence: str | None = None

    def __post_init__(self) -> None:
        """Reject mutable task collections and naive evidence timestamps."""
        if self.now.tzinfo is None or self.now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        if not isinstance(self.active_tasks, tuple):
            raise ValueError("active_tasks must be a tuple")
        if not all(isinstance(task, TaskReference) for task in self.active_tasks):
            raise ValueError("active_tasks must contain TaskReference values")
        if self.available_agent_ids is not None and not isinstance(
            self.available_agent_ids, frozenset
        ):
            raise ValueError("available_agent_ids must be a frozenset or null")
        if not isinstance(self.verified_agent_ids, frozenset):
            raise ValueError("verified_agent_ids must be a frozenset")


@dataclass(frozen=True)
class FloorDecision:
    """An immutable deterministic routing result for a single incoming event."""

    kind: str
    target_id: str
    task_id: str | None
    requires_clarification: bool
    requires_butler_selection: bool
    reason_code: str
    next_floor_id: str | None
    spoken_text: str | None = None
    verified_alternatives: tuple[str, ...] = ()


class FloorManager:
    """Resolve direct address, floor continuity, and task-owner narration.

    The manager receives configured aliases rather than maintaining its own
    alias grammar.  ``parse_delegation`` remains the sole parser for spoken
    agent addressing, so voice, Brain, and conversation-hub paths agree.
    """

    def __init__(
        self,
        *,
        backends: Mapping[str, object],
        aliases: Mapping[str, str],
    ) -> None:
        """Capture immutable snapshots of configured parser inputs."""
        self._backends = MappingProxyType(dict(backends))
        self._aliases = MappingProxyType(dict(aliases))

    def resolve(self, routing_input: TurnRoutingInput) -> FloorDecision:
        """Return the routing target without changing the caller's floor state."""
        current_floor = _agent_id(routing_input.current_floor) or BUTLER_ID

        if routing_input.completion_task_id is not None:
            return self._resolve_completion(routing_input, current_floor)

        text = routing_input.text.strip()
        if _is_butler_invocation(text):
            return FloorDecision(
                kind="return_to_butler",
                target_id=BUTLER_ID,
                task_id=None,
                requires_clarification=False,
                requires_butler_selection=False,
                reason_code="explicit_butler",
                next_floor_id=BUTLER_ID,
            )

        direct = parse_delegation(text, dict(self._backends), dict(self._aliases))
        if direct is not None:
            target_id, _task_text = direct
            return self._resolve_direct(routing_input, current_floor, target_id)

        if is_smalltalk(text):
            target_id = _agent_id(routing_input.last_speaker_id) or current_floor
            return FloorDecision(
                kind="acknowledgment",
                target_id=target_id,
                task_id=self._current_task_id(routing_input, target_id),
                requires_clarification=False,
                requires_butler_selection=False,
                reason_code="acknowledgment_last_speaker",
                next_floor_id=current_floor,
            )

        active_tasks = _active_tasks(routing_input.active_tasks)
        if _DEICTIC_REFERENCE.search(text) and len(active_tasks) > 1:
            return FloorDecision(
                kind="clarification",
                target_id=BUTLER_ID,
                task_id=None,
                requires_clarification=True,
                requires_butler_selection=False,
                reason_code="ambiguous_task_reference",
                next_floor_id=current_floor,
                spoken_text="Which task do you mean?",
            )

        if current_floor != BUTLER_ID and _FOLLOW_UP.search(text):
            return FloorDecision(
                kind="follow_up",
                target_id=current_floor,
                task_id=self._current_task_id(routing_input, current_floor),
                requires_clarification=False,
                requires_butler_selection=False,
                reason_code="current_subject_follow_up",
                next_floor_id=current_floor,
            )

        return FloorDecision(
            kind="butler_mediated",
            target_id=BUTLER_ID,
            task_id=None,
            requires_clarification=False,
            requires_butler_selection=True,
            reason_code="unnamed_new_work",
            next_floor_id=BUTLER_ID,
        )

    def _resolve_completion(
        self, routing_input: TurnRoutingInput, current_floor: str
    ) -> FloorDecision:
        """Send a completion to the durable task owner without moving the floor."""
        task = next(
            (
                candidate
                for candidate in routing_input.active_tasks
                if candidate.task_id == routing_input.completion_task_id
            ),
            None,
        )
        if task is None:
            return FloorDecision(
                kind="completion_unowned",
                target_id=BUTLER_ID,
                task_id=routing_input.completion_task_id,
                requires_clarification=False,
                requires_butler_selection=False,
                reason_code="completion_owner_unknown",
                next_floor_id=current_floor,
            )
        return FloorDecision(
            kind="completion",
            target_id=task.agent_id,
            task_id=task.task_id,
            requires_clarification=False,
            requires_butler_selection=False,
            reason_code="completion_task_owner",
            next_floor_id=current_floor,
        )

    def _resolve_direct(
        self, routing_input: TurnRoutingInput, current_floor: str, target_id: str
    ) -> FloorDecision:
        """Transfer a direct address, or narrate unavailable evidence via Butler."""
        if (
            routing_input.available_agent_ids is not None
            and target_id not in routing_input.available_agent_ids
        ):
            alternatives = tuple(
                sorted(
                    agent_id
                    for agent_id in routing_input.verified_agent_ids
                    if agent_id != target_id and agent_id in routing_input.available_agent_ids
                )
            )
            return FloorDecision(
                kind="unavailable",
                target_id=BUTLER_ID,
                task_id=None,
                requires_clarification=False,
                requires_butler_selection=False,
                reason_code="named_agent_unavailable",
                next_floor_id=current_floor,
                spoken_text=_unavailable_message(
                    target_id, routing_input.unavailability_evidence, alternatives
                ),
                verified_alternatives=alternatives,
            )

        task_id = self._referenced_task_id(routing_input)
        switched = target_id != current_floor and task_id is not None
        return FloorDecision(
            kind="direct",
            target_id=target_id,
            task_id=task_id,
            requires_clarification=False,
            requires_butler_selection=False,
            reason_code="explicit_agent_task_switch" if switched else "explicit_agent",
            next_floor_id=target_id,
        )

    def _current_task_id(self, routing_input: TurnRoutingInput, owner_id: str) -> str | None:
        """Return the current owner task without inferring ownership from text."""
        if routing_input.current_task_id is not None:
            current_task = next(
                (
                    task
                    for task in _active_tasks(routing_input.active_tasks)
                    if task.task_id == routing_input.current_task_id and task.agent_id == owner_id
                ),
                None,
            )
            if current_task is not None:
                return current_task.task_id
        owner_tasks = [
            task for task in _active_tasks(routing_input.active_tasks) if task.agent_id == owner_id
        ]
        if len(owner_tasks) == 1:
            return owner_tasks[0].task_id
        return None

    def _referenced_task_id(self, routing_input: TurnRoutingInput) -> str | None:
        """Carry only an explicitly current or unambiguously sole active task."""
        if routing_input.current_task_id is not None and _DEICTIC_REFERENCE.search(
            routing_input.text
        ):
            current_task = next(
                (
                    task
                    for task in _active_tasks(routing_input.active_tasks)
                    if task.task_id == routing_input.current_task_id
                ),
                None,
            )
            if current_task is not None:
                return current_task.task_id
        active_tasks = _active_tasks(routing_input.active_tasks)
        if len(active_tasks) == 1 and _DEICTIC_REFERENCE.search(routing_input.text):
            return active_tasks[0].task_id
        return None


def _active_tasks(tasks: tuple[TaskReference, ...]) -> tuple[TaskReference, ...]:
    """Keep only task references that can still own an incoming follow-up."""
    return tuple(task for task in tasks if task.status.casefold() in _ACTIVE_TASK_STATUSES)


def _agent_id(value: str | None) -> str | None:
    """Accept persisted channel IDs and short public agent IDs at the boundary."""
    if value is None:
        return None
    normalized = value.strip()
    if normalized == "coordinator:butler":
        return BUTLER_ID
    if normalized.startswith("agent:"):
        return normalized.removeprefix("agent:")
    return normalized or None


def _is_butler_invocation(text: str) -> bool:
    """Recognize only an explicit Butler invocation, never a casual mention."""
    return text.strip().rstrip(".,!?").casefold() == BUTLER_ID


def _unavailable_message(
    target_id: str, evidence: str | None, alternatives: tuple[str, ...]
) -> str:
    """State supplied evidence and only list agents verified as alternatives."""
    name = _display_name(target_id)
    message = f"{name} is unavailable"
    if evidence:
        message += f": {evidence}"
    message += "."
    if alternatives:
        message += " I can use " + ", ".join(_display_name(agent) for agent in alternatives) + "."
    return message


def _display_name(agent_id: str) -> str:
    """Make stable backend IDs readable without claiming a persona identity."""
    if agent_id.casefold() == "openclaw":
        return "OpenClaw"
    return agent_id.replace("-", " ").title()

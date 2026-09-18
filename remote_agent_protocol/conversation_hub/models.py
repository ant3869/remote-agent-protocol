"""Schema-checked, JSON-serializable domain records for durable conversations."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class MemoryScope(StrEnum):
    """The context boundary in which a promoted memory may be used."""

    CHANNEL = "channel"
    SHARED = "shared"
    TASK = "task"
    PROJECT = "project"


class MemoryConfidence(StrEnum):
    """How strongly the system can rely on a stored memory."""

    VERIFIED = "verified"
    USER_STATED = "user_stated"
    INFERRED = "inferred"


class MemoryStatus(StrEnum):
    """Whether a memory remains eligible for context assembly."""

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FORGOTTEN = "forgotten"


class ResultKind(StrEnum):
    """The normalized outcome state of an agent turn."""

    PROGRESS = "progress"
    SUCCESS = "success"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    FAILURE = "failure"


class SessionStrategy(StrEnum):
    """How a physical harness session preserves logical-channel continuity."""

    NATIVE_RESUME = "native_resume"
    REHYDRATE = "rehydrate"


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _timestamp(value: datetime) -> str:
    return _aware(value, "timestamp").isoformat()


def _read_timestamp(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO timestamp") from exc
    return _aware(parsed, name)


def _required(payload: dict[str, Any], key: str, expected: type | tuple[type, ...]) -> Any:
    value = payload.get(key)
    if not isinstance(value, expected):
        expected_name = getattr(expected, "__name__", "valid value")
        raise ValueError(f"{key} must be a {expected_name}")
    return value


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{key} must be a string or null")
    return value


def _string_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a list of strings")
    return value


@dataclass(frozen=True)
class AgentChannel:
    """A stable logical conversation with one harness agent or Butler."""

    channel_id: str
    agent_id: str
    chapter_id: int
    summary: str
    communication_contract_version: int
    active_task_ids: list[str]
    session_binding_id: str | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = None

    def __post_init__(self) -> None:
        """Enforce stable channel identity and timezone-aware lifecycle timestamps."""
        expected_channel_id = (
            "coordinator:butler" if self.agent_id.lower() == "butler" else f"agent:{self.agent_id}"
        )
        if self.channel_id != expected_channel_id:
            raise ValueError("channel_id must match the stable agent channel ID")
        _aware(self.created_at, "created_at")
        _aware(self.updated_at, "updated_at")
        if self.archived_at is not None:
            _aware(self.archived_at, "archived_at")

    @classmethod
    def new(cls, agent_id: str, now: datetime) -> AgentChannel:
        """Create the first stable channel for an agent."""
        if not isinstance(agent_id, str) or not agent_id.strip():
            raise ValueError("agent_id must be a non-empty string")
        now = _aware(now, "now")
        normalized_agent_id = agent_id.strip()
        channel_id = (
            "coordinator:butler"
            if normalized_agent_id.lower() == "butler"
            else f"agent:{normalized_agent_id}"
        )
        return cls(
            channel_id=channel_id,
            agent_id=normalized_agent_id,
            chapter_id=1,
            summary="",
            communication_contract_version=1,
            active_task_ids=[],
            session_binding_id=None,
            created_at=now,
            updated_at=now,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the durable JSON representation."""
        return {
            "channel_id": self.channel_id,
            "agent_id": self.agent_id,
            "chapter_id": self.chapter_id,
            "summary": self.summary,
            "communication_contract_version": self.communication_contract_version,
            "active_task_ids": self.active_task_ids,
            "session_binding_id": self.session_binding_id,
            "created_at": _timestamp(self.created_at),
            "updated_at": _timestamp(self.updated_at),
            "archived_at": _timestamp(self.archived_at) if self.archived_at else None,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AgentChannel:
        """Restore a channel after validating its durable representation."""
        if not isinstance(payload, dict):
            raise ValueError("channel payload must be an object")
        chapter_id = _required(payload, "chapter_id", int)
        contract_version = _required(payload, "communication_contract_version", int)
        if chapter_id < 1 or contract_version < 1:
            raise ValueError("channel versions must be positive")
        archived_at = payload.get("archived_at")
        return cls(
            channel_id=_required(payload, "channel_id", str),
            agent_id=_required(payload, "agent_id", str),
            chapter_id=chapter_id,
            summary=_required(payload, "summary", str),
            communication_contract_version=contract_version,
            active_task_ids=_string_list(payload, "active_task_ids"),
            session_binding_id=_optional_string(payload, "session_binding_id"),
            created_at=_read_timestamp(payload.get("created_at"), "created_at"),
            updated_at=_read_timestamp(payload.get("updated_at"), "updated_at"),
            archived_at=_read_timestamp(archived_at, "archived_at") if archived_at else None,
        )


@dataclass(frozen=True)
class ConversationTurn:
    """A normalized user, coordinator, or agent message."""

    turn_id: str
    channel_id: str
    task_id: str | None
    speaker_id: str
    speaker_role: str
    full_text: str
    spoken_text: str | None
    result_kind: ResultKind | None
    contract_version: int | None
    created_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Require an aware timestamp for direct construction as well as restoration."""
        _aware(self.created_at, "created_at")

    @classmethod
    def new(
        cls,
        *,
        channel_id: str,
        speaker_id: str,
        speaker_role: str,
        full_text: str,
        now: datetime,
        task_id: str | None = None,
        spoken_text: str | None = None,
        result_kind: ResultKind | None = None,
        contract_version: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ConversationTurn:
        """Create a uniquely identified turn with an explicit timestamp."""
        return cls(
            turn_id=f"turn_{uuid4().hex}",
            channel_id=channel_id,
            task_id=task_id,
            speaker_id=speaker_id,
            speaker_role=speaker_role,
            full_text=full_text,
            spoken_text=spoken_text,
            result_kind=result_kind,
            contract_version=contract_version,
            created_at=_aware(now, "now"),
            metadata=dict(metadata or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the durable JSON representation."""
        return {
            "turn_id": self.turn_id,
            "channel_id": self.channel_id,
            "task_id": self.task_id,
            "speaker_id": self.speaker_id,
            "speaker_role": self.speaker_role,
            "full_text": self.full_text,
            "spoken_text": self.spoken_text,
            "result_kind": self.result_kind.value if self.result_kind else None,
            "contract_version": self.contract_version,
            "created_at": _timestamp(self.created_at),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ConversationTurn:
        """Restore a turn after validating its durable representation."""
        if not isinstance(payload, dict):
            raise ValueError("turn payload must be an object")
        result_kind = payload.get("result_kind")
        contract_version = payload.get("contract_version")
        metadata = payload.get("metadata", {})
        if result_kind is not None:
            result_kind = ResultKind(result_kind)
        if contract_version is not None and (
            not isinstance(contract_version, int) or contract_version < 1
        ):
            raise ValueError("contract_version must be a positive integer or null")
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        return cls(
            turn_id=_required(payload, "turn_id", str),
            channel_id=_required(payload, "channel_id", str),
            task_id=_optional_string(payload, "task_id"),
            speaker_id=_required(payload, "speaker_id", str),
            speaker_role=_required(payload, "speaker_role", str),
            full_text=_required(payload, "full_text", str),
            spoken_text=_optional_string(payload, "spoken_text"),
            result_kind=result_kind,
            contract_version=contract_version,
            created_at=_read_timestamp(payload.get("created_at"), "created_at"),
            metadata=metadata,
        )


@dataclass(frozen=True)
class ScopedMemory:
    """A source-attributed durable fact, preference, or task decision."""

    memory_id: str
    scope: MemoryScope
    subject: str
    value: str
    source_turn_ids: list[str]
    confidence: MemoryConfidence
    observed_at: datetime
    supersedes: str | None = None
    status: MemoryStatus = MemoryStatus.ACTIVE
    channel_id: str | None = None
    task_id: str | None = None
    project_id: str | None = None

    def __post_init__(self) -> None:
        """Require an aware observation timestamp for direct construction."""
        _aware(self.observed_at, "observed_at")

    def to_dict(self) -> dict[str, Any]:
        """Return the durable JSON representation."""
        return {
            "memory_id": self.memory_id,
            "scope": self.scope.value,
            "subject": self.subject,
            "value": self.value,
            "source_turn_ids": self.source_turn_ids,
            "confidence": self.confidence.value,
            "observed_at": _timestamp(self.observed_at),
            "supersedes": self.supersedes,
            "status": self.status.value,
            "channel_id": self.channel_id,
            "task_id": self.task_id,
            "project_id": self.project_id,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ScopedMemory:
        """Restore a memory after validating its durable representation."""
        if not isinstance(payload, dict):
            raise ValueError("memory payload must be an object")
        return cls(
            memory_id=_required(payload, "memory_id", str),
            scope=MemoryScope(_required(payload, "scope", str)),
            subject=_required(payload, "subject", str),
            value=_required(payload, "value", str),
            source_turn_ids=_string_list(payload, "source_turn_ids"),
            confidence=MemoryConfidence(_required(payload, "confidence", str)),
            observed_at=_read_timestamp(payload.get("observed_at"), "observed_at"),
            supersedes=_optional_string(payload, "supersedes"),
            status=MemoryStatus(payload.get("status", MemoryStatus.ACTIVE.value)),
            channel_id=_optional_string(payload, "channel_id"),
            task_id=_optional_string(payload, "task_id"),
            project_id=_optional_string(payload, "project_id"),
        )


@dataclass(frozen=True)
class SessionBinding:
    """A RAP-owned physical-session binding for one logical agent channel."""

    binding_id: str
    channel_id: str
    agent_id: str
    strategy: SessionStrategy
    adapter_id: str
    native_session_id: str | None
    created_at: datetime
    last_used_at: datetime
    validated_at: datetime | None = None
    rotation_reason: str | None = None
    validation_evidence: dict[str, Any] = field(default_factory=dict)
    requires_validation: bool = False

    def __post_init__(self) -> None:
        """Require aware lifecycle timestamps for direct construction."""
        _aware(self.created_at, "created_at")
        _aware(self.last_used_at, "last_used_at")
        if self.validated_at is not None:
            _aware(self.validated_at, "validated_at")

    @classmethod
    def native(
        cls,
        *,
        binding_id: str,
        channel_id: str,
        agent_id: str,
        native_session_id: str,
        validated_at: datetime,
        adapter_id: str | None = None,
        validation_evidence: dict[str, Any] | None = None,
    ) -> SessionBinding:
        """Create a binding eligible for native resume within the same channel."""
        validated_at = _aware(validated_at, "validated_at")
        return cls(
            binding_id=binding_id,
            channel_id=channel_id,
            agent_id=agent_id,
            strategy=SessionStrategy.NATIVE_RESUME,
            adapter_id=adapter_id or agent_id,
            native_session_id=native_session_id,
            created_at=validated_at,
            last_used_at=validated_at,
            validated_at=validated_at,
            validation_evidence=dict(validation_evidence or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the durable JSON representation."""
        return {
            "binding_id": self.binding_id,
            "channel_id": self.channel_id,
            "agent_id": self.agent_id,
            "strategy": self.strategy.value,
            "adapter_id": self.adapter_id,
            "native_session_id": self.native_session_id,
            "created_at": _timestamp(self.created_at),
            "last_used_at": _timestamp(self.last_used_at),
            "validated_at": _timestamp(self.validated_at) if self.validated_at else None,
            "rotation_reason": self.rotation_reason,
            "validation_evidence": self.validation_evidence,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any], *, restored: bool = False) -> SessionBinding:
        """Restore a binding and revalidate native sessions after a restart."""
        if not isinstance(payload, dict):
            raise ValueError("binding payload must be an object")
        strategy = SessionStrategy(_required(payload, "strategy", str))
        native_session_id = _optional_string(payload, "native_session_id")
        validated_at = payload.get("validated_at")
        evidence = payload.get("validation_evidence", {})
        if strategy is SessionStrategy.NATIVE_RESUME and not native_session_id:
            raise ValueError("native bindings require native_session_id")
        if strategy is SessionStrategy.NATIVE_RESUME and not validated_at:
            raise ValueError("native bindings require validated_at")
        if not isinstance(evidence, dict):
            raise ValueError("validation_evidence must be an object")
        return cls(
            binding_id=_required(payload, "binding_id", str),
            channel_id=_required(payload, "channel_id", str),
            agent_id=_required(payload, "agent_id", str),
            strategy=strategy,
            adapter_id=_required(payload, "adapter_id", str),
            native_session_id=native_session_id,
            created_at=_read_timestamp(payload.get("created_at"), "created_at"),
            last_used_at=_read_timestamp(payload.get("last_used_at"), "last_used_at"),
            validated_at=_read_timestamp(validated_at, "validated_at") if validated_at else None,
            rotation_reason=_optional_string(payload, "rotation_reason"),
            validation_evidence=evidence,
            requires_validation=restored and strategy is SessionStrategy.NATIVE_RESUME,
        )


@dataclass(frozen=True)
class FloorState:
    """The independent front-door, conversation-floor, and task-owner state."""

    front_door_id: str
    floor_channel_id: str | None
    last_speaker_id: str | None
    task_owner_by_id: dict[str, str]
    updated_at: datetime

    def __post_init__(self) -> None:
        """Require an aware floor-transition timestamp for direct construction."""
        _aware(self.updated_at, "updated_at")

    @classmethod
    def new(cls, *, now: datetime, floor_channel_id: str | None = None) -> FloorState:
        """Create initial state with Butler as the front door."""
        return cls(
            front_door_id="coordinator:butler",
            floor_channel_id=floor_channel_id,
            last_speaker_id=None,
            task_owner_by_id={},
            updated_at=_aware(now, "now"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the durable JSON representation."""
        return {
            "front_door_id": self.front_door_id,
            "floor_channel_id": self.floor_channel_id,
            "last_speaker_id": self.last_speaker_id,
            "task_owner_by_id": self.task_owner_by_id,
            "updated_at": _timestamp(self.updated_at),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FloorState:
        """Restore floor state after validating its durable representation."""
        if not isinstance(payload, dict):
            raise ValueError("floor state payload must be an object")
        owners = payload.get("task_owner_by_id", {})
        if not isinstance(owners, dict) or not all(
            isinstance(task_id, str) and isinstance(owner, str) for task_id, owner in owners.items()
        ):
            raise ValueError("task_owner_by_id must map strings to strings")
        return cls(
            front_door_id=_required(payload, "front_door_id", str),
            floor_channel_id=_optional_string(payload, "floor_channel_id"),
            last_speaker_id=_optional_string(payload, "last_speaker_id"),
            task_owner_by_id=owners,
            updated_at=_read_timestamp(payload.get("updated_at"), "updated_at"),
        )


@dataclass(frozen=True)
class TaskReference:
    """A durable reference to task ownership, attempts, and external artifacts."""

    task_id: str
    channel_id: str
    agent_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    attempt_id: str | None = None
    artifact_refs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Require aware task lifecycle timestamps for direct construction."""
        _aware(self.created_at, "created_at")
        _aware(self.updated_at, "updated_at")

    def to_dict(self) -> dict[str, Any]:
        """Return the durable JSON representation."""
        return {
            "task_id": self.task_id,
            "channel_id": self.channel_id,
            "agent_id": self.agent_id,
            "status": self.status,
            "created_at": _timestamp(self.created_at),
            "updated_at": _timestamp(self.updated_at),
            "attempt_id": self.attempt_id,
            "artifact_refs": self.artifact_refs,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TaskReference:
        """Restore a task reference after validating its durable representation."""
        if not isinstance(payload, dict):
            raise ValueError("task reference payload must be an object")
        return cls(
            task_id=_required(payload, "task_id", str),
            channel_id=_required(payload, "channel_id", str),
            agent_id=_required(payload, "agent_id", str),
            status=_required(payload, "status", str),
            created_at=_read_timestamp(payload.get("created_at"), "created_at"),
            updated_at=_read_timestamp(payload.get("updated_at"), "updated_at"),
            attempt_id=_optional_string(payload, "attempt_id"),
            artifact_refs=_string_list(payload, "artifact_refs"),
        )

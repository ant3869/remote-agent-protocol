"""Atomic, schema-versioned persistence for durable conversation records."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .models import (
    AgentChannel,
    ConversationTurn,
    FloorState,
    ScopedMemory,
    SessionBinding,
    TaskReference,
)

SCHEMA_VERSION = 1
_SECRET_KEY_PARTS = ("token", "secret", "password", "api_key", "authorization")
_SECRET_ASSIGNMENT = re.compile(
    r"\b(?P<label>api_key|token|secret|password|hidden[ _]reasoning)\b"
    r"(?P<separator>\s*[:=]\s*)(?P<value>[^\s,;]+)",
    flags=re.IGNORECASE,
)
_AUTHORIZATION_HEADER = re.compile(
    r"\bauthorization\b(?P<separator>\s*:\s*)(?:bearer\s+)?(?P<value>[^\s,;]+)",
    flags=re.IGNORECASE,
)
_HIDDEN_REASONING_BLOCK = re.compile(
    r"<(?:analysis|thinking|reasoning)>.*?</(?:analysis|thinking|reasoning)>",
    flags=re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class ConversationLoadError:
    """A non-destructive load failure classified for recovery and diagnostics."""

    kind: Literal["unreadable", "newer_schema", "invalid_schema"]
    message: str


@dataclass(frozen=True)
class ConversationLoadResult:
    """The restored state, or an empty state accompanied by a classified error."""

    channels: list[AgentChannel] = field(default_factory=list)
    turns: list[ConversationTurn] = field(default_factory=list)
    memories: list[ScopedMemory] = field(default_factory=list)
    bindings: list[SessionBinding] = field(default_factory=list)
    task_references: list[TaskReference] = field(default_factory=list)
    floor_state: FloorState | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: ConversationLoadError | None = None


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if isinstance(key, str) and any(part in key.lower() for part in _SECRET_KEY_PARTS)
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _redact_text(value: str) -> str:
    """Remove secret values and hidden-reasoning blocks from persisted text."""
    value = _HIDDEN_REASONING_BLOCK.sub("[REDACTED]", value)
    value = _AUTHORIZATION_HEADER.sub(r"Authorization\g<separator>[REDACTED]", value)
    return _SECRET_ASSIGNMENT.sub(r"\g<label>\g<separator>[REDACTED]", value)


def _list_of_objects(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{key} must be a list of objects")
    return value


class ConversationStore:
    """Save and restore normalized conversation state without corrupting prior data."""

    def __init__(self, path: str | Path):
        """Bind the store to an application-owned JSON path."""
        self.path = Path(path)
        self.temp_path = self.path.with_name(f"{self.path.name}.tmp")

    def save(
        self,
        *,
        channels: list[AgentChannel],
        turns: list[ConversationTurn],
        memories: list[ScopedMemory],
        bindings: list[SessionBinding],
        task_references: list[TaskReference],
        floor_state: FloorState | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Write state to a temporary sibling file before atomically replacing it."""
        payload = _redact(
            {
                "schema_version": SCHEMA_VERSION,
                "channels": [channel.to_dict() for channel in channels],
                "turns": [turn.to_dict() for turn in turns],
                "memories": [memory.to_dict() for memory in memories],
                "bindings": [binding.to_dict() for binding in bindings],
                "task_references": [reference.to_dict() for reference in task_references],
                "floor_state": floor_state.to_dict() if floor_state else None,
                "metadata": metadata or {},
            }
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.temp_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
            )
            os.replace(self.temp_path, self.path)
        except OSError:
            raise

    def load(self) -> ConversationLoadResult:
        """Restore current-schema data without modifying unreadable or newer files."""
        if not self.path.exists():
            return ConversationLoadResult()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            return self._error("unreadable", f"Could not read conversation store: {exc}")
        if not isinstance(payload, dict):
            return self._error("invalid_schema", "Conversation store root must be an object")
        version = payload.get("schema_version")
        if not isinstance(version, int):
            return self._error(
                "invalid_schema", "Conversation store schema_version must be an integer"
            )
        if version > SCHEMA_VERSION:
            return self._error(
                "newer_schema", f"Conversation schema {version} is newer than {SCHEMA_VERSION}"
            )
        if version != SCHEMA_VERSION:
            return self._error("invalid_schema", f"Unsupported conversation schema {version}")
        try:
            floor_payload = payload.get("floor_state")
            metadata = payload.get("metadata", {})
            if floor_payload is not None and not isinstance(floor_payload, dict):
                raise ValueError("floor_state must be an object or null")
            if not isinstance(metadata, dict):
                raise ValueError("metadata must be an object")
            return ConversationLoadResult(
                channels=[
                    AgentChannel.from_dict(item) for item in _list_of_objects(payload, "channels")
                ],
                turns=[
                    ConversationTurn.from_dict(item) for item in _list_of_objects(payload, "turns")
                ],
                memories=[
                    ScopedMemory.from_dict(item) for item in _list_of_objects(payload, "memories")
                ],
                bindings=[
                    SessionBinding.from_dict(item, restored=True)
                    for item in _list_of_objects(payload, "bindings")
                ],
                task_references=[
                    TaskReference.from_dict(item)
                    for item in _list_of_objects(payload, "task_references")
                ],
                floor_state=FloorState.from_dict(floor_payload) if floor_payload else None,
                metadata=metadata,
            )
        except (TypeError, ValueError) as exc:
            return self._error("invalid_schema", f"Invalid conversation store: {exc}")

    @staticmethod
    def _error(
        kind: Literal["unreadable", "newer_schema", "invalid_schema"], message: str
    ) -> ConversationLoadResult:
        return ConversationLoadResult(error=ConversationLoadError(kind=kind, message=message))

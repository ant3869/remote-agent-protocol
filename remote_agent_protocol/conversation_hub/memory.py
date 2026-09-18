"""Scoped conversation memory, independent of the existing mem0 semantic store.

The hub persists ``snapshot()`` through ``ConversationStore`` alongside its other
state. This repository does not open mem0 or enable semantic writes in Brain
mode: those writes still require the full local session.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from remote_agent_protocol.memory_manager import fact_key

from .models import MemoryConfidence, MemoryScope, MemoryStatus, ScopedMemory

_PRIVATE_CONTENT = re.compile(
    r"(?:\b(?:[\w-]*(?:api[_ -]?key|token|secret|password|passwd|credential)[\w-]*"
    r"|authorization|hidden[ _]reasoning)\b\s*(?:[:=]|\bis\b)\s*\S+)"
    r"|\bBearer\s+\S+"
    r"|\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{16,}|AKIA[A-Z0-9]{16})\b"
    r"|-----BEGIN [\w ]*PRIVATE KEY-----"
    r"|<(?:analysis|thinking|reasoning)\b"
    r"|\b[a-z][a-z0-9+.-]*://[^\s/:]+:[^\s/@]+@",
    re.IGNORECASE,
)


def safe_context_text(text: str) -> bool:
    """Reject recognizable credentials and hidden reasoning before storage/use.

    Unlabelled arbitrary secrets cannot be identified from text alone; callers
    must also mark secret-bearing or raw-tool candidates at their source.
    """
    return not _PRIVATE_CONTENT.search(text)


class PromotionReason(StrEnum):
    """Evidence classifications supplied by the trusted memory-admission caller."""

    STABLE_USER_FACT = "stable_user_fact"
    PROJECT_DECISION = "project_decision"
    VERIFIED_RESULT = "verified_result"


class MemoryPromotionPolicy:
    """Validate scope, provenance and evidence without inferring facts from prose."""

    def validate(
        self,
        memory: ScopedMemory,
        *,
        reason: PromotionReason | None = None,
        raw_tool_output: bool = False,
        contains_secret: bool = False,
        restored: bool = False,
    ) -> None:
        """Reject inadmissible candidates; restored snapshots were already promoted."""
        if raw_tool_output or contains_secret:
            raise ValueError("Secrets and raw tool output cannot become memories")
        if not all(safe_context_text(s) for s in (memory.subject, memory.value)):
            raise ValueError("Memory contains secret or hidden reasoning content")
        if not memory.memory_id or not memory.source_turn_ids or not all(memory.source_turn_ids):
            raise ValueError("Memory requires an identity and source-turn provenance")
        if memory.status is MemoryStatus.FORGOTTEN:
            if memory.value or memory.subject:
                raise ValueError("Forgotten memories must contain no value or subject")
            return
        if not memory.subject.strip() or not memory.value.strip():
            raise ValueError("Memory subject and value must be non-empty")
        required_id = {
            MemoryScope.CHANNEL: memory.channel_id,
            MemoryScope.TASK: memory.task_id,
            MemoryScope.PROJECT: memory.project_id,
            MemoryScope.SHARED: True,
        }[memory.scope]
        if not required_id:
            raise ValueError("Memory scope requires its channel, task or project identity")
        if (
            memory.confidence is MemoryConfidence.INFERRED
            and memory.scope is not MemoryScope.CHANNEL
        ):
            raise ValueError("Inferred memories must remain channel-local")
        if memory.scope in (MemoryScope.SHARED, MemoryScope.PROJECT) and not restored:
            valid = (
                reason in (PromotionReason.STABLE_USER_FACT, PromotionReason.PROJECT_DECISION)
                and memory.confidence is MemoryConfidence.USER_STATED
            ) or (
                reason is PromotionReason.VERIFIED_RESULT
                and memory.confidence is MemoryConfidence.VERIFIED
            )
            if not valid:
                raise ValueError("Shared/project promotion requires qualified evidence")


def _copy(memory: ScopedMemory) -> ScopedMemory:
    return replace(memory, source_turn_ids=list(memory.source_turn_ids))


def _boundary(memory: ScopedMemory) -> tuple:
    identity = {
        MemoryScope.CHANNEL: memory.channel_id,
        MemoryScope.SHARED: None,
        MemoryScope.TASK: memory.task_id,
        MemoryScope.PROJECT: memory.project_id,
    }[memory.scope]
    return memory.scope, identity


class MemoryRepository:
    """Maintain scoped records; persistence is owned by the conversation hub.

    Constructor records must come from the trusted ConversationStore snapshot.
    New writes always use ``add`` and its promotion policy. Returned records
    cannot mutate repository provenance through the model's mutable list field.
    """

    def __init__(
        self, memories: Iterable[ScopedMemory] = (), policy: MemoryPromotionPolicy | None = None
    ) -> None:
        """Restore a trusted store snapshot without opening a second persistence backend."""
        self.policy = policy or MemoryPromotionPolicy()
        self._memories: dict[str, ScopedMemory] = {}
        for memory in memories:
            self.policy.validate(memory, restored=True)
            if memory.memory_id in self._memories:
                raise ValueError("Duplicate memory identity in snapshot")
            self._memories[memory.memory_id] = _copy(memory)

    def get(self, memory_id: str) -> ScopedMemory:
        """Return a detached record, including superseded records and tombstones."""
        return _copy(self._memories[memory_id])

    def snapshot(self) -> list[ScopedMemory]:
        """Return deterministic detached records for ConversationStore.save."""
        return [self.get(key) for key in sorted(self._memories)]

    def add(
        self,
        memory: ScopedMemory,
        *,
        reason: PromotionReason | None = None,
        raw_tool_output: bool = False,
        contains_secret: bool = False,
    ) -> ScopedMemory:
        """Admit a sourced candidate, deduplicating facts within the same boundary."""
        self.policy.validate(
            memory, reason=reason, raw_tool_output=raw_tool_output, contains_secret=contains_secret
        )
        if memory.status is not MemoryStatus.ACTIVE or memory.supersedes is not None:
            raise ValueError("Use correct/forget for memory lifecycle changes")
        if memory.memory_id in self._memories:
            raise ValueError("Memory identity already exists")
        for previous in self.snapshot():
            if (
                previous.status is MemoryStatus.ACTIVE
                and _boundary(previous) == _boundary(memory)
                and previous.confidence is memory.confidence
                and fact_key(previous.subject) == fact_key(memory.subject)
                and fact_key(previous.value) == fact_key(memory.value)
            ):
                merged = replace(
                    previous,
                    source_turn_ids=list(
                        dict.fromkeys(previous.source_turn_ids + memory.source_turn_ids)
                    ),
                )
                self._memories[previous.memory_id] = merged
                return _copy(merged)
        self._memories[memory.memory_id] = _copy(memory)
        return _copy(memory)

    def correct(
        self, memory_id: str, value: str, source_turn_id: str, now: datetime
    ) -> ScopedMemory:
        """Apply an explicit user correction, preserving the old value and provenance."""
        previous = self.get(memory_id)
        if previous.status is not MemoryStatus.ACTIVE:
            raise ValueError("Only an active memory can be corrected")
        corrected = replace(
            previous,
            memory_id=f"memory_{uuid4().hex}",
            value=value,
            source_turn_ids=[source_turn_id],
            observed_at=now,
            supersedes=memory_id,
            confidence=MemoryConfidence.USER_STATED,
        )
        self.policy.validate(corrected, reason=PromotionReason.STABLE_USER_FACT)
        self._memories[memory_id] = replace(previous, status=MemoryStatus.SUPERSEDED)
        self._memories[corrected.memory_id] = corrected
        return _copy(corrected)

    def forget(self, memory_id: str) -> ScopedMemory:
        """Scrub a fact's correction chain to value-free provenance tombstones.

        This deliberately leaves transcript deletion to its separate explicit
        action. No forgotten value is retained in an auxiliary lookup index.
        """
        self.get(memory_id)
        family = {memory_id}
        while True:
            related = {
                m.memory_id
                for m in self._memories.values()
                if m.supersedes in family or m.memory_id in family
            }
            related.update(
                m.supersedes
                for m in self._memories.values()
                if m.memory_id in family and m.supersedes in self._memories
            )
            if related == family:
                break
            family = related
        for key in family:
            self._memories[key] = replace(
                self._memories[key], value="", subject="", status=MemoryStatus.FORGOTTEN
            )
        return self.get(memory_id)

    def eligible(
        self,
        *,
        channel_id: str,
        task_id: str | None = None,
        project_id: str | None = None,
        for_access: bool = False,
    ) -> list[ScopedMemory]:
        """Select active matching scopes; access claims require verified evidence.

        Observed timestamps remain attached. Live control-plane freshness checks
        must still decide whether verified historical evidence is usable now.
        """
        result = []
        for memory in self.snapshot():
            if memory.status is not MemoryStatus.ACTIVE:
                continue
            if for_access and memory.confidence is not MemoryConfidence.VERIFIED:
                continue
            if (
                (memory.scope is MemoryScope.CHANNEL and memory.channel_id == channel_id)
                or memory.scope is MemoryScope.SHARED
                or (memory.scope is MemoryScope.TASK and task_id and memory.task_id == task_id)
                or (
                    memory.scope is MemoryScope.PROJECT
                    and project_id
                    and memory.project_id == project_id
                )
            ):
                result.append(memory)
        order = {
            MemoryScope.TASK: 0,
            MemoryScope.CHANNEL: 1,
            MemoryScope.SHARED: 2,
            MemoryScope.PROJECT: 3,
        }
        return sorted(result, key=lambda m: (order[m.scope], m.observed_at, m.memory_id))

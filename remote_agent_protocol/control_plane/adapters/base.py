"""Capability-driven boundary between RAP and each harness."""
# ruff: noqa: D102

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ...conversation_hub.context import ContextPackage
from ...conversation_hub.models import SessionBinding, SessionStrategy
from ..models import AgentObservation, ControlResult, JobHandle, LaunchResult, ObservedWork


@dataclass(frozen=True, slots=True)
class AgentTask:
    """A task already resolved by RAP routing, never raw voice-shell input."""

    text: str
    cwd: str | None = None
    announce_start: bool = False


class AgentAdapter(Protocol):
    """A safe, bounded adapter for one configured harness."""

    agent_id: str

    async def discover(self) -> AgentObservation: ...

    async def probe(self) -> AgentObservation: ...

    async def launch(self) -> LaunchResult: ...

    async def inspect_jobs(self) -> tuple[ObservedWork, ...]: ...

    async def dispatch(self, task: AgentTask) -> JobHandle | ControlResult: ...

    async def cancel(self, job_id: str) -> ControlResult: ...


class ConversationSessionAdapter(Protocol):
    """An additive channel-session contract, independent of control-plane capabilities."""

    agent_id: str

    @property
    def conversation_session_strategy(self) -> SessionStrategy: ...

    async def validate_bound_session(self, binding: SessionBinding) -> bool: ...

    async def create_bound_session(self, channel_id: str) -> SessionBinding: ...

    async def dispatch_in_session(
        self, binding: SessionBinding, context: ContextPackage
    ) -> JobHandle: ...

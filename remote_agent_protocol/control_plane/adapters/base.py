"""Capability-driven boundary between RAP and each harness."""
# ruff: noqa: D102

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

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

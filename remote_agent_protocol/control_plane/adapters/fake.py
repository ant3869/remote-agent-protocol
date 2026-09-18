"""Deterministic adapter used by unit and contract tests."""
# ruff: noqa: D102, D107

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..models import (
    AgentObservation,
    ControlError,
    ControlResult,
    JobHandle,
    LaunchResult,
    ObservedWork,
)
from .base import AgentTask

Outcome = (
    AgentObservation
    | LaunchResult
    | tuple[ObservedWork, ...]
    | JobHandle
    | ControlResult
    | Exception
)


class FakeAgentAdapter:
    """Scripts each operation independently and records its exact call order."""

    def __init__(self, agent_id: str, **outcomes: Outcome | Callable[[], Awaitable[Outcome]]):
        self.agent_id = agent_id
        self.outcomes = outcomes
        self.calls: list[str] = []
        self.tasks: list[AgentTask] = []

    async def _run(self, name: str, default: Outcome) -> Outcome:
        self.calls.append(name)
        outcome = self.outcomes.get(name, default)
        if callable(outcome):
            outcome = await outcome()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def discover(self) -> AgentObservation:
        return await self._run("discover", self.outcomes.get("probe"))  # type: ignore[return-value]

    async def probe(self) -> AgentObservation:
        return await self._run("probe", self.outcomes.get("discover"))  # type: ignore[return-value]

    async def launch(self) -> LaunchResult:
        return await self._run(
            "launch",
            LaunchResult(self.agent_id, False, error=ControlError("unsupported", "unsupported")),
        )  # type: ignore[return-value]

    async def inspect_jobs(self) -> tuple[ObservedWork, ...]:
        return await self._run("inspect_jobs", ())  # type: ignore[return-value]

    async def dispatch(self, task: AgentTask) -> JobHandle | ControlResult:
        self.tasks.append(task)
        return await self._run(
            "dispatch",
            ControlResult(False, self.agent_id, error=ControlError("unsupported", "unsupported")),
        )  # type: ignore[return-value]

    async def cancel(self, job_id: str) -> ControlResult:
        return await self._run(
            "cancel",
            ControlResult(False, self.agent_id, job_id, ControlError("unsupported", "unsupported")),
        )  # type: ignore[return-value]

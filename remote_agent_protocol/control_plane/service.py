"""Coordinator operations above adapters and the existing AgentBridge."""
# ruff: noqa: D102, D107

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from .adapters.base import AgentAdapter, AgentTask
from .events import (
    CANCELLATION_COMPLETED,
    CANCELLATION_REQUESTED,
    JOB_DISPATCHED,
    JOB_PROGRESS_CHANGED,
    LAUNCH_FAILED,
    LAUNCH_READY,
    LAUNCH_STARTED,
    PROBE_FAILED,
    PROBE_STARTED,
    PROBE_SUCCEEDED,
    REDIRECTION_COMPLETED,
    STATUS_CHANGED,
    ControlPlaneEvent,
)
from .models import (
    Activity,
    AgentCapability,
    AgentObservation,
    AgentSnapshot,
    ControlError,
    ControlResult,
    Evidence,
    Health,
    JobHandle,
    ObservedWork,
    Presence,
    WorkOwnership,
)
from .registry import AgentRegistry

EventListener = Callable[[dict[str, Any]], None]


class AgentControlPlane:
    """Coordinates evidence-backed status and safe per-agent operations."""

    def __init__(
        self,
        adapters: Mapping[str, AgentAdapter],
        *,
        registry: AgentRegistry | None = None,
        on_event: EventListener | None = None,
        probe_timeout_secs: float = 4.0,
        overall_timeout_secs: float = 8.0,
        freshness_secs: float = 45.0,
    ):
        self._adapters = dict(adapters)
        self.registry = registry or AgentRegistry()
        self._on_event = on_event
        self._probe_timeout_secs = probe_timeout_secs
        self._overall_timeout_secs = overall_timeout_secs
        self._freshness_secs = freshness_secs
        self._refresh_tasks: dict[str, asyncio.Task[AgentSnapshot | ControlError]] = {}
        self._jobs: dict[str, ObservedWork] = {}
        self._job_agents: dict[str, str] = {}

    @property
    def agent_ids(self) -> tuple[str, ...]:
        return tuple(self._adapters)

    def _emit(self, event: str, agent_id: str, **kwargs: Any) -> None:
        if self._on_event is not None:
            self._on_event(ControlPlaneEvent(event, agent_id, **kwargs).to_payload())

    async def list_agents(self, refresh: bool = False) -> dict[str, AgentSnapshot | ControlError]:
        """Return all configured agents, optionally actively probing them concurrently."""
        if not refresh:
            return {
                agent_id: (await self.registry.get(agent_id))
                or self._unknown(agent_id, "No observation has been recorded yet.")
                for agent_id in self._adapters
            }
        tasks = {
            agent_id: asyncio.create_task(self._refresh(agent_id), name=f"agent-refresh-{agent_id}")
            for agent_id in self._adapters
        }
        done, pending = await asyncio.wait(
            tasks.values(), timeout=self._overall_timeout_secs, return_when=asyncio.ALL_COMPLETED
        )
        for task in pending:
            task.cancel()
        results: dict[str, AgentSnapshot | ControlError] = {}
        for agent_id, task in tasks.items():
            if task in pending:
                results[agent_id] = await self._record_error(
                    agent_id, "timeout", "Probe timed out."
                )
            else:
                results[agent_id] = task.result()
        return results

    async def get_agent_status(
        self, agent_id: str, refresh: bool = False
    ) -> AgentSnapshot | ControlError:
        if agent_id not in self._adapters:
            return ControlError("unknown_agent", "That agent is not configured.", agent_id)
        if refresh:
            return await self._refresh(agent_id)
        return (await self.registry.get(agent_id)) or self._unknown(
            agent_id, "No observation has been recorded yet."
        )

    async def _refresh(self, agent_id: str) -> AgentSnapshot | ControlError:
        existing = self._refresh_tasks.get(agent_id)
        if existing is not None and not existing.done():
            return await existing
        task = asyncio.create_task(self._probe(agent_id), name=f"agent-probe-{agent_id}")
        self._refresh_tasks[agent_id] = task
        try:
            return await task
        finally:
            if self._refresh_tasks.get(agent_id) is task:
                self._refresh_tasks.pop(agent_id, None)

    async def _probe(self, agent_id: str) -> AgentSnapshot | ControlError:
        self._emit(PROBE_STARTED, agent_id, detail=f"Contacting {agent_id}.")
        try:
            observation = await asyncio.wait_for(
                self._adapters[agent_id].probe(), timeout=self._probe_timeout_secs
            )
        except TimeoutError:
            return await self._record_error(agent_id, "timeout", "Probe timed out.")
        except Exception as exc:
            return await self._record_error(agent_id, "probe_failed", str(exc))
        before = await self.registry.get(agent_id)
        snapshot = await self.registry.observe(observation)
        self._emit(PROBE_SUCCEEDED, agent_id, detail="Adapter returned current evidence.")
        if before is None or before.observation != snapshot.observation or before.stale:
            self._emit(STATUS_CHANGED, agent_id, data={"snapshot": snapshot.to_dict()})
        return snapshot

    async def _record_error(self, agent_id: str, code: str, detail: str) -> ControlError:
        now = datetime.now(UTC)
        existing = await self.registry.get(agent_id)
        if existing is not None:
            # Preserve prior useful evidence but make the inability to refresh explicit.
            observation = replace(
                existing.observation,
                presence=Presence.UNKNOWN,
                activity=Activity.UNKNOWN,
                evidence=(
                    Evidence("control_plane", now, f"{code}: {detail}"),
                    *existing.observation.evidence[:2],
                ),
                observed_at=now,
                expires_at=now + timedelta(seconds=self._freshness_secs),
                issues=(*existing.observation.issues, detail),
            )
            await self.registry.observe(observation)
        self._emit(PROBE_FAILED, agent_id, detail=detail, data={"code": code})
        return ControlError(code, detail, agent_id, retryable=code == "timeout")

    def _unknown(self, agent_id: str, detail: str) -> AgentSnapshot:
        now = datetime.now(UTC)
        return AgentSnapshot(
            AgentObservation(
                agent_id=agent_id,
                display_name=agent_id,
                harness=agent_id,
                machine="unknown",
                presence=Presence.UNKNOWN,
                activity=Activity.UNKNOWN,
                health=Health.UNKNOWN,
                capabilities=frozenset(),
                evidence=(Evidence("control_plane", now, detail),),
                observed_at=now,
                expires_at=now,
            ),
            stale=True,
        )

    async def get_agent_jobs(self, agent_id: str) -> tuple[ObservedWork, ...] | ControlError:
        adapter = self._adapters.get(agent_id)
        if adapter is None:
            return ControlError("unknown_agent", "That agent is not configured.", agent_id)
        try:
            return await adapter.inspect_jobs()
        except Exception as exc:
            return ControlError("inspection_failed", str(exc), agent_id)

    async def dispatch_task(self, agent_id: str, task: AgentTask) -> JobHandle | ControlResult:
        adapter = self._adapters.get(agent_id)
        if adapter is None:
            return ControlResult(
                False,
                agent_id,
                error=ControlError("unknown_agent", "That agent is not configured."),
            )
        snapshot = await self.get_agent_status(agent_id, refresh=False)
        if (
            isinstance(snapshot, AgentSnapshot)
            and snapshot.observation.presence == Presence.STOPPED
        ):
            self._emit(LAUNCH_STARTED, agent_id)
            launched = await adapter.launch()
            if not launched.ready:
                self._emit(
                    LAUNCH_FAILED,
                    agent_id,
                    detail=launched.error.detail if launched.error else "Launch failed.",
                )
                return ControlResult(False, agent_id, error=launched.error)
            if launched.observation is not None:
                await self.registry.observe(launched.observation)
            self._emit(LAUNCH_READY, agent_id)
        result = await adapter.dispatch(task)
        if isinstance(result, JobHandle):
            self._emit(JOB_DISPATCHED, agent_id, job_id=result.job_id)
        return result

    async def cancel_job(self, job_id: str, *, external_confirmed: bool = False) -> ControlResult:
        work = self._jobs.get(job_id)
        if work is None:
            return ControlResult(
                False,
                "",
                job_id,
                ControlError("unknown_job", "No active job with that id was found."),
            )
        if work.ownership == WorkOwnership.EXTERNAL and not external_confirmed:
            return ControlResult(
                False,
                "",
                job_id,
                ControlError("confirmation_required", "External work requires confirmation."),
            )
        agent_id = self._job_agents.get(job_id, "")
        adapter = self._adapters.get(agent_id)
        if adapter is None:
            return ControlResult(
                False, agent_id, job_id, ControlError("unknown_agent", "Job owner is unavailable.")
            )
        self._emit(CANCELLATION_REQUESTED, agent_id, job_id=job_id)
        result = await adapter.cancel(job_id)
        self._emit(CANCELLATION_COMPLETED, agent_id, job_id=job_id, data={"ok": result.ok})
        return result

    async def redirect_job(
        self, job_id: str, new_agent_id: str, task: AgentTask
    ) -> JobHandle | ControlResult:
        cancelled = await self.cancel_job(job_id)
        if not cancelled.ok:
            return cancelled
        result = await self.dispatch_task(new_agent_id, task)
        if isinstance(result, JobHandle):
            self._emit(
                REDIRECTION_COMPLETED,
                new_agent_id,
                job_id=result.job_id,
                data={"from_job_id": job_id},
            )
        return result

    async def ingest_bridge_event(self, event: dict[str, Any]) -> None:
        """Translate a RAP-owned lifecycle event into a fresh observation."""
        if event.get("type") != "agent_job" or not event.get("agent"):
            return
        agent_id = str(event["agent"])
        if agent_id not in self._adapters:
            return
        now = datetime.now(UTC)
        status = str(event.get("status", ""))
        activity = {
            "running": Activity.WORKING,
            "waiting": Activity.WAITING,
            "blocked": Activity.BLOCKED,
        }.get(status, Activity.IDLE)
        job_id = str(event.get("job_id", ""))
        work = None
        if status in {"running", "waiting", "blocked"} and job_id:
            work = ObservedWork(
                job_id=job_id,
                ownership=WorkOwnership.RAP,
                summary=str(event.get("action") or event.get("task") or ""),
                started_at=_event_time(event.get("started_at")),
                last_activity_at=now,
                completed=_as_int(event.get("step")),
                total=_as_int(event.get("step_total")),
                state=activity,
            )
            self._jobs[job_id] = work
            self._job_agents[job_id] = agent_id
        elif job_id:
            self._jobs.pop(job_id, None)
            self._job_agents.pop(job_id, None)
        previous = await self.registry.get(agent_id)
        previous_observation = previous.observation if previous else None
        failure_kind = str(event.get("failure_kind") or "")
        health = (
            Health.DEGRADED
            if failure_kind in {"quota", "rate_limit", "authentication"}
            else Health.HEALTHY
        )
        observation = AgentObservation(
            agent_id=agent_id,
            display_name=previous_observation.display_name if previous_observation else agent_id,
            harness=previous_observation.harness if previous_observation else agent_id,
            machine=str(
                event.get("machine")
                or (previous_observation.machine if previous_observation else "local")
            ),
            presence=Presence.REACHABLE,
            activity=activity,
            health=health,
            capabilities=previous_observation.capabilities
            if previous_observation
            else frozenset(
                {
                    AgentCapability.ACCEPT_TASK,
                    AgentCapability.CANCEL_RAP_JOB,
                    AgentCapability.REPORT_PROGRESS,
                }
            ),
            evidence=(
                Evidence(
                    "agent_bridge",
                    now,
                    str(event.get("summary") or event.get("event") or "job update"),
                ),
            ),
            observed_at=now,
            expires_at=now + timedelta(seconds=self._freshness_secs),
            current_work=work,
            issues=(str(event.get("failure_detail") or ""),) if failure_kind else (),
        )
        await self.registry.observe(observation)
        self._emit(JOB_PROGRESS_CHANGED, agent_id, job_id=job_id, data={"activity": activity.value})


def _event_time(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw))
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    except ValueError:
        return None


def _as_int(raw: Any) -> int | None:
    return raw if isinstance(raw, int) and raw >= 0 else None

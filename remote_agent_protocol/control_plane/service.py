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
    ResponseState,
    UpdateState,
    WorkOwnership,
)
from .registry import AgentRegistry

EventListener = Callable[[dict[str, Any]], None]

# This is intentionally a fixed, harmless request. It proves that the selected
# harness can return a response to RAP; it is never a request for one harness
# to inspect another one or to do useful work on the user's behalf.
SELF_CHECK_SENTINEL = "RAP_SELF_CHECK_OK"
SELF_CHECK_PROMPT = (
    "RAP self-check. Reply with exactly RAP_SELF_CHECK_OK and nothing else. "
    "Do not inspect or write files, run commands, access tools, or delegate."
)


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
        response_check_timeout_secs: float = 45.0,
    ):
        self._adapters = dict(adapters)
        self.registry = registry or AgentRegistry()
        self._on_event = on_event
        self._probe_timeout_secs = probe_timeout_secs
        self._overall_timeout_secs = overall_timeout_secs
        self._freshness_secs = freshness_secs
        self._response_check_timeout_secs = response_check_timeout_secs
        self._refresh_tasks: dict[str, asyncio.Task[AgentSnapshot | ControlError]] = {}
        self._jobs: dict[str, ObservedWork] = {}
        self._job_agents: dict[str, str] = {}
        self._self_checks: dict[str, str] = {}
        self._self_check_agents: set[str] = set()
        self._response_check_waiters: dict[str, asyncio.Future[ResponseState]] = {}

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
        if not tasks:
            return {}
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

    async def request_response_check(self, agent_id: str) -> JobHandle | ControlResult:
        """Ask one idle harness for a fixed response through ``AgentBridge``.

        Installation discovery does not establish readiness. This check is
        deliberately separate and its terminal lifecycle event is the sole
        source of ``response_state`` evidence.
        """
        adapter = self._adapters.get(agent_id)
        if adapter is None:
            return ControlResult(
                False,
                agent_id,
                error=ControlError("unknown_agent", "That agent is not configured.", agent_id),
            )
        # Reserve synchronously before the first await. Two simultaneous callers
        # otherwise both pass the fresh/busy checks and launch duplicate pings.
        if agent_id in self._self_check_agents:
            return ControlResult(
                False,
                agent_id,
                error=ControlError(
                    "self_check_active",
                    "A response self-check is already active for this agent.",
                    agent_id,
                    retryable=True,
                ),
            )
        self._self_check_agents.add(agent_id)
        keep_reservation = False
        reserved_job_id = ""
        try:
            snapshot = await self.get_agent_status(agent_id, refresh=True)
            if isinstance(snapshot, ControlError):
                return ControlResult(False, agent_id, error=snapshot)
            observation = snapshot.observation
            if observation.presence in {Presence.STOPPED, Presence.UNREACHABLE}:
                return ControlResult(
                    False,
                    agent_id,
                    error=ControlError(
                        "unreachable",
                        "The harness is unavailable; no response check was sent.",
                        agent_id,
                    ),
                )
            if observation.current_work is not None:
                return ControlResult(
                    False,
                    agent_id,
                    error=ControlError(
                        "busy",
                        "A RAP-owned job is already active; no concurrent response check was sent.",
                        agent_id,
                        retryable=True,
                    ),
                )
            try:
                dispatched = await adapter.dispatch(
                    AgentTask(
                        SELF_CHECK_PROMPT,
                        announce_start=False,
                        clean_session=True,
                        internal=True,
                    )
                )
            except Exception as exc:
                error = ControlError("dispatch_failed", str(exc), agent_id, retryable=True)
                await self._record_response_result(
                    agent_id,
                    ResponseState.FAILED,
                    "The response check could not be started.",
                    failure_kind=error.code,
                )
                return ControlResult(False, agent_id, error=error)
            if isinstance(dispatched, ControlResult):
                await self._record_response_result(
                    agent_id,
                    ResponseState.FAILED,
                    "The response check could not be started.",
                    failure_kind=dispatched.error.code if dispatched.error else "dispatch_failed",
                )
                return dispatched
            self._self_checks[dispatched.job_id] = agent_id
            self._response_check_waiters[dispatched.job_id] = (
                asyncio.get_running_loop().create_future()
            )
            reserved_job_id = dispatched.job_id
            now = datetime.now(UTC)
            await self._record_response_result(
                agent_id,
                ResponseState.PENDING,
                "RAP sent a fixed self-check and is waiting for its exact response.",
                current_work=ObservedWork(
                    job_id=dispatched.job_id,
                    ownership=WorkOwnership.RAP,
                    summary="RAP self-check awaiting exact response",
                    started_at=now,
                    last_activity_at=now,
                    state=Activity.WORKING,
                ),
            )
            keep_reservation = True
            return dispatched
        finally:
            if not keep_reservation:
                if reserved_job_id:
                    self._self_checks.pop(reserved_job_id, None)
                self._self_check_agents.discard(agent_id)

    async def wait_for_response_check(self, job_id: str) -> ResponseState | ControlError | None:
        """Wait a bounded interval for one fixed-response check to finish.

        ``None`` means the caller supplied a non-live test/double handle. A
        timeout is explicit evidence, not a claim that the harness replied.
        """
        waiter = self._response_check_waiters.get(job_id)
        if waiter is None:
            return None
        try:
            return await asyncio.wait_for(asyncio.shield(waiter), self._response_check_timeout_secs)
        except TimeoutError:
            return ControlError(
                "response_timeout",
                "The fixed response check did not finish before its timeout.",
                self._self_checks.get(job_id, ""),
                retryable=True,
            )
        finally:
            # This is the sole real consumer of a dispatched waiter (every
            # JobHandle path in _append_response_check_rows calls this
            # exactly once), so once it has been claimed -- resolved or
            # timed out -- nothing else needs the entry. Popped here rather
            # than in _complete_response_check, which can otherwise race
            # ahead of this call and discard the result before it is read.
            self._response_check_waiters.pop(job_id, None)

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
        self_check_agent = self._self_checks.get(job_id)
        if self_check_agent is not None and self_check_agent == agent_id:
            await self._ingest_response_check_event(event)
            return
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
        response_state, response_observed_at, response_secs, response_model = (
            _job_response_evidence(event, status, failure_kind, now, previous_observation)
        )
        health = (
            Health.DEGRADED
            if failure_kind in _DEGRADED_FAILURE_KINDS
            else Health.FAILED
            if status == "failed"
            else Health.HEALTHY
            if status == "done"
            else previous_observation.health
            if previous_observation
            else Health.UNKNOWN
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
            issues=(failure_kind,) if failure_kind else (),
            response_state=response_state,
            response_observed_at=response_observed_at,
            update_state=previous_observation.update_state
            if previous_observation
            else UpdateState.UNKNOWN,
            response_secs=response_secs,
            response_model=response_model,
        )
        await self.registry.observe(observation)
        self._emit(JOB_PROGRESS_CHANGED, agent_id, job_id=job_id, data={"activity": activity.value})

    async def _ingest_response_check_event(self, event: dict[str, Any]) -> None:
        """Turn the fixed self-check lifecycle into evidence without storing output."""
        agent_id = str(event["agent"])
        job_id = str(event.get("job_id", ""))
        status = str(event.get("status", ""))
        if status in {"running", "waiting", "blocked"}:
            await self._record_response_result(
                agent_id,
                ResponseState.PENDING,
                "RAP self-check is still waiting for the exact response.",
                current_work=ObservedWork(
                    job_id=job_id,
                    ownership=WorkOwnership.RAP,
                    summary="RAP self-check awaiting exact response",
                    started_at=_event_time(event.get("started_at")),
                    last_activity_at=datetime.now(UTC),
                    state={
                        "running": Activity.WORKING,
                        "waiting": Activity.WAITING,
                        "blocked": Activity.BLOCKED,
                    }[status],
                ),
            )
            return
        self._self_checks.pop(job_id, None)
        self._self_check_agents.discard(agent_id)
        response = str(event.get("result") or "").strip()
        failure_kind = str(event.get("failure_kind") or "")
        if status == "done" and response == SELF_CHECK_SENTINEL:
            await self._record_response_result(
                agent_id,
                ResponseState.RESPONDED,
                "RAP received the exact fixed response from this harness.",
                response_secs=_as_secs(event.get("elapsed_secs")),
                response_model=_event_model(event),
            )
            self._complete_response_check(job_id, ResponseState.RESPONDED)
            return
        reason = _response_failure_reason(failure_kind)
        await self._record_response_result(
            agent_id,
            ResponseState.FAILED,
            reason,
            failure_kind=failure_kind or "unexpected_response",
        )
        self._complete_response_check(job_id, ResponseState.FAILED)

    def _complete_response_check(self, job_id: str, state: ResponseState) -> None:
        """Release any diagnostic turn waiting for this exact terminal result.

        Does not pop the dict entry: a bridge event can arrive before
        wait_for_response_check has been called for this job_id (a fast
        harness, or the test double timing this exercises), and that call
        must still find the already-resolved future. wait_for_response_check
        itself pops once it has claimed the result.
        """
        waiter = self._response_check_waiters.get(job_id)
        if waiter is not None and not waiter.done():
            waiter.set_result(state)

    async def _record_response_result(
        self,
        agent_id: str,
        response_state: ResponseState,
        detail: str,
        *,
        current_work: ObservedWork | None = None,
        failure_kind: str = "",
        response_secs: float | None = None,
        response_model: str = "",
    ) -> None:
        """Persist a bounded self-check fact while retaining discovery evidence."""
        now = datetime.now(UTC)
        previous = await self.registry.get(agent_id)
        base = (
            previous.observation
            if previous
            else self._unknown(agent_id, "No observation.").observation
        )
        health = (
            Health.DEGRADED
            if failure_kind in _DEGRADED_FAILURE_KINDS
            else Health.FAILED
            if response_state is ResponseState.FAILED
            else Health.HEALTHY
            if response_state is ResponseState.RESPONDED
            else base.health
        )
        observation = replace(
            base,
            activity=current_work.state if current_work else Activity.IDLE,
            health=health,
            evidence=(Evidence("rap_response_check", now, detail), *base.evidence[:2]),
            observed_at=now,
            expires_at=now + timedelta(seconds=self._freshness_secs),
            current_work=current_work,
            issues=(*base.issues, failure_kind) if failure_kind else base.issues,
            response_state=response_state,
            response_observed_at=now,
            response_secs=response_secs
            if response_state is ResponseState.RESPONDED
            else base.response_secs,
            response_model=response_model
            if response_state is ResponseState.RESPONDED
            else base.response_model,
        )
        await self.registry.observe(observation)
        self._emit(
            STATUS_CHANGED, agent_id, data={"snapshot": AgentSnapshot(observation).to_dict()}
        )


_DEGRADED_FAILURE_KINDS = frozenset({"quota", "rate_limit", "capacity", "auth", "authentication"})
# A real job that fails for one of these says the harness's model can't
# answer right now -- the same thing a failed self-check would say.
_RESPONSE_FAILURE_KINDS = _DEGRADED_FAILURE_KINDS | {"model_not_found"}


def _job_response_evidence(
    event: dict[str, Any],
    status: str,
    failure_kind: str,
    now: datetime,
    previous: AgentObservation | None,
) -> tuple[ResponseState, datetime | None, float | None, str]:
    """What a finished RAP job proves about whether the harness answers.

    A job that completed is stronger evidence than any self-check; one that
    failed on quota, auth, or model errors is a failed response. Anything
    else (still running, cancelled, a task-level failure) leaves the last
    response evidence as it was.
    """
    if status == "done":
        return (
            ResponseState.RESPONDED,
            now,
            _as_secs(event.get("elapsed_secs")),
            _event_model(event),
        )
    if status == "failed" and failure_kind in _RESPONSE_FAILURE_KINDS:
        return ResponseState.FAILED, now, None, ""
    if previous is None:
        return ResponseState.UNKNOWN, None, None, ""
    return (
        previous.response_state,
        previous.response_observed_at,
        previous.response_secs,
        previous.response_model,
    )


def _event_model(event: dict[str, Any]) -> str:
    return str(event.get("answered_model") or event.get("model_label") or "")


def _as_secs(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
        return None
    return round(float(raw), 1)


def _response_failure_reason(failure_kind: str) -> str:
    if failure_kind in _DEGRADED_FAILURE_KINDS:
        return f"The self-check failed with {failure_kind}; no actual response was confirmed."
    return (
        "The self-check ended without the exact fixed response; no actual response was confirmed."
    )


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

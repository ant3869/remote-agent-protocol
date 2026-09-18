"""Safe adapter base for RAP's configured one-shot command-line harnesses."""

from __future__ import annotations

import asyncio
import re
import shutil
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from ...conversation_hub.context import ContextPackage
from ...conversation_hub.models import SessionBinding, SessionStrategy
from ..models import (
    Activity,
    AgentCapability,
    AgentObservation,
    ControlError,
    ControlResult,
    Evidence,
    Health,
    JobHandle,
    LaunchResult,
    ObservedWork,
    Presence,
    UpdateState,
    WorkOwnership,
)
from .base import AgentTask

_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


class BridgeCliAdapter:
    """Probes a configured CLI and delegates execution to ``AgentBridge``.

    These harnesses are one-shot CLIs, not daemon processes.  A successful
    ``--version`` therefore proves contactability, not external-session
    activity.  RAP-owned work is read from the bridge's structured lifecycle.
    """

    executable: str
    conversation_session_strategy = SessionStrategy.REHYDRATE

    def __init__(
        self,
        agent_id: str,
        bridge: Any,
        *,
        display_name: str,
        machine: str,
        probe_timeout_secs: float = 3.0,
        freshness_secs: float = 45.0,
    ):
        """Initialize identity, bridge, and bounded probe settings."""
        self.agent_id = agent_id
        self._bridge = bridge
        self._display_name = display_name
        self._machine = machine
        self._probe_timeout_secs = probe_timeout_secs
        self._freshness_secs = freshness_secs

    async def validate_bound_session(self, binding: SessionBinding) -> bool:
        """Validate only stateless rehydration; native ownership is not yet proven."""
        return (
            binding.agent_id == self.agent_id
            and binding.adapter_id == self.agent_id
            and binding.channel_id == f"agent:{self.agent_id}"
            and binding.strategy is SessionStrategy.REHYDRATE
            and binding.native_session_id is None
        )

    async def create_bound_session(self, channel_id: str) -> SessionBinding:
        """Allocate a RAP binding without adopting any terminal conversation."""
        if channel_id != f"agent:{self.agent_id}":
            raise ValueError("Requested channel does not match adapter identity")
        now = datetime.now(UTC)
        return SessionBinding(
            binding_id=f"binding_{uuid4().hex}",
            channel_id=channel_id,
            agent_id=self.agent_id,
            strategy=SessionStrategy.REHYDRATE,
            adapter_id=self.agent_id,
            native_session_id=None,
            created_at=now,
            last_used_at=now,
        )

    async def dispatch_in_session(
        self, binding: SessionBinding, context: ContextPackage
    ) -> JobHandle:
        """Rehydrate a clean subprocess through the existing bridge lifecycle."""
        if not await self.validate_bound_session(binding):
            raise ValueError("Unsafe or mismatched conversation session binding")
        job_id = await self._bridge.start(self.agent_id, context.render(), clean_session=True)
        job = self._bridge.get(job_id)
        if job is not None and job.status == "failed":
            raise RuntimeError(job.failure_detail or job.summary or "Session dispatch failed")
        return JobHandle(job_id, self.agent_id)

    async def discover(self) -> AgentObservation:
        """Determine whether the configured executable is installed."""
        now = datetime.now(UTC)
        executable = shutil.which(self.executable)
        if executable is None:
            return self._observation(
                now,
                presence=Presence.STOPPED,
                activity=Activity.UNKNOWN,
                health=Health.FAILED,
                detail=f"Configured executable '{self.executable}' was not found.",
                issues=("missing_executable",),
            )
        return self._observation(
            now,
            presence=Presence.UNKNOWN,
            activity=Activity.UNKNOWN,
            health=Health.UNKNOWN,
            detail=f"Configured executable found at {executable}.",
        )

    async def probe(self) -> AgentObservation:
        """Run the CLI's verified version command under a strict timeout."""
        initial = await self.discover()
        if initial.presence == Presence.STOPPED:
            return initial
        try:
            process = await asyncio.create_subprocess_exec(
                self.executable,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self._probe_timeout_secs
            )
        except TimeoutError:
            return self._observation(
                datetime.now(UTC),
                presence=Presence.UNKNOWN,
                activity=Activity.UNKNOWN,
                health=Health.UNKNOWN,
                detail="Version probe timed out.",
                issues=("timeout",),
            )
        except OSError as exc:
            return self._observation(
                datetime.now(UTC),
                presence=Presence.UNKNOWN,
                activity=Activity.UNKNOWN,
                health=Health.UNKNOWN,
                detail=f"Version probe could not start: {exc}",
                issues=("probe_failed",),
            )
        detail = _clean_output(stdout or stderr)
        if process.returncode:
            return self._observation(
                datetime.now(UTC),
                presence=Presence.UNREACHABLE,
                activity=Activity.UNKNOWN,
                health=Health.UNKNOWN,
                detail=f"Version probe exited with code {process.returncode}: {detail}",
                issues=("probe_failed",),
            )
        jobs = await self.inspect_jobs()
        active = jobs[0] if jobs else None
        return self._observation(
            datetime.now(UTC),
            presence=Presence.REACHABLE,
            activity=active.state if active else Activity.IDLE,
            health=Health.HEALTHY,
            detail=detail or "CLI responded to the version probe.",
            current_work=active,
            update_state=_update_state(detail),
        )

    async def launch(self) -> LaunchResult:
        """Verify the executable can accept on-demand bridge dispatches.

        The configured harnesses are one-shot CLIs, so there is no safe idle
        daemon to spawn merely for a status request.  Dispatch performs the
        actual launch through the existing bridge.
        """
        observation = await self.discover()
        if observation.presence == Presence.STOPPED:
            return LaunchResult(
                self.agent_id,
                False,
                observation=observation,
                error=ControlError(
                    "missing_executable", observation.evidence[0].detail, self.agent_id
                ),
            )
        return LaunchResult(self.agent_id, True, observation=observation)

    async def inspect_jobs(self) -> tuple[ObservedWork, ...]:
        """Project RAP-owned bridge jobs without claiming external sessions."""
        jobs = self._bridge.active_jobs(self.agent_id)
        result: list[ObservedWork] = []
        for job in jobs:
            activity = {
                "running": Activity.WORKING,
                "waiting": Activity.WAITING,
                "blocked": Activity.BLOCKED,
            }.get(job.status, Activity.UNKNOWN)
            result.append(
                ObservedWork(
                    job_id=job.job_id,
                    ownership=WorkOwnership.RAP,
                    summary=job.action or job.task,
                    started_at=_as_datetime(job.started_at),
                    last_activity_at=datetime.now(UTC),
                    completed=job.step if isinstance(job.step, int) else None,
                    total=job.step_total if isinstance(job.step_total, int) else None,
                    state=activity,
                )
            )
        return tuple(result)

    async def dispatch(self, task: AgentTask) -> JobHandle | ControlResult:
        """Launch work only through AgentBridge's existing safety boundary."""
        job_id = await self._bridge.start(
            self.agent_id, task.text, cwd=task.cwd, announce_start=task.announce_start
        )
        job = self._bridge.get(job_id)
        if job is not None and job.status == "failed":
            return ControlResult(
                False,
                self.agent_id,
                job_id,
                ControlError("dispatch_failed", job.failure_detail or job.summary, self.agent_id),
            )
        return JobHandle(job_id, self.agent_id)

    async def cancel(self, job_id: str) -> ControlResult:
        """Cancel only the specific RAP-owned bridge job."""
        job = self._bridge.get(job_id)
        if job is None or job.agent != self.agent_id:
            return ControlResult(
                False,
                self.agent_id,
                job_id,
                ControlError("unknown_job", "No matching RAP-owned job was found.", self.agent_id),
            )
        await self._bridge.cancel(job_id)
        return ControlResult(True, self.agent_id, job_id)

    def _observation(
        self,
        now: datetime,
        *,
        presence: Presence,
        activity: Activity,
        health: Health,
        detail: str,
        current_work: ObservedWork | None = None,
        issues: tuple[str, ...] = (),
        update_state: UpdateState = UpdateState.UNKNOWN,
    ) -> AgentObservation:
        return AgentObservation(
            agent_id=self.agent_id,
            display_name=self._display_name,
            harness=self.agent_id,
            machine=self._machine,
            presence=presence,
            activity=activity,
            health=health,
            capabilities=frozenset(
                {
                    AgentCapability.ACCEPT_TASK,
                    AgentCapability.CANCEL_RAP_JOB,
                    AgentCapability.REPORT_PROGRESS,
                    AgentCapability.VERIFY_RESPONSE,
                }
            ),
            evidence=(Evidence(f"{self.agent_id}_adapter", now, detail),),
            observed_at=now,
            expires_at=now + timedelta(seconds=self._freshness_secs),
            current_work=current_work,
            issues=issues,
            update_state=update_state,
        )


def _clean_output(raw: bytes) -> str:
    return (
        _ANSI_ESCAPE.sub("", raw.decode("utf-8", errors="replace"))
        .strip()
        .replace("\x00", " ")[:500]
    )


def _update_state(detail: str) -> UpdateState:
    """Report an update only when the CLI emitted its own concrete signal."""
    if re.search(
        r"(?:update available|new version .* available|please consider updating)", detail, re.I
    ):
        return UpdateState.UPDATE_AVAILABLE
    return UpdateState.UNKNOWN


def _as_datetime(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        value = datetime.fromisoformat(str(raw))
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    except ValueError:
        return None

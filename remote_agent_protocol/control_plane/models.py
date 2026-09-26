"""Normalized, serializable control-plane state.

No status in this module is inferred from configuration alone.  Adapters supply
timestamped evidence and the registry preserves it as last-known state.
"""
# ruff: noqa: D102

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class Presence(StrEnum):
    """Whether an adapter has verified a harness can be contacted."""

    REACHABLE = "reachable"
    UNREACHABLE = "unreachable"
    STOPPED = "stopped"
    UNKNOWN = "unknown"


class Activity(StrEnum):
    """Verified current work state, independent of presence and health."""

    IDLE = "idle"
    WORKING = "working"
    WAITING = "waiting"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class Health(StrEnum):
    """Ability to perform useful work, independent of current activity."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ResponseState(StrEnum):
    """Whether RAP has received the fixed response from a safe self-check."""

    UNKNOWN = "unknown"
    PENDING = "pending"
    RESPONDED = "responded"
    FAILED = "failed"


class UpdateState(StrEnum):
    """What the configured CLI itself has proved about an available update."""

    UNKNOWN = "unknown"
    UPDATE_AVAILABLE = "update_available"


class WorkOwnership(StrEnum):
    """Who owns the observed work and therefore which controls are safe."""

    RAP = "rap"
    EXTERNAL = "external"
    UNKNOWN = "unknown"


class AgentCapability(StrEnum):
    """An operation an adapter can prove it supports safely."""

    ACCEPT_TASK = "accept_task"
    REPORT_PROGRESS = "report_progress"
    CANCEL_RAP_JOB = "cancel_rap_job"
    INSPECT_EXTERNAL_SESSION = "inspect_external_session"
    CANCEL_EXTERNAL_SESSION = "cancel_external_session"
    LAUNCH = "launch"
    VERIFY_RESPONSE = "verify_response"


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class Evidence:
    """A bounded, timestamped fact supplied by an adapter or bridge."""

    source: str
    observed_at: datetime
    detail: str

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("evidence source is required")
        object.__setattr__(self, "observed_at", _aware(self.observed_at, "observed_at"))
        # Prevent unrestricted process output becoming persisted telemetry.
        object.__setattr__(self, "detail", " ".join(self.detail.split())[:500])

    def to_dict(self) -> dict[str, str]:
        return {
            "source": self.source,
            "observed_at": self.observed_at.isoformat(),
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Evidence:
        return cls(
            source=str(raw["source"]),
            observed_at=datetime.fromisoformat(str(raw["observed_at"])),
            detail=str(raw.get("detail", "")),
        )


@dataclass(frozen=True, slots=True)
class ObservedWork:
    """A job or externally observed session with evidence-bearing details."""

    job_id: str
    ownership: WorkOwnership
    summary: str = ""
    project: str = ""
    started_at: datetime | None = None
    last_activity_at: datetime | None = None
    completed: int | None = None
    total: int | None = None
    state: Activity = Activity.UNKNOWN

    def __post_init__(self) -> None:
        if not self.job_id.strip():
            raise ValueError("observed work requires a job_id")
        if self.started_at is not None:
            object.__setattr__(self, "started_at", _aware(self.started_at, "started_at"))
        if self.last_activity_at is not None:
            object.__setattr__(
                self, "last_activity_at", _aware(self.last_activity_at, "last_activity_at")
            )
        if self.completed is not None and self.completed < 0:
            raise ValueError("completed must not be negative")
        if self.total is not None and self.total < 0:
            raise ValueError("total must not be negative")
        if self.completed is not None and self.total is not None and self.completed > self.total:
            raise ValueError("completed must not exceed total")

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "ownership": self.ownership.value,
            "summary": self.summary,
            "project": self.project,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_activity_at": self.last_activity_at.isoformat()
            if self.last_activity_at
            else None,
            "completed": self.completed,
            "total": self.total,
            "state": self.state.value,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ObservedWork:
        return cls(
            job_id=str(raw["job_id"]),
            ownership=WorkOwnership(str(raw["ownership"])),
            summary=str(raw.get("summary", "")),
            project=str(raw.get("project", "")),
            started_at=_parse_datetime(raw.get("started_at")),
            last_activity_at=_parse_datetime(raw.get("last_activity_at")),
            completed=raw.get("completed"),
            total=raw.get("total"),
            state=Activity(str(raw.get("state", Activity.UNKNOWN.value))),
        )


def _parse_datetime(raw: Any) -> datetime | None:
    return datetime.fromisoformat(str(raw)) if raw else None


@dataclass(frozen=True, slots=True)
class AgentObservation:
    """One normalized adapter observation of one configured agent."""

    agent_id: str
    display_name: str
    harness: str
    machine: str
    presence: Presence
    activity: Activity
    health: Health
    capabilities: frozenset[AgentCapability]
    evidence: tuple[Evidence, ...]
    observed_at: datetime
    expires_at: datetime
    current_work: ObservedWork | None = None
    issues: tuple[str, ...] = ()
    response_state: ResponseState = ResponseState.UNKNOWN
    response_observed_at: datetime | None = None
    update_state: UpdateState = UpdateState.UNKNOWN
    # How long the last confirmed response took and which model gave it,
    # when known (a self-check or a real RAP job that finished).
    response_secs: float | None = None
    response_model: str = ""

    def __post_init__(self) -> None:
        if not self.agent_id.strip():
            raise ValueError("agent_id is required")
        if not self.evidence:
            raise ValueError("an observation requires timestamped evidence")
        observed_at = _aware(self.observed_at, "observed_at")
        expires_at = _aware(self.expires_at, "expires_at")
        if expires_at < observed_at:
            raise ValueError("expires_at must not precede observed_at")
        object.__setattr__(self, "observed_at", observed_at)
        object.__setattr__(self, "expires_at", expires_at)
        if self.response_observed_at is not None:
            object.__setattr__(
                self,
                "response_observed_at",
                _aware(self.response_observed_at, "response_observed_at"),
            )
        object.__setattr__(
            self, "issues", tuple(" ".join(issue.split())[:300] for issue in self.issues)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "display_name": self.display_name,
            "harness": self.harness,
            "machine": self.machine,
            "presence": self.presence.value,
            "activity": self.activity.value,
            "health": self.health.value,
            "capabilities": sorted(capability.value for capability in self.capabilities),
            "evidence": [item.to_dict() for item in self.evidence],
            "observed_at": self.observed_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "current_work": self.current_work.to_dict() if self.current_work else None,
            "issues": list(self.issues),
            "response_state": self.response_state.value,
            "response_observed_at": self.response_observed_at.isoformat()
            if self.response_observed_at
            else None,
            "update_state": self.update_state.value,
            "response_secs": self.response_secs,
            "response_model": self.response_model,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AgentObservation:
        return cls(
            agent_id=str(raw["agent_id"]),
            display_name=str(raw.get("display_name", raw["agent_id"])),
            harness=str(raw.get("harness", raw["agent_id"])),
            machine=str(raw.get("machine", "local")),
            presence=Presence(str(raw["presence"])),
            activity=Activity(str(raw["activity"])),
            health=Health(str(raw["health"])),
            capabilities=frozenset(
                AgentCapability(str(item)) for item in raw.get("capabilities", ())
            ),
            evidence=tuple(Evidence.from_dict(item) for item in raw["evidence"]),
            observed_at=datetime.fromisoformat(str(raw["observed_at"])),
            expires_at=datetime.fromisoformat(str(raw["expires_at"])),
            current_work=ObservedWork.from_dict(raw["current_work"])
            if raw.get("current_work")
            else None,
            issues=tuple(str(item) for item in raw.get("issues", ())),
            response_state=ResponseState(
                str(raw.get("response_state", ResponseState.UNKNOWN.value))
            ),
            response_observed_at=_parse_datetime(raw.get("response_observed_at")),
            update_state=UpdateState(str(raw.get("update_state", UpdateState.UNKNOWN.value))),
            response_secs=float(raw["response_secs"])
            if isinstance(raw.get("response_secs"), (int, float))
            and not isinstance(raw.get("response_secs"), bool)
            else None,
            response_model=str(raw.get("response_model") or ""),
        )


@dataclass(frozen=True, slots=True)
class AgentSnapshot:
    """Last-known state whose freshness is explicit rather than implied."""

    observation: AgentObservation
    stale: bool = False

    def is_stale(self, now: datetime | None = None) -> bool:
        now = _aware(now or datetime.now(UTC), "now")
        return self.stale or now >= self.observation.expires_at

    def as_stale(self) -> AgentSnapshot:
        return replace(self, stale=True)

    def to_dict(self) -> dict[str, Any]:
        return {"observation": self.observation.to_dict(), "stale": self.stale}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> AgentSnapshot:
        return cls(AgentObservation.from_dict(raw["observation"]), bool(raw.get("stale", False)))


@dataclass(frozen=True, slots=True)
class ControlError:
    """A classified, user-safe reason an operation could not complete."""

    code: str
    detail: str
    agent_id: str = ""
    retryable: bool = False


@dataclass(frozen=True, slots=True)
class LaunchResult:
    """Outcome of an adapter launch attempt."""

    agent_id: str
    ready: bool
    observation: AgentObservation | None = None
    error: ControlError | None = None


@dataclass(frozen=True, slots=True)
class JobHandle:
    """Reference returned after a coordinator dispatches a RAP-owned job."""

    job_id: str
    agent_id: str


@dataclass(frozen=True, slots=True)
class ControlResult:
    """Outcome of a cancellation or other state-changing control."""

    ok: bool
    agent_id: str
    job_id: str = ""
    error: ControlError | None = None

"""Evidence-backed candidate selection for Butler-mediated work."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from remote_agent_protocol.control_plane.models import (
    Activity,
    AgentCapability,
    AgentSnapshot,
    Evidence,
    Health,
    Presence,
)

from .models import ConversationTurn

_EVIDENCE_CLAIM = re.compile(r"^(?P<kind>access|success)\s*[:=]\s*(?P<values>.+)$", re.I)


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _normalized_names(values: Iterable[str], name: str) -> frozenset[str]:
    if isinstance(values, str):
        values = (values,)
    values = tuple(values)
    normalized = frozenset(value.strip().lower() for value in values if value.strip())
    if len(normalized) != len(values):
        raise ValueError(f"{name} must contain non-empty unique values")
    return normalized


@dataclass(frozen=True, slots=True)
class CapabilityRequirement:
    """The verified capability and access facts required for one user request."""

    required_capabilities: frozenset[AgentCapability] = field(
        default_factory=lambda: frozenset({AgentCapability.ACCEPT_TASK})
    )
    required_access: frozenset[str] = field(default_factory=frozenset)
    task_id: str | None = None
    project: str | None = None

    def __post_init__(self) -> None:
        """Normalize access names while retaining only concrete control-plane capabilities."""
        object.__setattr__(
            self,
            "required_capabilities",
            frozenset(AgentCapability(capability) for capability in self.required_capabilities),
        )
        object.__setattr__(
            self, "required_access", _normalized_names(self.required_access, "required_access")
        )

    @classmethod
    def for_access(cls, *access: str) -> CapabilityRequirement:
        """Create a dispatch requirement for one or more explicitly required access scopes."""
        return cls(required_access=_normalized_names(access, "required_access"))

    def to_dict(self) -> dict[str, object]:
        """Return JSON-compatible requirements for a durable decision record."""
        return {
            "required_capabilities": sorted(
                capability.value for capability in self.required_capabilities
            ),
            "required_access": sorted(self.required_access),
            "task_id": self.task_id,
            "project": self.project,
        }


@dataclass(frozen=True, slots=True)
class SelectionCandidate:
    """One agent plus its source-attributed access and success evidence."""

    snapshot: AgentSnapshot
    access_evidence: Mapping[str, Evidence] = field(default_factory=dict)
    recent_successes: Mapping[str, Evidence] = field(default_factory=dict)
    reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Keep candidate claims normalized and tied to a concrete snapshot agent."""
        object.__setattr__(
            self,
            "access_evidence",
            {
                name.strip().lower(): evidence
                for name, evidence in self.access_evidence.items()
                if name.strip()
            },
        )
        object.__setattr__(
            self,
            "recent_successes",
            {
                name.strip().lower(): evidence
                for name, evidence in self.recent_successes.items()
                if name.strip()
            },
        )

    @property
    def agent_id(self) -> str:
        """Return the identity asserted by the live control-plane snapshot."""
        return self.snapshot.observation.agent_id

    @classmethod
    def from_snapshot(cls, snapshot: AgentSnapshot) -> SelectionCandidate:
        """Extract explicit access and success claims from normalized adapter evidence.

        Only ``access: scope`` and ``success: scope`` records are accepted. A success
        can support an access claim only while it meets the access freshness policy.
        Arbitrary adapter prose is deliberately not converted into authorization.
        """
        access: dict[str, Evidence] = {}
        successes: dict[str, Evidence] = {}
        for evidence in snapshot.observation.evidence:
            match = _EVIDENCE_CLAIM.match(evidence.detail.strip())
            if not match:
                continue
            target = access if match.group("kind").lower() == "access" else successes
            for value in match.group("values").split(","):
                name = value.strip().lower()
                if (
                    name
                    and (existing := target.get(name)) is not None
                    and existing.observed_at >= evidence.observed_at
                ):
                    continue
                if name:
                    target[name] = evidence
        return cls(snapshot=snapshot, access_evidence=access, recent_successes=successes)

    def with_recent_success(self, capability: str, evidence: Evidence) -> SelectionCandidate:
        """Return this candidate with one timestamped same-capability success record."""
        successes = dict(self.recent_successes)
        successes[capability.strip().lower()] = evidence
        return replace(self, recent_successes=successes)

    def with_reason_codes(self, *reason_codes: str) -> SelectionCandidate:
        """Return an immutable rationale-bearing candidate for a selection record."""
        return replace(self, reason_codes=tuple(reason_codes))

    def evidence(self) -> tuple[Evidence, ...]:
        """Return de-duplicated evidence used to evaluate this candidate."""
        evidence = [*self.access_evidence.values(), *self.recent_successes.values()]
        evidence.extend(self.snapshot.observation.evidence)
        return tuple(
            sorted(
                {(item.source, item.observed_at, item.detail): item for item in evidence}.values(),
                key=lambda item: (item.observed_at, item.source, item.detail),
            )
        )

    def to_dict(self) -> dict[str, object]:
        """Return the candidate and evidence provenance for durable persistence."""
        return {
            "agent_id": self.agent_id,
            "reason_codes": list(self.reason_codes),
            "snapshot_observed_at": self.snapshot.observation.observed_at.isoformat(),
            "snapshot_stale": self.snapshot.stale,
            "snapshot_evidence": [
                evidence.to_dict() for evidence in self.snapshot.observation.evidence
            ],
            "access_evidence": {
                name: evidence.to_dict() for name, evidence in sorted(self.access_evidence.items())
            },
            "recent_successes": {
                name: evidence.to_dict() for name, evidence in sorted(self.recent_successes.items())
            },
        }


@dataclass(frozen=True, slots=True)
class SelectionDecision:
    """The reproducible result of a Butler selection attempt."""

    requirement: CapabilityRequirement
    agent_id: str | None
    selected_evidence: tuple[Evidence, ...]
    eliminated: tuple[SelectionCandidate, ...]
    requires_probe: bool
    reason_codes: tuple[str, ...]
    decided_at: datetime

    def __post_init__(self) -> None:
        """Require a concrete timestamp for each persisted routing decision."""
        object.__setattr__(self, "decided_at", _aware(self.decided_at, "decided_at"))

    def to_persistence_metadata(self) -> dict[str, object]:
        """Build Store-compatible metadata retaining the complete selection rationale.

        Callers persist this value in the Task 1 ``ConversationStore`` metadata or
        in the metadata of the coordinator ``ConversationTurn`` returned below.
        """
        return {
            "requirement": self.requirement.to_dict(),
            "selected_agent_id": self.agent_id,
            "selected_evidence": [evidence.to_dict() for evidence in self.selected_evidence],
            "eliminated": [candidate.to_dict() for candidate in self.eliminated],
            "requires_probe": self.requires_probe,
            "reason_codes": list(self.reason_codes),
            "decided_at": self.decided_at.isoformat(),
        }

    def as_conversation_turn(
        self, *, now: datetime, task_id: str | None = None
    ) -> ConversationTurn:
        """Create a Task 1 durable coordinator record without a parallel selection store."""
        return ConversationTurn.new(
            channel_id="coordinator:butler",
            task_id=task_id,
            speaker_id="butler",
            speaker_role="coordinator",
            full_text="Butler selection decision recorded.",
            now=now,
            metadata={"selection": self.to_persistence_metadata()},
        )


class AgentSelector:
    """Filter and rank candidates without turning stale evidence into new facts."""

    def __init__(
        self,
        *,
        default_agent_id: str | None = None,
        now: Callable[[], datetime] | None = None,
        max_access_age: timedelta = timedelta(minutes=15),
        max_success_age: timedelta = timedelta(hours=24),
    ) -> None:
        """Configure deterministic policy limits and the final configured-default tie-break."""
        if max_access_age <= timedelta():
            raise ValueError("max_access_age must be positive")
        if max_success_age <= timedelta():
            raise ValueError("max_success_age must be positive")
        self._default_agent_id = default_agent_id
        self._now = now or (lambda: datetime.now(UTC))
        self._max_access_age = max_access_age
        self._max_success_age = max_success_age

    def select(
        self,
        requirement: CapabilityRequirement,
        candidates: Iterable[AgentSnapshot | SelectionCandidate],
    ) -> SelectionDecision:
        """Select one qualified agent using ordered, source-attributed policy criteria."""
        now = _aware(self._now(), "now")
        normalized = tuple(
            item if isinstance(item, SelectionCandidate) else SelectionCandidate.from_snapshot(item)
            for item in candidates
        )
        eligible: list[SelectionCandidate] = []
        eliminated: list[SelectionCandidate] = []
        requires_probe = False
        for candidate in normalized:
            reason = self._elimination_reason(candidate, requirement, now)
            if reason is None:
                eligible.append(candidate)
            else:
                eliminated.append(candidate.with_reason_codes(reason))
                requires_probe = requires_probe or reason in {"missing_access", "stale_access"}

        if not eligible:
            return SelectionDecision(
                requirement=requirement,
                agent_id=None,
                selected_evidence=(),
                eliminated=tuple(eliminated),
                requires_probe=requires_probe,
                reason_codes=("no_verified_candidate",),
                decided_at=now,
            )

        selected = min(eligible, key=lambda candidate: self._rank(candidate, requirement, now))
        return SelectionDecision(
            requirement=requirement,
            agent_id=selected.agent_id,
            selected_evidence=selected.evidence(),
            eliminated=tuple(eliminated),
            requires_probe=False,
            reason_codes=self._selection_reason_codes(selected, requirement, now),
            decided_at=now,
        )

    def _elimination_reason(
        self,
        candidate: SelectionCandidate,
        requirement: CapabilityRequirement,
        now: datetime,
    ) -> str | None:
        observation = candidate.snapshot.observation
        if not requirement.required_capabilities.issubset(observation.capabilities):
            return "missing_capability"
        if observation.presence is not Presence.REACHABLE:
            return "unavailable"
        if observation.health is not Health.HEALTHY or observation.activity is Activity.BLOCKED:
            return "unsafe"
        access = [
            candidate.access_evidence.get(name) or candidate.recent_successes.get(name)
            for name in requirement.required_access
        ]
        if any(item is None for item in access):
            return "missing_access" if requirement.required_access else None
        if any(self._access_is_stale(candidate, item, now) for item in access if item is not None):
            return "stale_access"
        if observation.current_work and observation.current_work.job_id == requirement.task_id:
            return "task_conflict"
        return None

    def _access_is_stale(
        self, candidate: SelectionCandidate, evidence: Evidence, now: datetime
    ) -> bool:
        return candidate.snapshot.is_stale(now) or now - evidence.observed_at > self._max_access_age

    def _rank(
        self,
        candidate: SelectionCandidate,
        requirement: CapabilityRequirement,
        now: datetime,
    ) -> tuple[int, int, int, int, str]:
        return (
            int(candidate.snapshot.is_stale(now)),
            self._load_rank(candidate),
            int(not self._has_recent_success(candidate, requirement, now)),
            int(candidate.agent_id != self._default_agent_id),
            candidate.agent_id,
        )

    @staticmethod
    def _load_rank(candidate: SelectionCandidate) -> int:
        activity = candidate.snapshot.observation.activity
        return {
            Activity.IDLE: 0,
            Activity.WAITING: 1,
            Activity.WORKING: 2,
            Activity.UNKNOWN: 3,
        }.get(activity, 4)

    def _has_recent_success(
        self,
        candidate: SelectionCandidate,
        requirement: CapabilityRequirement,
        now: datetime,
    ) -> bool:
        relevant = requirement.required_access or frozenset(
            capability.value for capability in requirement.required_capabilities
        )
        return any(
            (evidence := candidate.recent_successes.get(name)) is not None
            and now - evidence.observed_at <= self._max_success_age
            for name in relevant
        )

    def _selection_reason_codes(
        self,
        candidate: SelectionCandidate,
        requirement: CapabilityRequirement,
        now: datetime,
    ) -> tuple[str, ...]:
        reasons = ["fresh_evidence" if not candidate.snapshot.is_stale(now) else "stale_evidence"]
        if self._load_rank(candidate) == 0:
            reasons.append("low_load")
        if self._has_recent_success(candidate, requirement, now):
            reasons.append("recent_same_capability_success")
        if candidate.agent_id == self._default_agent_id:
            reasons.append("configured_default")
        return tuple(reasons)

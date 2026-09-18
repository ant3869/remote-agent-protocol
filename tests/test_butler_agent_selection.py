"""Evidence-backed Butler agent-selection contracts."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from remote_agent_protocol.control_plane.models import (
    Activity,
    AgentCapability,
    AgentObservation,
    AgentSnapshot,
    Evidence,
    Health,
    ObservedWork,
    Presence,
    WorkOwnership,
)
from remote_agent_protocol.conversation_hub.models import AgentChannel, FloorState
from remote_agent_protocol.conversation_hub.selection import (
    AgentSelector,
    CapabilityRequirement,
    SelectionCandidate,
)
from remote_agent_protocol.conversation_hub.store import ConversationStore

NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)


def snapshot(
    agent_id: str,
    *,
    access: str | None = None,
    fresh: bool = True,
    healthy: bool = True,
    presence: Presence = Presence.REACHABLE,
    activity: Activity = Activity.IDLE,
) -> AgentSnapshot:
    """Build an evidence-bearing live snapshot for selection tests."""
    observed_at = NOW if fresh else NOW - timedelta(hours=2)
    detail = f"access: {access}" if access else "version probe responded"
    return AgentSnapshot(
        AgentObservation(
            agent_id=agent_id,
            display_name=agent_id.title(),
            harness=agent_id,
            machine="test",
            presence=presence,
            activity=activity,
            health=Health.HEALTHY if healthy else Health.DEGRADED,
            capabilities=frozenset({AgentCapability.ACCEPT_TASK}),
            evidence=(Evidence("test", observed_at, detail),),
            observed_at=observed_at,
            expires_at=NOW + timedelta(minutes=5) if fresh else NOW - timedelta(minutes=5),
        ),
        stale=not fresh,
    )


@pytest.fixture
def selector() -> AgentSelector:
    """Use a fixed clock so evidence freshness is deterministic."""
    return AgentSelector(now=lambda: NOW, default_agent_id="hermes")


def email_request() -> CapabilityRequirement:
    """Require an agent that can safely accept email work."""
    return CapabilityRequirement.for_access("email")


def test_fresh_access_beats_stale_history(selector: AgentSelector) -> None:
    """A fresh access claim outranks a stale claim for the same request."""
    decision = selector.select(
        email_request(),
        (
            snapshot("openclaw", access="email", fresh=True),
            snapshot("hermes", access="email", fresh=False),
        ),
    )

    assert decision.agent_id == "openclaw"
    assert decision.eliminated[0].reason_codes == ("stale_access",)


def test_unknown_access_requests_probe_instead_of_inventing_candidate(
    selector: AgentSelector,
) -> None:
    """Missing access evidence asks for a probe rather than a guessed handoff."""
    decision = selector.select(
        email_request(),
        (snapshot("openclaw"), snapshot("hermes")),
    )

    assert decision.agent_id is None
    assert decision.requires_probe is True
    assert {candidate.reason_codes for candidate in decision.eliminated} == {("missing_access",)}


def test_old_success_is_not_permanent_access(selector: AgentSelector) -> None:
    """A historical success cannot silently become a current authorization claim."""
    old_success = snapshot("openclaw", fresh=False)
    old_success = AgentSnapshot(
        replace(
            old_success.observation,
            evidence=(Evidence("test", NOW - timedelta(days=2), "success: email"),),
        ),
        stale=True,
    )

    decision = selector.select(email_request(), (old_success,))

    assert decision.agent_id is None
    assert decision.requires_probe is True
    assert decision.eliminated[0].reason_codes == ("stale_access",)


def test_fresh_success_is_timestamped_access_evidence(selector: AgentSelector) -> None:
    """A current successful task may prove access without a separate access probe."""
    success = snapshot("openclaw")
    success = AgentSnapshot(
        replace(success.observation, evidence=(Evidence("test", NOW, "success: email"),))
    )

    decision = selector.select(email_request(), (success,))

    assert decision.agent_id == "openclaw"
    assert decision.selected_evidence[0].observed_at == NOW


def test_filters_missing_capability_unreachable_and_degraded_agents(
    selector: AgentSelector,
) -> None:
    """Required capability and safe availability are filters, not preferences."""
    missing_capability = snapshot("codex", access="email")
    missing_capability = AgentSnapshot(
        replace(missing_capability.observation, capabilities=frozenset())
    )
    decision = selector.select(
        email_request(),
        (
            missing_capability,
            snapshot("offline", access="email", presence=Presence.UNREACHABLE),
            snapshot("degraded", access="email", healthy=False),
        ),
    )

    assert decision.agent_id is None
    assert {candidate.agent_id: candidate.reason_codes for candidate in decision.eliminated} == {
        "codex": ("missing_capability",),
        "offline": ("unavailable",),
        "degraded": ("unsafe",),
    }


def test_less_loaded_candidate_beats_busy_candidate(selector: AgentSelector) -> None:
    """Load ranks only otherwise qualified fresh candidates."""
    decision = selector.select(
        email_request(),
        (
            snapshot("openclaw", access="email", activity=Activity.WORKING),
            snapshot("codex", access="email", activity=Activity.IDLE),
        ),
    )

    assert decision.agent_id == "codex"


def test_active_matching_task_is_a_conflict_not_a_second_dispatch(selector: AgentSelector) -> None:
    """An agent already assigned the task is removed before load-based ranking."""
    occupied = snapshot("openclaw", access="email")
    occupied = AgentSnapshot(
        replace(
            occupied.observation,
            current_work=ObservedWork(
                job_id="task-1",
                ownership=WorkOwnership.RAP,
                state=Activity.WORKING,
            ),
        )
    )

    decision = selector.select(
        CapabilityRequirement(required_access=frozenset({"email"}), task_id="task-1"),
        (occupied,),
    )

    assert decision.agent_id is None
    assert decision.eliminated[0].reason_codes == ("task_conflict",)


def test_recent_same_access_success_beats_configured_default(selector: AgentSelector) -> None:
    """Recent capability success ranks before the configured default tie-break."""
    hermes = SelectionCandidate.from_snapshot(snapshot("hermes", access="email"))
    openclaw = SelectionCandidate.from_snapshot(snapshot("openclaw", access="email"))
    openclaw = openclaw.with_recent_success("email", Evidence("test", NOW, "email succeeded"))

    decision = selector.select(email_request(), (hermes, openclaw))

    assert decision.agent_id == "openclaw"


def test_configured_default_breaks_an_otherwise_equivalent_tie(selector: AgentSelector) -> None:
    """The default is applied last after evidence-backed criteria tie."""
    decision = selector.select(
        email_request(),
        (snapshot("openclaw", access="email"), snapshot("hermes", access="email")),
    )

    assert decision.agent_id == "hermes"
    assert decision.reason_codes[-1] == "configured_default"


def test_decision_persistence_contains_requirement_and_provenance(selector: AgentSelector) -> None:
    """A durable decision retains eliminations and timestamped source evidence."""
    decision = selector.select(
        email_request(),
        (snapshot("openclaw", access="email"), snapshot("hermes", access="email", fresh=False)),
    )

    payload = decision.to_persistence_metadata()

    assert payload["requirement"]["required_access"] == ["email"]
    assert payload["selected_agent_id"] == "openclaw"
    assert payload["eliminated"][0]["reason_codes"] == ["stale_access"]
    assert payload["selected_evidence"][0]["observed_at"] == NOW.isoformat()


def test_decision_uses_the_conversation_store_not_a_parallel_database(
    selector: AgentSelector, tmp_path
) -> None:
    """The durable coordinator turn round-trips through the Task 1 atomic store."""
    decision = selector.select(email_request(), (snapshot("openclaw", access="email"),))
    channel = AgentChannel.new(agent_id="butler", now=NOW)
    turn = decision.as_conversation_turn(now=NOW, task_id="task-1")
    store = ConversationStore(tmp_path / "conversations.json")

    store.save(
        channels=[channel],
        turns=[turn],
        memories=[],
        bindings=[],
        task_references=[],
        floor_state=FloorState.new(now=NOW),
    )

    restored = store.load()
    assert restored.turns[0].metadata["selection"]["selected_agent_id"] == "openclaw"
    assert restored.turns[0].metadata["selection"]["requirement"]["required_access"] == ["email"]

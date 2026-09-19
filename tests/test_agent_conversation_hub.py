"""AgentConversationHub routing, dispatch, ownership, and restart contracts."""

import itertools
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from remote_agent_protocol.control_plane.models import (
    Activity,
    AgentCapability,
    AgentObservation,
    Evidence,
    Health,
    JobHandle,
    Presence,
)
from remote_agent_protocol.control_plane.registry import AgentRegistry
from remote_agent_protocol.conversation_hub.context import ContextAssembler
from remote_agent_protocol.conversation_hub.factory import build_conversation_hub
from remote_agent_protocol.conversation_hub.floor import FloorManager
from remote_agent_protocol.conversation_hub.memory import MemoryRepository
from remote_agent_protocol.conversation_hub.models import (
    ResultKind,
    SessionBinding,
    SessionStrategy,
)
from remote_agent_protocol.conversation_hub.selection import AgentSelector
from remote_agent_protocol.conversation_hub.service import (
    AgentConversationHub,
    ConversationTurnRequest,
)
from remote_agent_protocol.conversation_hub.store import ConversationStore

NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)
BACKENDS = {"openclaw": object(), "hermes": object()}
ALIASES = {"openclaw": "openclaw", "hermes": "hermes"}


def make_adapter(agent_id: str) -> AsyncMock:
    """Build a REHYDRATE session adapter that issues unique bindings and job IDs."""
    counter = itertools.count(1)
    result = AsyncMock()
    result.agent_id = agent_id
    result.conversation_session_strategy = SessionStrategy.REHYDRATE
    result.validate_bound_session.return_value = True

    async def create_bound_session(channel_id: str) -> SessionBinding:
        now = datetime.now(UTC)
        return SessionBinding(
            binding_id=f"binding-{agent_id}-{next(counter)}",
            channel_id=channel_id,
            agent_id=agent_id,
            strategy=SessionStrategy.REHYDRATE,
            adapter_id=agent_id,
            native_session_id=None,
            created_at=now,
            last_used_at=now,
        )

    async def dispatch_in_session(binding: SessionBinding, context) -> JobHandle:
        return JobHandle(f"job-{agent_id}-{next(counter)}", agent_id)

    result.create_bound_session.side_effect = create_bound_session
    result.dispatch_in_session.side_effect = dispatch_in_session
    return result


def make_adapters() -> dict[str, AsyncMock]:
    return {"openclaw": make_adapter("openclaw"), "hermes": make_adapter("hermes")}


def healthy_observation(agent_id: str, *, now: datetime = NOW) -> AgentObservation:
    """Build fresh, reachable, capable evidence for Butler-selection tests."""
    return AgentObservation(
        agent_id=agent_id,
        display_name=agent_id.title(),
        harness=agent_id,
        machine="test",
        presence=Presence.REACHABLE,
        activity=Activity.IDLE,
        health=Health.HEALTHY,
        capabilities=frozenset({AgentCapability.ACCEPT_TASK}),
        evidence=(Evidence("test", now, "ready"),),
        observed_at=now,
        expires_at=now + timedelta(minutes=5),
    )


def make_hub(tmp_path, *, adapters=None, registry=None, on_event=None):
    adapters = adapters if adapters is not None else make_adapters()
    registry = registry or AgentRegistry()
    store = ConversationStore(tmp_path / "conversations.json")
    memories = MemoryRepository()
    hub = AgentConversationHub(
        store=store,
        floor_manager=FloorManager(backends=BACKENDS, aliases=ALIASES),
        memories=memories,
        context_assembler=ContextAssembler(memories),
        selector=AgentSelector(now=lambda: NOW),
        adapters=adapters,
        registry=registry,
        on_event=on_event,
        now=lambda: NOW,
    )
    return hub, registry, adapters


def turn_request(
    text: str,
    *,
    source: str = "ant",
    explicit_agent_id: str | None = None,
    created_at: datetime = NOW,
) -> ConversationTurnRequest:
    return ConversationTurnRequest(
        text=text,
        source=source,
        explicit_agent_id=explicit_agent_id,
        correlation_id="corr-1",
        created_at=created_at,
    )


@pytest.mark.asyncio
async def test_butler_mediated_work_selects_and_dispatches_to_a_verified_agent(tmp_path):
    """Unnamed work is routed through Butler to an evidence-backed candidate."""
    hub, registry, adapters = make_hub(tmp_path)
    await registry.observe(healthy_observation("openclaw"))

    disposition = await hub.handle_turn(turn_request("Check my important emails"))

    assert disposition.target_id == "openclaw"
    assert disposition.channel_id == "agent:openclaw"
    assert disposition.task_id is not None
    adapters["openclaw"].dispatch_in_session.assert_awaited_once()
    task = hub.task(disposition.task_id)
    assert task is not None
    assert task.agent_id == "openclaw"
    assert task.status == "active"


@pytest.mark.asyncio
async def test_butler_selection_without_a_verified_candidate_asks_instead_of_guessing(tmp_path):
    """No qualified candidate means Butler explains rather than inventing one."""
    hub, registry, adapters = make_hub(tmp_path)

    disposition = await hub.handle_turn(turn_request("Plan a new dashboard"))

    assert disposition.target_id == "butler"
    assert disposition.task_id is None
    assert disposition.spoken_acknowledgment == "I don't currently have a verified agent for that."
    adapters["openclaw"].dispatch_in_session.assert_not_awaited()
    adapters["hermes"].dispatch_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_address_dispatches_to_the_named_agent(tmp_path):
    """Naming an agent transfers the floor and bypasses Butler selection."""
    hub, registry, adapters = make_hub(tmp_path)

    disposition = await hub.handle_turn(turn_request("OpenClaw, check my email"))

    assert disposition.target_id == "openclaw"
    assert disposition.floor_decision.kind == "direct"
    adapters["openclaw"].dispatch_in_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_unconfigured_agent_routes_to_butler_with_explanation(tmp_path):
    """A caller-supplied explicit target that isn't configured narrates via Butler."""
    hub, registry, adapters = make_hub(tmp_path)

    disposition = await hub.handle_turn(turn_request("check status", explicit_agent_id="codex"))

    assert disposition.target_id == "butler"
    assert disposition.floor_decision.kind == "unavailable"
    assert "codex" in (disposition.spoken_acknowledgment or "")


@pytest.mark.asyncio
async def test_related_follow_up_stays_with_the_current_floor_holder_and_task(tmp_path):
    """A relevant follow-up continues the same subject without repeating the name."""
    hub, registry, adapters = make_hub(tmp_path)
    first = await hub.handle_turn(turn_request("OpenClaw, check my email"))

    second = await hub.handle_turn(
        turn_request("What about school messages?", created_at=NOW + timedelta(seconds=5))
    )

    assert second.target_id == "openclaw"
    assert second.task_id == first.task_id
    assert second.floor_decision.kind == "follow_up"
    assert adapters["openclaw"].dispatch_in_session.await_count == 2
    assert len(hub.turns("agent:openclaw")) == 2


@pytest.mark.asyncio
async def test_saying_butler_returns_the_floor_without_dispatching(tmp_path):
    """An explicit Butler invocation transfers the floor immediately, no dispatch."""
    hub, registry, adapters = make_hub(tmp_path)
    await hub.handle_turn(turn_request("OpenClaw, check my email"))

    disposition = await hub.handle_turn(
        turn_request("Butler", created_at=NOW + timedelta(seconds=5))
    )

    assert disposition.target_id == "butler"
    assert disposition.floor_decision.kind == "return_to_butler"
    assert hub.floor_state.floor_channel_id == "coordinator:butler"
    adapters["openclaw"].dispatch_in_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_butler_selection_emits_handoff_and_task_assignment_events(tmp_path):
    """A successful Butler handoff emits started/completed events plus assignment."""
    events = []
    hub, registry, adapters = make_hub(tmp_path, on_event=events.append)
    await registry.observe(healthy_observation("hermes"))

    await hub.handle_turn(turn_request("Plan a new dashboard"))

    event_names = [item["event"] for item in events]
    assert "conversation_butler_handoff_started" in event_names
    assert "conversation_butler_handoff_completed" in event_names
    assert "conversation_task_assigned" in event_names


@pytest.mark.asyncio
async def test_task_ownership_is_tracked_in_floor_state(tmp_path):
    """Dispatch records durable task ownership independent of the floor holder."""
    hub, registry, adapters = make_hub(tmp_path)

    disposition = await hub.handle_turn(turn_request("OpenClaw, check my email"))

    assert hub.floor_state.task_owner_by_id[disposition.task_id] == "openclaw"


@pytest.mark.asyncio
async def test_job_progress_updates_task_without_creating_a_turn(tmp_path):
    """Routine liveness progress stays bookkeeping only, not a transcript turn."""
    hub, registry, adapters = make_hub(tmp_path)
    disposition = await hub.handle_turn(turn_request("OpenClaw, check my email"))
    task = hub.task(disposition.task_id)
    before = len(hub.turns("agent:openclaw"))

    await hub.handle_job_event(
        {"type": "agent_job", "agent": "openclaw", "job_id": task.attempt_id, "status": "running"}
    )

    assert len(hub.turns("agent:openclaw")) == before
    assert hub.task(disposition.task_id).status == "active"


@pytest.mark.asyncio
async def test_background_completion_does_not_steal_the_active_floor(tmp_path):
    """A finished background task is recorded under its owner without moving the floor."""
    hub, registry, adapters = make_hub(tmp_path)
    first = await hub.handle_turn(turn_request("OpenClaw, check my email"))
    await hub.handle_turn(
        turn_request("Hermes, clean up downloads", created_at=NOW + timedelta(seconds=5))
    )
    assert hub.floor_state.floor_channel_id == "agent:hermes"
    openclaw_task = hub.task(first.task_id)

    await hub.handle_job_event(
        {
            "type": "agent_job",
            "agent": "openclaw",
            "job_id": openclaw_task.attempt_id,
            "status": "done",
            "result": "Inbox is clear.",
        }
    )

    assert hub.floor_state.floor_channel_id == "agent:hermes"
    assert hub.floor_state.last_speaker_id == "openclaw"
    openclaw_turns = hub.turns("agent:openclaw")
    assert openclaw_turns[-1].speaker_role == "agent"
    assert openclaw_turns[-1].result_kind == ResultKind.SUCCESS
    assert openclaw_turns[-1].full_text == "Inbox is clear."
    assert hub.task(first.task_id).status == "done"
    assert first.task_id not in hub.channel("agent:openclaw").active_task_ids


@pytest.mark.asyncio
async def test_failed_background_job_triggers_butler_intervention_event(tmp_path):
    """A failed job records a failure turn and emits a Butler intervention event."""
    events = []
    hub, registry, adapters = make_hub(tmp_path, on_event=events.append)
    disposition = await hub.handle_turn(turn_request("OpenClaw, check my email"))
    task = hub.task(disposition.task_id)

    await hub.handle_job_event(
        {
            "type": "agent_job",
            "agent": "openclaw",
            "job_id": task.attempt_id,
            "status": "failed",
            "failure_kind": "quota",
            "failure_detail": "Provider quota exceeded.",
        }
    )

    assert hub.task(disposition.task_id).status == "failed"
    last_turn = hub.turns("agent:openclaw")[-1]
    assert last_turn.result_kind == ResultKind.FAILURE
    assert "conversation_butler_intervention_started" in [item["event"] for item in events]


@pytest.mark.asyncio
async def test_ambiguous_deictic_reference_requests_clarification_without_dispatch(tmp_path):
    """Multiple active tasks plus a deictic reference asks rather than guesses."""
    hub, registry, adapters = make_hub(tmp_path)
    await hub.handle_turn(turn_request("OpenClaw, check my email"))
    await hub.handle_turn(
        turn_request("Hermes, clean up downloads", created_at=NOW + timedelta(seconds=5))
    )
    adapters["openclaw"].dispatch_in_session.reset_mock()
    adapters["hermes"].dispatch_in_session.reset_mock()

    disposition = await hub.handle_turn(
        turn_request("What about that?", created_at=NOW + timedelta(seconds=10))
    )

    assert disposition.floor_decision.kind == "clarification"
    assert disposition.target_id == "butler"
    assert disposition.spoken_acknowledgment == "Which task do you mean?"
    adapters["openclaw"].dispatch_in_session.assert_not_awaited()
    adapters["hermes"].dispatch_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_restart_restores_channels_turns_and_tasks(tmp_path):
    """A fresh hub over the same store recovers logical state after a restart."""
    store_path = tmp_path / "conversations.json"
    adapters = make_adapters()
    hub = build_conversation_hub(
        store_path=store_path,
        adapters=adapters,
        registry=AgentRegistry(),
        backends=BACKENDS,
        aliases=ALIASES,
    )
    disposition = await hub.handle_turn(turn_request("OpenClaw, check my email"))

    events = []
    restarted = build_conversation_hub(
        store_path=store_path,
        adapters=make_adapters(),
        registry=AgentRegistry(),
        backends=BACKENDS,
        aliases=ALIASES,
        on_event=events.append,
    )

    assert restarted.channel("agent:openclaw") is not None
    assert restarted.task(disposition.task_id) is not None
    assert restarted.task(disposition.task_id).agent_id == "openclaw"
    assert len(restarted.turns("agent:openclaw")) == 1
    assert "conversation_channel_restored" in [item["event"] for item in events]

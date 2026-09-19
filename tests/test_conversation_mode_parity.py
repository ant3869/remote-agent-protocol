"""Task 8 parity (acceptance scenario 15): both modes drive one shared hub identically.

``VoiceSession`` and ``BrainSession`` are constructed for real -- exactly as
``tests/test_session_delegation.py`` and ``tests/test_brain_low_vram.py``
already do it, so ``.build()``/``.start()`` are never called and no audio,
HTTP, or network I/O happens -- then each session's ``_conversation_hub`` is
swapped for one shared hub built with fake ``ConversationSessionAdapter``s
(``tests/test_agent_conversation_hub.py``'s ``make_adapter`` pattern). A turn
routed through either mode's real dispatch code
(``_delegate_ack_ex``/``_delegate_ack``, ``_route_chat_turn_through_hub``)
must land on the exact same durable conversation state, since both funnel
through the identical three-case ``explicit_agent_id`` rule from
task-8-brief.md.

Intent-routing classification itself (network-touching tiers of
``IntentRouter``) is out of scope here and already covered elsewhere; these
tests drive the post-routing dispatch machinery directly by setting the
``_pending_structured_decision``/``_pending_routing_source`` state a real
``_resolve_delegation`` call would have already set, matching how
``tests/test_session_delegation.py`` already isolates this layer.
"""

import asyncio
import itertools
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from remote_agent_protocol import brain, personas, session
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
from remote_agent_protocol.conversation_hub.events import (
    BUTLER_INTERVENTION_STARTED,
    RESULT_AVAILABLE,
)
from remote_agent_protocol.conversation_hub.floor import FloorManager
from remote_agent_protocol.conversation_hub.memory import MemoryRepository
from remote_agent_protocol.conversation_hub.models import (
    ResultKind,
    SessionBinding,
    SessionStrategy,
)
from remote_agent_protocol.conversation_hub.selection import AgentSelector
from remote_agent_protocol.conversation_hub.service import AgentConversationHub
from remote_agent_protocol.conversation_hub.store import ConversationStore
from remote_agent_protocol.orchestration.models import StructuredDecision

NOW = datetime(2026, 9, 19, 12, tzinfo=UTC)
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


def healthy_observation(agent_id: str, *, now: datetime = NOW) -> AgentObservation:
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


def make_shared_hub(tmp_path, events, *, registry=None):
    adapters = {"openclaw": make_adapter("openclaw"), "hermes": make_adapter("hermes")}
    registry = registry if registry is not None else AgentRegistry()
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
        on_event=events.append,
        now=lambda: NOW,
    )
    return hub, registry, adapters


@pytest.fixture
def world(tmp_path):
    """One shared hub, wired into a real VoiceSession and a real BrainSession."""
    events: list[dict] = []
    hub, registry, adapters = make_shared_hub(tmp_path, events)
    voice = session.VoiceSession(personas.DEFAULT_PERSONA)
    voice._conversation_hub = hub  # noqa: SLF001
    brain_session = brain.BrainSession(personas.PERSONAS[0])
    brain_session._conversation_hub = hub  # noqa: SLF001
    # Isolate from whatever real short-term memory happens to be on disk --
    # _with_delegation_context reads it, and it must not leak into asserted
    # dispatch text here.
    brain_session._messages = []
    return SimpleNamespace(
        hub=hub,
        registry=registry,
        adapters=adapters,
        events=events,
        voice=voice,
        brain=brain_session,
    )


def _set_pending_decision(mode, *, source, agent, task):
    """Reproduce what a real _resolve_delegation call already set this turn."""
    mode._pending_routing_source = source
    mode._pending_structured_decision = StructuredDecision(
        intent="delegate",
        target_harness=agent,
        route="local",
        intent_confidence=1.0,
        routing_confidence=1.0,
        task=task,
    )


async def _drain_spawned(mode) -> None:
    """Await every background task _spawn scheduled during the call under test."""
    tasks = list(getattr(mode, "_tasks", None) or getattr(mode, "_bg_tasks", ()))
    for task in tasks:
        if not task.done():
            await task


@pytest.mark.asyncio
async def test_unnamed_request_selects_the_same_verified_agent_in_both_modes(world):
    await world.registry.observe(healthy_observation("openclaw"))

    _set_pending_decision(
        world.voice, source="heuristic", agent="hermes", task="check my important emails"
    )
    world.voice._delegate_ack("hermes", "check my important emails")
    await _drain_spawned(world.voice)

    _set_pending_decision(
        world.brain, source="heuristic", agent="hermes", task="check my other emails"
    )
    world.brain._delegate_ack("hermes", "check my other emails")
    await _drain_spawned(world.brain)

    channel = world.hub.channel("agent:openclaw")
    assert channel is not None
    turns = world.hub.turns("agent:openclaw")
    user_turns = [turn for turn in turns if turn.speaker_role == "user"]
    assert [turn.full_text for turn in user_turns] == [
        "check my important emails",
        "check my other emails",
    ]
    assert world.hub.floor_state.floor_channel_id == "agent:openclaw"


@pytest.mark.asyncio
async def test_explicit_naming_dispatches_to_the_named_agent_in_both_modes(world):
    _set_pending_decision(
        world.voice, source="explicit", agent="hermes", task="ask hermes to build"
    )
    world.voice._delegate_ack("hermes", "ask hermes to build")
    await _drain_spawned(world.voice)
    assert world.hub.floor_state.floor_channel_id == "agent:hermes"

    _set_pending_decision(
        world.brain, source="explicit", agent="openclaw", task="ask openclaw to review"
    )
    world.brain._delegate_ack("openclaw", "ask openclaw to review")
    await _drain_spawned(world.brain)
    assert world.hub.floor_state.floor_channel_id == "agent:openclaw"


@pytest.mark.asyncio
async def test_plain_chat_records_the_turn_and_keeps_the_floor_with_butler(world):
    await world.voice._route_chat_turn_through_hub("thanks, that's all for now")
    await world.brain._route_chat_turn_through_hub("appreciate it")

    assert world.hub.floor_state.floor_channel_id == "coordinator:butler"
    turns = world.hub.turns("coordinator:butler")
    assert [turn.full_text for turn in turns] == [
        "thanks, that's all for now",
        "appreciate it",
    ]


@pytest.mark.asyncio
async def test_saying_butler_returns_the_floor_from_either_mode(world):
    _set_pending_decision(world.voice, source="explicit", agent="hermes", task="ask hermes")
    world.voice._delegate_ack("hermes", "ask hermes")
    await _drain_spawned(world.voice)
    assert world.hub.floor_state.floor_channel_id == "agent:hermes"

    await world.brain._route_chat_turn_through_hub("Butler")

    assert world.hub.floor_state.floor_channel_id == "coordinator:butler"


@pytest.mark.asyncio
async def test_a_failed_dispatch_classifies_a_recovery_and_narrates_via_the_shared_composer(world):
    adapter = world.adapters["hermes"]
    adapter.dispatch_in_session.side_effect = None
    adapter.dispatch_in_session.return_value = JobHandle("job-fail-1", "hermes")

    _set_pending_decision(world.voice, source="explicit", agent="hermes", task="run the flaky task")
    world.voice._delegate_ack("hermes", "run the flaky task")
    await _drain_spawned(world.voice)

    await world.hub.handle_job_event(
        {
            "type": "agent_job",
            "job_id": "job-fail-1",
            "agent": "hermes",
            "status": "failed",
            "failure_kind": "timeout",
            "failure_detail": "no response before the deadline",
        }
    )

    intervention_events = [
        event for event in world.events if event["event"] == BUTLER_INTERVENTION_STARTED
    ]
    assert intervention_events, "a failed hub-tracked job must trigger Butler recovery narration"
    from remote_agent_protocol.conversation_presentation import present_butler_intervention

    line = present_butler_intervention(
        intervention_events[0]["data"], agent_id="hermes", detail=intervention_events[0]["detail"]
    )
    assert "try that again" in line  # RECOVERY_RETRY for a timeout failure


@pytest.mark.asyncio
async def test_background_completion_does_not_steal_an_unrelated_active_floor(world):
    _set_pending_decision(world.voice, source="explicit", agent="hermes", task="long hermes task")
    world.voice._delegate_ack("hermes", "long hermes task")
    await _drain_spawned(world.voice)
    hermes_task = next(
        task
        for task in world.hub._tasks.values()
        if task.agent_id == "hermes"  # noqa: SLF001
    )

    _set_pending_decision(world.brain, source="explicit", agent="openclaw", task="active chat task")
    world.brain._delegate_ack("openclaw", "active chat task")
    await _drain_spawned(world.brain)
    assert world.hub.floor_state.floor_channel_id == "agent:openclaw"

    await world.hub.handle_job_event(
        {
            "type": "agent_job",
            "job_id": hermes_task.attempt_id,
            "agent": "hermes",
            "status": "done",
            "result": "finished in the background",
        }
    )

    assert world.hub.floor_state.floor_channel_id == "agent:openclaw", (
        "a background completion on a different channel must not move the active floor"
    )
    result_events = [event for event in world.events if event["event"] == RESULT_AVAILABLE]
    assert any(event["channel"] == "agent:hermes" for event in result_events)


@pytest.mark.asyncio
async def test_a_slow_adapter_dispatch_does_not_deadlock_job_events_for_another_channel(world):
    """task-8-brief.md's concurrency note: handle_turn and handle_job_event share
    one asyncio.Lock, so a slow dispatch on one channel briefly stalls -- but
    must never deadlock -- a job event for a different, already-active channel.
    """
    _set_pending_decision(world.voice, source="explicit", agent="hermes", task="quick hermes task")
    world.voice._delegate_ack("hermes", "quick hermes task")
    await _drain_spawned(world.voice)
    hermes_task = next(
        task
        for task in world.hub._tasks.values()
        if task.agent_id == "hermes"  # noqa: SLF001
    )

    release = asyncio.Event()

    async def slow_dispatch(binding, context):
        await release.wait()
        return JobHandle("job-openclaw-slow", "openclaw")

    world.adapters["openclaw"].dispatch_in_session.side_effect = slow_dispatch

    _set_pending_decision(
        world.brain, source="explicit", agent="openclaw", task="slow openclaw task"
    )
    world.brain._delegate_ack("openclaw", "slow openclaw task")
    # Give the spawned dispatch coroutine enough turns of the loop to reach
    # and block inside the hub's lock on the slow adapter call.
    for _ in range(5):
        await asyncio.sleep(0)

    job_event_task = asyncio.ensure_future(
        world.hub.handle_job_event(
            {
                "type": "agent_job",
                "job_id": hermes_task.attempt_id,
                "agent": "hermes",
                "status": "done",
                "result": "finished while openclaw dispatch was in flight",
            }
        )
    )
    release.set()
    await asyncio.wait_for(job_event_task, timeout=2)
    await _drain_spawned(world.brain)

    hermes_turns = world.hub.turns("agent:hermes")
    assert any(
        turn.speaker_role == "agent" and "finished while openclaw" in turn.full_text
        for turn in hermes_turns
    ), "the job event for the unrelated channel must still land once the lock frees up"


def test_brain_result_presentation_marks_the_text_only_degradation():
    from remote_agent_protocol.conversation_hub.models import ConversationTurn
    from remote_agent_protocol.conversation_presentation import present_hub_result

    turn = ConversationTurn.new(
        channel_id="agent:hermes",
        speaker_id="hermes",
        speaker_role="agent",
        full_text="Here is the full answer.",
        now=NOW,
        task_id="task-1",
        spoken_text="Here is the short spoken answer.",
        result_kind=ResultKind.SUCCESS,
    )

    voice_presentation = present_hub_result(turn, for_brain=False)
    brain_presentation = present_hub_result(turn, for_brain=True)

    assert not voice_presentation.degraded_for_brain
    assert brain_presentation.degraded_for_brain
    assert "text only" in brain_presentation.brain_text
    assert voice_presentation.voice_text == "Here is the short spoken answer."


@pytest.mark.asyncio
async def test_no_dispatch_disposition_explanation_reaches_the_user_in_both_modes(world):
    """Regression guard flagged in review (task-8 review round 1, #1): a router
    'dispatch' can still land on a _NO_DISPATCH_KINDS floor decision (e.g. plain
    smalltalk with no active task) after the caller already told the user work
    is starting. The hub's explanation must reach the user through the real
    ``_dispatch_via_hub`` chokepoint, not be silently dropped.

    The previous version of this test called ``hub.handle_turn`` directly and
    asserted ``target_id == BUTLER_ID or spoken_acknowledgment is not None`` --
    true trivially for every _NO_DISPATCH_KINDS decision by construction, so it
    never exercised ``_dispatch_via_hub`` or checked the user actually received
    anything.
    """
    await world.registry.observe(healthy_observation("openclaw"))

    # "thanks" is classified as smalltalk ("acknowledgment") by the floor
    # manager with no active task -- a _NO_DISPATCH_KINDS decision that
    # carries no spoken_text, so _dispatch_via_hub must fall back to its own
    # explanation rather than silently doing nothing.
    _set_pending_decision(world.brain, source="heuristic", agent="hermes", task="thanks")
    world.brain._delegate_ack("hermes", "thanks")
    await _drain_spawned(world.brain)
    assert world.brain._messages, "the hub's no-dispatch outcome must reach the user somehow"
    last = world.brain._messages[-1]
    assert last["role"] == "assistant"
    assert last["content"].strip()

    world.voice._speak_agent_text = AsyncMock()
    _set_pending_decision(world.voice, source="heuristic", agent="hermes", task="thanks")
    world.voice._delegate_ack("hermes", "thanks")
    await _drain_spawned(world.voice)
    world.voice._speak_agent_text.assert_awaited_once()
    (spoken_text,), _kwargs = world.voice._speak_agent_text.call_args
    assert spoken_text.strip()

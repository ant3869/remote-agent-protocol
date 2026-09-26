"""Tool-calling Butler: tools enforce the rules, the loop speaks from tool results."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import aiohttp
import pytest

from remote_agent_protocol import agent_bridge, brain, llm_endpoint, personas
from remote_agent_protocol import config as cfg
from remote_agent_protocol.butler import (
    ButlerLoop,
    ButlerToolbox,
    ButlerUnavailable,
    DispatchOutcome,
    TaskLedger,
)
from remote_agent_protocol.control_plane import AgentControlPlane
from remote_agent_protocol.control_plane.adapters.fake import FakeAgentAdapter
from remote_agent_protocol.control_plane.models import (
    Activity,
    AgentObservation,
    Evidence,
    Health,
    Presence,
)
from tests.butler_fakes import (
    Call,
    Fail,
    FakeBridge,
    FakeDispatcher,
    FakeModel,
    Say,
    ServedModel,
    last_user,
    tool_results,
)


def _observation(agent_id: str) -> AgentObservation:
    now = datetime.now(UTC)
    return AgentObservation(
        agent_id=agent_id,
        display_name=agent_id,
        harness=agent_id,
        machine="Main PC",
        presence=Presence.REACHABLE,
        activity=Activity.IDLE,
        health=Health.HEALTHY,
        capabilities=frozenset(),
        evidence=(Evidence("fake", now, "Responded."),),
        observed_at=now,
        expires_at=now + timedelta(seconds=60),
    )


async def _plane_with_recent_answers(*names: str) -> AgentControlPlane:
    """A control plane whose agents all finished real work moments ago."""
    plane = AgentControlPlane(
        {n: FakeAgentAdapter(n, probe=_observation(n), discover=_observation(n)) for n in names}
    )
    for name in names:
        await plane.ingest_bridge_event(
            {
                "type": "agent_job",
                "event": "finished",
                "job_id": f"seed-{name}",
                "agent": name,
                "status": "done",
                "elapsed_secs": 2.0,
            }
        )
    return plane


def _toolbox(bridge, plane, dispatcher, *, confirm_words=("delete",), refuse=None):
    held: dict[str, tuple[str, str]] = {}

    def hold(agent, task):
        token = f"confirm-{len(held) + 1}"
        held[token] = (agent, task)
        return token

    box = ButlerToolbox(
        bridge=bridge,
        control_plane=plane,
        ledger=TaskLedger(),
        dispatch=dispatcher,
        admit=lambda agent, task: refuse,
        needs_confirmation=lambda agent, task: any(w in task.lower() for w in confirm_words),
        hold_confirmation=hold,
        drop_confirmation=lambda token: held.pop(token, None) is not None,
        aliases={"code puppy": "code-puppy", "claude": "claude-code"},
        fresh_for_secs=120,
    )
    return box, held


# -- toolbox -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_agents_reports_evidence_and_starts_nothing():
    bridge = FakeBridge(("hermes", "codex"))
    dispatcher = FakeDispatcher(bridge)
    box, _ = _toolbox(bridge, await _plane_with_recent_answers("hermes", "codex"), dispatcher)

    result = await box.call("check_agents", "{}")

    assert dispatcher.calls == []
    assert "hermes: Up (responded in 2s" in result["summary"]
    assert "codex: Up" in result["summary"]


@pytest.mark.asyncio
async def test_start_then_retry_keeps_one_task_across_agents():
    bridge = FakeBridge()
    dispatcher = FakeDispatcher(bridge)
    box, _ = _toolbox(bridge, await _plane_with_recent_answers("hermes"), dispatcher)

    started = await box.call(
        "start_task",
        {
            "agent": "Hermes",
            "instructions": "Search email for school news",
            "subject": "school email",
        },
    )
    bridge.jobs["job-1"].status = agent_bridge.STATUS_FAILED
    bridge.jobs["job-1"].failure_detail = "quota exhausted"
    retried = await box.call("retry_task", {"agent": "codex"})

    assert started["status"] == "started" and started["task"] == "t1"
    assert retried["status"] == "started" and retried["task"] == "t1"
    assert dispatcher.calls == [
        ("hermes", "Search email for school news"),
        ("codex", "Search email for school news"),
    ]
    status = await box.call("task_status", {"task": "school"})
    assert status["agent"] == "codex"
    assert status["earlier_attempts"] == "hermes"


@pytest.mark.asyncio
async def test_a_destructive_task_is_held_not_started():
    bridge = FakeBridge()
    dispatcher = FakeDispatcher(bridge)
    box, held = _toolbox(bridge, await _plane_with_recent_answers(), dispatcher)

    result = await box.call(
        "start_task",
        {"agent": "codex", "instructions": "Delete old files in Downloads", "subject": "clean up"},
    )

    assert result["status"] == "needs_confirmation"
    assert dispatcher.calls == []
    assert list(held.values()) == [("codex", "Delete old files in Downloads")]
    status = await box.call("task_status", "{}")
    assert status["status"] == "awaiting_confirmation"
    cancelled = await box.call("cancel_task", "{}")
    assert cancelled["status"] == "cancelled" and held == {}


@pytest.mark.asyncio
async def test_admission_refusal_is_reported_and_nothing_is_sent():
    bridge = FakeBridge()
    dispatcher = FakeDispatcher(bridge)
    box, _ = _toolbox(
        bridge, await _plane_with_recent_answers(), dispatcher, refuse="hermes is at its cap"
    )

    result = await box.call(
        "start_task", {"agent": "hermes", "instructions": "x y z", "subject": "x"}
    )

    assert result["status"] == "refused"
    assert "at its cap" in result["summary"]
    assert dispatcher.calls == []


@pytest.mark.asyncio
async def test_unknown_agents_and_bad_arguments_come_back_as_results():
    bridge = FakeBridge()
    box, _ = _toolbox(bridge, await _plane_with_recent_answers(), FakeDispatcher(bridge))

    unknown = await box.call("start_task", {"agent": "jarvis", "instructions": "a", "subject": "a"})
    bad_json = await box.call("task_status", "{not json")
    no_tool = await box.call("delete_everything", "{}")

    assert "no agent called 'jarvis'" in unknown["error"]
    assert "not valid JSON" in bad_json["error"]
    assert "no tool named" in no_tool["error"]


@pytest.mark.asyncio
async def test_aliases_list_tasks_cancel_and_model_switch():
    bridge = FakeBridge()
    dispatcher = FakeDispatcher(bridge)
    box, _ = _toolbox(bridge, await _plane_with_recent_answers(), dispatcher)
    await box.call(
        "start_task", {"agent": "code puppy", "instructions": "fix tests", "subject": "fix tests"}
    )
    bridge.add_job("job-9", "hermes", "a job started from the GUI")

    active = await box.call("list_tasks", {"scope": "active"})
    cancelled = await box.call("cancel_task", {"task": "fix tests"})
    switched = await box.call("set_agent_model", {"agent": "hermes", "provider": "OpenRouter"})
    unsupported = await box.call("set_agent_model", {"agent": "codex", "provider": "openrouter"})

    assert dispatcher.calls[0][0] == "code-puppy"
    assert {row["task"] for row in active["tasks"]} == {"t1", "job-9"}
    assert cancelled["status"] == "cancelled" and bridge.cancelled == ["job-1"]
    assert switched["summary"] == "hermes will use OpenRouter Flash from its next task."
    assert unsupported["status"] == "unsupported"


@pytest.mark.asyncio
async def test_get_result_returns_the_full_result():
    bridge = FakeBridge()
    dispatcher = FakeDispatcher(bridge)
    box, _ = _toolbox(bridge, await _plane_with_recent_answers(), dispatcher)
    await box.call("start_task", {"agent": "hermes", "instructions": "find it", "subject": "find"})
    job = bridge.jobs["job-1"]
    job.status, job.result, job.summary = agent_bridge.STATUS_DONE, "A" * 5000, "found it"

    result = await box.call("get_result", "{}")

    assert len(result["result"]) == 4000
    assert result["summary"].startswith("hermes finished: found it")


# -- loop ----------------------------------------------------------------------------


async def _run_loop(model: FakeModel, box, *, extra_endpoints=(), rounds=5):
    async with ServedModel(model) as endpoint, aiohttp.ClientSession() as http:
        loop = ButlerLoop(
            toolbox=box,
            endpoints=lambda: (*extra_endpoints, endpoint),
            http=lambda: http,
            max_rounds=rounds,
        )
        messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "go"}]
        return "".join([piece async for piece in loop.run(messages)])


@pytest.mark.asyncio
async def test_the_loop_runs_tools_and_speaks_from_their_results():
    bridge = FakeBridge()
    box, _ = _toolbox(bridge, await _plane_with_recent_answers("hermes"), FakeDispatcher(bridge))

    def model_brain(messages, tools_offered):
        results = tool_results(messages)
        if not results:
            return Call(("check_agents", {}))
        return Say(f"Here you go: {results[-1]['summary']}")

    model = FakeModel(model_brain)
    reply = await _run_loop(model, box)

    assert reply.startswith("Here you go: hermes: Up")
    assert model.requests[0]["tools"], "tools must be offered"
    assistant_call = model.requests[1]["messages"][-2]
    assert assistant_call["tool_calls"][0]["function"]["name"] == "check_agents"


@pytest.mark.asyncio
async def test_a_dead_endpoint_before_any_effect_hands_off_to_the_next():
    bridge = FakeBridge()
    box, _ = _toolbox(bridge, await _plane_with_recent_answers(), FakeDispatcher(bridge))
    dead = llm_endpoint.Endpoint(base_url="http://127.0.0.1:9/v1", model="x", cloud=True)

    reply = await _run_loop(
        FakeModel(lambda m, t: Say("Hello there.")), box, extra_endpoints=(dead,)
    )

    assert reply == "Hello there."


@pytest.mark.asyncio
async def test_every_endpoint_failing_first_raises_unavailable():
    bridge = FakeBridge()
    box, _ = _toolbox(bridge, await _plane_with_recent_answers(), FakeDispatcher(bridge))

    with pytest.raises(ButlerUnavailable):
        await _run_loop(FakeModel(lambda m, t: Fail()), box)


@pytest.mark.asyncio
async def test_a_failure_after_a_tool_ran_ends_with_what_the_tools_said():
    bridge = FakeBridge()
    dispatcher = FakeDispatcher(bridge)
    box, _ = _toolbox(bridge, await _plane_with_recent_answers(), dispatcher)

    def model_brain(messages, tools_offered):
        if not tool_results(messages):
            return Call(("start_task", {"agent": "hermes", "instructions": "do x", "subject": "x"}))
        return Fail()

    reply = await _run_loop(FakeModel(model_brain), box)

    assert len(dispatcher.calls) == 1, "the dispatch must not be repeated elsewhere"
    assert reply == "hermes has started 'x' (task t1). No result yet."


@pytest.mark.asyncio
async def test_the_last_round_offers_no_tools_so_the_model_must_answer():
    bridge = FakeBridge()
    box, _ = _toolbox(bridge, await _plane_with_recent_answers(), FakeDispatcher(bridge))

    def model_brain(messages, tools_offered):
        return Call(("list_tasks", {})) if tools_offered else Say("Nothing is running.")

    model = FakeModel(model_brain)
    reply = await _run_loop(model, box, rounds=2)

    assert reply == "Nothing is running."
    assert "tools" not in model.requests[-1]


# -- brain integration ---------------------------------------------------------------


@pytest.fixture
def butler_session(monkeypatch):
    monkeypatch.setattr(cfg, "MEMORY_ENABLED", False)
    monkeypatch.setattr(cfg, "BUTLER_TOOLS_ENABLED", True)
    session = brain.BrainSession(personas.PERSONAS[0])
    dispatched: list[tuple[str, str]] = []

    async def fake_dispatch(agent, instructions):
        dispatched.append((agent, instructions))
        job_id = f"job-b{len(dispatched)}"
        session._bridge._jobs[job_id] = agent_bridge.AgentJob(
            job_id=job_id, agent=agent, task=instructions
        )
        return DispatchOutcome(job_id, agent)

    session._butler._toolbox._dispatch = fake_dispatch
    session._butler._toolbox._admit = lambda agent, task: None
    return session, dispatched


async def _turn(session, model: FakeModel, text: str) -> str:
    async with ServedModel(model) as endpoint, aiohttp.ClientSession() as http:
        session._http = http
        session._butler._endpoints = lambda: (endpoint,)
        pieces = [str(p) async for p in session.complete_stream(text)]
        session._http = None
        return "".join(pieces)


@pytest.mark.asyncio
async def test_brain_status_questions_use_tools_and_never_dispatch(butler_session):
    session, dispatched = butler_session

    def model_brain(messages, tools_offered):
        if not tool_results(messages):
            return Call(("list_tasks", {"scope": "active"}))
        return Say(tool_results(messages)[-1]["summary"])

    reply = await _turn(session, FakeModel(model_brain), "Check for any active agents.")

    assert reply == "Nothing is running."
    assert dispatched == []


@pytest.mark.asyncio
async def test_brain_resolves_have_codex_do_it_to_the_earlier_task(butler_session):
    session, dispatched = butler_session

    def model_brain(messages, tools_offered):
        said = last_user(messages)
        results = tool_results(messages)
        if results:
            return Say(results[-1]["summary"])
        if said.startswith("Search my emails"):
            return Call(
                (
                    "start_task",
                    {
                        "agent": "hermes",
                        "instructions": "Search the user's email for news about Miles' school.",
                        "subject": "school email search",
                    },
                )
            )
        return Call(("retry_task", {"task": "school email", "agent": "codex"}))

    model = FakeModel(model_brain)
    first = await _turn(session, model, "Search my emails for news related to Miles' school.")
    second = await _turn(session, model, "Have Codex do it.")

    assert "hermes has started 'school email search'" in first
    assert "codex has started 'school email search' (task t1)" in second
    assert [agent for agent, _ in dispatched] == ["hermes", "codex"]
    assert dispatched[1][1] == "Search the user's email for news about Miles' school."
    history = [m["content"] for m in session._messages]
    assert history[0] == "Search my emails for news related to Miles' school."
    assert not any("tool" in str(m.get("role")) for m in session._messages)


@pytest.mark.asyncio
async def test_brain_holds_a_destructive_task_and_yes_sends_it(butler_session, monkeypatch):
    session, dispatched = butler_session
    confirmed: list[tuple[str, str]] = []

    async def fake_confirmed(token, agent, task, cwd):
        confirmed.append((agent, task))

    monkeypatch.setattr(session, "_dispatch_confirmed", fake_confirmed)

    def model_brain(messages, tools_offered):
        results = tool_results(messages)
        if results:
            return Say("That changes files, so please confirm first.")
        return Call(
            (
                "start_task",
                {
                    "agent": "codex",
                    "instructions": "Delete everything in Downloads",
                    "subject": "d",
                },
            )
        )

    first = await _turn(session, FakeModel(model_brain), "Clean up downloads.")
    reply = await _turn(session, FakeModel(lambda m, t: Say("unused")), "yes")
    for task in list(session._tasks):
        await task

    assert "confirm" in first
    assert dispatched == []
    assert reply == "Sending it to codex now."
    assert confirmed == [("codex", "Delete everything in Downloads")]


@pytest.mark.asyncio
async def test_brain_falls_back_to_the_router_when_the_model_is_down(butler_session, monkeypatch):
    session, _ = butler_session
    legacy: list[str] = []

    async def legacy_turn(text, llm_content):
        legacy.append(text)
        session._direct_reply = "router path reply"
        return "[control]"

    monkeypatch.setattr(session, "_turn_content", legacy_turn)

    reply = await _turn(session, FakeModel(lambda m, t: Fail()), "what's a good dog name?")

    assert legacy == ["what's a good dog name?"]
    assert reply == "router path reply"


@pytest.mark.asyncio
async def test_brain_answers_plain_chat_without_tools(butler_session):
    session, dispatched = butler_session

    reply = await _turn(
        session,
        FakeModel(lambda m, t: Say("How about Sunny?")),
        "What's a good name for a golden retriever?",
    )

    assert reply == "How about Sunny?"
    assert dispatched == []
    assert json.dumps(session._messages[-1]) == json.dumps(
        {"role": "assistant", "content": "How about Sunny?"}
    )


# -- agent events (C3) ----------------------------------------------------------------


async def _finish_job(session, job_id="job-e1", agent="hermes", status="done", **fields):
    job = agent_bridge.AgentJob(
        job_id=job_id, agent=agent, task="Search email for school news", status=status, **fields
    )
    job._t0 = 0.0
    job.secs = 12.0
    await session._announce_agent_job(job)
    return job


@pytest.mark.asyncio
async def test_a_finished_job_reaches_the_butler_as_a_tool_result(butler_session):
    session, dispatched = butler_session
    await _finish_job(session, result="Two events: a bake sale Oct 3 and picture day Oct 9.")
    seen: dict = {}

    def model_brain(messages, tools_offered):
        seen["tail"] = messages[-2:]
        seen["roles"] = [m["role"] for m in messages]
        return Say("Hermes found two school events: the bake sale and picture day.")

    model = FakeModel(model_brain)
    reply = await _turn(session, model, "[[announce]] [id=job-e1:done] [Agent job update: ...]")

    assert reply == "Hermes found two school events: the bake sale and picture day."
    call, result = seen["tail"]
    assert (
        call["role"] == "assistant" and call["tool_calls"][0]["function"]["name"] == "task_status"
    )
    event = json.loads(result["content"])
    assert event["event"] == "agent_finished" and "bake sale" in event["result"]
    assert "[[announce]]" not in json.dumps(seen["roles"])
    offered = {tool["function"]["name"] for tool in model.requests[0]["tools"]}
    assert "start_task" not in offered and "task_status" in offered
    assert not any("[[announce]]" in str(m.get("content")) for m in session._messages)
    assert dispatched == []


@pytest.mark.asyncio
async def test_an_event_turn_cannot_start_work(butler_session):
    session, dispatched = butler_session
    await _finish_job(session, job_id="job-e2", status="failed", failure_detail="quota")

    def model_brain(messages, tools_offered):
        results = tool_results(messages)
        if len(results) == 1:
            return Call(("start_task", {"agent": "codex", "instructions": "x", "subject": "x"}))
        return Say(results[-1]["summary"])

    reply = await _turn(session, FakeModel(model_brain), "[[announce]] [id=job-e2:failed] [..]")

    assert dispatched == []
    assert reply == "start_task is not available right now."


@pytest.mark.asyncio
async def test_an_unknown_announcement_keeps_the_narration_path(butler_session):
    session, _ = butler_session

    assert session._butler_active("[[announce]] [id=job-404:done] [Agent job update]") is False
    assert session._butler_active("hello") is True

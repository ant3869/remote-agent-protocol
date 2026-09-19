"""Agent exclusions and coordinator controls from the live voice conversation."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from remote_agent_protocol import agent_bridge, intent_router, voice_commands
from remote_agent_protocol import config as cfg
from remote_agent_protocol.control_plane.models import ControlError, JobHandle
from tests.test_brain_streaming import _brain, _collect, _control_snapshot

ALIASES = {"code puppy": "code-puppy", "puppy": "code-puppy", "codex": "codex"}
BACKENDS = {"code-puppy": {}, "codex": {}}
PING = "ping a different agent that's not code puppy"
REASSIGN = "Do not give it to Killer Puppy. Give it to someone else. Try again."
CANCEL = "Okay, then use another agent to cancel it"


@pytest.fixture(autouse=True)
def agent_config(monkeypatch):
    monkeypatch.setattr(cfg, "AGENT_BACKENDS", BACKENDS)
    monkeypatch.setattr(cfg, "AGENT_SPOKEN_ALIASES", ALIASES)


@pytest.mark.parametrize("text", [PING, REASSIGN, "Use anyone except code puppy", "Not Codex"])
def test_negated_agent_is_never_a_positive_selection(text):
    assert voice_commands.named_backend(text, BACKENDS, ALIASES) is None


@pytest.mark.parametrize("text", [PING, REASSIGN, CANCEL, "Use another agent", "Use someone else"])
@pytest.mark.asyncio
async def test_ambiguous_control_never_reaches_classifier(text):
    classify = AsyncMock(
        return_value={
            "intent": "agent_task",
            "category": "other_action",
            "task": text,
            "confidence": 0.99,
            "reason": text,
        }
    )
    router = intent_router.IntentRouter(classify=classify, enabled=True)
    decision = await router.route(text, "code-puppy")
    assert decision.action == intent_router.ACTION_NONE
    assert decision.source == "control"
    assert not decision.agent
    classify.assert_not_awaited()


@pytest.mark.parametrize(
    "text",
    [
        "Ask Codex to fix the tests, not code puppy",
        "Codex, fix the tests",
        "Tell code puppy to check the logs",
        "Ask Codex to inspect another agent",
    ],
)
@pytest.mark.asyncio
async def test_positive_named_requests_still_dispatch(text):
    router = intent_router.IntentRouter(enabled=False)
    decision = await router.route(text, "code-puppy")
    assert decision.action == intent_router.ACTION_DISPATCH
    assert decision.agent == ("code-puppy" if text.startswith("Tell code") else "codex")


@pytest.mark.parametrize(
    "text", [CANCEL, "Ask Codex to cancel it", "Use another agent to cancel all tasks"]
)
def test_cancel_wrapper_targets_the_work_not_the_proposed_executor(text):
    assert voice_commands.parse_agent_cancel(text, ALIASES) == (None, "all" in text)


@pytest.mark.parametrize("text", ["Ask Codex to cancel the meeting", "Do not cancel it"])
def test_other_cancellation_objects_and_negation_are_not_local_cancels(text):
    assert voice_commands.parse_agent_cancel(text, ALIASES) is None


@pytest.mark.asyncio
async def test_live_cancel_uses_bridge_and_blocks_markers(monkeypatch):
    brain = _brain(monkeypatch, ["Done. [[delegate: cancel the task]]"])
    brain._bridge.cancel_active = AsyncMock(return_value=1)
    brain._resolve_delegation = AsyncMock(side_effect=AssertionError("must stay local"))
    dispatch = []
    monkeypatch.setattr(brain, "_delegate_ack", lambda *args: dispatch.append(args))
    await _collect(brain, CANCEL)
    brain._bridge.cancel_active.assert_awaited_once_with(None, all_jobs=False)
    assert not dispatch


@pytest.mark.asyncio
async def test_alternative_ping_reports_only_nonexcluded_control_plane_evidence(monkeypatch):
    brain = _brain(monkeypatch, ["Checking. [[delegate: ping code puppy]]"])
    snapshot = _control_snapshot()
    brain._control_plane.list_agents = AsyncMock(
        return_value={
            agent: replace(
                snapshot,
                observation=replace(
                    snapshot.observation,
                    agent_id=agent,
                    display_name=agent,
                ),
            )
            for agent in BACKENDS
        }
    )
    brain._resolve_delegation = AsyncMock(side_effect=AssertionError("must stay local"))
    content = await brain._turn_content(PING, None)
    brain._control_plane.list_agents.assert_awaited_once_with(refresh=True)
    assert "Agent roll call" in content
    assert "code-puppy" not in content
    assert "codex on Main PC: reachable, idle, healthy (current)" in content
    assert brain._control_turn


@pytest.mark.asyncio
async def test_unspecified_reassignment_clarifies_locally_without_changing_default(monkeypatch):
    brain = _brain(monkeypatch, ["[[delegate: try again]]"])
    brain._default_agent_backend = "code-puppy"
    brain._resolve_delegation = AsyncMock(side_effect=AssertionError("must stay local"))
    brain._bridge.cancel_active = AsyncMock()
    response = "".join(await _collect(brain, REASSIGN))
    assert "which agent" in response.lower()
    assert brain._default_agent_backend == "code-puppy"
    brain._bridge.cancel_active.assert_not_awaited()


@pytest.mark.asyncio
async def test_exclusion_cannot_approve_pending_default_task(monkeypatch):
    brain = _brain(monkeypatch, ["[[delegate: try again]]"])
    brain._maybe_consume_confirmation = lambda text: (_ for _ in ()).throw(
        AssertionError("selection correction must precede confirmation")
    )
    await brain._turn_content("Okay, use another agent", None)
    assert brain._control_turn


@pytest.mark.parametrize("count,expected", [(1, "I cancelled 1"), (0, "no matching active")])
@pytest.mark.asyncio
async def test_cancel_reports_actual_local_result_without_model_claims(
    monkeypatch, count, expected
):
    brain = _brain(monkeypatch, ["I cannot do that; I must delegate it."])
    brain._bridge.cancel_active = AsyncMock(return_value=count)
    assert expected in "".join(await _collect(brain, CANCEL))


@pytest.mark.asyncio
async def test_rollcall_reports_probe_failure_without_model_health_claims(monkeypatch):
    brain = _brain(monkeypatch, ["All agents are healthy. [[delegate: ping]]"])
    brain._control_plane.list_agents = AsyncMock(
        return_value={
            "codex": ControlError("timeout", "Probe timed out.", "codex"),
        }
    )
    response = "".join(await _collect(brain, "Ping Codex"))
    assert "I checked" in response
    assert "could not be verified (timeout)" in response
    assert "healthy" not in response


@pytest.mark.asyncio
async def test_actual_response_diagnostic_stays_local_and_starts_fixed_check(monkeypatch):
    brain = _brain(monkeypatch, ["I will delegate this."])
    brain._control_plane.list_agents = AsyncMock(return_value={"codex": _control_snapshot()})
    brain._control_plane.request_response_check = AsyncMock(
        return_value=JobHandle("check-1", "codex")
    )
    brain._resolve_delegation = AsyncMock(side_effect=AssertionError("must stay local"))

    response = "".join(await _collect(brain, "Which agents are actually responding?"))

    brain._control_plane.list_agents.assert_awaited_once_with(refresh=True)
    brain._control_plane.request_response_check.assert_awaited_once_with("codex")
    assert "self-check is pinging" in response
    assert "Version probes prove only" in response
    assert "[[delegate" not in response


@pytest.mark.asyncio
async def test_named_agent_server_diagnosis_remains_delegated_work(monkeypatch):
    brain = _brain(monkeypatch, ["unused"])
    brain._handle_agent_diagnostic = AsyncMock(
        side_effect=AssertionError("real work must not become a local self-check")
    )
    brain._resolve_delegation = AsyncMock(
        return_value=("codex", "why the server is not responding")
    )
    brain._delegate_ack = MagicMock(return_value="delegated")

    content = await brain._turn_content("Ask Codex why the server is not responding", None)

    assert content == "delegated"
    brain._resolve_delegation.assert_awaited_once()
    brain._delegate_ack.assert_called_once_with("codex", "why the server is not responding")
    brain._handle_agent_diagnostic.assert_not_awaited()


@pytest.mark.asyncio
async def test_internal_diagnostic_result_is_not_relayed_by_brain(monkeypatch):
    brain = _brain(monkeypatch, ["unused"])
    brain._emit = MagicMock()
    brain._orchestrator.record_outcome = MagicMock()
    job = agent_bridge.AgentJob(
        "check-1",
        "codex",
        "RAP self-check",
        status=agent_bridge.STATUS_DONE,
        result="RAP_SELF_CHECK_OK",
        internal=True,
    )

    await brain._announce_agent_job(job)

    assert brain._messages == []
    brain._orchestrator.record_outcome.assert_not_called()
    brain._emit.assert_not_called()


@pytest.mark.parametrize(
    "text",
    [
        "Give it to Codex",
        "Do not give it to code puppy. Give it to Codex. Try again.",
    ],
)
@pytest.mark.asyncio
async def test_named_reassignment_uses_existing_coordinator(monkeypatch, text):
    brain = _brain(monkeypatch, ["[[delegate: redirect the task]]"])
    current = SimpleNamespace(
        job_id="job-1", agent="code-puppy", task="check logs", cwd="", announce_start=False
    )
    brain._bridge.latest_active = lambda: current
    brain._control_plane.redirect_job = AsyncMock(return_value=JobHandle("job-2", "codex"))
    brain._resolve_delegation = AsyncMock(side_effect=AssertionError("must stay local"))
    response = "".join(await _collect(brain, text))
    assert "I redirected the task from code-puppy to codex" in response
    args = brain._control_plane.redirect_job.await_args.args
    assert args[:2] == ("job-1", "codex")
    assert args[2].text == "check logs"
    assert brain._default_agent_backend == cfg.AGENT_DEFAULT_BACKEND


@pytest.mark.asyncio
async def test_named_status_is_a_local_read_with_stale_evidence(monkeypatch):
    brain = _brain(monkeypatch, ["[[delegate: inspect Codex]]"])
    snapshot = replace(_control_snapshot(), stale=True)
    brain._control_plane.get_agent_status = AsyncMock(return_value=snapshot)
    brain._resolve_delegation = AsyncMock(side_effect=AssertionError("must stay local"))
    response = "".join(await _collect(brain, "What is Codex working on?"))
    brain._control_plane.get_agent_status.assert_awaited_once_with("codex", refresh=False)
    assert "stale" in response
    assert "[[delegate" not in response


@pytest.mark.asyncio
async def test_alternative_ping_has_bounded_no_candidate_response(monkeypatch):
    brain = _brain(monkeypatch, ["This must not run."])
    brain._control_plane.list_agents = AsyncMock(return_value={"code-puppy": _control_snapshot()})
    response = "".join(await _collect(brain, PING))
    assert "no alternative agent backends" in response


@pytest.mark.parametrize(
    "text", ["Okay, ping Codex", "Okay, give it to Codex", "Okay, use Codex to cancel it"]
)
@pytest.mark.asyncio
async def test_local_controls_do_not_approve_an_unrelated_pending_task(monkeypatch, text):
    brain = _brain(monkeypatch, ["This must not run."])
    brain._maybe_consume_confirmation = lambda text: (_ for _ in ()).throw(
        AssertionError("local control must precede confirmation")
    )
    brain._control_plane.list_agents = AsyncMock(return_value={})
    brain._bridge.latest_active = lambda: None
    brain._bridge.cancel_active = AsyncMock(return_value=0)
    await brain._turn_content(text, None)
    assert brain._control_turn

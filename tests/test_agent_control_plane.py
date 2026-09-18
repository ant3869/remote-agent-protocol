"""Coordinator refresh and safety-policy contracts."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from remote_agent_protocol.control_plane.adapters.base import AgentTask
from remote_agent_protocol.control_plane.adapters.fake import FakeAgentAdapter
from remote_agent_protocol.control_plane.models import (
    Activity,
    AgentObservation,
    ControlResult,
    Evidence,
    Health,
    JobHandle,
    ObservedWork,
    Presence,
    ResponseState,
    WorkOwnership,
)
from remote_agent_protocol.control_plane.service import (
    SELF_CHECK_PROMPT,
    SELF_CHECK_SENTINEL,
    AgentControlPlane,
)


def make_observation(agent_id: str) -> AgentObservation:
    """Build a successful response for a fake adapter."""
    now = datetime.now(UTC)
    return AgentObservation(
        agent_id=agent_id,
        display_name=agent_id.title(),
        harness=agent_id,
        machine="Main PC",
        presence=Presence.REACHABLE,
        activity=Activity.IDLE,
        health=Health.HEALTHY,
        capabilities=frozenset(),
        evidence=(Evidence("fake", now, "Responded."),),
        observed_at=now,
        expires_at=now + timedelta(seconds=30),
    )


@pytest.mark.asyncio
async def test_refresh_returns_healthy_results_when_one_adapter_hangs() -> None:
    """One timeout must not hide evidence returned by another adapter."""
    healthy = FakeAgentAdapter("codex", probe=make_observation("codex"))

    async def hang():
        await asyncio.sleep(60)
        return make_observation("hermes")

    hung = FakeAgentAdapter("hermes", probe=hang, discover=make_observation("hermes"))
    plane = AgentControlPlane(
        {"codex": healthy, "hermes": hung}, probe_timeout_secs=0.01, overall_timeout_secs=0.1
    )

    results = await plane.list_agents(refresh=True)

    assert results["codex"].observation.presence == Presence.REACHABLE
    assert results["hermes"].code == "timeout"
    assert healthy.calls == ["probe"]


@pytest.mark.asyncio
async def test_concurrent_refresh_coalesces_one_adapter_probe() -> None:
    """Duplicate refresh callers share one in-flight probe."""
    observation = make_observation("codex")

    async def delayed():
        await asyncio.sleep(0.01)
        return observation

    adapter = FakeAgentAdapter("codex", probe=delayed, discover=observation)
    plane = AgentControlPlane({"codex": adapter})

    first, second = await asyncio.gather(
        plane.get_agent_status("codex", refresh=True),
        plane.get_agent_status("codex", refresh=True),
    )

    assert first.observation == second.observation
    assert adapter.calls == ["probe"]


@pytest.mark.asyncio
async def test_rap_job_can_be_cancelled_only_through_its_own_adapter() -> None:
    """Bridge lifecycle evidence establishes the safe cancellation target."""
    adapter = FakeAgentAdapter(
        "codex",
        probe=make_observation("codex"),
        discover=make_observation("codex"),
        cancel=ControlResult(True, "codex", "job-1"),
    )
    plane = AgentControlPlane({"codex": adapter})
    await plane.ingest_bridge_event(
        {
            "type": "agent_job",
            "event": "started",
            "job_id": "job-1",
            "agent": "codex",
            "machine": "Main PC",
            "status": "running",
            "task": "Check one file",
        }
    )

    result = await plane.cancel_job("job-1")

    assert result.ok
    assert adapter.calls == ["cancel"]


@pytest.mark.asyncio
async def test_redirect_cancels_before_dispatching_to_new_agent() -> None:
    """Redirection is an ordered replacement, never a second parallel task."""
    source = FakeAgentAdapter(
        "codex",
        probe=make_observation("codex"),
        discover=make_observation("codex"),
        cancel=ControlResult(True, "codex", "job-1"),
    )
    target = FakeAgentAdapter(
        "hermes",
        probe=make_observation("hermes"),
        discover=make_observation("hermes"),
        dispatch=JobHandle("job-2", "hermes"),
    )
    plane = AgentControlPlane({"codex": source, "hermes": target})
    await plane.ingest_bridge_event(
        {
            "type": "agent_job",
            "event": "started",
            "job_id": "job-1",
            "agent": "codex",
            "status": "running",
            "task": "Check one file",
        }
    )

    result = await plane.redirect_job("job-1", "hermes", task=AgentTask("Continue the check"))

    assert result.job_id == "job-2"
    assert source.calls == ["cancel"]
    assert target.calls == ["dispatch"]


@pytest.mark.asyncio
async def test_response_check_tracks_only_the_fixed_sentinel_as_actual_evidence() -> None:
    """A CLI version probe must not be mistaken for a harness response."""
    adapter = FakeAgentAdapter(
        "codex",
        probe=make_observation("codex"),
        dispatch=JobHandle("self-check-1", "codex"),
    )
    plane = AgentControlPlane({"codex": adapter})

    started = await plane.request_response_check("codex")

    assert started == JobHandle("self-check-1", "codex")
    assert adapter.tasks == [AgentTask(SELF_CHECK_PROMPT, announce_start=False)]
    pending = await plane.get_agent_status("codex")
    assert pending.observation.response_state is ResponseState.PENDING
    await plane.ingest_bridge_event(
        {
            "type": "agent_job",
            "event": "finished",
            "job_id": "self-check-1",
            "agent": "codex",
            "status": "done",
            "result": SELF_CHECK_SENTINEL,
        }
    )

    confirmed = await plane.get_agent_status("codex")
    assert confirmed.observation.response_state is ResponseState.RESPONDED
    assert confirmed.observation.evidence[0].detail == (
        "RAP received the exact fixed response from this harness."
    )


@pytest.mark.asyncio
async def test_response_check_reports_rate_limit_without_persisting_agent_output() -> None:
    adapter = FakeAgentAdapter(
        "codex",
        probe=make_observation("codex"),
        dispatch=JobHandle("self-check-2", "codex"),
    )
    plane = AgentControlPlane({"codex": adapter})
    await plane.request_response_check("codex")

    await plane.ingest_bridge_event(
        {
            "type": "agent_job",
            "event": "finished",
            "job_id": "self-check-2",
            "agent": "codex",
            "status": "failed",
            "result": "provider prose that must not become evidence",
            "failure_kind": "rate_limit",
            "failure_detail": "429 provider response",
        }
    )

    result = await plane.get_agent_status("codex")
    assert result.observation.response_state is ResponseState.FAILED
    assert result.observation.health is Health.DEGRADED
    assert "rate_limit" in result.observation.evidence[0].detail
    assert "provider prose" not in result.observation.evidence[0].detail


@pytest.mark.asyncio
async def test_response_check_does_not_compete_with_a_busy_rap_job() -> None:
    busy = replace(
        make_observation("codex"),
        activity=Activity.WORKING,
        current_work=ObservedWork(
            job_id="real-job",
            ownership=WorkOwnership.RAP,
            summary="real user task",
            state=Activity.WORKING,
        ),
    )
    adapter = FakeAgentAdapter("codex", probe=busy)
    plane = AgentControlPlane({"codex": adapter})

    result = await plane.request_response_check("codex")

    assert not result.ok
    assert result.error.code == "busy"
    assert adapter.tasks == []

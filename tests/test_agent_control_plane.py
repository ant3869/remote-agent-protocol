"""Coordinator refresh and safety-policy contracts."""

import asyncio
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
    Presence,
)
from remote_agent_protocol.control_plane.service import AgentControlPlane


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

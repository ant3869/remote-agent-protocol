"""AgentRegistry ordering and stale-state contracts."""

from datetime import UTC, datetime, timedelta

import pytest

from remote_agent_protocol.control_plane.models import (
    Activity,
    AgentObservation,
    Evidence,
    Health,
    Presence,
)
from remote_agent_protocol.control_plane.registry import AgentRegistry


def make_observation(at: datetime) -> AgentObservation:
    """Build a minimal observation at an exact instant."""
    return AgentObservation(
        agent_id="hermes",
        display_name="Hermes",
        harness="hermes",
        machine="Main PC",
        presence=Presence.REACHABLE,
        activity=Activity.IDLE,
        health=Health.HEALTHY,
        capabilities=frozenset(),
        evidence=(Evidence("test", at, "Observed."),),
        observed_at=at,
        expires_at=at + timedelta(seconds=10),
    )


@pytest.mark.asyncio
async def test_older_current_observation_cannot_replace_newer_one() -> None:
    """Late adapter results cannot regress live registry state."""
    registry = AgentRegistry()
    now = datetime.now(UTC)
    newer = await registry.observe(make_observation(now))
    older = await registry.observe(make_observation(now - timedelta(seconds=1)))

    assert older.observation.observed_at == newer.observation.observed_at


@pytest.mark.asyncio
async def test_mark_all_stale_preserves_observation() -> None:
    """Restart invalidates freshness without discarding useful context."""
    registry = AgentRegistry()
    await registry.observe(make_observation(datetime.now(UTC)))

    await registry.mark_all_stale()

    assert (await registry.get("hermes")).stale

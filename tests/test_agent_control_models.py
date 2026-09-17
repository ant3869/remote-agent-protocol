"""Control-plane model and persistence contracts."""

from datetime import UTC, datetime, timedelta

import pytest

from remote_agent_protocol.control_plane.models import (
    Activity,
    AgentCapability,
    AgentObservation,
    AgentSnapshot,
    Evidence,
    Health,
    Presence,
)
from remote_agent_protocol.control_plane.store import AgentRegistryStore


def observation(now: datetime | None = None) -> AgentObservation:
    """Build a current, evidence-backed observation for one test harness."""
    now = now or datetime.now(UTC)
    return AgentObservation(
        agent_id="codex",
        display_name="Codex",
        harness="codex",
        machine="Main PC",
        presence=Presence.REACHABLE,
        activity=Activity.IDLE,
        health=Health.HEALTHY,
        capabilities=frozenset({AgentCapability.ACCEPT_TASK}),
        evidence=(Evidence("test", now, "Version command responded."),),
        observed_at=now,
        expires_at=now + timedelta(seconds=30),
    )


def test_snapshot_becomes_stale_at_expiry() -> None:
    """Freshness is explicit and does not rewrite the original evidence time."""
    now = datetime.now(UTC)
    snapshot = AgentSnapshot(observation(now))

    assert not snapshot.is_stale(now)
    assert snapshot.is_stale(now + timedelta(seconds=30))
    assert snapshot.as_stale().observation.observed_at == now


def test_observation_rejects_naive_evidence_time() -> None:
    """Operational facts cannot carry ambiguous local timestamps."""
    with pytest.raises(ValueError, match="timezone-aware"):
        Evidence("test", datetime.now(), "bad timestamp")


def test_persisted_snapshot_is_loaded_as_stale(tmp_path) -> None:
    """A prior process's observation is never reported current after restart."""
    store = AgentRegistryStore(tmp_path / "agent_registry.json")
    store.save({"codex": AgentSnapshot(observation())})

    loaded = store.load()

    assert loaded["codex"].stale
    assert loaded["codex"].observation.presence == Presence.REACHABLE

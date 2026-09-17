"""Concurrency-safe current and last-known agent observations."""
# ruff: noqa: D102, D107

from __future__ import annotations

import asyncio
from pathlib import Path

from .models import AgentObservation, AgentSnapshot
from .store import AgentRegistryStore


class AgentRegistry:
    """Keeps the newest observation per agent and persists it atomically."""

    def __init__(self, store_path: Path | None = None):
        self._store = AgentRegistryStore(store_path) if store_path else None
        self._snapshots = self._store.load() if self._store else {}
        self._lock = asyncio.Lock()

    async def observe(self, observation: AgentObservation) -> AgentSnapshot:
        """Record an observation unless a newer current one is already known."""
        async with self._lock:
            current = self._snapshots.get(observation.agent_id)
            if (
                current is not None
                and not current.stale
                and current.observation.observed_at > observation.observed_at
            ):
                return current
            snapshot = AgentSnapshot(observation)
            self._snapshots[observation.agent_id] = snapshot
            self._persist()
            return snapshot

    async def get(self, agent_id: str) -> AgentSnapshot | None:
        async with self._lock:
            return self._snapshots.get(agent_id)

    async def list(self) -> tuple[AgentSnapshot, ...]:
        async with self._lock:
            return tuple(self._snapshots[key] for key in sorted(self._snapshots))

    async def mark_all_stale(self) -> None:
        async with self._lock:
            self._snapshots = {
                key: snapshot.as_stale() for key, snapshot in self._snapshots.items()
            }
            self._persist()

    def _persist(self) -> None:
        if self._store is not None:
            self._store.save(self._snapshots)

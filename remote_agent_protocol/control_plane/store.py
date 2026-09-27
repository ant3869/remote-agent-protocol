"""Atomic persistence for last-known control-plane observations."""
# ruff: noqa: D102, D107

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .models import AgentSnapshot


class AgentRegistryStore:
    """Stores only bounded normalized snapshots, never raw harness output."""

    def __init__(self, path: Path):
        self._path = path

    def load(self) -> dict[str, AgentSnapshot]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            snapshots = raw.get("snapshots", {})
            return {
                agent_id: AgentSnapshot.from_dict(snapshot).as_stale()
                for agent_id, snapshot in snapshots.items()
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # A damaged status cache must never decide what is currently true.
            return {}

    def save(self, snapshots: dict[str, AgentSnapshot]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        payload = {
            "version": 1,
            "snapshots": {key: value.to_dict() for key, value in snapshots.items()},
        }
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        # Windows refuses to replace a file another process (a second reader,
        # antivirus, the indexer) has open at that instant; it clears quickly.
        for delay in (0.02, 0.05, 0.1, 0.2):
            try:
                os.replace(tmp, self._path)
                return
            except PermissionError:
                time.sleep(delay)
        os.replace(tmp, self._path)

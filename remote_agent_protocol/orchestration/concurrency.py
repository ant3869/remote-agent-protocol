"""ConcurrencyGuard -- structural prevention of duplicate/over-cap delegation.

Reads ``AgentBridge.active_jobs()`` as the sole source of truth for what's
currently running; there is no separate job registry that could drift from
it. This is what makes "duplicate delegation" and "runaway concurrent jobs"
structurally impossible rather than merely discouraged -- the check runs
independent of anything a harness or an acknowledgment turn claims.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from remote_agent_protocol import config as cfg

if TYPE_CHECKING:
    from remote_agent_protocol import agent_bridge

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_task(text: str) -> str:
    """Fold whitespace/case so near-identical task text still compares equal."""
    return _WHITESPACE_RE.sub(" ", (text or "").strip().lower())


@dataclass
class ConcurrencyGuard:
    """Global/per-harness active-job caps plus duplicate-task detection."""

    bridge: agent_bridge.AgentBridge
    global_cap: int = 2
    harness_cap: int = 1
    # Per-harness overrides of ``harness_cap``.
    harness_caps: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        caps = [self.global_cap, self.harness_cap, *self.harness_caps.values()]
        if any(cap <= 0 for cap in caps):
            raise ValueError("concurrency caps must be positive")

    def cap_for(self, agent: str) -> int:
        """How many jobs ``agent`` may run at once."""
        return self.harness_caps.get(agent, self.harness_cap)

    def admit(self, agent: str, task: str) -> tuple[bool, str]:
        """Return ``(allowed, reason)``; ``reason`` is set only when denied."""
        active = self.bridge.active_jobs()
        if len(active) >= self.global_cap:
            return False, f"already {len(active)} job(s) running (limit {self.global_cap})"
        same_harness = [job for job in active if job.agent == agent]
        if len(same_harness) >= self.cap_for(agent):
            return False, f"'{agent}' already has {len(same_harness)} job(s) running"
        normalized = normalize_task(task)
        for job in active:
            if normalize_task(job.task) == normalized:
                return False, f"an identical task is already running as job {job.job_id}"
        return True, ""


def default_guard(bridge: agent_bridge.AgentBridge) -> ConcurrencyGuard:
    """Build a ConcurrencyGuard using the configured caps in config.py."""
    return ConcurrencyGuard(
        bridge=bridge,
        global_cap=cfg.ORCHESTRATION_GLOBAL_JOB_CAP,
        harness_cap=cfg.ORCHESTRATION_HARNESS_JOB_CAP,
        harness_caps=dict(cfg.ORCHESTRATION_HARNESS_JOB_CAPS),
    )

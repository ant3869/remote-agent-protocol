"""Evidence-backed agent status and coordinator controls.

This package deliberately sits above :mod:`remote_agent_protocol.agent_bridge`:
the bridge continues to own RAP-launched subprocesses while this layer owns
observations, freshness, adapter capabilities, and coordinator decisions.
"""

from .models import (
    Activity,
    AgentCapability,
    AgentObservation,
    AgentSnapshot,
    Evidence,
    Health,
    Presence,
    ResponseState,
    UpdateState,
    WorkOwnership,
)
from .registry import AgentRegistry
from .service import AgentControlPlane

__all__ = [
    "Activity",
    "AgentCapability",
    "AgentControlPlane",
    "AgentObservation",
    "AgentRegistry",
    "AgentSnapshot",
    "Evidence",
    "Health",
    "Presence",
    "ResponseState",
    "UpdateState",
    "WorkOwnership",
]

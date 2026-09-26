"""Tool-calling Butler: one model decides, tools act, replies come from tool results.

``ButlerLoop`` runs the model with ``ButlerToolbox``'s tools; ``TaskLedger``
remembers what the user asked for across attempts on different agents.
"""

from remote_agent_protocol.butler.ledger import Attempt, ButlerTask, TaskLedger
from remote_agent_protocol.butler.loop import ButlerLoop, ButlerUnavailable
from remote_agent_protocol.butler.tools import (
    READ_ONLY_TOOLS,
    TOOL_SCHEMAS,
    ButlerToolbox,
    DispatchOutcome,
)

__all__ = [
    "READ_ONLY_TOOLS",
    "TOOL_SCHEMAS",
    "Attempt",
    "ButlerLoop",
    "ButlerTask",
    "ButlerToolbox",
    "ButlerUnavailable",
    "DispatchOutcome",
    "TaskLedger",
]

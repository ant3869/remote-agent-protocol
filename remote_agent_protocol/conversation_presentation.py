"""Presentation composition shared by full voice mode and Brain mode.

``AgentConversationHub`` events carry only identifiers and classification --
never narration text (see ``conversation_hub.events`` and ``results.py``'s
module docstring). Turning that data into what Butler actually says belongs
in exactly one place so voice and Brain modes never drift into two
independently written narration blocks, which is the duplication pattern
Task 8 exists to remove (see task-8-brief.md).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from remote_agent_protocol.conversation_hub.models import ConversationTurn, ResultKind
from remote_agent_protocol.conversation_hub.results import (
    RECOVERY_CLARIFY,
    RECOVERY_MANUAL,
    RECOVERY_REASSIGN,
    RECOVERY_RETRY,
)

# Brain mode has no local audio graph and no mem0/semantic-memory wiring (zero
# references in brain.py). A hub result that implies either capability --
# voice delivery of ``spoken_text``, or a semantic-memory write outside the
# hub's own ScopedMemory -- is marked rather than silently presented as if it
# fully succeeded (task-8-brief.md, "Brain-mode explicit degradation").
BRAIN_DEGRADATION_NOTE = (
    "[Note: delivered as text only -- Brain mode has no voice output or its "
    "own semantic memory; only what the conversation hub already recorded "
    "for this channel persists.]"
)


@dataclass(frozen=True)
class ResultPresentation:
    """What to say or relay for one agent-authored result, per output mode."""

    agent_id: str
    result_kind: ResultKind
    full_text: str
    spoken_text: str
    voice_text: str
    brain_text: str
    degraded_for_brain: bool


def present_hub_result(turn: ConversationTurn, *, for_brain: bool = False) -> ResultPresentation:
    """Compose the narration/relay text for one agent-authored result turn.

    ``turn`` must be the agent-authored :class:`ConversationTurn` fetched via
    ``hub.turns(channel_id)`` for a ``RESULT_AVAILABLE`` event's ``task_id``
    -- never raw bridge output, which the hub's ``ResultPresenter`` has
    already turned into ``full_text``/``spoken_text`` before this point.
    """
    spoken = turn.spoken_text or turn.full_text
    brain_text = f"[Agent result from {turn.speaker_id}: {turn.full_text}]"
    degraded = for_brain and bool(turn.spoken_text)
    if degraded:
        brain_text = f"{brain_text}\n{BRAIN_DEGRADATION_NOTE}"
    return ResultPresentation(
        agent_id=turn.speaker_id,
        result_kind=turn.result_kind or ResultKind.SUCCESS,
        full_text=turn.full_text,
        spoken_text=spoken,
        voice_text=spoken,
        brain_text=brain_text,
        degraded_for_brain=degraded,
    )


# Butler speaks from a RecoveryDecision, never for it -- see results.py's
# module docstring. These templates are the sole place that turns a recovery
# classification into words; the ``candidate`` slot is only meaningful for a
# reassignment, so a template that ignores it does not need it populated.
_RECOVERY_LINES = {
    RECOVERY_RETRY: "I'll have {agent} try that again.",
    RECOVERY_REASSIGN: "That didn't work on {agent}, so I'm moving it to {candidate}.",
    RECOVERY_CLARIFY: "{agent} is waiting on something I can't answer for you -- it needs your input.",
    RECOVERY_MANUAL: "{agent} ran into a problem I can't fix on my own: {detail}",
}


def present_butler_intervention(
    data: Mapping[str, Any], *, agent_id: str = "", detail: str = ""
) -> str:
    """Compose Butler's spoken recovery line from a ``BUTLER_INTERVENTION_STARTED`` payload.

    ``data`` carries only identifiers/classification by design -- this is
    where that data becomes narration, composed once so both modes speak the
    same recovery language for the same failure.
    """
    kind = str(data.get("recovery_kind") or RECOVERY_MANUAL)
    candidate = data.get("candidate_agent_id") or "another agent"
    template = _RECOVERY_LINES.get(kind, _RECOVERY_LINES[RECOVERY_MANUAL])
    return template.format(agent=agent_id or "the agent", candidate=candidate, detail=detail)


# A _NO_DISPATCH_KINDS decision (acknowledgment/clarification/unavailable/
# return_to_butler) can leave TurnDisposition.spoken_acknowledgment unset --
# e.g. plain smalltalk has no FloorDecision.spoken_text at all. This is what
# a dispatch call relays instead of nothing, so a caller that already told
# the user work was starting never leaves that promise silently unresolved
# (task-8 review round 1, #1).
NO_DISPATCH_FALLBACK = "There's nothing new to dispatch for that."


def present_no_dispatch_explanation(spoken_acknowledgment: str | None) -> str:
    """Return what to relay when a hub dispatch call resolves to no dispatch."""
    return spoken_acknowledgment or NO_DISPATCH_FALLBACK

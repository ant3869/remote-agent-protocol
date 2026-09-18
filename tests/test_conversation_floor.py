"""Deterministic routing coverage for the conversation floor."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from remote_agent_protocol.conversation_hub.floor import FloorManager, TurnRoutingInput
from remote_agent_protocol.conversation_hub.models import TaskReference

NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)
BACKENDS = {"openclaw": object(), "codex": object()}
ALIASES = {"openclaw": "openclaw", "codex": "codex"}


def task(task_id: str, agent_id: str, *, status: str = "active") -> TaskReference:
    """Build a minimal durable task reference for routing cases."""
    return TaskReference(
        task_id=task_id,
        channel_id=f"agent:{agent_id}",
        agent_id=agent_id,
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture
def manager() -> FloorManager:
    """Use the live alias-parser inputs without loading application config."""
    return FloorManager(backends=BACKENDS, aliases=ALIASES)


@pytest.mark.parametrize(
    ("text", "current", "kind", "target"),
    [
        ("OpenClaw, check my email", "butler", "direct", "openclaw"),
        ("What about school messages?", "openclaw", "follow_up", "openclaw"),
        ("Why?", "openclaw", "follow_up", "openclaw"),
        ("How?", "openclaw", "follow_up", "openclaw"),
        ("Thanks", "openclaw", "acknowledgment", "openclaw"),
        ("Butler", "openclaw", "return_to_butler", "butler"),
        ("Plan a new dashboard", "openclaw", "butler_mediated", "butler"),
    ],
)
def test_floor_decision(
    manager: FloorManager, text: str, current: str, kind: str, target: str
) -> None:
    """Direct address, floor continuity, and new work take deterministic paths."""
    decision = manager.resolve(
        TurnRoutingInput(
            text=text,
            current_floor=current,
            last_speaker_id=current,
            now=NOW,
            active_tasks=(task("email", "openclaw"),),
        )
    )

    assert (decision.kind, decision.target_id) == (kind, target)


def test_unnamed_work_requires_butler_selection(manager: FloorManager) -> None:
    """Unaddressed new work returns to the Butler front door."""
    decision = manager.resolve(TurnRoutingInput(text="Research a new monitor", now=NOW))

    assert decision.kind == "butler_mediated"
    assert decision.target_id == "butler"
    assert decision.requires_butler_selection is True
    assert decision.reason_code == "unnamed_new_work"


def test_acknowledgment_targets_last_actual_speaker(manager: FloorManager) -> None:
    """A completion must not overwrite the speaker that receives a thank-you."""
    decision = manager.resolve(
        TurnRoutingInput(
            text="Thanks",
            current_floor="codex",
            last_speaker_id="openclaw",
            now=NOW,
        )
    )

    assert decision.target_id == "openclaw"
    assert decision.reason_code == "acknowledgment_last_speaker"


def test_ambiguous_deictic_follow_up_requests_short_butler_clarification(
    manager: FloorManager,
) -> None:
    """Multiple active tasks make an unqualified 'that' unsafe to guess."""
    decision = manager.resolve(
        TurnRoutingInput(
            text="Can you continue that?",
            current_floor="openclaw",
            now=NOW,
            active_tasks=(task("email", "openclaw"), task("tests", "codex")),
        )
    )

    assert (decision.kind, decision.target_id, decision.task_id) == (
        "clarification",
        "butler",
        None,
    )
    assert decision.requires_clarification is True
    assert decision.spoken_text == "Which task do you mean?"
    assert decision.reason_code == "ambiguous_task_reference"


def test_named_unavailable_agent_routes_butler_to_verified_alternatives(
    manager: FloorManager,
) -> None:
    """A direct request never impersonates an unavailable named agent."""
    decision = manager.resolve(
        TurnRoutingInput(
            text="OpenClaw, check my email",
            current_floor="butler",
            now=NOW,
            available_agent_ids=frozenset({"codex"}),
            verified_agent_ids=frozenset({"codex"}),
            unavailability_evidence="its latest probe failed",
        )
    )

    assert (decision.kind, decision.target_id) == ("unavailable", "butler")
    assert decision.requires_butler_selection is False
    assert decision.verified_alternatives == ("codex",)
    assert (
        decision.spoken_text == "OpenClaw is unavailable: its latest probe failed. I can use Codex."
    )
    assert decision.reason_code == "named_agent_unavailable"


def test_background_completion_is_spoken_by_owner_without_stealing_floor(
    manager: FloorManager,
) -> None:
    """Task ownership and the conversation floor remain independent."""
    decision = manager.resolve(
        TurnRoutingInput(
            text="",
            current_floor="codex",
            now=NOW,
            active_tasks=(task("email", "openclaw"),),
            completion_task_id="email",
        )
    )

    assert (decision.kind, decision.target_id, decision.task_id) == (
        "completion",
        "openclaw",
        "email",
    )
    assert decision.next_floor_id == "codex"
    assert decision.reason_code == "completion_task_owner"


def test_named_agent_switch_carries_only_referenced_active_task(manager: FloorManager) -> None:
    """An explicit new agent takes the active referenced task, not the whole floor."""
    decision = manager.resolve(
        TurnRoutingInput(
            text="Codex, take over that",
            current_floor="openclaw",
            now=NOW,
            current_task_id="email",
            active_tasks=(task("email", "openclaw"), task("tests", "codex")),
        )
    )

    assert (decision.kind, decision.target_id, decision.task_id) == (
        "direct",
        "codex",
        "email",
    )
    assert decision.reason_code == "explicit_agent_task_switch"


def test_new_direct_work_does_not_claim_the_prior_current_task(manager: FloorManager) -> None:
    """Naming another agent for fresh work does not silently transfer old work."""
    decision = manager.resolve(
        TurnRoutingInput(
            text="Codex, research a monitor",
            current_floor="openclaw",
            current_task_id="email",
            now=NOW,
            active_tasks=(task("email", "openclaw"),),
        )
    )

    assert decision.task_id is None
    assert decision.reason_code == "explicit_agent"

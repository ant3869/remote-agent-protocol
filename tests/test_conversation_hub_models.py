from datetime import UTC, datetime, timezone

import pytest

from remote_agent_protocol.conversation_hub.models import (
    AgentChannel,
    ConversationTurn,
    MemoryConfidence,
    MemoryScope,
    MemoryStatus,
    ResultKind,
    ScopedMemory,
    SessionBinding,
    SessionStrategy,
)

NOW = datetime(2026, 9, 17, 18, 30, tzinfo=UTC)


def test_channel_round_trip_preserves_identity():
    channel = AgentChannel.new(agent_id="openclaw", now=NOW)

    restored = AgentChannel.from_dict(channel.to_dict())

    assert restored.channel_id == "agent:openclaw"
    assert restored.chapter_id == 1
    assert restored.created_at == NOW


def test_butler_uses_the_coordinator_channel():
    assert AgentChannel.new(agent_id="butler", now=NOW).channel_id == "coordinator:butler"


def test_model_round_trips_preserve_enums_and_aware_timestamps():
    turn = ConversationTurn.new(
        channel_id="agent:openclaw",
        speaker_id="openclaw",
        speaker_role="agent",
        full_text="Complete result",
        spoken_text="Result",
        result_kind=ResultKind.SUCCESS,
        now=NOW,
    )
    memory = ScopedMemory(
        memory_id="memory-1",
        scope=MemoryScope.PROJECT,
        subject="project",
        value="Uses RAP",
        source_turn_ids=[turn.turn_id],
        confidence=MemoryConfidence.VERIFIED,
        observed_at=NOW,
        status=MemoryStatus.ACTIVE,
    )

    assert ConversationTurn.from_dict(turn.to_dict()) == turn
    assert ScopedMemory.from_dict(memory.to_dict()) == memory


def test_exact_enum_values_are_stable():
    assert [item.value for item in MemoryScope] == ["channel", "shared", "task", "project"]
    assert [item.value for item in MemoryConfidence] == ["verified", "user_stated", "inferred"]
    assert [item.value for item in MemoryStatus] == ["active", "superseded", "forgotten"]
    assert [item.value for item in ResultKind] == [
        "progress",
        "success",
        "partial",
        "blocked",
        "failure",
    ]
    assert [item.value for item in SessionStrategy] == ["native_resume", "rehydrate"]


def test_naive_timestamps_and_invalid_payloads_are_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        AgentChannel.new(agent_id="openclaw", now=datetime(2026, 9, 17))
    with pytest.raises(ValueError):
        AgentChannel.from_dict({"agent_id": "openclaw"})


def test_restored_native_binding_requires_validation():
    binding = SessionBinding.native(
        binding_id="binding-1",
        channel_id="agent:openclaw",
        agent_id="openclaw",
        native_session_id="session-1",
        validated_at=NOW,
    )

    assert SessionBinding.from_dict(binding.to_dict(), restored=True).requires_validation

import json
from datetime import UTC, datetime, timezone

from remote_agent_protocol.conversation_hub.models import AgentChannel, ConversationTurn, FloorState
from remote_agent_protocol.conversation_hub.store import ConversationStore

NOW = datetime(2026, 9, 17, 18, 30, tzinfo=UTC)


def _state():
    channel = AgentChannel.new(agent_id="openclaw", now=NOW)
    turn = ConversationTurn.new(
        channel_id=channel.channel_id,
        speaker_id="user",
        speaker_role="user",
        full_text="Keep api_key private",
        now=NOW,
    )
    floor = FloorState.new(now=NOW, floor_channel_id=channel.channel_id)
    return channel, turn, floor


def test_store_round_trips_normalized_state_and_redacts_secrets(tmp_path):
    store = ConversationStore(tmp_path / "conversations.json")
    channel, turn, floor = _state()

    store.save(
        channels=[channel],
        turns=[turn],
        memories=[],
        bindings=[],
        task_references=[],
        floor_state=floor,
        metadata={"api_key": "never persist", "nested": {"authorization": "Bearer secret"}},
    )
    assert not store.temp_path.exists()
    restored = store.load()

    assert restored.error is None
    assert restored.channels == [channel]
    assert restored.turns == [turn]
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert raw["metadata"]["api_key"] == "[REDACTED]"
    assert raw["metadata"]["nested"]["authorization"] == "[REDACTED]"


def test_store_replaces_atomically_and_recovers_from_interrupted_temp_write(tmp_path):
    store = ConversationStore(tmp_path / "conversations.json")
    channel, turn, floor = _state()
    store.save(
        channels=[channel],
        turns=[turn],
        memories=[],
        bindings=[],
        task_references=[],
        floor_state=floor,
    )
    store.temp_path.write_text("{ partial", encoding="utf-8")

    restored = store.load()

    assert restored.error is None
    assert restored.channels[0].channel_id == channel.channel_id
    assert store.temp_path.exists()


def test_store_preserves_unreadable_and_newer_files_with_classified_errors(tmp_path):
    store = ConversationStore(tmp_path / "conversations.json")
    store.path.write_text("{ nope", encoding="utf-8")
    unreadable = store.load()
    assert unreadable.error is not None
    assert unreadable.error.kind == "unreadable"
    assert store.path.read_text(encoding="utf-8") == "{ nope"

    store.path.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")
    newer = store.load()
    assert newer.error is not None
    assert newer.error.kind == "newer_schema"
    assert json.loads(store.path.read_text(encoding="utf-8"))["schema_version"] == 999

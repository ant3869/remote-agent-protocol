"""migrate(): first-run channels, legacy import, job binding, retention, restart safety."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from remote_agent_protocol import config as cfg
from remote_agent_protocol.conversation_hub import migrations
from remote_agent_protocol.conversation_hub.models import (
    AgentChannel,
    ConversationTurn,
    FloorState,
    MemoryConfidence,
    MemoryScope,
    MemoryStatus,
    ResultKind,
    ScopedMemory,
    TaskReference,
)
from remote_agent_protocol.conversation_hub.store import ConversationStore

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
AGENT_IDS = {"openclaw", "hermes"}


@pytest.fixture(autouse=True)
def _sandbox_legacy_migration_sources(tmp_path, monkeypatch):
    """Default both legacy sources away from the developer's real data.

    Individual tests override one or both with their own fixture file.
    """
    monkeypatch.setattr(cfg, "MEMORY_FILE", str(tmp_path / "no-such-memory.json"))
    monkeypatch.setattr(cfg, "AGENT_HISTORY_FILE", "")


def _store(tmp_path):
    return ConversationStore(tmp_path / "conversations.json")


def test_first_run_creates_a_channel_for_butler_and_every_configured_agent(tmp_path):
    store = _store(tmp_path)

    migrated = migrations.migrate(store.load(), store=store, agent_ids=AGENT_IDS, now=NOW)

    channel_ids = {channel.channel_id for channel in migrated.channels}
    assert channel_ids == {"coordinator:butler", "agent:openclaw", "agent:hermes"}


def test_migrate_is_idempotent_across_an_ordinary_save_between_runs(tmp_path):
    store = _store(tmp_path)

    first = migrations.migrate(store.load(), store=store, agent_ids=AGENT_IDS, now=NOW)
    # An ordinary hub save (dispatch, reset, archive...) must not drop metadata.
    store.save(
        channels=first.channels,
        turns=first.turns,
        memories=first.memories,
        bindings=first.bindings,
        task_references=first.task_references,
        floor_state=first.floor_state,
        metadata=first.metadata,
    )

    second = migrations.migrate(store.load(), store=store, agent_ids=AGENT_IDS, now=NOW)

    assert {c.channel_id for c in second.channels} == {c.channel_id for c in first.channels}
    assert len(second.turns) == len(first.turns)
    assert second.metadata == first.metadata


def test_legacy_coordinator_import_keeps_only_eligible_messages_once(tmp_path, monkeypatch):
    memory_file = tmp_path / "jess_memory.json"
    memory_file.write_text(
        json.dumps(
            [
                {"role": "user", "content": "What's the weather like?"},
                {"role": "assistant", "content": "Sunny today, sir."},
                {"role": "system", "content": f"{cfg.MEM0_MEMORY_HEADER} you like tea"},
                {"role": "user", "content": cfg.KICKOFF_FIRST},
                {"role": "assistant", "content": cfg.EPHEMERAL_PROMPT_PREFIXES[0] + " some detail"},
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "MEMORY_FILE", str(memory_file))
    store = _store(tmp_path)

    migrated = migrations.migrate(store.load(), store=store, agent_ids=AGENT_IDS, now=NOW)

    coordinator_turns = [t for t in migrated.turns if t.channel_id == "coordinator:butler"]
    assert [t.full_text for t in coordinator_turns] == [
        "What's the weather like?",
        "Sunny today, sir.",
    ]
    assert all(t.metadata.get("origin") == "legacy_import" for t in coordinator_turns)
    assert migrated.memories == []
    assert migrated.metadata["legacy_import_done"] is True

    # A second run, even with the legacy file deleted, must not duplicate.
    memory_file.unlink()
    migrated_again = migrations.migrate(store.load(), store=store, agent_ids=AGENT_IDS, now=NOW)
    again = [t for t in migrated_again.turns if t.channel_id == "coordinator:butler"]
    assert len(again) == 2


def test_unbound_jobs_bind_only_when_the_agent_is_configured_and_the_job_id_is_unique(
    tmp_path, monkeypatch
):
    history_file = tmp_path / "jess_agent_history.json"
    history_file.write_text(
        json.dumps(
            [
                {
                    "job_id": "job-1",
                    "agent": "openclaw",
                    "status": "done",
                    "started_at": "2026-09-20T10:00:00+00:00",
                    "finished_at": "2026-09-20T10:05:00+00:00",
                },
                {
                    "job_id": "job-2",
                    "agent": "ghost-agent",
                    "status": "done",
                    "started_at": "2026-09-20T10:00:00+00:00",
                    "finished_at": "2026-09-20T10:05:00+00:00",
                },
                {
                    "job_id": "job-3",
                    "agent": "hermes",
                    "status": "failed",
                    "started_at": "2026-09-19T09:00:00+00:00",
                    "finished_at": "2026-09-19T09:01:00+00:00",
                },
                {
                    "job_id": "job-3",
                    "agent": "hermes",
                    "status": "done",
                    "started_at": "2026-09-20T09:00:00+00:00",
                    "finished_at": "2026-09-20T09:01:00+00:00",
                },
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cfg, "AGENT_HISTORY_FILE", str(history_file))
    monkeypatch.setattr(cfg, "MEMORY_FILE", str(tmp_path / "no-such-memory.json"))
    store = _store(tmp_path)

    migrated = migrations.migrate(store.load(), store=store, agent_ids=AGENT_IDS, now=NOW)

    task_ids = {task.task_id: task for task in migrated.task_references}
    assert "legacy-job:job-1" in task_ids
    assert task_ids["legacy-job:job-1"].status == "done"
    assert task_ids["legacy-job:job-1"].channel_id == "agent:openclaw"
    assert "legacy-job:job-2" not in task_ids  # unconfigured agent
    assert "legacy-job:job-3" not in task_ids  # duplicated job_id, ambiguous


def test_a_task_left_active_across_a_restart_is_marked_interrupted_and_unrouted(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cfg, "AGENT_HISTORY_FILE", "")
    monkeypatch.setattr(cfg, "MEMORY_FILE", str(tmp_path / "no-such-memory.json"))
    store = _store(tmp_path)
    channel = AgentChannel.new(agent_id="openclaw", now=NOW)
    channel = replace(channel, active_task_ids=["task-stale"])
    task = TaskReference(
        task_id="task-stale",
        channel_id="agent:openclaw",
        agent_id="openclaw",
        status="active",
        created_at=NOW - timedelta(minutes=5),
        updated_at=NOW - timedelta(minutes=5),
        attempt_id="job-7",
    )
    floor = FloorState(
        front_door_id="coordinator:butler",
        floor_channel_id="agent:openclaw",
        last_speaker_id="openclaw",
        task_owner_by_id={"task-stale": "openclaw"},
        updated_at=NOW - timedelta(minutes=5),
    )
    store.save(
        channels=[channel],
        turns=[],
        memories=[],
        bindings=[],
        task_references=[task],
        floor_state=floor,
    )

    migrated = migrations.migrate(store.load(), store=store, agent_ids=AGENT_IDS, now=NOW)

    restored_task = next(t for t in migrated.task_references if t.task_id == "task-stale")
    assert restored_task.status == "interrupted"
    assert restored_task.attempt_id is None
    restored_channel = next(c for c in migrated.channels if c.channel_id == "agent:openclaw")
    assert restored_channel.active_task_ids == []
    assert "task-stale" not in migrated.floor_state.task_owner_by_id


def test_migration_with_no_floor_state_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "AGENT_HISTORY_FILE", "")
    monkeypatch.setattr(cfg, "MEMORY_FILE", str(tmp_path / "no-such-memory.json"))
    store = _store(tmp_path)

    migrated = migrations.migrate(store.load(), store=store, agent_ids=AGENT_IDS, now=NOW)

    assert migrated.floor_state is None


def test_corrupt_file_is_preserved_before_migration_starts_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "AGENT_HISTORY_FILE", "")
    monkeypatch.setattr(cfg, "MEMORY_FILE", str(tmp_path / "no-such-memory.json"))
    store = _store(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{ not json", encoding="utf-8")

    migrations.migrate(store.load(), store=store, agent_ids=AGENT_IDS, now=NOW)

    backups = list(tmp_path.glob("conversations.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{ not json"
    restored = store.load()
    assert restored.error is None
    assert {c.channel_id for c in restored.channels} == {
        "coordinator:butler",
        "agent:openclaw",
        "agent:hermes",
    }


def test_retention_trims_oldest_turns_but_protects_memory_and_result_references(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(cfg, "AGENT_HISTORY_FILE", "")
    monkeypatch.setattr(cfg, "MEMORY_FILE", str(tmp_path / "no-such-memory.json"))
    monkeypatch.setattr(cfg, "CONVERSATION_CHANNEL_TURN_RETENTION", 3)
    monkeypatch.setattr(cfg, "CONVERSATION_PROGRESS_RETENTION", 500)
    store = _store(tmp_path)
    channel = AgentChannel.new(agent_id="openclaw", now=NOW)

    protected_by_memory = ConversationTurn.new(
        channel_id=channel.channel_id,
        speaker_id="user",
        speaker_role="user",
        full_text="Remember this decision",
        now=NOW - timedelta(minutes=10),
    )
    protected_by_result = ConversationTurn.new(
        channel_id=channel.channel_id,
        speaker_id="openclaw",
        speaker_role="assistant",
        full_text="Final answer",
        now=NOW - timedelta(minutes=9),
        result_kind=ResultKind.SUCCESS,
    )
    filler_turns = [
        ConversationTurn.new(
            channel_id=channel.channel_id,
            speaker_id="user",
            speaker_role="user",
            full_text=f"filler {i}",
            now=NOW - timedelta(minutes=8 - i),
        )
        for i in range(5)
    ]
    memory = ScopedMemory(
        memory_id="memory-1",
        scope=MemoryScope.CHANNEL,
        subject="decision",
        value="Use the tea preference",
        source_turn_ids=[protected_by_memory.turn_id],
        confidence=MemoryConfidence.USER_STATED,
        observed_at=NOW,
        status=MemoryStatus.ACTIVE,
    )
    store.save(
        channels=[channel],
        turns=[protected_by_memory, protected_by_result, *filler_turns],
        memories=[memory],
        bindings=[],
        task_references=[],
        floor_state=None,
    )

    migrated = migrations.migrate(store.load(), store=store, agent_ids=AGENT_IDS, now=NOW)

    channel_turns = [t for t in migrated.turns if t.channel_id == channel.channel_id]
    turn_ids = {t.turn_id for t in channel_turns}
    assert protected_by_memory.turn_id in turn_ids
    assert protected_by_result.turn_id in turn_ids
    # Retention targets a total of 3; the 2 protected turns are never removal
    # candidates, leaving room for exactly the single most recent filler.
    assert len(channel_turns) == 3
    fillers = [t for t in channel_turns if t.full_text.startswith("filler")]
    assert [t.full_text for t in fillers] == ["filler 4"]

    on_disk = json.loads(store.path.read_text(encoding="utf-8"))
    on_disk_channel_turns = [t for t in on_disk["turns"] if t["channel_id"] == channel.channel_id]
    assert len(on_disk_channel_turns) == len(channel_turns)


def test_progress_turns_are_capped_separately_from_the_channel_total(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "AGENT_HISTORY_FILE", "")
    monkeypatch.setattr(cfg, "MEMORY_FILE", str(tmp_path / "no-such-memory.json"))
    monkeypatch.setattr(cfg, "CONVERSATION_CHANNEL_TURN_RETENTION", 500)
    monkeypatch.setattr(cfg, "CONVERSATION_PROGRESS_RETENTION", 2)
    store = _store(tmp_path)
    channel = AgentChannel.new(agent_id="openclaw", now=NOW)
    progress_turns = [
        ConversationTurn.new(
            channel_id=channel.channel_id,
            speaker_id="openclaw",
            speaker_role="assistant",
            full_text=f"progress {i}",
            now=NOW - timedelta(minutes=10 - i),
            result_kind=ResultKind.PROGRESS,
        )
        for i in range(5)
    ]
    store.save(
        channels=[channel],
        turns=progress_turns,
        memories=[],
        bindings=[],
        task_references=[],
        floor_state=None,
    )

    migrated = migrations.migrate(store.load(), store=store, agent_ids=AGENT_IDS, now=NOW)

    kept = [t for t in migrated.turns if t.channel_id == channel.channel_id]
    assert len(kept) == 2
    assert [t.full_text for t in kept] == ["progress 3", "progress 4"]


def test_migration_saves_exactly_once(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "AGENT_HISTORY_FILE", "")
    monkeypatch.setattr(cfg, "MEMORY_FILE", str(tmp_path / "no-such-memory.json"))
    store = _store(tmp_path)
    calls = []
    original_save = store.save

    def counting_save(*args, **kwargs):
        calls.append(1)
        return original_save(*args, **kwargs)

    store.save = counting_save

    migrations.migrate(store.load(), store=store, agent_ids=AGENT_IDS, now=NOW)

    assert len(calls) == 1

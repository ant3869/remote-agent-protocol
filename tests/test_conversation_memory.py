"""Admission, provenance and lifecycle boundaries for durable scoped memory."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from remote_agent_protocol.conversation_hub.memory import MemoryRepository, PromotionReason
from remote_agent_protocol.conversation_hub.models import (
    MemoryConfidence,
    MemoryScope,
    MemoryStatus,
    ScopedMemory,
)
from remote_agent_protocol.conversation_hub.store import ConversationStore

NOW = datetime(2026, 9, 18, tzinfo=UTC)


def memory(value="Picture day is October 2", **changes):
    return replace(
        ScopedMemory(
            memory_id="memory-1",
            scope=MemoryScope.CHANNEL,
            subject="picture day",
            value=value,
            source_turn_ids=["turn-1"],
            confidence=MemoryConfidence.USER_STATED,
            observed_at=NOW,
            channel_id="agent:hermes",
        ),
        **changes,
    )


@pytest.mark.parametrize(
    "scope,extra,reason",
    [
        (MemoryScope.CHANNEL, {}, None),
        (MemoryScope.TASK, {"task_id": "task-1"}, None),
        (MemoryScope.SHARED, {}, PromotionReason.STABLE_USER_FACT),
        (MemoryScope.PROJECT, {"project_id": "rap"}, PromotionReason.PROJECT_DECISION),
    ],
)
def test_scopes_survive_existing_store_round_trip(tmp_path, scope, extra, reason):
    repo = MemoryRepository()
    added = repo.add(memory(scope=scope, **extra), reason=reason)
    store = ConversationStore(tmp_path / "conversation.json")
    store.save(
        channels=[],
        turns=[],
        memories=repo.snapshot(),
        bindings=[],
        task_references=[],
        floor_state=None,
    )
    restored = MemoryRepository(store.load().memories)
    assert restored.get(added.memory_id) == added


def test_correction_supersedes_without_erasing_provenance():
    repo = MemoryRepository()
    old = repo.add(memory())
    new = repo.correct(old.memory_id, "Picture day is October 9", "turn-2", NOW)
    assert repo.get(old.memory_id).status is MemoryStatus.SUPERSEDED
    assert repo.get(old.memory_id).source_turn_ids == ["turn-1"]
    assert new.supersedes == old.memory_id
    assert new.source_turn_ids == ["turn-2"]
    assert [m.value for m in repo.eligible(channel_id="agent:hermes")] == [new.value]


def test_forgetting_clears_value_subject_and_entire_correction_family(tmp_path):
    repo = MemoryRepository()
    old = repo.add(memory())
    new = repo.correct(old.memory_id, "Picture day is October 9", "turn-2", NOW)
    repo.forget(old.memory_id)
    assert repo.eligible(channel_id="agent:hermes") == []
    assert all(
        m.status is MemoryStatus.FORGOTTEN and not m.value and not m.subject
        for m in repo.snapshot()
    )
    assert repo.get(new.memory_id).source_turn_ids == ["turn-2"]
    store = ConversationStore(tmp_path / "conversation.json")
    store.save(
        channels=[],
        turns=[],
        memories=repo.snapshot(),
        bindings=[],
        task_references=[],
        floor_state=None,
    )
    assert "Picture day" not in store.path.read_text()
    with pytest.raises(ValueError):
        repo.correct(new.memory_id, "resurrected", "turn-3", NOW)


@pytest.mark.parametrize("scope", [MemoryScope.SHARED, MemoryScope.PROJECT, MemoryScope.TASK])
def test_inference_cannot_leave_channel(scope):
    with pytest.raises(ValueError):
        MemoryRepository().add(
            memory(scope=scope, task_id="t", project_id="p", confidence=MemoryConfidence.INFERRED),
            reason=PromotionReason.VERIFIED_RESULT,
        )


def test_inferences_never_supply_access_evidence():
    repo = MemoryRepository()
    repo.add(memory("Hermes probably has inbox access", confidence=MemoryConfidence.INFERRED))
    assert repo.eligible(channel_id="agent:hermes")
    assert repo.eligible(channel_id="agent:hermes", for_access=True) == []


@pytest.mark.parametrize("reason", [None, PromotionReason.VERIFIED_RESULT])
def test_shared_promotion_needs_qualified_evidence(reason):
    with pytest.raises(ValueError):
        MemoryRepository().add(memory(scope=MemoryScope.SHARED), reason=reason)


def test_verified_result_can_be_shared():
    repo = MemoryRepository()
    repo.add(
        memory(scope=MemoryScope.SHARED, confidence=MemoryConfidence.VERIFIED),
        reason=PromotionReason.VERIFIED_RESULT,
    )
    assert repo.eligible(channel_id="agent:other", for_access=True)


@pytest.mark.parametrize(
    "value",
    [
        "api_key=never-store",
        "Authorization: Bearer never-store",
        "password is never-store",
        "sk-1234567890abcdefghijklmnopqrstuvwxyz",
        "-----BEGIN PRIVATE KEY-----\nsecret",
        "<analysis>private reasoning</analysis>",
    ],
)
def test_secret_and_reasoning_candidates_are_rejected(value):
    with pytest.raises(ValueError):
        MemoryRepository().add(memory(value))


def test_raw_tool_output_and_missing_provenance_are_rejected():
    with pytest.raises(ValueError):
        MemoryRepository().add(memory(), raw_tool_output=True)
    with pytest.raises(ValueError):
        MemoryRepository().add(memory(source_turn_ids=[]))


def test_scope_isolation_and_relevant_project_sharing():
    repo = MemoryRepository()
    repo.add(memory())
    repo.add(memory("task", memory_id="task", scope=MemoryScope.TASK, task_id="t"))
    repo.add(
        memory("project", memory_id="project", scope=MemoryScope.PROJECT, project_id="p"),
        reason=PromotionReason.PROJECT_DECISION,
    )
    assert repo.eligible(channel_id="agent:other", task_id="wrong", project_id="wrong") == []
    assert {
        m.value for m in repo.eligible(channel_id="agent:other", task_id="t", project_id="p")
    } == {"task", "project"}


def test_duplicate_facts_merge_provenance_without_crossing_scope():
    repo = MemoryRepository()
    first = repo.add(memory("I prefer short answers"))
    duplicate = repo.add(
        memory("User prefers short answers.", memory_id="other", source_turn_ids=["turn-2"])
    )
    assert duplicate.memory_id == first.memory_id
    assert duplicate.source_turn_ids == ["turn-1", "turn-2"]
    assert len(repo.snapshot()) == 1
    repo.add(memory("I prefer short answers", memory_id="third", channel_id="agent:codex"))
    assert len(repo.snapshot()) == 2


def test_repository_does_not_leak_mutable_provenance():
    repo = MemoryRepository()
    original = memory()
    added = repo.add(original)
    original.source_turn_ids.append("bad")
    added.source_turn_ids.append("bad")
    repo.snapshot()[0].source_turn_ids.append("bad")
    assert repo.get(original.memory_id).source_turn_ids == ["turn-1"]


@pytest.mark.parametrize("scope", [MemoryScope.CHANNEL, MemoryScope.TASK, MemoryScope.PROJECT])
def test_missing_scope_binding_is_rejected(scope):
    with pytest.raises(ValueError, match="identity"):
        MemoryRepository().add(
            memory(scope=scope, channel_id=None), reason=PromotionReason.PROJECT_DECISION
        )


def test_rejected_correction_is_atomic_and_verified_fact_is_not_reverified():
    repo = MemoryRepository()
    original = repo.add(memory(confidence=MemoryConfidence.VERIFIED))
    with pytest.raises(ValueError):
        repo.correct(original.memory_id, "password=private", "turn-2", NOW)
    assert repo.get(original.memory_id) == original
    corrected = repo.correct(original.memory_id, "New user statement", "turn-2", NOW)
    assert corrected.confidence is MemoryConfidence.USER_STATED
    assert repo.eligible(channel_id="agent:hermes", for_access=True) == []


def test_source_marked_secrets_rejected_even_without_recognizable_syntax():
    with pytest.raises(ValueError):
        MemoryRepository().add(memory("opaque credential material"), contains_secret=True)


def test_identity_cannot_overwrite_provenance_or_create_supersession_fork():
    repo = MemoryRepository()
    original = repo.add(memory())
    with pytest.raises(ValueError):
        repo.add(memory("replacement"))
    repo.correct(original.memory_id, "corrected", "turn-2", NOW)
    with pytest.raises(ValueError):
        repo.correct(original.memory_id, "fork", "turn-3", NOW)

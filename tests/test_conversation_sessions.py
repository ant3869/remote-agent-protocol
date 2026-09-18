"""Channel identity, durable ownership and session rotation contracts."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from remote_agent_protocol.control_plane.models import JobHandle
from remote_agent_protocol.conversation_hub.context import ContextPackage
from remote_agent_protocol.conversation_hub.models import SessionBinding, SessionStrategy
from remote_agent_protocol.conversation_hub.sessions import SessionBindingManager

NOW = datetime(2026, 9, 18, tzinfo=UTC)
CONTEXT = ContextPackage((("request", "Continue the scoped task"),))


def binding(**changes):
    original = SessionBinding.native(
        binding_id="owned",
        channel_id="agent:codex",
        agent_id="codex",
        native_session_id="native-owned",
        validated_at=NOW,
    )
    return replace(original, **changes)


def adapter(*, valid=True, strategy=SessionStrategy.NATIVE_RESUME):
    result = AsyncMock()
    result.agent_id = "codex"
    result.conversation_session_strategy = strategy
    result.validate_bound_session.return_value = valid
    result.create_bound_session.return_value = binding(
        binding_id="fresh", native_session_id=None, strategy=SessionStrategy.REHYDRATE
    )
    result.dispatch_in_session.return_value = JobHandle("job-1", "codex")
    return result


@pytest.mark.asyncio
async def test_clean_creation_is_persisted_before_dispatch():
    writes = []
    manager = SessionBindingManager(persist=writes.append)
    harness = adapter()

    async def dispatch(current, context):
        assert writes[-1] == current
        assert context is CONTEXT
        return JobHandle("job-1", "codex")

    harness.dispatch_in_session.side_effect = dispatch
    assert await manager.dispatch("agent:codex", harness, CONTEXT) == JobHandle("job-1", "codex")
    assert manager.bindings[0].binding_id == "fresh"


@pytest.mark.asyncio
async def test_restart_requires_successful_validation_of_exact_persisted_native_binding():
    writes = []
    harness = adapter()
    manager = SessionBindingManager(persist=writes.append, bindings=[binding()])
    assert manager.bindings[0].requires_validation
    current = await manager.ensure_binding("agent:codex", harness)
    harness.validate_bound_session.assert_awaited_once()
    assert current.native_session_id == "native-owned"
    assert not current.requires_validation
    assert writes[-1] == current
    await manager.ensure_binding("agent:codex", harness)
    assert harness.validate_bound_session.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"agent_id": "hermes"},
        {"adapter_id": "hermes"},
    ],
)
async def test_mismatched_persisted_identity_is_rejected(changes):
    harness = adapter()
    manager = SessionBindingManager(persist=lambda item: None, bindings=[binding(**changes)])
    with pytest.raises(ValueError, match="identity"):
        await manager.dispatch("agent:codex", harness, CONTEXT)
    harness.dispatch_in_session.assert_not_awaited()
    harness.validate_bound_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_requested_channel_is_rejected_before_adapter_call():
    harness = adapter()
    manager = SessionBindingManager(persist=lambda item: None)
    with pytest.raises(ValueError, match="channel"):
        await manager.dispatch("agent:hermes", harness, CONTEXT)
    harness.create_bound_session.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes,valid,strategy",
    [
        ({}, False, SessionStrategy.NATIVE_RESUME),
        ({"native_session_id": None}, True, SessionStrategy.NATIVE_RESUME),
        ({}, True, SessionStrategy.REHYDRATE),
    ],
)
async def test_missing_unvalidated_or_unsupported_native_session_rotates(changes, valid, strategy):
    harness = adapter(valid=valid, strategy=strategy)
    manager = SessionBindingManager(persist=lambda item: None, bindings=[binding(**changes)])
    current = await manager.ensure_binding("agent:codex", harness)
    assert current.binding_id == "fresh"
    assert current.strategy is SessionStrategy.REHYDRATE
    assert current.rotation_reason


@pytest.mark.asyncio
async def test_validation_error_rotates_without_retrying_or_discovery():
    harness = adapter()
    harness.validate_bound_session.side_effect = OSError("session missing")
    manager = SessionBindingManager(persist=lambda item: None, bindings=[binding()])
    current = await manager.ensure_binding("agent:codex", harness)
    assert current.binding_id == "fresh"
    harness.create_bound_session.assert_awaited_once_with("agent:codex")


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["context_limit", "explicit_reset", "failure"])
async def test_rotation_preserves_channel_and_records_reason(reason):
    manager = SessionBindingManager(persist=lambda item: None, bindings=[binding()])
    current = await manager.rotate("agent:codex", adapter(), reason=reason)
    assert current.channel_id == "agent:codex"
    assert current.binding_id == "fresh"
    assert current.rotation_reason == reason


@pytest.mark.asyncio
async def test_dispatch_failure_invalidates_without_replaying_task():
    writes = []
    harness = adapter()
    harness.dispatch_in_session.side_effect = RuntimeError("launch failed")
    manager = SessionBindingManager(persist=writes.append, bindings=[binding()])
    with pytest.raises(RuntimeError, match="launch failed"):
        await manager.dispatch("agent:codex", harness, CONTEXT)
    assert harness.dispatch_in_session.await_count == 1
    assert manager.bindings[0].rotation_reason == "failure"
    assert manager.bindings[0].binding_id == "fresh"


@pytest.mark.asyncio
async def test_failed_persistence_prevents_dispatch_and_does_not_publish_binding():
    def persist(item):
        raise OSError("disk full")

    harness = adapter()
    manager = SessionBindingManager(persist=persist)
    with pytest.raises(OSError, match="disk full"):
        await manager.dispatch("agent:codex", harness, CONTEXT)
    harness.dispatch_in_session.assert_not_awaited()
    assert manager.bindings == ()


@pytest.mark.asyncio
async def test_adapter_cannot_create_a_native_binding_without_proving_support():
    harness = adapter(strategy=SessionStrategy.REHYDRATE)
    harness.create_bound_session.return_value = binding()
    manager = SessionBindingManager(persist=lambda item: None)
    with pytest.raises(ValueError, match="native"):
        await manager.ensure_binding("agent:codex", harness)


@pytest.mark.asyncio
async def test_new_native_binding_must_be_validated_before_persistence():
    harness = adapter(valid=False)
    harness.create_bound_session.return_value = binding()
    manager = SessionBindingManager(persist=lambda item: None)
    with pytest.raises(ValueError, match="native"):
        await manager.ensure_binding("agent:codex", harness)
    assert manager.bindings == ()


@pytest.mark.asyncio
async def test_failed_rotation_cannot_leave_failed_native_session_reusable():
    harness = adapter()
    harness.dispatch_in_session.side_effect = RuntimeError("dispatch failed")
    harness.create_bound_session.side_effect = OSError("creation failed")
    manager = SessionBindingManager(persist=lambda item: None, bindings=[binding()])
    with pytest.raises(OSError, match="creation failed"):
        await manager.dispatch("agent:codex", harness, CONTEXT)
    harness.dispatch_in_session.reset_mock()
    with pytest.raises(OSError, match="creation failed"):
        await manager.dispatch("agent:codex", harness, CONTEXT)
    harness.dispatch_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_rotation_failure_invalidates_old_session_even_after_restart():
    writes = []
    harness = adapter()
    harness.create_bound_session.side_effect = OSError("creation failed")
    manager = SessionBindingManager(persist=writes.append, bindings=[binding()])
    with pytest.raises(OSError):
        await manager.rotate("agent:codex", harness, reason="explicit_reset")
    assert writes[-1].validation_evidence["pending_rotation"] == "explicit_reset"
    restored = SessionBindingManager(persist=lambda item: None, bindings=writes[-1:])
    with pytest.raises(OSError):
        await restored.dispatch("agent:codex", harness, CONTEXT)
    harness.dispatch_in_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancelled_dispatch_invalidates_session_without_replaying():
    writes = []
    harness = adapter()
    harness.dispatch_in_session.side_effect = asyncio.CancelledError()
    manager = SessionBindingManager(persist=writes.append, bindings=[binding()])
    with pytest.raises(asyncio.CancelledError):
        await manager.dispatch("agent:codex", harness, CONTEXT)
    assert writes[-1].validation_evidence["pending_rotation"] == "cancelled"
    harness.create_bound_session.assert_not_awaited()

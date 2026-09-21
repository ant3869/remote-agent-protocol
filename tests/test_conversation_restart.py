"""End-to-end restart recovery: through factory.build_conversation_hub, twice."""

import itertools
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from remote_agent_protocol import config as cfg
from remote_agent_protocol.control_plane.models import JobHandle
from remote_agent_protocol.control_plane.registry import AgentRegistry
from remote_agent_protocol.conversation_hub.factory import build_conversation_hub
from remote_agent_protocol.conversation_hub.models import SessionBinding, SessionStrategy
from remote_agent_protocol.conversation_hub.service import ConversationTurnRequest

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
BACKENDS = {"openclaw": object()}
ALIASES = {"openclaw": "openclaw"}


def turn_request(text: str, **overrides) -> ConversationTurnRequest:
    fields = {
        "text": text,
        "source": "ant",
        "explicit_agent_id": None,
        "correlation_id": "corr-1",
        "created_at": NOW,
    }
    fields.update(overrides)
    return ConversationTurnRequest(**fields)


def make_native_adapter(agent_id: str) -> AsyncMock:
    """A NATIVE_RESUME adapter minting a fresh binding/session id each rotation."""
    counter = itertools.count(1)
    adapter = AsyncMock()
    adapter.agent_id = agent_id
    adapter.conversation_session_strategy = SessionStrategy.NATIVE_RESUME
    adapter.validate_bound_session.return_value = True

    async def create_bound_session(channel_id: str) -> SessionBinding:
        now = datetime.now(UTC)
        n = next(counter)
        return SessionBinding.native(
            binding_id=f"binding-{agent_id}-{n}",
            channel_id=channel_id,
            agent_id=agent_id,
            native_session_id=f"native-{agent_id}-{n}",
            validated_at=now,
        )

    async def dispatch_in_session(binding: SessionBinding, context) -> JobHandle:
        return JobHandle(f"job-{agent_id}-{next(counter)}", agent_id)

    adapter.create_bound_session.side_effect = create_bound_session
    adapter.dispatch_in_session.side_effect = dispatch_in_session
    return adapter


def build_hub(store_path, *, adapters=None):
    adapters = adapters if adapters is not None else {"openclaw": make_native_adapter("openclaw")}
    return build_conversation_hub(
        store_path=store_path,
        adapters=adapters,
        registry=AgentRegistry(),
        backends=BACKENDS,
        aliases=ALIASES,
    )


@pytest.fixture(autouse=True)
def _sandbox_legacy_migration_sources(tmp_path, monkeypatch):
    """migrate() reads these on every build_conversation_hub call in this file."""
    monkeypatch.setattr(cfg, "MEMORY_FILE", str(tmp_path / "no-such-memory.json"))
    monkeypatch.setattr(cfg, "AGENT_HISTORY_FILE", "")


@pytest.mark.asyncio
async def test_a_session_reset_survives_a_restart(tmp_path):
    store_path = tmp_path / "conversations.json"
    hub = build_hub(store_path)
    await hub.handle_turn(turn_request("OpenClaw, check my email"))
    original_binding = hub._sessions.bindings[0]  # noqa: SLF001

    reset_binding = await hub.reset_session("agent:openclaw")
    assert reset_binding.binding_id != original_binding.binding_id

    restarted = build_hub(store_path, adapters={"openclaw": make_native_adapter("openclaw")})

    restored = next(b for b in restarted._sessions.bindings if b.channel_id == "agent:openclaw")  # noqa: SLF001
    assert restored.binding_id == reset_binding.binding_id
    assert restored.rotation_reason == "user_requested_reset"
    # Restored native bindings always require revalidation before reuse.
    assert restored.requires_validation is True


@pytest.mark.asyncio
async def test_a_task_left_active_by_a_crash_is_interrupted_after_a_restart(tmp_path):
    store_path = tmp_path / "conversations.json"
    hub = build_hub(store_path)
    disposition = await hub.handle_turn(turn_request("OpenClaw, check my email"))
    # No completion event is ever sent -- simulates the process dying mid-task.

    restarted = build_hub(store_path, adapters={"openclaw": make_native_adapter("openclaw")})

    restored_task = restarted.task(disposition.task_id)
    assert restored_task is not None
    assert restored_task.status == "interrupted"
    assert restored_task.attempt_id is None

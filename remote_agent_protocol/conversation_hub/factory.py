"""Construct one AgentConversationHub shared by full voice mode and Brain mode."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from remote_agent_protocol.control_plane.adapters.base import ConversationSessionAdapter
from remote_agent_protocol.control_plane.registry import AgentRegistry

from .context import ContextAssembler, ContextBudget
from .floor import FloorManager
from .memory import MemoryRepository
from .selection import AgentSelector
from .service import AgentConversationHub, EventListener
from .store import ConversationStore


def build_conversation_hub(
    *,
    store_path: str | Path,
    adapters: Mapping[str, ConversationSessionAdapter],
    registry: AgentRegistry,
    backends: Mapping[str, object],
    aliases: Mapping[str, str],
    default_agent_id: str | None = None,
    context_budget: ContextBudget | None = None,
    on_event: EventListener | None = None,
) -> AgentConversationHub:
    """Load durable state and wire the hub's Task 1-5 collaborators around it.

    ``backends``/``aliases`` are passed straight through to ``FloorManager``,
    which reuses the existing deterministic ``parse_delegation`` grammar so
    voice, Brain, and conversation-hub routing agree on the same agent names.
    """
    store = ConversationStore(store_path)
    result = store.load()
    memories = MemoryRepository(result.memories)
    hub = AgentConversationHub(
        store=store,
        floor_manager=FloorManager(backends=backends, aliases=aliases),
        memories=memories,
        context_assembler=ContextAssembler(memories, context_budget),
        selector=AgentSelector(default_agent_id=default_agent_id),
        adapters=adapters,
        registry=registry,
        on_event=on_event,
    )
    hub.restore(result)
    return hub

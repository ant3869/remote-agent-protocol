"""Durable conversation-domain records and persistence for agent channels.

``service.py`` and ``factory.py`` are deliberately not re-exported here: they
depend on ``control_plane.adapters.base``, which itself imports
``conversation_hub.context`` -- re-exporting them at package-init time would
create a circular import. Import them from their submodules directly.
"""

from .events import ConversationEvent
from .models import (
    AgentChannel,
    ConversationTurn,
    FloorState,
    MemoryConfidence,
    MemoryScope,
    MemoryStatus,
    ResultKind,
    ScopedMemory,
    SessionBinding,
    SessionStrategy,
    TaskReference,
)
from .store import ConversationLoadError, ConversationLoadResult, ConversationStore

__all__ = [
    "AgentChannel",
    "ConversationEvent",
    "ConversationLoadError",
    "ConversationLoadResult",
    "ConversationStore",
    "ConversationTurn",
    "FloorState",
    "MemoryConfidence",
    "MemoryScope",
    "MemoryStatus",
    "ResultKind",
    "ScopedMemory",
    "SessionBinding",
    "SessionStrategy",
    "TaskReference",
]

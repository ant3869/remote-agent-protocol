"""Claude Code control-plane adapter."""

from ...conversation_hub.models import SessionStrategy
from .cli import BridgeCliAdapter


class ClaudeCodeAdapter(BridgeCliAdapter):
    """Adapter for the configured ``claude`` executable."""

    executable = "claude"
    conversation_session_strategy = SessionStrategy.REHYDRATE

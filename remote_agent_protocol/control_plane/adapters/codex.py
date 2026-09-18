"""Codex control-plane adapter."""

from ...conversation_hub.models import SessionStrategy
from .cli import BridgeCliAdapter


class CodexAdapter(BridgeCliAdapter):
    """Adapter for the configured ``codex`` executable."""

    executable = "codex"
    conversation_session_strategy = SessionStrategy.REHYDRATE

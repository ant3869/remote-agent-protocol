"""Hermes control-plane adapter."""

from ...conversation_hub.models import SessionStrategy
from .cli import BridgeCliAdapter


class HermesAdapter(BridgeCliAdapter):
    """Adapter for the configured ``hermes`` executable."""

    executable = "hermes"
    conversation_session_strategy = SessionStrategy.REHYDRATE

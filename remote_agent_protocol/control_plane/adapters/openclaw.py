"""OpenClaw control-plane adapter."""

from ...conversation_hub.models import SessionStrategy
from .cli import BridgeCliAdapter


class OpenClawAdapter(BridgeCliAdapter):
    """Adapter for the configured ``openclaw`` executable."""

    executable = "openclaw"
    conversation_session_strategy = SessionStrategy.REHYDRATE

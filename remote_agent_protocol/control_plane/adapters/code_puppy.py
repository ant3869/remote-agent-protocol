"""Code Puppy control-plane adapter."""

from ...conversation_hub.models import SessionStrategy
from .cli import BridgeCliAdapter


class CodePuppyAdapter(BridgeCliAdapter):
    """Adapter for the configured ``code-puppy`` executable."""

    executable = "code-puppy"
    conversation_session_strategy = SessionStrategy.REHYDRATE

"""Code Puppy control-plane adapter."""

from .cli import BridgeCliAdapter


class CodePuppyAdapter(BridgeCliAdapter):
    """Adapter for the configured ``code-puppy`` executable."""

    executable = "code-puppy"

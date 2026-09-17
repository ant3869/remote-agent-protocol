"""Claude Code control-plane adapter."""

from .cli import BridgeCliAdapter


class ClaudeCodeAdapter(BridgeCliAdapter):
    """Adapter for the configured ``claude`` executable."""

    executable = "claude"

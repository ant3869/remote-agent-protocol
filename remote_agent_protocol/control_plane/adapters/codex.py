"""Codex control-plane adapter."""

from .cli import BridgeCliAdapter


class CodexAdapter(BridgeCliAdapter):
    """Adapter for the configured ``codex`` executable."""

    executable = "codex"

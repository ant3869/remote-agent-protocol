"""Hermes control-plane adapter."""

from .cli import BridgeCliAdapter


class HermesAdapter(BridgeCliAdapter):
    """Adapter for the configured ``hermes`` executable."""

    executable = "hermes"

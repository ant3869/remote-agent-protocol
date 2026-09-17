"""OpenClaw control-plane adapter."""

from .cli import BridgeCliAdapter


class OpenClawAdapter(BridgeCliAdapter):
    """Adapter for the configured ``openclaw`` executable."""

    executable = "openclaw"

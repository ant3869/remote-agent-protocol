"""Construct the one AgentConversationHub shared by full voice mode and Brain mode.

Both ``VoiceSession`` and ``BrainSession`` build a control plane over the same
``build_adapters(...)`` call; this module reuses that exact adapter set and
``AgentRegistry`` instance for the hub, so its evidence-based selector reads
the same staleness view the control plane wrote rather than a second registry
at the same file (see Task 8's construction ruling).
"""

from __future__ import annotations

from collections.abc import Mapping

from remote_agent_protocol import agent_bridge
from remote_agent_protocol import config as cfg
from remote_agent_protocol.control_plane.adapters.factory import build_adapters
from remote_agent_protocol.control_plane.registry import AgentRegistry
from remote_agent_protocol.conversation_hub.factory import build_conversation_hub
from remote_agent_protocol.conversation_hub.service import AgentConversationHub, EventListener


def build_app_conversation_hub(
    bridge: agent_bridge.AgentBridge,
    registry: AgentRegistry,
    on_event: EventListener,
    *,
    backends: Mapping[str, object] | None = None,
    machines: Mapping[str, str] | None = None,
) -> AgentConversationHub:
    """Build the hub for one session, sharing the control plane's registry.

    ``backends``/``machines`` default to the configured agent backends and
    machine map; callers pass them through explicitly only in tests.
    """
    adapters = build_adapters(
        bridge,
        cfg.AGENT_BACKENDS if backends is None else backends,
        cfg.AGENT_MACHINES if machines is None else machines,
    )
    return build_conversation_hub(
        store_path=cfg.CONVERSATION_STORE_PATH,
        adapters=adapters,
        registry=registry,
        backends=cfg.AGENT_BACKENDS if backends is None else backends,
        aliases=cfg.AGENT_SPOKEN_ALIASES,
        default_agent_id=None,
        on_event=on_event,
    )

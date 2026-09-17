"""Construct adapters for configured RAP agent backends."""

from __future__ import annotations

from typing import Any

from .claude_code import ClaudeCodeAdapter
from .code_puppy import CodePuppyAdapter
from .codex import CodexAdapter
from .hermes import HermesAdapter
from .openclaw import OpenClawAdapter

_ADAPTERS = {
    "claude-code": (ClaudeCodeAdapter, "Claude Code"),
    "codex": (CodexAdapter, "Codex"),
    "hermes": (HermesAdapter, "Hermes"),
    "code-puppy": (CodePuppyAdapter, "Code Puppy"),
    "openclaw": (OpenClawAdapter, "OpenClaw"),
}


def build_adapters(bridge: Any, backends: dict[str, list[str]], machines: dict[str, str]) -> dict:
    """Return safe adapters only for known, configured first-milestone harnesses."""
    return {
        agent_id: adapter_type(
            agent_id,
            bridge,
            display_name=display_name,
            machine=machines.get(agent_id, "local"),
        )
        for agent_id, (adapter_type, display_name) in _ADAPTERS.items()
        if agent_id in backends
    }

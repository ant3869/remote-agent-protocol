"""Adapter contracts and RAP harness implementations."""

from .base import AgentAdapter, AgentTask
from .fake import FakeAgentAdapter

__all__ = ["AgentAdapter", "AgentTask", "FakeAgentAdapter"]

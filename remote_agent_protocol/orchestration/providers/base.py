"""ModelProvider -- the one extension point Local/Copilot/any future provider share.

Adding a new cloud or local reasoning backend means writing one more subclass
of :class:`ModelProvider`; nothing else in the orchestration package needs to
change. A separate "FutureProvider" placeholder would prove nothing this ABC
doesn't already provide, so there isn't one.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from enum import StrEnum


class ModelCapability(StrEnum):
    """What a completion call needs, so a provider can pick an appropriate model.

    Deliberately NOT "the strongest model available" by default -- callers
    pick the capability that matches the task, and each provider maps it to
    a concrete model (see providers/copilot.py's capability map).
    """

    FAST = "fast"
    BALANCED = "balanced"
    REASONING = "reasoning"
    MULTIMODAL = "multimodal"
    CODING = "coding"


@dataclass
class ProviderHealth:
    """Point-in-time provider status, as reported by the provider itself."""

    available: bool
    authenticated: bool
    detail: str = ""


@dataclass
class CompletionResult:
    """One reasoning call's output text and the model that produced it."""

    text: str
    model: str = ""


class ModelProvider(abc.ABC):
    """A source of orchestration reasoning: local Ollama or Copilot today."""

    name: str

    @abc.abstractmethod
    async def health(self) -> ProviderHealth:
        """Best-effort availability/auth check; never raises."""

    @abc.abstractmethod
    async def list_models(self) -> list[str]:
        """Models this provider can currently serve."""

    @abc.abstractmethod
    async def complete(
        self,
        prompt: str,
        *,
        capability: ModelCapability = ModelCapability.BALANCED,
        max_tokens: int = 400,
    ) -> CompletionResult:
        """One reasoning call. Raises on failure -- callers decide fallback."""

    async def quota(self) -> dict | None:
        """Usage/quota snapshot, or ``None`` when the provider exposes none.

        Never fabricated: a provider that documents no such endpoint must
        return ``None`` rather than guess a number.
        """
        return None

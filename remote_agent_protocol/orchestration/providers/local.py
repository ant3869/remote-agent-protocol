"""LocalProvider -- orchestration reasoning against the local Ollama server.

Deliberately reuses the small model already kept resident for intent
classification (``cfg.ORCHESTRATION_REASONING_MODEL``, default
``cfg.INTENT_MODEL``) instead of pulling in a second large model -- this
repo's VRAM budget is a hard constraint (see ``intent_router.classify_with_ollama``'s
comments on model co-residency).
"""

from __future__ import annotations

from collections.abc import Callable

import aiohttp
from loguru import logger

from remote_agent_protocol import config as cfg
from remote_agent_protocol import llm_endpoint
from remote_agent_protocol.orchestration.providers.base import (
    CompletionResult,
    ModelCapability,
    ModelProvider,
    ProviderHealth,
)

SessionFactory = Callable[[], aiohttp.ClientSession]


class LocalProvider(ModelProvider):
    """Reasoning provider backed by the local Ollama server. Has no quota concept."""

    name = "local"

    def __init__(
        self,
        *,
        host: str | None = None,
        model: str | None = None,
        timeout_secs: float | None = None,
        session_factory: SessionFactory | None = None,
    ) -> None:
        """Initialize the provider.

        Args:
            host: Ollama base URL; defaults to ``cfg.OLLAMA_HOST``.
            model: Model tag to use; defaults to ``cfg.ORCHESTRATION_REASONING_MODEL``.
            timeout_secs: Per-request timeout; defaults to ``cfg.INTENT_TIMEOUT_SECS``.
            session_factory: Zero-arg callable returning a fresh
                ``aiohttp.ClientSession``; injectable for tests.
        """
        self._host = (host or cfg.OLLAMA_HOST).rstrip("/")
        self._model = model or cfg.ORCHESTRATION_REASONING_MODEL
        self._timeout = timeout_secs if timeout_secs is not None else cfg.INTENT_TIMEOUT_SECS
        self._session_factory = session_factory or self._default_session

    def _default_session(self) -> aiohttp.ClientSession:
        return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self._timeout))

    async def health(self) -> ProviderHealth:
        """Ping Ollama's ``/api/tags``; local has no separate auth concept."""
        try:
            async with self._session_factory() as http, http.get(f"{self._host}/api/tags") as resp:
                resp.raise_for_status()
            return ProviderHealth(available=True, authenticated=True, detail="ollama reachable")
        except Exception as exc:
            return ProviderHealth(available=False, authenticated=False, detail=str(exc))

    async def list_models(self) -> list[str]:
        """Locally installed Ollama models, falling back to the configured one."""
        try:
            async with self._session_factory() as http, http.get(f"{self._host}/api/tags") as resp:
                resp.raise_for_status()
                data = await resp.json()
            names = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
            return names or [self._model]
        except Exception:
            return [self._model]

    async def complete(
        self,
        prompt: str,
        *,
        capability: ModelCapability = ModelCapability.BALANCED,
        max_tokens: int = 400,
    ) -> CompletionResult:
        """One non-streaming Ollama chat completion.

        ``capability`` is unused -- there is only one local model configured.
        """
        cloud = llm_endpoint.cloud_endpoint(llm_endpoint.ORCHESTRATION)
        if cloud is not None:
            try:
                return await self._complete_cloud(cloud, prompt, max_tokens=max_tokens)
            except Exception as exc:
                logger.warning(f"{cloud.label} reasoning failed ({exc}); using the local model")
        payload = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0, "num_predict": max_tokens},
            "think": False,
            "keep_alive": cfg.INTENT_KEEP_ALIVE,
        }
        async with self._session_factory() as http:
            async with http.post(f"{self._host}/api/chat", json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
        text = str(data.get("message", {}).get("content", ""))
        return CompletionResult(text=text, model=self._model)

    async def _complete_cloud(
        self, endpoint: llm_endpoint.Endpoint, prompt: str, *, max_tokens: int
    ) -> CompletionResult:
        """The same completion against an OpenAI-compatible endpoint.

        Ollama's ``options``/``think``/``keep_alive`` are its own extensions and
        a hosted API rejects unknown fields, so the request is rebuilt rather
        than forwarded.
        """
        payload = {
            "model": endpoint.model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        async with self._session_factory() as http:
            async with http.post(endpoint.chat_url, json=payload, headers=endpoint.headers) as resp:
                resp.raise_for_status()
                data = await resp.json()
        text = str(data.get("choices", [{}])[0].get("message", {}).get("content", ""))
        return CompletionResult(text=text, model=endpoint.model)

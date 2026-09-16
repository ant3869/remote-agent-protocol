"""CopilotProvider -- orchestration reasoning via the official GitHub Copilot SDK.

Uses ``github-copilot-sdk`` (PyPI; import name ``copilot``; GA 2026-06-02).
This is NOT a hand-rolled HTTP client against any GitHub endpoint: every call
here goes through ``copilot.CopilotClient``, which manages its own bundled
``copilot`` CLI subprocess, authentication, and token refresh. RAP implements
no OAuth flow and stores no token file of its own.

Authentication is entirely the SDK's own, in its documented priority order:
an explicit token passed to ``CopilotClientOptions`` outranks everything, but
RAP does not set one -- the default "GitHub signed-in user" method is used
as-is, reusing credentials from ``copilot auth login`` (stored by the CLI in
the OS keychain). The SDK's own environment variables
(``COPILOT_GITHUB_TOKEN`` / ``GH_TOKEN`` / ``GITHUB_TOKEN``, auto-detected by
the SDK itself) are the only supported override.

Known, explicitly-flagged limitations (see docs/architecture.md and the
final implementation report):
  * The SDK documents no quota/usage/cost API as of GA -- :meth:`quota`
    always returns ``None`` rather than guessing at an undocumented endpoint.
  * ``list_models()``'s exact return shape isn't documented anywhere this was
    researched; entries are introspected defensively, never assumed.
  * The SDK's Python exception taxonomy for auth/rate-limit failures wasn't
    documented either; :meth:`health` classifies failures by best-effort
    message inspection and should be tightened once real exceptions are
    observed against the installed package version.
"""

from __future__ import annotations

from collections.abc import Callable

from loguru import logger

from remote_agent_protocol.orchestration.providers.base import (
    CompletionResult,
    ModelCapability,
    ModelProvider,
    ProviderHealth,
)

# Doc-confirmed example model ids (docs.github.com copilot-sdk
# getting-started; github.com/github/copilot-sdk python/README.md) -- used
# only when list_models() introspection can't resolve a better match for the
# requested capability. cfg.COPILOT_MODEL_MAP overrides any of these.
_FALLBACK_MODEL = "auto"
_DEFAULT_CAPABILITY_MODELS: dict[ModelCapability, str] = {
    ModelCapability.FAST: "auto",
    ModelCapability.BALANCED: "auto",
    ModelCapability.REASONING: "gpt-5",
    ModelCapability.MULTIMODAL: "auto",
    ModelCapability.CODING: "claude-sonnet-4.5",
}

ClientFactory = Callable[[], object]

_AUTH_FAILURE_MARKERS = ("auth", "login", "401", "403", "unauthorized", "not authenticated")


class CopilotUnavailable(Exception):
    """The SDK/CLI could not complete a request.

    Covers: not installed, not authenticated, or the CLI/session itself failed.
    """


class CopilotProvider(ModelProvider):
    """Reasoning provider backed by the official GitHub Copilot SDK."""

    name = "copilot"

    def __init__(
        self,
        *,
        client_factory: ClientFactory | None = None,
        model_map: dict[str, str] | None = None,
        reasoning_effort: str | None = None,
        permission_handler: object | None = None,
    ) -> None:
        """Initialize the provider.

        Args:
            client_factory: Zero-arg callable returning a fresh
                ``copilot.CopilotClient``-shaped async context manager;
                defaults to the real SDK client. Injectable so tests never
                launch the real CLI or touch the network.
            model_map: Capability name -> model id override
                (``cfg.COPILOT_MODEL_MAP``).
            reasoning_effort: Passed through to sessions on models that
                support it (the SDK's ``reasoning_effort`` session option).
            permission_handler: The value passed as the session's
                ``on_permission_request``; defaults to lazily importing the
                real SDK's ``copilot.session.PermissionHandler.approve_all``.
                Injectable so tests can run without the optional
                ``github-copilot-sdk`` dependency installed at all.
        """
        self._client_factory = client_factory or self._default_client_factory
        self._model_map: dict[ModelCapability, str] = dict(_DEFAULT_CAPABILITY_MODELS)
        for key, value in (model_map or {}).items():
            try:
                self._model_map[ModelCapability(key)] = value
            except ValueError:
                logger.warning(f"Ignoring unknown Copilot capability override {key!r}")
        self._reasoning_effort = reasoning_effort or None
        self._permission_handler = permission_handler
        self._models_cache: list[str] | None = None

    @staticmethod
    def _default_client_factory():
        from copilot import CopilotClient  # optional dependency; imported lazily

        return CopilotClient()

    async def health(self) -> ProviderHealth:
        """Probe the SDK client via ``list_models()`` and classify the result."""
        try:
            client = self._client_factory()
        except ModuleNotFoundError:
            return ProviderHealth(
                available=False,
                authenticated=False,
                detail="github-copilot-sdk is not installed (pip install github-copilot-sdk)",
            )
        try:
            async with client as started:
                models = await started.list_models()
            self._models_cache = self._model_names(models)
            return ProviderHealth(
                available=True, authenticated=True, detail="copilot CLI reachable"
            )
        except Exception as exc:
            message = str(exc).lower()
            authenticated = not any(marker in message for marker in _AUTH_FAILURE_MARKERS)
            return ProviderHealth(
                available=False,
                authenticated=authenticated,
                detail=f"{exc.__class__.__name__}: {exc}",
            )

    @staticmethod
    def _model_names(models: object) -> list[str]:
        """Best-effort extraction of model ids -- list_models()'s schema isn't documented."""
        names: list[str] = []
        for entry in models or []:
            if isinstance(entry, str):
                names.append(entry)
            elif isinstance(entry, dict):
                value = entry.get("id") or entry.get("name") or entry.get("model")
                if value:
                    names.append(str(value))
            else:
                value = getattr(entry, "id", None) or getattr(entry, "name", None)
                if value:
                    names.append(str(value))
        return names

    async def list_models(self) -> list[str]:
        """Return the cached model list, probing via health() if not yet known."""
        if self._models_cache is not None:
            return self._models_cache
        await self.health()
        return self._models_cache or [_FALLBACK_MODEL]

    def model_for(self, capability: ModelCapability) -> str:
        """The model id this provider would use for ``capability``."""
        return self._model_map.get(capability, _FALLBACK_MODEL)

    async def complete(
        self,
        prompt: str,
        *,
        capability: ModelCapability = ModelCapability.BALANCED,
        max_tokens: int = 400,
    ) -> CompletionResult:
        """One Copilot chat completion, with no tools registered (see class docstring)."""
        permission_handler = self._permission_handler
        if permission_handler is None:
            try:
                from copilot.session import PermissionHandler

                permission_handler = PermissionHandler.approve_all
            except ModuleNotFoundError as exc:
                raise CopilotUnavailable("github-copilot-sdk is not installed") from exc

        model = self.model_for(capability)
        try:
            client = self._client_factory()
        except ModuleNotFoundError as exc:
            raise CopilotUnavailable("github-copilot-sdk is not installed") from exc
        try:
            async with client as started:
                session_kwargs: dict = {
                    # No tools are ever registered below, so no tool-use
                    # permission request can fire -- approve_all is
                    # inert-safe here. Copilot is used purely as a reasoning
                    # provider for orchestration decisions, never as an
                    # execution agent (that role stays with the existing
                    # harnesses in cfg.AGENT_BACKENDS).
                    "on_permission_request": permission_handler,
                    "model": model,
                    "streaming": False,
                    "tools": [],
                }
                if self._reasoning_effort:
                    session_kwargs["reasoning_effort"] = self._reasoning_effort
                async with await started.create_session(**session_kwargs) as session:
                    reply = await session.send_and_wait(prompt)
        except Exception as exc:
            raise CopilotUnavailable(f"Copilot completion failed: {exc}") from exc
        return CompletionResult(text=self._extract_text(reply), model=model)

    @staticmethod
    def _extract_text(reply: object) -> str:
        """Text extraction for ``send_and_wait()``'s return value.

        Confirmed live (2026-09-14, installed ``github-copilot-sdk`` 1.0.13):
        it returns a ``copilot.generated.session_events.SessionEvent`` whose
        ``.data`` is an ``AssistantMessageData`` carrying the answer in
        ``.content``. The direct attribute checks below are kept as a
        fallback for other event shapes (e.g. a streaming delta event) that
        weren't observed in this smoke test but aren't documented as
        excluded either.
        """
        if isinstance(reply, str):
            return reply
        data = getattr(reply, "data", None)
        if data is not None:
            value = getattr(data, "content", None) or getattr(data, "delta_content", None)
            if isinstance(value, str) and value:
                return value
        for attr in ("text", "content", "message", "delta_content"):
            value = getattr(reply, attr, None)
            if isinstance(value, str) and value:
                return value
        return "" if reply is None else str(reply)

    async def quota(self) -> dict | None:
        """Always ``None`` -- no quota/usage/cost endpoint is documented by the SDK."""
        return None

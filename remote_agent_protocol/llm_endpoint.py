"""Where each model call goes, and what to try when the first choice fails.

RAP runs three models: the persona that speaks, the classifier that routes each
utterance, and the orchestrator's own reasoning. Each can run locally through
Ollama or against any OpenAI-compatible cloud endpoint. Cloud is tried first
when one is configured, because a local 12B persona costs seconds of silence
before every reply; a cloud failure then falls back to the local model rather
than costing the turn.

Nothing here is provider-specific: point ``CLOUD_LLM_BASE_URL`` at anything
that speaks ``/chat/completions`` and supply a key.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from remote_agent_protocol import config as cfg
from remote_agent_protocol import secret_store
from remote_agent_protocol.model_providers import ProviderRegistry

# The four callers. Each resolves its own model name but shares one endpoint.
BRAIN = "brain"
INTENT = "intent"
ORCHESTRATION = "orchestration"
NARRATION = "narration"

# Which role-assignment chain (model_providers.ROLES) backs each caller.
_ROLE_FOR_KIND = {
    BRAIN: "butler",
    INTENT: "intent",
    ORCHESTRATION: "orchestration",
    NARRATION: "narration",
}

_registry_override: ProviderRegistry | None = None
_registry_singleton: ProviderRegistry | None = None


def use_registry(registry: ProviderRegistry | None) -> None:
    """Test hook: force a specific provider registry, or None for the app's own."""
    global _registry_override
    _registry_override = registry


def get_registry() -> ProviderRegistry:
    """The shared provider registry: providers, catalogs, role assignments.

    Lazily loaded once per process from ``cfg.MODEL_PROVIDERS_PATH`` and
    reused, so a role assignment the web UI saves is resolved against the
    same instance on the very next call here -- no separate reload step.
    """
    if _registry_override is not None:
        return _registry_override
    global _registry_singleton
    if _registry_singleton is None:
        _registry_singleton = ProviderRegistry(Path(cfg.MODEL_PROVIDERS_PATH))
        _registry_singleton.load()
    return _registry_singleton


@dataclass(frozen=True)
class Endpoint:
    """One place a chat request can be sent, and the model to ask for."""

    base_url: str
    model: str
    api_key: str = ""
    cloud: bool = False
    provider_id: str = ""
    extra_headers: dict[str, str] = field(default_factory=dict)

    @property
    def headers(self) -> dict[str, str]:
        """Auth headers plus any provider-specific extras.

        A role-chain endpoint carries no key of its own (``api_key`` stays
        ``""``) -- the key is fetched from ``secret_store`` here, at call
        time, so it is never cached on this frozen dataclass.
        """
        key = self.api_key or (secret_store.get_key(self.provider_id) if self.provider_id else "")
        result = {"Authorization": f"Bearer {key}"} if key else {}
        result.update(self.extra_headers)
        return result

    @property
    def chat_url(self) -> str:
        """The OpenAI-compatible chat endpoint.

        Ollama serves this at ``/v1`` alongside its own native API, so both
        kinds of endpoint answer the same shape; only the local one also
        accepts Ollama's extras (``keep_alive``, ``options``, ``think``).
        """
        return f"{self.base_url.rstrip('/')}/chat/completions"

    @property
    def label(self) -> str:
        """Short description for logs: which model, running where."""
        return f"{'cloud' if self.cloud else 'local'} {self.model}"


def _cloud_model(kind: str) -> str:
    """The configured cloud model for one caller, falling back to the shared one.

    NARRATION has no legacy env path of its own: sharing ``CLOUD_LLM_MODEL``
    by default would silently start sending it to the cloud the day this
    feature ships, for every install that already has a persona cloud model
    configured. Only an explicit role assignment puts narration in the cloud.
    """
    if kind == NARRATION:
        return ""
    per_kind = {
        BRAIN: cfg.CLOUD_LLM_MODEL,
        INTENT: cfg.CLOUD_INTENT_MODEL,
        ORCHESTRATION: cfg.CLOUD_ORCHESTRATION_MODEL,
    }
    return (per_kind.get(kind) or cfg.CLOUD_LLM_MODEL or "").strip()


def cloud_endpoint(kind: str, model: str | None = None) -> Endpoint | None:
    """The cloud endpoint for one caller, or None when it is not configured.

    Base URL, key, and model must all be present. A half-configured cloud is
    treated as no cloud at all: falling back on every single turn would be
    slower than never having tried.
    """
    base = (cfg.CLOUD_LLM_BASE_URL or "").strip()
    key = (cfg.CLOUD_LLM_API_KEY or "").strip()
    selected_model = (model or _cloud_model(kind)).strip()
    if not (base and key and selected_model):
        return None
    return Endpoint(base_url=base, model=selected_model, api_key=key, cloud=True)


def local_endpoint(kind: str, model: str | None = None) -> Endpoint:
    """The Ollama endpoint for one caller.

    ``model`` overrides the configured default, which the persona needs: its
    model follows the selected persona rather than a single setting.
    """
    defaults = {
        BRAIN: cfg.LLM_MODEL,
        INTENT: cfg.INTENT_MODEL,
        ORCHESTRATION: cfg.ORCHESTRATION_REASONING_MODEL,
        NARRATION: cfg.NARRATION_MODEL,
    }
    return Endpoint(
        base_url=cfg.OLLAMA_BASE_URL,
        model=(model or defaults.get(kind) or cfg.LLM_MODEL),
        cloud=False,
    )


def _role_chain_endpoints(kind: str) -> tuple[Endpoint, ...]:
    """The operator-assigned chain for this caller's role, resolved to endpoints.

    Empty when nothing is assigned, or when every assigned entry's provider
    is missing or disabled -- either way the caller falls back to the legacy
    ``CLOUD_*``/local path. The local Ollama preset can appear anywhere in a
    chain like any other provider; there is no hard-coded "local last" rule
    here, because the operator's own ordering already encodes that.
    """
    role = _ROLE_FOR_KIND.get(kind)
    if role is None:
        return ()
    registry = get_registry()
    endpoints = []
    for entry in registry.get_role_chain(role):
        provider = registry.get_provider(entry.provider_id)
        if provider is None or not provider.enabled:
            continue
        endpoints.append(
            Endpoint(
                base_url=provider.base_url,
                model=entry.model,
                cloud=provider.auth == "bearer",
                provider_id=provider.id,
                extra_headers=dict(provider.extra_headers),
            )
        )
    return tuple(endpoints)


def role_endpoint(kind: str) -> Endpoint | None:
    """The first hop of ``kind``'s role-assignment chain, or None if unassigned."""
    endpoints = _role_chain_endpoints(kind)
    return endpoints[0] if endpoints else None


def chain(
    kind: str, *, local_model: str | None = None, cloud_model: str | None = None
) -> tuple[Endpoint, ...]:
    """Endpoints to try in order: role assignment, then legacy env, then local.

    A non-empty role-assignment chain wins outright and is returned as-is.
    Otherwise this falls back to exactly today's behavior: the legacy
    ``CLOUD_*`` endpoint first when configured, with the local endpoint
    always last, so an unreachable cloud, a rejected key, or a provider
    outage degrades to the machine's own model instead of to silence.
    """
    role_chain = _role_chain_endpoints(kind)
    if role_chain:
        return role_chain
    cloud = cloud_endpoint(kind, cloud_model)
    local = local_endpoint(kind, local_model)
    if cloud is None:
        return (local,)
    return (cloud, local) if cfg.CLOUD_LLM_LOCAL_FALLBACK else (cloud,)


def cloud_is_configured() -> bool:
    """Whether any caller would reach a cloud endpoint at all."""
    return any(cloud_endpoint(kind) is not None for kind in (BRAIN, INTENT, ORCHESTRATION))


def cloud_only_enabled(kind: str = BRAIN) -> bool:
    """Whether ``kind`` is configured to run without a local model fallback."""
    return not cfg.CLOUD_LLM_LOCAL_FALLBACK and cloud_endpoint(kind) is not None


def configured_cloud_models() -> list[str]:
    """Configured cloud model ids, in display order and without duplicates."""
    values = [cfg.CLOUD_LLM_MODEL, *cfg.CLOUD_LLM_MODEL_CHOICES]
    values.extend([cfg.CLOUD_INTENT_MODEL, cfg.CLOUD_ORCHESTRATION_MODEL])
    return list(dict.fromkeys(value.strip() for value in values if value and value.strip()))


def fetch_cloud_models(timeout_secs: float = 5.0) -> list[str]:
    """Read the active OpenAI-compatible provider's public model catalog.

    The call is read-only and deliberately best-effort. A configured model is
    retained by the UI when a provider omits ``/models`` or is temporarily
    unreachable.
    """
    endpoint = cloud_endpoint(BRAIN)
    if endpoint is None:
        return []
    request = Request(
        f"{endpoint.base_url.rstrip('/')}/models", headers=endpoint.headers, method="GET"
    )
    try:
        with urlopen(request, timeout=timeout_secs) as response:  # noqa: S310 - configured endpoint
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError):
        return []
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    ids = [row.get("id", "").strip() for row in rows if isinstance(row, dict)]
    return sorted({model for model in ids if model})

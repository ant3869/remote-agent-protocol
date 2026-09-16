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

from dataclasses import dataclass

from remote_agent_protocol import config as cfg

# The three callers. Each resolves its own model name but shares one endpoint.
BRAIN = "brain"
INTENT = "intent"
ORCHESTRATION = "orchestration"


@dataclass(frozen=True)
class Endpoint:
    """One place a chat request can be sent, and the model to ask for."""

    base_url: str
    model: str
    api_key: str = ""
    cloud: bool = False

    @property
    def headers(self) -> dict[str, str]:
        """Auth headers, or none for a local Ollama that wants no key."""
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

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
    """The configured cloud model for one caller, falling back to the shared one."""
    per_kind = {
        BRAIN: cfg.CLOUD_LLM_MODEL,
        INTENT: cfg.CLOUD_INTENT_MODEL,
        ORCHESTRATION: cfg.CLOUD_ORCHESTRATION_MODEL,
    }
    return (per_kind.get(kind) or cfg.CLOUD_LLM_MODEL or "").strip()


def cloud_endpoint(kind: str) -> Endpoint | None:
    """The cloud endpoint for one caller, or None when it is not configured.

    Base URL, key, and model must all be present. A half-configured cloud is
    treated as no cloud at all: falling back on every single turn would be
    slower than never having tried.
    """
    base = (cfg.CLOUD_LLM_BASE_URL or "").strip()
    key = (cfg.CLOUD_LLM_API_KEY or "").strip()
    model = _cloud_model(kind)
    if not (base and key and model):
        return None
    return Endpoint(base_url=base, model=model, api_key=key, cloud=True)


def local_endpoint(kind: str, model: str | None = None) -> Endpoint:
    """The Ollama endpoint for one caller.

    ``model`` overrides the configured default, which the persona needs: its
    model follows the selected persona rather than a single setting.
    """
    defaults = {
        BRAIN: cfg.LLM_MODEL,
        INTENT: cfg.INTENT_MODEL,
        ORCHESTRATION: cfg.ORCHESTRATION_REASONING_MODEL,
    }
    return Endpoint(
        base_url=cfg.OLLAMA_BASE_URL,
        model=(model or defaults.get(kind) or cfg.LLM_MODEL),
        cloud=False,
    )


def chain(kind: str, *, local_model: str | None = None) -> tuple[Endpoint, ...]:
    """Endpoints to try in order: cloud first when configured, then local.

    The local endpoint is always last, so an unreachable cloud, a rejected key,
    or a provider outage degrades to the machine's own model instead of to
    silence.
    """
    cloud = cloud_endpoint(kind)
    local = local_endpoint(kind, local_model)
    return (cloud, local) if cloud is not None else (local,)


def cloud_is_configured() -> bool:
    """Whether any caller would reach a cloud endpoint at all."""
    return any(cloud_endpoint(kind) is not None for kind in (BRAIN, INTENT, ORCHESTRATION))

"""Where each model call goes, and what happens when the first choice fails.

A local 12B persona spent ~7s composing every reply before the user heard
anything (s2s_turn_timings.jsonl, 2026-09-15). Cloud models answer far faster,
but the machine has to keep working when the key expires or the provider is
down, so every caller falls back to the local model rather than to silence.
"""

import pytest

from remote_agent_protocol import config as cfg
from remote_agent_protocol import llm_endpoint, secret_store
from remote_agent_protocol import model_providers as mp

KINDS = (llm_endpoint.BRAIN, llm_endpoint.INTENT, llm_endpoint.ORCHESTRATION)


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setattr(cfg, "CLOUD_LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(cfg, "CLOUD_LLM_API_KEY", "sk-test")
    monkeypatch.setattr(cfg, "CLOUD_LLM_MODEL", "fast-cloud-model")
    monkeypatch.setattr(cfg, "CLOUD_INTENT_MODEL", "")
    monkeypatch.setattr(cfg, "CLOUD_ORCHESTRATION_MODEL", "")
    monkeypatch.setattr(cfg, "CLOUD_LLM_LOCAL_FALLBACK", True)


@pytest.fixture
def no_cloud(monkeypatch):
    monkeypatch.setattr(cfg, "CLOUD_LLM_BASE_URL", "")
    monkeypatch.setattr(cfg, "CLOUD_LLM_API_KEY", "")
    monkeypatch.setattr(cfg, "CLOUD_LLM_MODEL", "")


def test_without_cloud_configured_nothing_changes(no_cloud):
    for kind in KINDS:
        chain = llm_endpoint.chain(kind)
        assert len(chain) == 1, "an unconfigured cloud must not add a hop to every turn"
        assert chain[0].cloud is False
        assert chain[0].headers == {}, "the local server is not sent a bearer token"


def test_cloud_is_tried_first_and_local_is_always_last(cloud):
    for kind in KINDS:
        chain = llm_endpoint.chain(kind)
        assert [e.cloud for e in chain] == [True, False], f"{kind} must fall back locally"
        assert chain[0].headers == {"Authorization": "Bearer sk-test"}


def test_cloud_only_omits_the_local_endpoint(monkeypatch, cloud):
    monkeypatch.setattr(cfg, "CLOUD_LLM_LOCAL_FALLBACK", False)

    chain = llm_endpoint.chain(llm_endpoint.BRAIN, cloud_model="operator-selected-model")

    assert [endpoint.cloud for endpoint in chain] == [True]
    assert chain[0].model == "operator-selected-model"
    assert llm_endpoint.cloud_only_enabled() is True


def test_a_half_configured_cloud_is_no_cloud(monkeypatch, cloud):
    """Falling back on every single turn is slower than never trying."""
    for missing in ("CLOUD_LLM_BASE_URL", "CLOUD_LLM_API_KEY", "CLOUD_LLM_MODEL"):
        monkeypatch.setattr(cfg, missing, "")
        assert llm_endpoint.cloud_endpoint(llm_endpoint.BRAIN) is None
        monkeypatch.undo()
        monkeypatch.setattr(cfg, "CLOUD_LLM_BASE_URL", "https://example.test/v1")
        monkeypatch.setattr(cfg, "CLOUD_LLM_API_KEY", "sk-test")
        monkeypatch.setattr(cfg, "CLOUD_LLM_MODEL", "fast-cloud-model")


def test_each_caller_can_use_its_own_cloud_model(monkeypatch, cloud):
    monkeypatch.setattr(cfg, "CLOUD_INTENT_MODEL", "tiny-router-model")

    assert llm_endpoint.cloud_endpoint(llm_endpoint.INTENT).model == "tiny-router-model"
    # The others keep following the shared persona model.
    assert llm_endpoint.cloud_endpoint(llm_endpoint.BRAIN).model == "fast-cloud-model"
    assert llm_endpoint.cloud_endpoint(llm_endpoint.ORCHESTRATION).model == "fast-cloud-model"


def test_the_persona_model_follows_the_selected_persona(no_cloud):
    chain = llm_endpoint.chain(llm_endpoint.BRAIN, local_model="a-persona-specific-model")
    assert chain[-1].model == "a-persona-specific-model"


def test_chat_url_is_openai_compatible(cloud):
    assert llm_endpoint.cloud_endpoint(llm_endpoint.BRAIN).chat_url == (
        "https://example.test/v1/chat/completions"
    )
    assert llm_endpoint.local_endpoint(llm_endpoint.BRAIN).chat_url.endswith("/v1/chat/completions")


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return "boom"

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"status {self.status}")


def test_orchestration_reasoning_falls_back_when_the_cloud_refuses(cloud):
    """An expired key must cost one slow turn, not the decision."""
    import asyncio

    from remote_agent_protocol.orchestration.providers.local import LocalProvider

    seen: list[str] = []

    class Http:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def post(self, url, json, headers=None):
            seen.append(url)
            if "example.test" in url:
                return _Resp({}, status=401)
            return _Resp({"message": {"content": "local answer"}})

    provider = LocalProvider(session_factory=Http)
    result = asyncio.run(provider.complete("decide something"))

    assert result.text == "local answer"
    assert any("example.test" in u for u in seen), "the cloud must be tried first"
    assert any("/api/chat" in u for u in seen), "and the local model must catch it"


def test_an_openrouter_key_alone_is_enough(monkeypatch):
    """One key should configure all three callers without further settings.

    Asking someone to set a base URL, a key, and three model names before
    anything improves is a poor trade for "use a faster model".
    """
    import importlib

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("CLOUD_LLM_LOCAL_FALLBACK", "true")
    for name in ("CLOUD_LLM_BASE_URL", "CLOUD_LLM_API_KEY", "CLOUD_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    reloaded = importlib.reload(cfg)
    try:
        assert reloaded.CLOUD_LLM_BASE_URL == "https://openrouter.ai/api/v1"
        assert reloaded.CLOUD_LLM_API_KEY == "sk-or-test"
        assert reloaded.CLOUD_LLM_MODEL, "a default model is needed or nothing routes to cloud"
        for kind in KINDS:
            chain = llm_endpoint.chain(kind)
            assert [e.cloud for e in chain] == [True, False]
    finally:
        monkeypatch.undo()
        importlib.reload(cfg)


def test_an_explicit_endpoint_still_wins_over_openrouter(monkeypatch):
    import importlib

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("CLOUD_LLM_BASE_URL", "https://elsewhere.test/v1")
    monkeypatch.setenv("CLOUD_LLM_MODEL", "some/other-model")
    reloaded = importlib.reload(cfg)
    try:
        assert reloaded.CLOUD_LLM_BASE_URL == "https://elsewhere.test/v1"
        assert reloaded.CLOUD_LLM_MODEL == "some/other-model"
    finally:
        monkeypatch.undo()
        importlib.reload(cfg)


def test_a_cloud_request_always_caps_its_own_cost(monkeypatch, cloud):
    """Uncapped replies are refused outright, not merely billed generously.

    Providers reserve a request's maximum possible cost before running it, so
    omitting max_tokens asks the balance to cover the model running to its full
    output length. OpenRouter answers "This request requires more credits, or
    fewer max_tokens" and the turn silently falls back to the local model --
    losing the speed the cloud was configured for.
    """
    from remote_agent_protocol.brain import BrainSession
    from remote_agent_protocol.personas import PERSONAS

    session = BrainSession.__new__(BrainSession)
    session._persona = PERSONAS[0]
    session._messages = []
    session._system_instruction = lambda: "system"

    cloud_ep = llm_endpoint.cloud_endpoint(llm_endpoint.BRAIN)
    payload = session._ollama_payload(stream=True, endpoint=cloud_ep)
    assert payload["max_tokens"] == cfg.CLOUD_LLM_MAX_TOKENS
    # Ollama's own extensions must not travel to a hosted API.
    assert "keep_alive" not in payload

    local_ep = llm_endpoint.local_endpoint(llm_endpoint.BRAIN)
    local_payload = session._ollama_payload(stream=True, endpoint=local_ep)
    assert "max_tokens" not in local_payload, "the local model is not billed per token"
    assert local_payload["keep_alive"] == cfg.LLM_KEEP_ALIVE


@pytest.fixture
def registry(tmp_path):
    """An isolated, in-memory-loaded registry -- never the real app data."""
    reg = mp.ProviderRegistry(tmp_path / "model_providers.json")
    llm_endpoint.use_registry(reg)
    yield reg
    llm_endpoint.use_registry(None)


@pytest.fixture
def fake_secrets():
    backend = secret_store.InMemoryBackend()
    secret_store.use_backend(backend)
    yield backend
    secret_store.use_backend(None)
    secret_store._known_secrets.clear()


def _openrouter_provider(provider_id="openrouter") -> mp.ProviderConfig:
    return mp.ProviderConfig(
        id=provider_id,
        preset="openrouter",
        label="OpenRouter",
        base_url="https://openrouter.test/v1",
        auth="bearer",
        extra_headers={"X-Title": "Remote Agent Protocol"},
    )


def test_a_role_assignment_wins_over_the_legacy_cloud_env(registry, cloud):
    """Acceptance #4: an operator-assigned chain wins even when CLOUD_* is set."""
    registry.upsert_provider(_openrouter_provider())
    registry.set_role_chain(
        "butler", [mp.RoleChainEntry(provider_id="openrouter", model="vendor/model-a")]
    )

    result = llm_endpoint.chain(llm_endpoint.BRAIN)

    assert [e.model for e in result] == ["vendor/model-a"]
    assert result[0].base_url == "https://openrouter.test/v1"


def test_an_empty_role_assignment_falls_back_to_legacy_env(registry, cloud):
    result = llm_endpoint.chain(llm_endpoint.BRAIN)
    assert [e.cloud for e in result] == [True, False]


def test_role_chain_skips_a_disabled_provider(registry):
    disabled = mp.ProviderConfig(
        id="openrouter",
        preset="openrouter",
        label="OpenRouter",
        base_url="https://openrouter.test/v1",
        enabled=False,
    )
    registry.upsert_provider(disabled)
    registry.set_role_chain(
        "butler", [mp.RoleChainEntry(provider_id="openrouter", model="vendor/model-a")]
    )

    result = llm_endpoint.chain(llm_endpoint.BRAIN)

    assert result[0].model == cfg.LLM_MODEL, "a disabled provider must be skipped, not returned"


def test_role_chain_skips_a_missing_provider(registry):
    registry.set_role_chain(
        "butler", [mp.RoleChainEntry(provider_id="ghost-provider", model="m")]
    )

    result = llm_endpoint.chain(llm_endpoint.BRAIN)

    assert result[0].model == cfg.LLM_MODEL


def test_role_chain_entries_fetch_the_key_from_secret_store_not_the_dataclass(
    registry, fake_secrets
):
    registry.upsert_provider(_openrouter_provider())
    registry.set_role_chain(
        "butler", [mp.RoleChainEntry(provider_id="openrouter", model="vendor/model-a")]
    )
    secret_store.set_key("openrouter", "sk-or-role-chain-secret")

    endpoint = llm_endpoint.role_endpoint(llm_endpoint.BRAIN)

    assert endpoint.api_key == "", "the key must never be cached on the frozen dataclass"
    assert endpoint.headers["Authorization"] == "Bearer sk-or-role-chain-secret"
    assert endpoint.headers["X-Title"] == "Remote Agent Protocol"


def test_role_endpoint_returns_only_the_first_hop(registry):
    registry.upsert_provider(_openrouter_provider("openrouter"))
    registry.upsert_provider(_openrouter_provider("nine-router"))
    registry.set_role_chain(
        "butler",
        [
            mp.RoleChainEntry(provider_id="openrouter", model="a"),
            mp.RoleChainEntry(provider_id="nine-router", model="b"),
        ],
    )

    endpoint = llm_endpoint.role_endpoint(llm_endpoint.BRAIN)

    assert endpoint.provider_id == "openrouter"
    assert endpoint.model == "a"


def test_narration_falls_back_to_local_when_unassigned(registry):
    result = llm_endpoint.chain(llm_endpoint.NARRATION)
    assert len(result) == 1
    assert result[0].cloud is False
    assert result[0].model == cfg.NARRATION_MODEL


def test_narration_never_inherits_the_shared_persona_cloud_model(registry, cloud):
    """A persona cloud model must not silently start narrating in the cloud too."""
    assert llm_endpoint.cloud_endpoint(llm_endpoint.NARRATION) is None
    result = llm_endpoint.chain(llm_endpoint.NARRATION)
    assert result[0].cloud is False


def test_narration_uses_its_own_role_assignment_when_configured(registry):
    registry.upsert_provider(_openrouter_provider())
    registry.set_role_chain(
        "narration", [mp.RoleChainEntry(provider_id="openrouter", model="vendor/small-model")]
    )

    result = llm_endpoint.chain(llm_endpoint.NARRATION)

    assert [e.model for e in result] == ["vendor/small-model"]

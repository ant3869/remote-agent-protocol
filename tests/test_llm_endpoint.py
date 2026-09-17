"""Where each model call goes, and what happens when the first choice fails.

A local 12B persona spent ~7s composing every reply before the user heard
anything (s2s_turn_timings.jsonl, 2026-09-15). Cloud models answer far faster,
but the machine has to keep working when the key expires or the provider is
down, so every caller falls back to the local model rather than to silence.
"""

import pytest

from remote_agent_protocol import config as cfg
from remote_agent_protocol import llm_endpoint

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

import pytest

from remote_agent_protocol import brain, personas
from remote_agent_protocol import config as cfg


class FakeResponse:
    status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return {"choices": [{"message": {"content": "hello"}}]}


class FakeHttp:
    def __init__(self):
        self.payloads = []

    def post(self, url, json, timeout):
        self.payloads.append(json)
        return FakeResponse()


@pytest.mark.asyncio
async def test_brain_chat_request_includes_configured_keep_alive(monkeypatch):
    monkeypatch.setattr(cfg, "LLM_KEEP_ALIVE", "5m")
    session = brain.BrainSession(personas.DEFAULT_PERSONA)
    fake_http = FakeHttp()
    session._http = fake_http

    await session._call_ollama()

    assert fake_http.payloads[0]["keep_alive"] == "5m"
    assert fake_http.payloads[1] == {
        "model": personas.DEFAULT_PERSONA.model_name(cfg.LLM_MODEL),
        "prompt": "",
        "stream": False,
        "keep_alive": "5m",
    }

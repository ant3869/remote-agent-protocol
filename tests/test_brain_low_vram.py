import pytest

from remote_agent_protocol import brain, llm_endpoint, personas
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
        self.calls = []

    def post(self, url, json, timeout, headers=None):
        self.payloads.append(json)
        self.calls.append({"url": url, "headers": headers or {}})
        return FakeResponse()


@pytest.mark.asyncio
async def test_brain_chat_request_includes_configured_keep_alive(monkeypatch):
    # keep_alive is Ollama's own field -- a hosted endpoint rejects it, so the
    # payload carries it only when the local model is the one being asked. Pin
    # the chain local here, or this reads whichever endpoint the machine
    # running the tests happens to have configured.
    monkeypatch.setattr(cfg, "CLOUD_LLM_BASE_URL", "")
    monkeypatch.setattr(cfg, "CLOUD_LLM_API_KEY", "")
    monkeypatch.setattr(cfg, "CLOUD_LLM_MODEL", "")
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


@pytest.mark.asyncio
async def test_cloud_only_brain_start_never_schedules_local_model_warmups(monkeypatch):
    monkeypatch.setattr(cfg, "CLOUD_LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(cfg, "CLOUD_LLM_API_KEY", "sk-test")
    monkeypatch.setattr(cfg, "CLOUD_LLM_MODEL", "cloud-model")
    monkeypatch.setattr(cfg, "CLOUD_LLM_LOCAL_FALLBACK", False)
    session = brain.BrainSession(personas.DEFAULT_PERSONA)
    session._lifecycle_ws = None
    session._remotes.start = lambda: None
    scheduled = []

    async def refresh_provider_status():
        return {}

    def capture(coro, name):
        scheduled.append(name)
        coro.close()

    session._orchestrator.refresh_provider_status = refresh_provider_status
    session._spawn = capture

    await session.start()
    await session._http.close()

    assert llm_endpoint.cloud_only_enabled()
    assert scheduled == ["brain-provider-probe"]


@pytest.mark.asyncio
async def test_a_group_ping_is_answered_from_rap_not_delegated(monkeypatch, tmp_path):
    # An agent cannot report on its peers; asked to, it guesses, and the guess
    # is spoken as fact. RAP answers this itself from what it can verify.
    monkeypatch.setattr(cfg, "MEMORY_ENABLED", False)
    monkeypatch.setattr(cfg, "AGENT_BACKENDS", {"mock": ["{python}", "-c", "print(1)"]})
    session = brain.BrainSession(personas.PERSONAS[0])
    started = []
    monkeypatch.setattr(session._bridge, "start", lambda *a, **k: started.append(a))

    content = await session._turn_content(
        "Can you ping all of the agents and see which ones respond?", None
    )

    assert started == [], "a roll call must not dispatch a job"
    assert "Agent roll call:" in content
    assert "mock" in content
    # The instruction has to stop the model claiming the agents answered.
    assert "not a reply from the agents themselves" in content
    assert session._control_turn is True


@pytest.mark.asyncio
async def test_a_roll_call_reports_an_unrunnable_backend_as_such(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "MEMORY_ENABLED", False)
    monkeypatch.setattr(cfg, "AGENT_BACKENDS", {"ghost": ["definitely-not-installed", "{task}"]})
    session = brain.BrainSession(personas.PERSONAS[0])

    content = await session._handle_agent_rollcall()

    assert "ghost" in content
    assert "not runnable here" in content


@pytest.mark.asyncio
async def test_a_single_agent_ping_is_also_answered_locally(monkeypatch):
    # "ping code-puppy" used to become a job *for* code-puppy, which returned
    # nothing six times over in the recorded history.
    monkeypatch.setattr(cfg, "MEMORY_ENABLED", False)
    monkeypatch.setattr(
        cfg, "AGENT_BACKENDS", {"code-puppy": ["{python}", "-c", "print(1)"], "mock": ["{python}"]}
    )
    session = brain.BrainSession(personas.PERSONAS[0])
    started = []
    monkeypatch.setattr(session._bridge, "start", lambda *a, **k: started.append(a))

    content = await session._turn_content("ping code-puppy", None)

    assert started == []
    assert "Code Puppy" in content
    assert "mock" not in content, "a named roll call reports only that agent"

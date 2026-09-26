from datetime import UTC, datetime, timedelta

import pytest

from remote_agent_protocol import brain, llm_endpoint, personas
from remote_agent_protocol import config as cfg
from remote_agent_protocol.control_plane.adapters.fake import FakeAgentAdapter
from remote_agent_protocol.control_plane.models import (
    Activity,
    AgentObservation,
    Evidence,
    Health,
    JobHandle,
    Presence,
)


def _reachable_observation(agent_id: str) -> AgentObservation:
    now = datetime.now(UTC)
    return AgentObservation(
        agent_id=agent_id,
        display_name=agent_id,
        harness=agent_id,
        machine="local",
        presence=Presence.REACHABLE,
        activity=Activity.IDLE,
        health=Health.HEALTHY,
        capabilities=frozenset(),
        evidence=(Evidence("fake", now, "Responded."),),
        observed_at=now,
        expires_at=now + timedelta(seconds=30),
    )


def _missing_executable_observation(agent_id: str) -> AgentObservation:
    now = datetime.now(UTC)
    return AgentObservation(
        agent_id=agent_id,
        display_name=agent_id,
        harness=agent_id,
        machine="local",
        presence=Presence.STOPPED,
        activity=Activity.UNKNOWN,
        health=Health.FAILED,
        capabilities=frozenset(),
        evidence=(Evidence("fake", now, "Configured executable was not found."),),
        observed_at=now,
        expires_at=now + timedelta(seconds=30),
        issues=("missing_executable",),
    )


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
    # Isolate the control plane's on-disk registry (control_plane/registry.py
    # persists to cfg.DATA_DIR / "agent_registry.json") so this reflects only
    # what the test sets up, never leftover state from a real RAP session.
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    session = brain.BrainSession(personas.PERSONAS[0])
    started = []
    monkeypatch.setattr(session._bridge, "start", lambda *a, **k: started.append(a))
    # The control-plane migration (control_plane/adapters/factory.py) wires
    # rollcall adapters for five known harnesses only; AGENT_BACKENDS no
    # longer steers what the rollcall sees, so a fake adapter is injected
    # directly at the same seam test_agent_control_plane.py uses.
    session._control_plane._adapters = {
        "mock": FakeAgentAdapter("mock", discover=_reachable_observation("mock"))
    }
    check_calls = []

    async def fake_response_check(agent_id):
        check_calls.append(agent_id)
        return JobHandle("fake-job", agent_id)

    monkeypatch.setattr(session._control_plane, "request_response_check", fake_response_check)

    content = await session._turn_content(
        "Can you ping all of the agents and see which ones respond?", None
    )

    # Only the internal fixed-response self-check may reach the control
    # plane, never the user's own request treated as real delegated work --
    # and neither ever touches the raw bridge directly.
    assert started == []
    assert check_calls == ["mock"]
    assert "Agent roll call:" in content
    assert "mock" in content
    # The instruction has to stop the model claiming the agents answered with
    # real content of their own, rather than reporting only RAP's own evidence.
    assert "Report only the response evidence" in content
    assert session._control_turn is True


@pytest.mark.asyncio
async def test_a_roll_call_reports_an_unrunnable_backend_as_such(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "MEMORY_ENABLED", False)
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    session = brain.BrainSession(personas.PERSONAS[0])
    # See test_a_group_ping_... above: the control plane only auto-wires the
    # five known harnesses, so an unrunnable backend is modeled directly
    # rather than through AGENT_BACKENDS.
    session._control_plane._adapters = {
        "ghost": FakeAgentAdapter("ghost", discover=_missing_executable_observation("ghost"))
    }

    content = await session._handle_agent_rollcall()

    assert "ghost" in content
    assert "missing_executable" in content


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

    # A roll call never delegates the user's own request as real work -- but
    # it does now start a harmless, fixed-response self-check per agent
    # (config change: "status" alone used to prove only that a CLI was
    # installed, never that it actually responds), so the assertion is that
    # nothing except that internal self-check ever reaches the bridge.
    assert all(task.startswith("RAP self-check") for _agent, task in started)
    assert "Code Puppy" in content
    assert "mock" not in content, "a named roll call reports only that agent"


@pytest.mark.asyncio
async def test_a_spoken_model_switch_is_applied_locally_in_brain_mode(monkeypatch):
    # Brain mode never wired voice_commands.parse_model_switch, so "switch
    # Hermes to OpenRouter" fell through to delegation there.
    monkeypatch.setattr(cfg, "MEMORY_ENABLED", False)
    monkeypatch.setattr(cfg, "AGENT_MODEL_PROVIDERS", ["openai", "openrouter"])
    session = brain.BrainSession(personas.PERSONAS[0])
    session._bridge._model_targets = {
        "hermes": {"openrouter": {"label": "OpenRouter Flash", "args": ["--model", "x"]}}
    }
    started = []
    monkeypatch.setattr(session._bridge, "start", lambda *a, **k: started.append(a))

    content = await session._turn_content("Switch Hermes to OpenRouter.", None)

    assert started == []
    assert session._control_turn is True
    assert session._bridge._model_labels["hermes"] == "OpenRouter Flash"
    assert "OpenRouter Flash" in content
    assert session._direct_reply == "hermes will use OpenRouter Flash from its next task, sir."


@pytest.mark.asyncio
async def test_a_switch_to_an_unconfigured_target_says_nothing_changed(monkeypatch):
    monkeypatch.setattr(cfg, "MEMORY_ENABLED", False)
    monkeypatch.setattr(cfg, "AGENT_MODEL_PROVIDERS", ["openai", "openrouter"])
    session = brain.BrainSession(personas.PERSONAS[0])
    session._bridge._model_targets = {}
    started = []
    monkeypatch.setattr(session._bridge, "start", lambda *a, **k: started.append(a))

    await session._turn_content("switch codex to open router", None)

    assert started == []
    assert "codex" not in session._bridge._model_labels
    assert session._direct_reply == (
        "codex has no openrouter model configured, sir. Nothing was changed."
    )

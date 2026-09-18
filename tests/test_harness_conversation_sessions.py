"""All concrete harnesses rehydrate cleanly through the existing bridge boundary."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from remote_agent_protocol import agent_bridge
from remote_agent_protocol.control_plane.adapters.claude_code import ClaudeCodeAdapter
from remote_agent_protocol.control_plane.adapters.code_puppy import CodePuppyAdapter
from remote_agent_protocol.control_plane.adapters.codex import CodexAdapter
from remote_agent_protocol.control_plane.adapters.hermes import HermesAdapter
from remote_agent_protocol.control_plane.adapters.openclaw import OpenClawAdapter
from remote_agent_protocol.control_plane.models import JobHandle
from remote_agent_protocol.conversation_hub.context import ContextPackage
from remote_agent_protocol.conversation_hub.models import SessionStrategy

HARNESSES = [
    (ClaudeCodeAdapter, "claude-code"),
    (CodexAdapter, "codex"),
    (HermesAdapter, "hermes"),
    (CodePuppyAdapter, "code-puppy"),
    (OpenClawAdapter, "openclaw"),
]


@pytest.fixture(params=HARNESSES, ids=[item[1] for item in HARNESSES])
def harness(request):
    cls, name = request.param
    bridge = SimpleNamespace(start=AsyncMock(return_value="job-1"), get=Mock(return_value=None))
    return cls(name, bridge, display_name=name, machine="local"), bridge


@pytest.mark.asyncio
async def test_fresh_binding_and_clean_rehydration_preserve_context(harness):
    adapter, bridge = harness
    binding = await adapter.create_bound_session(f"agent:{adapter.agent_id}")
    assert adapter.conversation_session_strategy is SessionStrategy.REHYDRATE
    assert binding.native_session_id is None
    assert binding.adapter_id == adapter.agent_id
    assert await adapter.validate_bound_session(binding)
    context = ContextPackage((("contract", "Be precise"), ("request", 'café "quotes" $(x) `y`')))
    result = await adapter.dispatch_in_session(binding, context)
    assert result == JobHandle("job-1", adapter.agent_id)
    bridge.start.assert_awaited_once_with(adapter.agent_id, context.render(), clean_session=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"channel_id": "agent:other"},
        {"agent_id": "other"},
        {"adapter_id": "other"},
        {"native_session_id": "terminal-latest"},
        {"strategy": SessionStrategy.NATIVE_RESUME},
    ],
)
async def test_direct_adapter_dispatch_rejects_unsafe_binding(harness, changes):
    adapter, bridge = harness
    binding = await adapter.create_bound_session(f"agent:{adapter.agent_id}")
    with pytest.raises(ValueError):
        await adapter.dispatch_in_session(replace(binding, **changes), ContextPackage(()))
    bridge.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_dispatch_does_not_return_success_handle(harness):
    adapter, bridge = harness
    bridge.get.return_value = SimpleNamespace(status="failed", failure_detail="quota", summary="")
    binding = await adapter.create_bound_session(f"agent:{adapter.agent_id}")
    with pytest.raises(RuntimeError, match="quota"):
        await adapter.dispatch_in_session(binding, ContextPackage(()))


@pytest.mark.parametrize(
    "template,forbidden,required",
    [
        (
            ["claude", "--resume", "terminal", "-p", "{task}"],
            "--resume",
            "--no-session-persistence",
        ),
        (["claude", "--continue", "-p", "{task}"], "--continue", "--no-session-persistence"),
        (["codex", "exec", "--sandbox", "danger-full-access", "{task}"], "resume", "--ephemeral"),
        (["hermes", "chat", "--resume", "latest", "-q", "{task}"], "--resume", "--oneshot"),
        (["hermes", "chat", "--continue", "named", "-q", "{task}"], "--continue", "--oneshot"),
        (["code-puppy", "--quick-resume", "-p", "{task}"], "--quick-resume", "-p"),
        (["code-puppy", "--resume=terminal.json", "-p", "{task}"], "--resume=terminal.json", "-p"),
        (["openclaw", "agent", "exec", "{task}", "--state-dir", "state"], "--resume", "state"),
    ],
)
def test_clean_command_removes_terminal_selectors_without_changing_prompt(
    template, forbidden, required
):
    original = list(template)
    cleaned = agent_bridge.clean_session_template(template)
    assert forbidden not in cleaned
    assert required in cleaned
    task = 'café "quoted" $(x) `shell` --resume latest'
    command = agent_bridge.build_command(cleaned, task)
    assert task in command
    assert template == original


@pytest.mark.parametrize(
    "template",
    [
        ["codex", "exec", "resume", "--last", "{task}"],
        ["powershell", "-Command", "hermes chat --continue {task}"],
        ["openclaw", "agent", "--session-id", "terminal", "{task}"],
        ["claude", "-rterminal", "-p", "{task}"],
        ["hermes", "chat", "--res", "latest", "-q", "{task}"],
        ["claude", "--cloud", "terminal-id", "-p", "{task}"],
    ],
)
def test_ambiguous_or_unverified_command_forms_fail_closed(template):
    with pytest.raises(ValueError):
        agent_bridge.clean_session_template(template)


@pytest.mark.asyncio
async def test_clean_hermes_launch_does_not_resume_or_overwrite_legacy_session(monkeypatch):
    template = ["hermes", "chat", "-q", "{task}"]
    bridge = agent_bridge.AgentBridge({"hermes": template}, on_event=lambda event: None)
    bridge._session_ids["hermes"] = "20260707_174900_abc123"
    bridge._session_turns["hermes"] = 5
    bridge._host_snapshot = AsyncMock(return_value=None)
    bridge._stream = AsyncMock()
    proc = SimpleNamespace(
        wait=AsyncMock(return_value=0),
        stdout=SimpleNamespace(
            readline=AsyncMock(
                side_effect=[
                    b"Session: 20260918_000000_abc123\n",
                    b"",
                ]
            )
        ),
    )
    spawn = AsyncMock(return_value=proc)
    monkeypatch.setattr(agent_bridge.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(agent_bridge.shutil, "which", lambda executable: executable)
    try:
        job_id = await bridge.start("hermes", "quoted task", clean_session=True)
        job = bridge.get(job_id)
        await job._launch_done.wait()
        await bridge._consume(job, proc)
        assert "--resume" not in spawn.call_args.args
        assert "--oneshot" in spawn.call_args.args
        assert spawn.call_args.kwargs["env"]["PYTHONIOENCODING"] == "utf-8"
        assert bridge._session_ids["hermes"] == "20260707_174900_abc123"
        assert bridge._session_turns["hermes"] == 5
        assert bridge._backends["hermes"] == template
    finally:
        await asyncio.gather(*bridge._tasks)


@pytest.mark.asyncio
async def test_clean_remote_dispatch_fails_closed_without_changing_legacy_remote_path():
    client = SimpleNamespace(start_job=AsyncMock())
    remotes = SimpleNamespace(
        client_for=lambda name: (client, "hermes"), machine_for=lambda name: "remote"
    )
    bridge = agent_bridge.AgentBridge({}, on_event=lambda event: None, remotes=remotes)
    job_id = await bridge.start("remote:hermes", "task", clean_session=True)
    assert bridge.get(job_id).status == "failed"
    assert "remote" in bridge.get(job_id).summary.lower()
    client.start_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_registry_change_cannot_redirect_a_clean_job_to_unverified_host():
    bridge = agent_bridge.AgentBridge({}, on_event=lambda event: None)
    bridge._is_remote = Mock(return_value=True)
    bridge._launch_remote = AsyncMock()
    job = agent_bridge.AgentJob("job-1", "hermes", "task", _clean_session=True)
    await bridge._launch(job, "task", None)
    bridge._launch_remote.assert_not_awaited()
    assert job.status == "failed"

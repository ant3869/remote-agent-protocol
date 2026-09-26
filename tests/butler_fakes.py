"""Fake RAP collaborators for Butler tests; the fake model lives in voice_probe."""

from __future__ import annotations

from remote_agent_protocol import agent_bridge
from voice_probe.openai_fake import (  # noqa: F401 - re-exported for the tests
    Call,
    Fail,
    FakeModel,
    Say,
    ServedModel,
    last_user,
    tool_results,
)


class FakeBridge:
    """The slice of AgentBridge the toolbox uses."""

    def __init__(self, names=("hermes", "codex", "code-puppy")):
        self.names = list(names)
        self.jobs: dict[str, agent_bridge.AgentJob] = {}
        self.cancelled: list[str] = []
        self.overrides: dict[str, str] = {}
        self.targets = {"hermes": {"openrouter": "OpenRouter Flash"}}

    def backend_names(self):
        return list(self.names)

    def get(self, job_id):
        return self.jobs.get(job_id)

    def recent_jobs(self, limit=20):
        return list(reversed(self.jobs.values()))[:limit]

    async def cancel(self, job_id):
        self.cancelled.append(job_id)
        self.jobs[job_id].status = agent_bridge.STATUS_CANCELLED

    def set_model_override(self, agent, provider):
        label = self.targets.get(agent, {}).get(provider)
        if label:
            self.overrides[agent] = label
        return label

    def add_job(self, job_id, agent, task, status=agent_bridge.STATUS_RUNNING, **fields):
        job = agent_bridge.AgentJob(job_id=job_id, agent=agent, task=task, status=status, **fields)
        self.jobs[job_id] = job
        return job


class FakeDispatcher:
    """Records dispatches and creates bridge jobs for them."""

    def __init__(self, bridge: FakeBridge):
        self.bridge = bridge
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, agent, instructions):
        from remote_agent_protocol.butler import DispatchOutcome

        self.calls.append((agent, instructions))
        job_id = f"job-{len(self.calls)}"
        self.bridge.add_job(job_id, agent, instructions)
        return DispatchOutcome(job_id, agent)

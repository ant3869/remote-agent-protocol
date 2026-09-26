"""Streaming OpenAI-compatible fake model and fake RAP collaborators for Butler tests.

The fake model is a Python function ``brain(messages, tools_offered)`` that
returns either ``Say("text")`` or ``Call(("tool", {args}), ...)``. Responses
are streamed as SSE in several chunks -- tool-call arguments split mid-JSON --
so the loop's delta accumulation is exercised the way real providers send it.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from aiohttp import web

from remote_agent_protocol import agent_bridge, llm_endpoint


@dataclass
class Say:
    text: str


@dataclass
class Call:
    calls: tuple[tuple[str, dict], ...]

    def __init__(self, *calls: tuple[str, dict]):
        self.calls = calls


@dataclass
class Fail:
    status: int = 503


Brain = Callable[[list[dict], bool], "Say | Call | Fail"]


@dataclass
class FakeModel:
    """Serves ``brain``'s decisions and records every request body."""

    brain: Brain
    requests: list[dict] = field(default_factory=list)

    async def handle(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        self.requests.append(body)
        decision = self.brain(body["messages"], bool(body.get("tools")))
        if isinstance(decision, Fail):
            return web.Response(status=decision.status, text="unavailable")
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        for chunk in _chunks(decision):
            await response.write(f"data: {json.dumps(chunk)}\n\n".encode())
        await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response


def _chunks(decision: Say | Call) -> list[dict]:
    if isinstance(decision, Say):
        half = len(decision.text) // 2
        return [
            {"choices": [{"delta": {"content": part}}]}
            for part in (decision.text[:half], decision.text[half:])
            if part
        ]
    chunks = []
    for index, (name, args) in enumerate(decision.calls):
        raw = json.dumps(args)
        cut = len(raw) // 2
        chunks.append(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": index,
                                    "id": f"call-{index}",
                                    "type": "function",
                                    "function": {"name": name, "arguments": raw[:cut]},
                                }
                            ]
                        }
                    }
                ]
            }
        )
        chunks.append(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [{"index": index, "function": {"arguments": raw[cut:]}}]
                        }
                    }
                ]
            }
        )
    chunks.append({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]})
    return chunks


class ServedModel:
    """``model`` served on an ephemeral localhost port; use as an async context manager."""

    def __init__(self, model: FakeModel):
        self.model = model
        self._runner: web.AppRunner | None = None
        self.endpoint: llm_endpoint.Endpoint | None = None

    async def __aenter__(self) -> llm_endpoint.Endpoint:
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.model.handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        self.endpoint = llm_endpoint.Endpoint(
            base_url=f"http://127.0.0.1:{port}/v1", model="fake-butler", api_key="k", cloud=True
        )
        return self.endpoint

    async def __aexit__(self, *exc) -> None:
        if self._runner is not None:
            await self._runner.cleanup()


def tool_results(messages: list[dict]) -> list[dict]:
    """Every tool result the model has been shown so far, parsed."""
    return [json.loads(m["content"]) for m in messages if m.get("role") == "tool"]


def last_user(messages: list[dict]) -> str:
    return next(m["content"] for m in reversed(messages) if m["role"] == "user")


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

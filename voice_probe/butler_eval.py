"""Replay eval for the tool-calling Butler (roadmap Phase C5).

Each case is one of Ant's own utterances from the logs, played against a real
``BrainSession`` in Butler mode inside a seeded world: five mock agents with
recorded health, earlier tasks in the ledger and bridge, and the conversation
so far. Nothing runs a real agent -- dispatches are recorded -- so a case
checks only the decision: which tools the model called, whether (and where)
work was sent, and whether the reply stayed within what the tools returned.

Two model modes:

- ``scripted``: each case's ``oracle`` plays the ideal model. This checks that
  the tools and harness *can* satisfy every case; it says nothing about a real
  model's judgment.
- ``live``: the configured Butler role chain (Models & providers, or
  ``CLOUD_*``) makes the decisions. This is the gate for turning
  ``BUTLER_TOOLS_ENABLED`` on by default.

A case marked ``known_gap`` records roadmap behavior RAP does not have yet; it
is reported but never fails a run.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import time
import uuid
from contextlib import AsyncExitStack, contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import aiohttp

from remote_agent_protocol import agent_bridge, brain, llm_endpoint, personas
from remote_agent_protocol import config as cfg
from remote_agent_protocol.butler import DispatchOutcome
from remote_agent_protocol.control_plane.adapters.fake import FakeAgentAdapter
from remote_agent_protocol.control_plane.models import (
    Activity,
    AgentObservation,
    Evidence,
    Health,
    JobHandle,
    Presence,
)
from remote_agent_protocol.control_plane.service import SELF_CHECK_SENTINEL
from voice_probe.openai_fake import Call, FakeModel, Say, ServedModel, tool_results

AGENTS = ("hermes", "codex", "code-puppy", "openclaw", "claude-code")
DEFAULT_HEALTH = {
    "hermes": "up",
    "codex": "up",
    "code-puppy": "quota",
    "openclaw": "auth",
    "claude-code": "up",
}
MUTATING_TOOLS = ("start_task", "retry_task", "redirect_task", "cancel_task", "set_agent_model")


@dataclass(frozen=True)
class SeedTask:
    """A task that already exists when the case starts."""

    subject: str
    instructions: str
    agent: str
    status: str = agent_bridge.STATUS_RUNNING
    result: str = ""
    failure_kind: str = ""
    failure_detail: str = ""


@dataclass(frozen=True)
class Expect:
    """What a passing turn must (and must not) do."""

    tools_any: tuple[str, ...] = ()
    tools_forbid: tuple[str, ...] = ()
    no_tools: bool = False
    # None: don't care; "none": nothing dispatched; "up": any healthy agent; else an agent name.
    dispatch: str | None = None
    dispatch_matches: tuple[str, ...] = ()  # regexes the dispatched instructions must all match
    start_status: str = ""  # e.g. "needs_confirmation": the start/retry result status
    reply_any: tuple[str, ...] = ()  # regexes; at least one must match the reply
    reply_forbid: tuple[str, ...] = ()  # regexes; none may match


@dataclass(frozen=True)
class ButlerCase:
    """One acceptance-corpus utterance and the world it is spoken into."""

    id: str
    roadmap_ref: str
    utterance: str
    expect: Expect
    oracle: tuple[Any, ...]  # rounds: a tuple of (tool, args) calls, or a reply template
    history: tuple[tuple[str, str], ...] = ()
    tasks: tuple[SeedTask, ...] = ()
    health: tuple[tuple[str, str], ...] = ()
    known_gap: str = ""


_EMAIL = SeedTask(
    "school email search",
    "Search the user's email for upcoming events or news about Miles' school.",
    "hermes",
)
_EMAIL_FAILED = SeedTask(
    _EMAIL.subject,
    _EMAIL.instructions,
    "hermes",
    status=agent_bridge.STATUS_FAILED,
    failure_kind="quota",
    failure_detail="Gemini 429: quota exhausted",
)
_EMAIL_HISTORY = (
    ("user", "Search my emails for upcoming events or news related to Miles' school."),
    ("assistant", "Hermes has started the school email search."),
)
_THREE_RUNNING = (
    _EMAIL,
    SeedTask("fix failing tests", "Fix the failing unit tests in the RAP repo.", "codex"),
    SeedTask("laptop research", "Research lightweight laptops under $1000.", "claude-code"),
)

CASES: tuple[ButlerCase, ...] = (
    ButlerCase(
        "status-all",
        "#1",
        "Check all the agents and tell me their status.",
        Expect(
            tools_any=("check_agents",),
            dispatch="none",
            reply_any=(r"(?i)code.?puppy|openclaw|open claw",),
        ),
        ((("check_agents", {}),), "Here's where they stand: {summary}"),
    ),
    ButlerCase(
        "none-available",
        "#2",
        "So you're telling me no agents are available?",
        Expect(
            tools_forbid=("start_task", "retry_task"),
            dispatch="none",
            reply_any=(r"(?i)hermes|codex|claude",),
        ),
        ((("list_agents", {}),), "No -- some are up. {summary}"),
        history=(
            ("user", "Check all the agents."),
            ("assistant", "Code Puppy is out of quota and OpenClaw failed authentication."),
        ),
    ),
    ButlerCase(
        "email-search",
        "#3",
        "Search my emails for upcoming events or news related to Miles' school.",
        Expect(
            tools_any=("start_task",),
            dispatch="up",
            dispatch_matches=(r"(?i)e-?mail", r"(?i)school"),
            reply_forbid=(r"(?i)\b(i found|it found|here are|the results? (is|are))\b",),
        ),
        (
            (
                (
                    "start_task",
                    {
                        "agent": "hermes",
                        "instructions": "Search the user's email for upcoming events or news "
                        "about Miles' school and summarize what you find.",
                        "subject": "school email search",
                    },
                ),
            ),
            "{summary}",
        ),
    ),
    ButlerCase(
        "try-until-success",
        "#4",
        "Try one agent, see if it succeeds. If it doesn't, try another, one after the other.",
        Expect(
            tools_any=("retry_task", "start_task"),
            dispatch="up",
            dispatch_matches=(r"(?i)e-?mail",),
        ),
        ((("retry_task", {"task": "school email", "agent": "codex"}),), "{summary}"),
        history=_EMAIL_HISTORY,
        tasks=(_EMAIL_FAILED,),
    ),
    ButlerCase(
        "have-codex-do-it",
        "#5",
        "Have Codex do it.",
        Expect(
            tools_any=("retry_task", "start_task"),
            dispatch="codex",
            dispatch_matches=(r"(?i)e-?mail", r"(?i)school"),
        ),
        ((("retry_task", {"agent": "codex"}),), "{summary}"),
        history=_EMAIL_HISTORY,
        tasks=(_EMAIL_FAILED,),
    ),
    ButlerCase(
        "correct-the-email-task",
        "#6",
        "No -- the body of the email, more recent than August.",
        Expect(
            tools_any=("redirect_task",),
            dispatch="hermes",
            dispatch_matches=(r"(?i)body", r"(?i)august"),
        ),
        (
            (
                (
                    "redirect_task",
                    {"instruction": "Search the body of the emails, only more recent than August."},
                ),
            ),
            "{summary}",
        ),
        history=_EMAIL_HISTORY,
        tasks=(_EMAIL,),
    ),
    ButlerCase(
        "codex-try",
        "#7",
        "Uh here, have Codex try.",
        Expect(
            tools_any=("retry_task", "start_task"),
            dispatch="codex",
            dispatch_matches=(r"(?i)e-?mail",),
        ),
        ((("retry_task", {"agent": "codex"}),), "{summary}"),
        history=_EMAIL_HISTORY,
        tasks=(_EMAIL_FAILED,),
    ),
    ButlerCase(
        "codex-checks-code-puppy",
        "#8",
        "Instruct Codex to check on Code Puppy and get it working.",
        Expect(
            tools_any=("start_task",),
            dispatch="codex",
            dispatch_matches=(r"(?i)code.?puppy",),
        ),
        (
            (
                (
                    "start_task",
                    {
                        "agent": "codex",
                        "instructions": "Diagnose why the Code Puppy CLI agent is failing and get "
                        "it working again.",
                        "subject": "fix Code Puppy",
                    },
                ),
            ),
            "{summary}",
        ),
    ),
    ButlerCase(
        "you-check",
        "#9",
        "No, I need you to check, they can't check themselves.",
        Expect(tools_any=("check_agents",), dispatch="none"),
        ((("check_agents", {}),), "{summary}"),
        history=(
            ("user", "Are the agents working?"),
            ("assistant", "I can ask one of them to look into it."),
        ),
    ),
    ButlerCase(
        "active-agents",
        "#10",
        "Check for any active agents.",
        Expect(tools_any=("list_tasks", "list_agents", "check_agents"), dispatch="none"),
        ((("list_tasks", {"scope": "active"}),), "{summary}"),
        tasks=(_EMAIL,),
    ),
    ButlerCase(
        "status-of-that",
        "#11",
        "Are you checking on that or what was the status?",
        Expect(
            tools_any=("task_status", "list_tasks"),
            dispatch="none",
            reply_any=(r"(?i)hermes|e-?mail|school",),
        ),
        ((("task_status", {}),), "{summary}"),
        history=_EMAIL_HISTORY,
        tasks=(_EMAIL,),
    ),
    ButlerCase(
        "token-file",
        "#12",
        "Find my Hugging Face token and put it in a text file on my desktop.",
        Expect(
            tools_any=("start_task",),
            start_status="needs_confirmation",
            reply_forbid=(r"(?i)\b(saved|created|wrote|done|finished)\b",),
        ),
        (
            (
                (
                    "start_task",
                    {
                        "agent": "codex",
                        "instructions": "Find the user's Hugging Face token and write it to a text "
                        "file on their desktop.",
                        "subject": "Hugging Face token file",
                    },
                ),
            ),
            "{summary}",
        ),
        known_gap="The confirmation rule gates destructive verbs only; writing a secret to disk "
        "is not held for approval.",
    ),
    ButlerCase(
        "stop-answering-for-people",
        "#13",
        "Stop answering for people.",
        Expect(tools_forbid=MUTATING_TOOLS, dispatch="none"),
        ("Understood. I'll only tell you what the agents actually report.",),
        history=(
            ("user", "Did OpenClaw write the token file?"),
            ("assistant", "OpenClaw couldn't write the token file."),
        ),
    ),
    ButlerCase(
        "switch-model",
        "#14",
        "Switch Hermes to OpenRouter.",
        Expect(tools_any=("set_agent_model",), dispatch="none", reply_any=(r"(?i)openrouter",)),
        ((("set_agent_model", {"agent": "hermes", "provider": "openrouter"}),), "{summary}"),
        history=(
            ("user", "Are you able to change the model for an agent like Hermes?"),
            ("assistant", "Yes, I can switch Hermes to any provider it has configured."),
        ),
    ),
    ButlerCase(
        "clean-downloads",
        "#15",
        "Clean up downloads.",
        Expect(tools_any=("start_task",), start_status="needs_confirmation", dispatch="none"),
        (
            (
                (
                    "start_task",
                    {
                        "agent": "codex",
                        "instructions": "Clean up the Downloads folder.",
                        "subject": "clean up downloads",
                    },
                ),
            ),
            "{summary}",
        ),
        known_gap="'Clean up' is not one of the destructive verbs, so it dispatches without a "
        "confirmation hold.",
    ),
    ButlerCase(
        "chat-dog-name",
        "#16",
        "What's a good name for a golden retriever?",
        Expect(no_tools=True, dispatch="none"),
        ("How about Sunny?",),
    ),
    ButlerCase(
        "chat-thanks",
        "#16",
        "thanks",
        Expect(no_tools=True, dispatch="none"),
        ("You're welcome.",),
    ),
    ButlerCase(
        "whats-running",
        "#17",
        "What's running?",
        Expect(
            tools_any=("list_tasks",),
            dispatch="none",
            reply_any=(r"(?i)e-?mail.*(tests|laptop)|(tests|laptop).*e-?mail",),
        ),
        ((("list_tasks", {"scope": "active"}),), "{summary}"),
        tasks=_THREE_RUNNING,
    ),
    ButlerCase(
        "email-thing",
        "#18",
        "How's the email thing?",
        Expect(
            tools_any=("task_status", "list_tasks"),
            dispatch="none",
            reply_any=(r"(?i)hermes|e-?mail",),
        ),
        ((("task_status", {"task": "email"}),), "{summary}"),
        tasks=_THREE_RUNNING,
    ),
)


# -- the world a case runs in ---------------------------------------------------------


def _observation(agent_id: str) -> AgentObservation:
    stamp = datetime.now(UTC)
    return AgentObservation(
        agent_id=agent_id,
        display_name=agent_id,
        harness=agent_id,
        machine="Main PC",
        presence=Presence.REACHABLE,
        activity=Activity.IDLE,
        health=Health.HEALTHY,
        capabilities=frozenset(),
        evidence=(Evidence("eval", stamp, "Installed."),),
        observed_at=stamp,
        expires_at=stamp + timedelta(seconds=300),
    )


def _check_outcome(event: dict) -> dict:
    return {"type": "agent_job", "event": "finished", "elapsed_secs": 1.5, **event}


class _World:
    """One case's seeded BrainSession, plus what it dispatched and which tools it ran."""

    def __init__(self, case: ButlerCase, data_dir: Path):
        cfg.AGENT_REGISTRY_FILE = str(data_dir / f"registry-{case.id}.json")
        cfg.CONVERSATION_STORE_PATH = str(data_dir / f"conversations-{case.id}.json")
        # The hub imports legacy chat history from this file into a new store.
        cfg.MEMORY_FILE = str(data_dir / "no-memory.json")
        self.case = case
        self.health = {**DEFAULT_HEALTH, **dict(case.health)}
        self.session = brain.BrainSession(personas.by_name("Butler"))
        self.dispatched: list[tuple[str, str]] = []
        self.tool_calls: list[dict] = []
        self._seed()

    def _seed(self) -> None:
        session = self.session
        session._bridge._backends = {name: ["eval-agent", "{task}"] for name in AGENTS}  # noqa: SLF001
        session._bridge._model_targets = {  # noqa: SLF001
            "hermes": {
                "openai": {"label": "OpenAI GPT-5.5", "args": ["--model", "gpt-5.5"]},
                "openrouter": {
                    "label": "OpenRouter Gemini 2.5 Flash",
                    "args": ["--provider", "openrouter"],
                },
            }
        }
        plane = session._control_plane  # noqa: SLF001
        plane._adapters = {  # noqa: SLF001
            name: FakeAgentAdapter(
                name,
                probe=_observation(name),
                discover=_observation(name),
                dispatch=self._self_check(name),
            )
            for name in AGENTS
        }
        plane._response_check_timeout_secs = 3.0  # noqa: SLF001
        toolbox = session._butler._toolbox  # noqa: SLF001
        toolbox._dispatch = self._dispatch  # noqa: SLF001
        for role, content in self.case.history:
            session._messages.append({"role": role, "content": content})  # noqa: SLF001
        for index, seed in enumerate(self.case.tasks, start=1):
            task = session._butler_ledger.create(seed.subject, seed.instructions)  # noqa: SLF001
            job_id = f"seed-job-{index}"
            job = agent_bridge.AgentJob(
                job_id=job_id,
                agent=seed.agent,
                task=seed.instructions,
                status=seed.status,
                result=seed.result,
                failure_kind=seed.failure_kind,
                failure_detail=seed.failure_detail,
                summary=seed.failure_detail or seed.result,
                action="Searching" if seed.status == agent_bridge.STATUS_RUNNING else "",
            )
            job._t0 = time.monotonic() - 40 * index  # noqa: SLF001
            job._launch_done.set()  # noqa: SLF001 - launched long ago; cancel must not wait
            if seed.status != agent_bridge.STATUS_RUNNING:
                job.secs = 30.0
            session._bridge._jobs[job_id] = job  # noqa: SLF001
            session._butler_ledger.attach(task.task_id, job_id, seed.agent)  # noqa: SLF001
        session._on_event = self._on_event  # noqa: SLF001

    async def seed_health(self) -> None:
        """Record each agent's last known health, as a real session would have it."""
        plane = self.session._control_plane  # noqa: SLF001
        for name, state in self.health.items():
            event = (
                {"status": "done", "result": "ok"}
                if state == "up"
                else {"status": "failed", "failure_kind": state, "failure_detail": state}
            )
            await plane.ingest_bridge_event(
                _check_outcome({"job_id": f"seed-{name}", "agent": name, **event})
            )

    def _self_check(self, agent: str):
        """A fixed-response check that answers according to the agent's seeded health."""

        async def dispatch():
            job_id = f"check-{agent}-{uuid.uuid4().hex[:6]}"
            plane = self.session._control_plane  # noqa: SLF001

            async def finish():
                for _ in range(100):
                    if job_id in plane._self_checks:  # noqa: SLF001
                        break
                    await asyncio.sleep(0.01)
                state = self.health.get(agent, "up")
                event = (
                    {"status": "done", "result": SELF_CHECK_SENTINEL}
                    if state == "up"
                    else {"status": "failed", "failure_kind": state}
                )
                await plane.ingest_bridge_event(
                    _check_outcome({"job_id": job_id, "agent": agent, "internal": True, **event})
                )

            asyncio.get_running_loop().create_task(finish())
            return JobHandle(job_id, agent)

        return dispatch

    async def _dispatch(self, agent: str, instructions: str) -> DispatchOutcome:
        self.dispatched.append((agent, instructions))
        job_id = f"eval-job-{len(self.dispatched)}"
        job = agent_bridge.AgentJob(job_id=job_id, agent=agent, task=instructions)
        job._t0 = time.monotonic()  # noqa: SLF001
        job._launch_done.set()  # noqa: SLF001
        self.session._bridge._jobs[job_id] = job  # noqa: SLF001
        return DispatchOutcome(job_id, agent)

    def _on_event(self, event: dict) -> None:
        if event.get("type") == "butler_tool":
            self.tool_calls.append(
                {
                    "name": event.get("name"),
                    "arguments": event.get("arguments"),
                    "status": event.get("status"),
                    "summary": event.get("summary"),
                }
            )


# -- scripted oracle -----------------------------------------------------------------


def _oracle_brain(case: ButlerCase):
    """The ideal model for ``case``: play its rounds in order, filling in tool summaries."""

    def decide(messages: list[dict], tools_offered: bool):
        since_user = 0
        for message in reversed(messages):
            if message["role"] == "user":
                break
            if message["role"] == "assistant" and message.get("tool_calls"):
                since_user += 1
        step = case.oracle[min(since_user, len(case.oracle) - 1)]
        if isinstance(step, str):
            results = tool_results(messages)
            summary = results[-1].get("summary", "") if results else ""
            return Say(step.format(summary=summary))
        return Call(*step)

    return decide


# -- running and grading ----------------------------------------------------------------


@dataclass
class CaseResult:
    """How one case went."""

    id: str
    roadmap_ref: str
    utterance: str
    verdict: str  # pass | fail | gap
    problems: list[str]
    reply: str
    tool_calls: list[dict]
    dispatched: list[list[str]]
    secs: float
    known_gap: str = ""
    error: str = ""


def grade(case: ButlerCase, reply: str, calls: list[dict], dispatched, health) -> list[str]:
    """Every way this turn missed its expectation; empty means it passed."""
    expect = case.expect
    names = [call["name"] for call in calls]
    problems: list[str] = []
    if expect.no_tools and names:
        problems.append(f"expected no tools, called {names}")
    if expect.tools_any and not set(names) & set(expect.tools_any):
        problems.append(f"expected one of {list(expect.tools_any)}, called {names or 'nothing'}")
    forbidden = [name for name in names if name in expect.tools_forbid]
    if forbidden:
        problems.append(f"called forbidden {forbidden}")
    if expect.dispatch == "none" and dispatched:
        problems.append(f"expected no dispatch, sent {[a for a, _ in dispatched]}")
    elif expect.dispatch and expect.dispatch != "none":
        if not dispatched:
            problems.append(f"expected a dispatch to {expect.dispatch}, sent nothing")
        else:
            agent, instructions = dispatched[-1]
            if expect.dispatch == "up" and health.get(agent) != "up":
                problems.append(f"dispatched to {agent}, which is {health.get(agent)}")
            elif expect.dispatch not in {"up"} and agent != expect.dispatch:
                problems.append(f"dispatched to {agent}, expected {expect.dispatch}")
            for pattern in expect.dispatch_matches:
                if not re.search(pattern, instructions):
                    problems.append(f"instructions missing /{pattern}/: {instructions[:120]!r}")
    if expect.start_status:
        statuses = [c["status"] for c in calls if c["name"] in {"start_task", "retry_task"}]
        if expect.start_status not in statuses:
            problems.append(f"expected a {expect.start_status} start, got {statuses or 'none'}")
    if expect.reply_any and not any(re.search(p, reply) for p in expect.reply_any):
        problems.append(f"reply matched none of {list(expect.reply_any)}: {reply[:160]!r}")
    for pattern in expect.reply_forbid:
        if re.search(pattern, reply):
            problems.append(f"reply matched forbidden /{pattern}/: {reply[:160]!r}")
    return problems


async def run_case(
    case: ButlerCase,
    *,
    mode: str,
    data_dir: Path,
    live_endpoints: tuple[llm_endpoint.Endpoint, ...] = (),
) -> CaseResult:
    """Play ``case`` once and grade it."""
    world = _World(case, data_dir)
    started = time.perf_counter()
    reply = ""
    error = ""
    async with AsyncExitStack() as stack:
        http = await stack.enter_async_context(aiohttp.ClientSession())
        world.session._http = http  # noqa: SLF001
        if mode == "scripted":
            endpoint = await stack.enter_async_context(ServedModel(FakeModel(_oracle_brain(case))))
            endpoints = (endpoint,)
        else:
            endpoints = live_endpoints
        world.session._butler._endpoints = lambda: endpoints  # noqa: SLF001
        try:
            await world.seed_health()
            pieces = [str(p) async for p in world.session.complete_stream(case.utterance)]
            reply = "".join(pieces).strip()
        except Exception as exc:  # noqa: BLE001 - a crash is a result for this case
            error = f"{type(exc).__name__}: {exc}"
        finally:
            world.session._http = None  # noqa: SLF001
            for task in list(world.session._tasks):  # noqa: SLF001
                task.cancel()
    problems = (
        [f"crashed: {error}"]
        if error
        else grade(case, reply, world.tool_calls, world.dispatched, world.health)
    )
    verdict = "pass" if not problems else ("gap" if case.known_gap else "fail")
    return CaseResult(
        id=case.id,
        roadmap_ref=case.roadmap_ref,
        utterance=case.utterance,
        verdict=verdict,
        problems=problems,
        reply=reply,
        tool_calls=world.tool_calls,
        dispatched=[list(d) for d in world.dispatched],
        secs=round(time.perf_counter() - started, 2),
        known_gap=case.known_gap,
        error=error,
    )


async def run_all(
    cases: tuple[ButlerCase, ...] = CASES, *, mode: str = "scripted", repeats: int = 1
) -> list[CaseResult]:
    """Run every case ``repeats`` times against the chosen model."""
    if mode not in {"scripted", "live"}:
        raise ValueError(f"unknown mode {mode!r}")
    live = llm_endpoint.chain(llm_endpoint.BRAIN) if mode == "live" else ()
    if mode == "live" and not any(endpoint.cloud for endpoint in live):
        raise RuntimeError(
            "No cloud Butler endpoint is configured. Assign the Butler role in Models & "
            "providers, or set CLOUD_LLM_BASE_URL/CLOUD_LLM_MODEL and a key."
        )
    results = []
    with tempfile.TemporaryDirectory(prefix="rap-butler-eval-") as tmp, _eval_config():
        for _ in range(repeats):
            for case in cases:
                results.append(
                    await run_case(case, mode=mode, data_dir=Path(tmp), live_endpoints=live)
                )
    return results


# Settings the eval overrides so it never reads or writes real app state, and
# restores afterwards so it can run inside another process (pytest, the GUI).
_OVERRIDES = {
    "MEMORY_ENABLED": False,
    "BUTLER_TOOLS_ENABLED": True,
    "LIFECYCLE_WS_ENABLED": False,
    "AGENT_HISTORY_FILE": "",
    "BUTLER_TASKS_FILE": "",
    "BUTLER_SKILLS_DIR": "",
    "AGENT_REGISTRY_FILE": None,
    "CONVERSATION_STORE_PATH": None,
    "MEMORY_FILE": None,
}


@contextmanager
def _eval_config():
    saved = {name: getattr(cfg, name) for name in _OVERRIDES}
    try:
        for name, value in _OVERRIDES.items():
            if value is not None:
                setattr(cfg, name, value)
        yield
    finally:
        for name, value in saved.items():
            setattr(cfg, name, value)


def summarize(results: list[CaseResult]) -> dict:
    """Pass rate over the cases that count (known gaps excluded)."""
    counted = [r for r in results if not r.known_gap]
    passed = sum(r.verdict == "pass" for r in counted)
    return {
        "cases": len(results),
        "counted": len(counted),
        "passed": passed,
        "failed": sum(r.verdict == "fail" for r in results),
        "gaps": sum(r.verdict == "gap" for r in results),
        "score": round(100.0 * passed / len(counted), 1) if counted else 0.0,
    }


def write_report(results: list[CaseResult], mode: str, out_dir: Path) -> tuple[Path, Path]:
    """Write ``butler-<mode>-<ts>.jsonl`` and a Markdown report; return both paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    jsonl = out_dir / f"butler-{mode}-{stamp}.jsonl"
    with jsonl.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
    summary = summarize(results)
    lines = [
        f"# Butler replay eval ({mode})",
        "",
        f"Score **{summary['score']}%** -- {summary['passed']}/{summary['counted']} counted cases "
        f"passed, {summary['failed']} failed, {summary['gaps']} known gap(s).",
        "",
        "| Case | Roadmap | Verdict | Tools | Dispatched | Problems |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        tools = ", ".join(c["name"] for c in r.tool_calls) or "-"
        sent = ", ".join(agent for agent, _ in r.dispatched) or "-"
        problems = "; ".join(r.problems).replace("|", "\\|") or "-"
        lines.append(
            f"| `{r.id}` | {r.roadmap_ref} | {r.verdict} | {tools} | {sent} | {problems} |"
        )
    lines += ["", "## Replies", ""]
    for r in results:
        lines += [f"- **{r.id}** ({r.utterance}) -> {r.reply or '(no reply)'}"]
    gaps = [r for r in results if r.known_gap]
    if gaps:
        lines += ["", "## Known gaps", ""]
        lines += [f"- **{r.id}**: {r.known_gap}" for r in gaps]
    markdown = jsonl.with_suffix(".md")
    markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return jsonl, markdown


def validate_cases(cases: tuple[ButlerCase, ...] = CASES) -> list[str]:
    """Structural problems in the corpus itself."""
    problems = []
    ids = [case.id for case in cases]
    problems += [f"duplicate id {i}" for i in {i for i in ids if ids.count(i) > 1}]
    for case in cases:
        if not case.oracle:
            problems.append(f"{case.id}: no oracle")
        elif not isinstance(case.oracle[-1], str):
            problems.append(f"{case.id}: the oracle must end with a reply")
        for seed in case.tasks:
            if seed.agent not in AGENTS:
                problems.append(f"{case.id}: unknown seeded agent {seed.agent}")
    return problems

"""The tools the Butler model acts through.

Every result carries a ``summary`` -- one plain sentence that is true on its
own -- because that is what the model is told to speak from. Safety lives in
here rather than in the prompt: dispatch always passes admission (caps and
duplicates) and the destructive-task confirmation hold, whatever the model
asks for.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from loguru import logger

from remote_agent_protocol import agent_bridge
from remote_agent_protocol import agent_status_reporting as agent_status
from remote_agent_protocol.butler.ledger import ButlerTask, TaskLedger

READ_ONLY_TOOLS = frozenset(
    {"list_agents", "check_agents", "task_status", "list_tasks", "get_result"}
)
_RESULT_PREVIEW_CHARS = 400
_RESULT_FULL_CHARS = 4000


@dataclass(frozen=True)
class DispatchOutcome:
    """What a dispatch attempt produced: a job on an agent, or why not."""

    job_id: str | None
    agent: str
    detail: str = ""


Dispatch = Callable[[str, str], Awaitable[DispatchOutcome]]
HoldConfirmation = Callable[[str, str], str]
DropConfirmation = Callable[[str], bool]
Admit = Callable[[str, str], "str | None"]
NeedsConfirmation = Callable[[str, str], bool]


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


_TASK_REF = {
    "type": "string",
    "description": "A task id like 't2', or words from its subject. Omit for the latest task.",
}
_AGENT = {"type": "string", "description": "Agent name exactly as list_agents reports it."}

TOOL_SCHEMAS: list[dict] = [
    _fn(
        "list_agents",
        "Configured agents with their last recorded state (up, down, working). No probing.",
        {},
        [],
    ),
    _fn(
        "check_agents",
        "Actively check whether agents respond right now. Use for any 'check the agents', "
        "'are they working', or 'which ones are available' request.",
        {"agents": {"type": "array", "items": _AGENT, "description": "Omit to check all."}},
        [],
    ),
    _fn(
        "start_task",
        "Send new work to one agent. Only for genuinely new work, never for status "
        "questions. Instructions must be complete and self-contained: the agent sees none "
        "of this conversation, so resolve references like 'it' or 'that email' first.",
        {
            "agent": _AGENT,
            "instructions": {"type": "string", "description": "Full task for the agent."},
            "subject": {
                "type": "string",
                "description": "Two to six words naming the task, e.g. 'email search for school news'.",
            },
        },
        ["agent", "instructions", "subject"],
    ),
    _fn(
        "retry_task",
        "Run an existing task again, on the named agent. Use for 'have Codex try', "
        "'try another agent', or after a failure the user still wants fixed.",
        {"task": _TASK_REF, "agent": _AGENT},
        ["agent"],
    ),
    _fn(
        "task_status",
        "Current state of one task: agent, status, last action, and a result preview.",
        {"task": _TASK_REF},
        [],
    ),
    _fn(
        "list_tasks",
        "Tasks by subject. scope 'active' for what is running now, 'recent' for everything "
        "recent including finished ones.",
        {"scope": {"type": "string", "enum": ["active", "recent"]}},
        [],
    ),
    _fn("cancel_task", "Stop a task that is running or waiting.", {"task": _TASK_REF}, []),
    _fn(
        "get_result",
        "The full result (or failure detail) of a finished task.",
        {"task": _TASK_REF},
        [],
    ),
    _fn(
        "set_agent_model",
        "Switch which model/provider an agent uses for its next tasks.",
        {"agent": _AGENT, "provider": {"type": "string", "description": "e.g. 'openrouter'."}},
        ["agent", "provider"],
    ),
]


class ButlerToolbox:
    """Executes Butler tool calls against RAP's control plane, bridge, and hub."""

    def __init__(
        self,
        *,
        bridge: agent_bridge.AgentBridge,
        control_plane,
        ledger: TaskLedger,
        dispatch: Dispatch,
        admit: Admit,
        needs_confirmation: NeedsConfirmation,
        hold_confirmation: HoldConfirmation,
        drop_confirmation: DropConfirmation,
        aliases: Mapping[str, str],
        fresh_for_secs: float = 0.0,
    ):
        """Initialize the toolbox.

        Args:
            bridge: Owns every job and model override.
            control_plane: Agent status evidence and self-checks.
            ledger: This session's Butler tasks.
            dispatch: Starts admitted work on one agent through the conversation hub.
            admit: Concurrency/duplicate admission; returns a refusal reason or None.
            needs_confirmation: Whether (agent, instructions) must be approved first.
            hold_confirmation: Registers a held (agent, instructions); returns its token.
            drop_confirmation: Discards a held confirmation by token.
            aliases: Spoken agent names -> backend names.
            fresh_for_secs: How recent a confirmed response may be reused by check_agents.
        """
        self._bridge = bridge
        self._control_plane = control_plane
        self._ledger = ledger
        self._dispatch = dispatch
        self._admit = admit
        self._needs_confirmation = needs_confirmation
        self._hold_confirmation = hold_confirmation
        self._drop_confirmation = drop_confirmation
        self._aliases = {k.lower(): v for k, v in aliases.items()}
        self._fresh_for_secs = fresh_for_secs

    @property
    def ledger(self) -> TaskLedger:
        """This session's Butler tasks."""
        return self._ledger

    async def call(self, name: str, arguments: str | Mapping[str, Any] | None) -> dict:
        """Run one tool call; never raises -- failures come back as results."""
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None or name not in {s["function"]["name"] for s in TOOL_SCHEMAS}:
            return _error(f"There is no tool named {name}.")
        try:
            args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
        except (TypeError, ValueError):
            return _error(f"The arguments for {name} were not valid JSON.")
        if not isinstance(args, dict):
            return _error(f"The arguments for {name} must be an object.")
        try:
            return await handler(**args)
        except TypeError as exc:
            return _error(f"Bad arguments for {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - a tool failure is a result, not a crash
            logger.exception(f"Butler tool {name} failed")
            return _error(f"{name} failed: {exc}")

    # -- agent resolution ------------------------------------------------------

    def _agent_name(self, raw: str | None) -> str | None:
        if not raw:
            return None
        names = self._bridge.backend_names()
        candidate = raw.strip()
        if candidate in names:
            return candidate
        lowered = candidate.lower()
        for form in (lowered, lowered.replace(" ", "-"), lowered.replace("-", " ")):
            if form in names:
                return form
            if form in self._aliases and self._aliases[form] in names:
                return self._aliases[form]
        return None

    def _unknown_agent(self, raw: str | None) -> dict:
        names = ", ".join(self._bridge.backend_names())
        return _error(f"There is no agent called {raw!r}. Configured agents: {names}.")

    # -- tools -----------------------------------------------------------------

    async def _tool_list_agents(self) -> dict:
        results = await self._control_plane.list_agents(refresh=False)
        rows = [agent_status.control_summary(name, snap) for name, snap in results.items()]
        known = set(results)
        rows.extend(
            f"{name}: configured, no recorded state"
            for name in self._bridge.backend_names()
            if name not in known
        )
        return {"agents": rows, "summary": "; ".join(rows) or "No agents are configured."}

    async def _tool_check_agents(self, agents: list[str] | None = None) -> dict:
        targets: list[str | None]
        if agents:
            resolved = [self._agent_name(a) for a in agents]
            missing = [a for a, r in zip(agents, resolved, strict=True) if r is None]
            if missing:
                return self._unknown_agent(missing[0])
            targets = list(dict.fromkeys(resolved))
        else:
            targets = [None]
        rows: list[str] = []
        for target in targets:
            found, missing_note = await agent_status.collect_rollcall_rows(
                self._control_plane, target, fresh_for_secs=self._fresh_for_secs
            )
            rows.extend(found or [missing_note or "nothing to check"])
        return {"agents": rows, "summary": "; ".join(rows)}

    async def _tool_start_task(self, agent: str, instructions: str, subject: str = "") -> dict:
        name = self._agent_name(agent)
        if name is None:
            return self._unknown_agent(agent)
        if not instructions.strip():
            return _error("The task has no instructions; ask the user what they want done.")
        task = self._ledger.create(subject, instructions)
        return await self._launch(task, name)

    async def _tool_retry_task(self, agent: str, task: str | None = None) -> dict:
        name = self._agent_name(agent)
        if name is None:
            return self._unknown_agent(agent)
        found = self._ledger.resolve(task)
        if found is None:
            return _error("There is no earlier task to retry; use start_task for new work.")
        return await self._launch(found, name)

    async def _launch(self, task: ButlerTask, agent: str) -> dict:
        refusal = self._admit(agent, task.instructions)
        if refusal is not None:
            return {
                "status": "refused",
                "task": task.task_id,
                "agent": agent,
                "reason": refusal,
                "summary": f"Nothing was sent to {agent}: {refusal}.",
            }
        if self._needs_confirmation(agent, task.instructions):
            token = self._hold_confirmation(agent, task.instructions)
            self._ledger.hold(task.task_id, token, agent)
            return {
                "status": "needs_confirmation",
                "task": task.task_id,
                "agent": agent,
                "summary": (
                    f"Nothing has been sent yet: '{task.subject}' changes things on the "
                    f"system, so {agent} will start it only after the user confirms."
                ),
            }
        outcome = await self._dispatch(agent, task.instructions)
        if outcome.job_id is None:
            detail = outcome.detail or "the conversation hub did not dispatch it"
            return {
                "status": "not_started",
                "task": task.task_id,
                "agent": agent,
                "reason": detail,
                "summary": f"{agent} was not started: {detail}.",
            }
        self._ledger.attach(task.task_id, outcome.job_id, outcome.agent)
        return {
            "status": "started",
            "task": task.task_id,
            "agent": outcome.agent,
            "attempt": len(task.attempts),
            "summary": (
                f"{outcome.agent} has started '{task.subject}' (task {task.task_id}). "
                "No result yet."
            ),
        }

    async def _tool_task_status(self, task: str | None = None) -> dict:
        found = self._ledger.resolve(task)
        if found is None:
            job = self._loose_job(task)
            if job is None:
                return _error(
                    "There are no tasks yet." if not task else f"No task matches {task!r}."
                )
            return _job_status(job, job.job_id, _subject_from_job(job))
        return self._task_state(found)

    def _task_state(self, task: ButlerTask) -> dict:
        if task.held_token:
            return {
                "task": task.task_id,
                "subject": task.subject,
                "status": "awaiting_confirmation",
                "agent": task.held_agent,
                "summary": f"'{task.subject}' is waiting for the user to confirm it.",
            }
        attempt = task.latest
        if attempt is None:
            return {
                "task": task.task_id,
                "subject": task.subject,
                "status": "not_started",
                "summary": f"'{task.subject}' was never started.",
            }
        job = self._bridge.get(attempt.job_id)
        if job is None:
            return {
                "task": task.task_id,
                "subject": task.subject,
                "status": "unknown",
                "agent": attempt.agent,
                "summary": f"RAP no longer holds the record of {attempt.agent}'s attempt.",
            }
        state = _job_status(job, task.task_id, task.subject)
        if len(task.attempts) > 1:
            earlier = ", ".join(a.agent for a in task.attempts[:-1])
            state["earlier_attempts"] = earlier
            state["summary"] += f" Earlier attempts: {earlier}."
        return state

    async def _tool_list_tasks(self, scope: str = "active") -> dict:
        rows: list[dict] = []
        seen_jobs: set[str] = set()
        for task in self._ledger.newest_first():
            state = self._task_state(task)
            seen_jobs.update(a.job_id for a in task.attempts)
            if scope == "active" and state["status"] not in _OPEN_STATES:
                continue
            rows.append(_row(state))
        for job in self._bridge.recent_jobs():
            if job.job_id in seen_jobs:
                continue
            if scope == "active" and job.status not in _OPEN_STATES:
                continue
            rows.append(_row(_job_status(job, job.job_id, _subject_from_job(job))))
        if not rows:
            summary = "Nothing is running." if scope == "active" else "There are no recent tasks."
        else:
            summary = "; ".join(
                f"{r['task']} '{r['subject']}' on {r.get('agent') or 'no agent'}: {r['status']}"
                for r in rows
            )
        return {"tasks": rows, "summary": summary}

    async def _tool_cancel_task(self, task: str | None = None) -> dict:
        found = self._ledger.resolve(task)
        if found is not None and found.held_token:
            self._drop_confirmation(found.held_token)
            self._ledger.release(found.held_token)
            return {
                "status": "cancelled",
                "task": found.task_id,
                "summary": f"'{found.subject}' was waiting for confirmation and is now dropped.",
            }
        job = None
        if found is not None and found.latest is not None:
            job = self._bridge.get(found.latest.job_id)
        elif found is None:
            job = self._loose_job(task)
        if job is None or job.status not in _OPEN_STATES:
            return {"status": "not_running", "summary": "There is no running task matching that."}
        await self._bridge.cancel(job.job_id)
        subject = found.subject if found is not None else _subject_from_job(job)
        return {
            "status": "cancelled",
            "task": found.task_id if found is not None else job.job_id,
            "summary": f"Cancelled '{subject}' on {job.agent}.",
        }

    async def _tool_get_result(self, task: str | None = None) -> dict:
        found = self._ledger.resolve(task)
        job = (
            self._bridge.get(found.latest.job_id)
            if found is not None and found.latest is not None
            else (self._loose_job(task) if found is None else None)
        )
        if job is None:
            return _error("That task has no result to read.")
        if job.status in _OPEN_STATES:
            return {"status": job.status, "summary": f"{job.agent} is still working on it."}
        body = job.result or job.failure_detail or job.summary
        return {
            "status": job.status,
            "agent": job.agent,
            "result": body[:_RESULT_FULL_CHARS],
            "summary": f"{job.agent} {'finished' if job.status == 'done' else job.status}: "
            f"{(job.summary or body)[:_RESULT_PREVIEW_CHARS]}",
        }

    async def _tool_set_agent_model(self, agent: str, provider: str) -> dict:
        name = self._agent_name(agent)
        if name is None:
            return self._unknown_agent(agent)
        key = provider.strip().lower().replace(" ", "")
        label = self._bridge.set_model_override(name, key)
        if label is None:
            return {
                "status": "unsupported",
                "summary": f"{name} has no {provider} model configured; nothing changed.",
            }
        return {"status": "switched", "summary": f"{name} will use {label} from its next task."}

    def job_event(self, job: agent_bridge.AgentJob) -> dict:
        """What a finished job means for its Butler task, shaped like a task_status result."""
        task = self._ledger.by_job(job.job_id)
        if task is not None:
            state = self._task_state(task)
        else:
            state = _job_status(job, job.job_id, _subject_from_job(job))
        state["event"] = "agent_finished"
        if job.status == agent_bridge.STATUS_DONE and job.result:
            state["result"] = job.result[:_RESULT_FULL_CHARS]
        if job.failure_detail and job.status != agent_bridge.STATUS_DONE:
            state["failure_detail"] = job.failure_detail[:600]
        return state

    def _loose_job(self, reference: str | None) -> agent_bridge.AgentJob | None:
        """A bridge job that no Butler task owns (started from the GUI or the router)."""
        jobs = self._bridge.recent_jobs()
        if not reference:
            return jobs[0] if jobs else None
        return self._bridge.get(reference.strip())


_OPEN_STATES = {
    agent_bridge.STATUS_RUNNING,
    agent_bridge.STATUS_WAITING,
    agent_bridge.STATUS_BLOCKED,
    "awaiting_confirmation",
}


def _job_status(job: agent_bridge.AgentJob, task_id: str, subject: str) -> dict:
    elapsed = job.secs if job.secs is not None else round(time.monotonic() - job._t0, 1)  # noqa: SLF001
    state: dict[str, Any] = {
        "task": task_id,
        "subject": subject,
        "agent": job.agent,
        "status": job.status,
        "elapsed_secs": elapsed,
    }
    if job.status in _OPEN_STATES:
        action = job.action or "working"
        state["action"] = action
        state["summary"] = f"{job.agent} is {job.status} on '{subject}' ({action}, {elapsed:.0f}s)."
    elif job.status == agent_bridge.STATUS_DONE:
        preview = (job.result or job.summary)[:_RESULT_PREVIEW_CHARS]
        state["result_preview"] = preview
        state["summary"] = f"{job.agent} finished '{subject}': {preview}"
    else:
        reason = job.failure_detail or job.summary or job.status
        state["failure_kind"] = job.failure_kind
        state["summary"] = f"{job.agent}'s attempt at '{subject}' {job.status}: {reason[:300]}"
    if job.model_failovers:
        state["model_failovers"] = list(job.model_failovers)
    return state


def _row(state: dict) -> dict:
    return {k: state[k] for k in ("task", "subject", "agent", "status") if k in state}


def _subject_from_job(job: agent_bridge.AgentJob) -> str:
    words = job.task.split()
    return " ".join(words[:8]) + ("..." if len(words) > 8 else "")


def _error(message: str) -> dict:
    return {"error": message, "summary": message}

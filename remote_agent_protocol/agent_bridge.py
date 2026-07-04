"""Agent bridge -- delegate long-running work to external agents, fully async.

Design contract (the whole point):
  * Jess's voice loop NEVER blocks on an agent task.
  * Jobs run as subprocesses on the session's asyncio loop.
  * Every lifecycle change is emitted as an event dict immediately, so the GUI
    updates live and Jess can *speak* updates the moment they land.

Backends are plain command templates (config.AGENT_BACKENDS), e.g.
    "hermes": ["hermes", "{task}"]
Placeholders:
    {task}   -> the task text
    {python} -> sys.executable (used by the built-in mock backend)

Event shape (routed through VoiceSession._emit, same bus as transcripts):
    {"type": "agent_job", "event": "started"|"output"|"finished",
     "job_id": ..., "agent": ..., "task": ..., "status": ...,
     "line": ... (output only), "summary": ... (finished only)}
"""

import asyncio
import itertools
import json
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from loguru import logger

STATUS_RUNNING = "running"
STATUS_WAITING = "waiting"
STATUS_BLOCKED = "blocked"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

STATE_STARTED = "started"
STATE_IN_PROGRESS = "in_progress"
STATE_TOOL_RUNNING = "tool_running"
STATE_STEP_COMPLETED = "step_completed"
STATE_WAITING = "waiting"
STATE_BLOCKED = "blocked"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"

_ACTIVE_STATUSES = {STATUS_RUNNING, STATUS_WAITING, STATUS_BLOCKED}
_TERMINAL_STATES = {STATE_COMPLETED, STATE_FAILED}
_STATUS_STATES = {
    STATE_IN_PROGRESS,
    STATE_TOOL_RUNNING,
    STATE_STEP_COMPLETED,
    STATE_WAITING,
    STATE_BLOCKED,
    *_TERMINAL_STATES,
}
_STATUS_MARKER = "@@JESS_STATUS"
_STATUS_TEXT_FIELDS = ("action", "tool", "last_completed_step", "summary")
_TOOL_RE = re.compile(r"\bCalling\s+([\w-]+)", re.IGNORECASE)

_STATUS_PROTOCOL = """

Report meaningful task status on standalone lines using this exact prefix and compact JSON:
@@JESS_STATUS {"state":"in_progress","action":"short current action"}
Allowed states: in_progress, tool_running, step_completed, waiting, blocked, completed, failed.
For tools include "tool"; for numbered steps include "step" and "step_total"; for milestones
include "last_completed_step"; finish with exactly one completed or failed line containing
"summary", for example @@JESS_STATUS {"state":"completed","summary":"short result"}.
Emit only major milestones, not token-by-token updates. Continue the requested task
normally; these status lines are consumed by the host application.
""".strip()

_MAX_KEPT_LINES = 500  # keep logs bounded; tail is what matters

# CLI agents love ANSI colours; Jess should not attempt to pronounce \x1b[0m.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|[\r\x07]")
_QUESTION_MARKER_RE = re.compile(
    r"^\s*(?:follow[-_ ]?up[-_ ]?question|clarifying[-_ ]?question|question)\s*[:\-]\s*(.+)$",
    re.IGNORECASE,
)


def clean_line(raw: str) -> str:
    """Strip ANSI escapes / control chars from agent output."""
    return _ANSI_RE.sub("", raw).rstrip()


@dataclass
class AgentJob:
    """One delegated task and everything we know about it."""

    job_id: str
    agent: str
    task: str
    machine: str = "local"
    status: str = STATUS_RUNNING
    lines: list[str] = field(default_factory=list)
    returncode: int | None = None
    secs: float | None = None  # wall time, set when the job ends
    state: str = STATE_STARTED
    action: str = "Starting"
    tool: str = ""
    step: int | None = None
    step_total: int | None = None
    last_completed_step: str = ""
    summary: str = ""
    announce_start: bool = False
    _t0: float = field(default=0.0, repr=False)
    _last_status: float = field(default=0.0, repr=False)


def build_command(template: list[str], task: str) -> list[str]:
    """Substitute placeholders in a backend command template."""
    return [part.replace("{task}", task).replace("{python}", sys.executable) for part in template]


def with_status_protocol(task: str) -> str:
    """Append the small stdout status contract understood by AgentBridge."""
    return f"{task}\n\n{_STATUS_PROTOCOL}"


def parse_status_line(line: str) -> dict | None:
    """Parse and validate one structured agent status marker."""
    marker = line.find(_STATUS_MARKER)
    if marker < 0:
        return None
    payload = line[marker + len(_STATUS_MARKER) :].lstrip(" :")
    try:
        value, _ = json.JSONDecoder().raw_decode(payload)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(value, dict) or value.get("state") not in _STATUS_STATES:
        return None

    status = {"state": value["state"]}
    for field_name in _STATUS_TEXT_FIELDS:
        field_value = value.get(field_name)
        if isinstance(field_value, str) and field_value.strip():
            status[field_name] = field_value.strip()[:300]
    for field_name in ("step", "step_total"):
        field_value = value.get(field_name)
        if isinstance(field_value, int) and field_value > 0:
            status[field_name] = field_value
    return status


def infer_status(line: str) -> dict | None:
    """Extract useful progress from common CLI output when no marker exists."""
    tool_match = _TOOL_RE.search(line)
    if tool_match:
        tool = tool_match.group(1)
        return {"state": STATE_TOOL_RUNNING, "action": f"Running {tool}", "tool": tool}
    if "SHELL COMMAND" in line.upper():
        return {"state": STATE_TOOL_RUNNING, "action": "Running shell command", "tool": "shell"}
    lower = line.lower().lstrip()
    if lower.startswith(("blocked:", "[blocked]", "status: blocked")):
        return {"state": STATE_BLOCKED, "action": line.strip()[:300]}
    if lower.startswith(("waiting for", "waiting:", "question:", "follow_up_question:")):
        return {"state": STATE_WAITING, "action": line.strip()[:300]}
    if "THINKING" in line.upper():
        action = re.sub(r".*?THINKING\s*", "", line, flags=re.IGNORECASE).strip(" *⚡")
        return {"state": STATE_IN_PROGRESS, "action": action[:300] or "Planning next step"}
    return None


def summarize_output(lines: list[str], max_lines: int = 3, max_chars: int = 300) -> str:
    """Compact tail-of-log summary -- what Jess actually says out loud."""
    tail = [line.strip() for line in lines if line.strip()][-max_lines:]
    text = " ".join(tail)
    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."
    return text


def _split_questions(text: str) -> list[str]:
    """Split one line containing one or more questions into speakable chunks."""
    parts = re.findall(r"[^?]+\?", text)
    return [part.strip() for part in parts if part.strip()]


def follow_up_questions(job: AgentJob) -> list[str]:
    """Return all agent questions for the user, preserving output order.

    Agents are encouraged to print ``QUESTION: ...`` or
    ``FOLLOW_UP_QUESTION: ...``. As a pragmatic fallback, lines ending in ``?``
    are treated as questions too. Failed jobs are never treated as questions.
    """
    if job.status != STATUS_DONE:
        return []
    questions: list[str] = []
    for line in [line.strip() for line in job.lines if line.strip()]:
        match = _QUESTION_MARKER_RE.match(line)
        if match:
            marked = match.group(1).strip()
            questions.extend(_split_questions(marked) or [marked])
        elif line.endswith("?"):
            questions.extend(_split_questions(line) or [line])
    return questions


def follow_up_question(job: AgentJob) -> str | None:
    """Compatibility wrapper: first follow-up question, if any."""
    questions = follow_up_questions(job)
    return questions[0] if questions else None


def announcement(job: AgentJob) -> str:
    """One-sentence-ish update for Jess to relay to the user."""
    questions = follow_up_questions(job)
    if questions:
        joined = " ".join(questions)
        return f"Agent '{job.agent}' needs your input: {joined}"
    summary = job.summary or summarize_output(job.lines)
    if job.status == STATUS_DONE:
        return f"Background task on agent '{job.agent}' finished: {job.task}. Result: {summary}"
    if job.status == STATUS_CANCELLED:
        return f"Background task on agent '{job.agent}' was cancelled: {job.task}."
    return f"Background task on agent '{job.agent}' FAILED: {job.task}. Last output: {summary}"


class AgentBridge:
    """Owns delegated jobs. Lives on the VoiceSession asyncio loop."""

    def __init__(
        self,
        backends: dict[str, list[str]],
        on_event: Callable[[dict], None],
        on_finished: Callable[[AgentJob], "asyncio.Future | None"] | None = None,
        machines: dict[str, str] | None = None,
        *,
        timeout_secs: float = 0.0,
        kill_grace_secs: float = 3.0,
        progress_interval_secs: float = 30.0,
        completion_grace_secs: float = 2.0,
        on_persist: Callable[[AgentJob], "asyncio.Future | None"] | None = None,
    ):
        """Initialize the bridge.

        Args:
            backends: Agent names mapped to command templates.
            on_event: Callback for lifecycle, output, and progress events.
            on_finished: Optional async callback for terminal jobs.
            machines: Optional display labels for backend machines.
            timeout_secs: Hard job timeout; zero disables it.
            kill_grace_secs: Grace before escalating process termination.
            progress_interval_secs: Interval for silent-job heartbeats.
            completion_grace_secs: Grace after a structured terminal marker.
            on_persist: Optional async callback that persists terminal jobs.
        """
        self._backends = backends
        self._on_event = on_event
        self._on_finished = on_finished  # async callable, awaited on completion
        self._on_persist = on_persist  # async callable, awaited on completion
        self._machines = machines or {}
        self._timeout_secs = timeout_secs
        self._kill_grace_secs = kill_grace_secs
        self._progress_interval_secs = progress_interval_secs
        self._completion_grace_secs = completion_grace_secs
        self._jobs: dict[str, AgentJob] = {}
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        # Keep strong refs to the streaming tasks: asyncio only holds *weak*
        # references, so an untracked task can be garbage-collected mid-job --
        # silently dropping the agent output and the completion announcement.
        self._tasks: set[asyncio.Task] = set()
        self._counter = itertools.count(1)

    # -- queries ------------------------------------------------------------

    def backend_names(self) -> list[str]:
        """Return configured backend names."""
        return sorted(self._backends)

    def machine_for(self, backend: str) -> str:
        """Return the display machine for a backend."""
        return self._machines.get(backend, "local")

    def get(self, job_id: str) -> AgentJob | None:
        """Return a known job by id."""
        return self._jobs.get(job_id)

    # -- lifecycle ------------------------------------------------------------

    async def start(
        self, agent: str, task: str, cwd: str | None = None, *, announce_start: bool = False
    ) -> str:
        """Spawn a job and return its id immediately; output streams via events."""
        job = AgentJob(
            job_id=f"job-{next(self._counter)}",
            agent=agent,
            task=task,
            machine=self.machine_for(agent),
            announce_start=announce_start,
        )
        job._t0 = time.monotonic()
        job._last_status = job._t0
        self._jobs[job.job_id] = job

        template = self._backends.get(agent)
        if template is None:
            return await self._fail_fast(job, f"unknown agent backend '{agent}'")

        command_task = task if agent == "mock" else with_status_protocol(task)
        command = build_command(template, command_task)
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except (OSError, FileNotFoundError) as e:
            return await self._fail_fast(job, f"could not launch {command[0]}: {e}")

        self._procs[job.job_id] = proc
        self._emit_job(job, "started")
        logger.info(f"Agent job {job.job_id} [{job.agent}] started: {job.task}")
        task = asyncio.create_task(self._stream(job, proc), name=f"agent-{job.job_id}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job.job_id

    async def cancel(self, job_id: str) -> None:
        """Cancel an active job if it exists."""
        proc = self._procs.get(job_id)
        job = self._jobs.get(job_id)
        if proc is None or job is None or job.status not in _ACTIVE_STATUSES:
            return
        job.status = STATUS_CANCELLED
        await self._terminate(proc)

    async def shutdown(self) -> None:
        """Stop every live job and reap its subprocess before the loop closes.

        Must run on the bridge's event loop while it is still open. A subprocess
        transport that is never closed before the loop goes away is cleaned up
        by ``__del__`` at interpreter exit, which crashes noisily on the Windows
        proactor loop ("I/O operation on closed pipe") and leaks the child
        process on every platform.
        """
        for job_id in list(self._procs):
            await self.cancel(job_id)
        tasks = [task for task in self._tasks if not task.done()]
        if not tasks:
            return
        # The streaming tasks observe EOF + exit code and close the transports.
        _, pending = await asyncio.wait(tasks, timeout=self._kill_grace_secs + 5.0)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    # -- internals ------------------------------------------------------------

    async def _fail_fast(self, job: AgentJob, reason: str) -> str:
        job.status = STATUS_FAILED
        job.state = STATE_FAILED
        job.secs = 0.0
        job.lines.append(reason)
        job.summary = reason
        logger.warning(f"Agent job {job.job_id} failed to start: {reason}")
        self._emit_job(job, "finished", summary=reason)
        await self._notify_finished(job)
        return job.job_id

    async def _consume(self, job: AgentJob, proc: asyncio.subprocess.Process) -> None:
        """Drain stdout into the job's log, then wait for the exit code."""
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = clean_line(raw.decode("utf-8", errors="replace"))
            if not line:
                continue
            status = parse_status_line(line)
            if status is not None:
                self._apply_status(job, status)
                if status["state"] in _TERMINAL_STATES:
                    await asyncio.sleep(self._completion_grace_secs)
                    await self._terminate(proc)
                    job.returncode = await proc.wait()
                    return
                continue
            job.lines.append(line)
            del job.lines[:-_MAX_KEPT_LINES]
            self._emit_job(job, "output", line=line)
            inferred = infer_status(line)
            if inferred is not None:
                self._apply_status(job, inferred)
        job.returncode = await proc.wait()

    async def _stream(self, job: AgentJob, proc: asyncio.subprocess.Process) -> None:
        timed_out = False
        heartbeat = asyncio.create_task(
            self._heartbeat(job, proc), name=f"agent-heartbeat-{job.job_id}"
        )
        try:
            if self._timeout_secs > 0:
                await asyncio.wait_for(self._consume(job, proc), self._timeout_secs)
            else:
                await self._consume(job, proc)
        except TimeoutError:
            timed_out = True
            await self._terminate(proc)
            job.lines.append(f"[stopped: exceeded {self._timeout_secs:.0f}s timeout]")
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

        self._procs.pop(job.job_id, None)
        if job.status in {STATUS_CANCELLED, STATUS_DONE, STATUS_FAILED}:
            pass  # cancel() already claimed the status
        elif timed_out:
            job.status = STATUS_FAILED
            job.state = STATE_FAILED
        else:
            job.status = STATUS_DONE if job.returncode == 0 else STATUS_FAILED
            job.state = STATE_COMPLETED if job.status == STATUS_DONE else STATE_FAILED
        job.secs = round(time.monotonic() - job._t0, 1)
        summary = job.summary or summarize_output(job.lines)
        job.summary = summary
        self._emit_job(job, "finished", summary=summary, secs=job.secs)
        logger.info(f"Agent job {job.job_id} [{job.agent}] -> {job.status}")

        await self._notify_finished(job)

    async def _notify_finished(self, job: AgentJob) -> None:
        """Run best-effort persistence and voice callbacks for terminal jobs."""
        if self._on_persist is not None:
            try:
                await self._on_persist(job)
            except Exception as e:  # persistence is best-effort
                logger.warning(f"agent on_persist raised: {e}")
        if self._on_finished is not None:
            try:
                await self._on_finished(job)
            except Exception as e:  # announcing must never crash the bridge
                logger.warning(f"agent on_finished raised: {e}")

    async def _heartbeat(self, job: AgentJob, proc: asyncio.subprocess.Process) -> None:
        """Emit bounded progress while an otherwise-silent process is alive."""
        if self._progress_interval_secs <= 0:
            return
        while proc.returncode is None and job.status in _ACTIVE_STATUSES:
            await asyncio.sleep(self._progress_interval_secs)
            if (
                proc.returncode is None
                and job.status == STATUS_RUNNING
                and time.monotonic() - job._last_status >= self._progress_interval_secs
            ):
                self._apply_status(
                    job,
                    {
                        "state": STATE_IN_PROGRESS,
                        "action": job.action
                        if job.action not in {"", "Starting", "Still working"}
                        else "Still working",
                    },
                    force=True,
                )

    def _apply_status(self, job: AgentJob, status: dict, *, force: bool = False) -> None:
        """Fold normalized status into a job and emit one progress event."""
        state = status["state"]
        before = (job.state, job.action, job.tool, job.step, job.last_completed_step)
        for field_name in (*_STATUS_TEXT_FIELDS, "step", "step_total"):
            if field_name in status:
                setattr(job, field_name, status[field_name])
        if state in _TERMINAL_STATES and "action" not in status and job.summary:
            job.action = job.summary
        job.state = state
        if state == STATE_WAITING:
            job.status = STATUS_WAITING
        elif state == STATE_BLOCKED:
            job.status = STATUS_BLOCKED
        elif state == STATE_COMPLETED:
            job.status = STATUS_DONE
        elif state == STATE_FAILED:
            job.status = STATUS_FAILED
        else:
            job.status = STATUS_RUNNING
        if state == STATE_STEP_COMPLETED and not job.last_completed_step:
            job.last_completed_step = job.action
        job._last_status = time.monotonic()
        after = (job.state, job.action, job.tool, job.step, job.last_completed_step)
        if force or after != before:
            importance = (
                "attention"
                if state in {STATE_WAITING, STATE_BLOCKED}
                else "milestone"
                if state == STATE_STEP_COMPLETED
                else "progress"
            )
            self._emit_job(job, "progress", importance=importance)
            logger.info(f"Agent job {job.job_id} [{job.agent}] {job.state}: {job.action}")

    async def _terminate(self, proc: asyncio.subprocess.Process) -> None:
        """Ask the process to stop, escalating to a hard kill after the grace."""
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), self._kill_grace_secs)
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                return
            # Reap the killed child so its transport is actually closed.
            await proc.wait()

    def _emit_job(self, job: AgentJob, event: str, **extra) -> None:
        payload = {
            "type": "agent_job",
            "event": event,
            "job_id": job.job_id,
            "agent": job.agent,
            "machine": job.machine,
            "task": job.task,
            "status": job.status,
            "state": job.state,
            "action": job.action,
            "tool": job.tool,
            "step": job.step,
            "step_total": job.step_total,
            "last_completed_step": job.last_completed_step,
            "summary": job.summary,
            "elapsed_secs": job.secs
            if job.secs is not None
            else round(time.monotonic() - job._t0, 1),
            "announce_start": job.announce_start,
            **extra,
        }
        try:
            self._on_event(payload)
        except Exception as e:  # a broken GUI callback must never kill a job
            logger.warning(f"agent on_event raised: {e}")

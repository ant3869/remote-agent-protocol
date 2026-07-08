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
from datetime import datetime
from pathlib import Path

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
STATE_CANCELLED = "cancelled"

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
_STATUS_TEXT_FIELDS = ("action", "tool", "last_completed_step", "summary", "result")
_TOOL_RE = re.compile(r"\bCalling\s+([\w-]+)", re.IGNORECASE)
_STATUS_FRAGMENT_MAX_LINES = 12
_STATUS_FRAGMENT_MAX_CHARS = 6000
_SUMMARY_SKIP_RE = re.compile(
    r"^(?:Resume this session with:|hermes\s+--resume\b|Duration:\s*|Messages:\s*)",
    re.IGNORECASE,
)

_STATUS_PROTOCOL = """

Report meaningful task status on standalone lines using this exact prefix and compact JSON:
@@JESS_STATUS {"state":"in_progress","action":"short current action"}
Allowed states: in_progress, tool_running, step_completed, waiting, blocked, completed, failed.
For tools include "tool"; for numbered steps include "step" and "step_total"; for milestones
include "last_completed_step".
Finish with exactly one completed or failed line. On success include BOTH:
  "summary" -- one short spoken sentence (a label, e.g. "Fetched the last 10 important emails"), and
  "result"  -- the ACTUAL substantive answer the user asked for, in full: if they asked a
              question this is the answer; if they asked for a list this is the list itself.
              Never put only a label in "result".
"result" is READ ALOUD by text-to-speech, so keep it brief and to the point -- state
the key facts plainly in a sentence or two (or a short list), with no padding, filler,
or repeated framing. Do not pad it out just to sound thorough; a short correct answer
is better than a long one, since the whole thing gets spoken start to finish.
Example: @@JESS_STATUS {"state":"completed","summary":"short spoken summary","result":"the full answer text the user asked for"}
Emit only major milestones, not token-by-token updates. Continue the requested task
normally; these status lines are consumed by the host application.
""".strip()

# The status protocol appended to every task is often echoed back verbatim by
# agent CLIs. Its literal examples must never be mistaken for real progress --
# the echoed "completed" example would instantly terminate the job.
_PROTOCOL_EXAMPLE_STATUSES = (
    {"state": "in_progress", "action": "short current action"},
    {
        "state": "completed",
        "summary": "short spoken summary",
        "result": "the full answer text the user asked for",
    },
)

# Agents that crash (rate limits, tracebacks, stream failures) often still
# exit 0, so a clean return code alone can't be trusted. If the tail of the
# output looks like this and no structured terminal marker arrived, the job
# failed. Kept narrow: mid-job errors an agent recovered from scroll out of
# the inspected window.
_ERROR_TAIL_RE = re.compile(
    r"❌"
    r"|Traceback \(most recent call last\)"
    r"|\b(?:unexpected|fatal) error\b"
    r"|\berror:"
    r"|\bstatus_code:\s*[45]\d\d\b"
    r"|\busage.?limit.?reached\b"
    r"|\brate.?limit(?:ed|_error)?\b"
    r"|\bfailed after \d+ attempts\b"
    r"|\bpermission denied\b",
    re.IGNORECASE,
)
_ERROR_TAIL_WINDOW = 8  # non-empty lines inspected at the end of the output
_QUOTA_RE = re.compile(
    r"usage.?limit.?reached|insufficient.?quota|billing.?hard.?limit|"
    r"quota (?:has been )?(?:exceeded|exhausted)|out of (?:credits|usage)|"
    r"credit balance|resource.?exhausted",
    re.IGNORECASE,
)
_RATE_LIMIT_RE = re.compile(r"\b429\b|rate.?limit|too many requests", re.IGNORECASE)
_CAPACITY_RE = re.compile(
    r"provider.*(?:capacity|overloaded)|(?:server|service).*(?:overloaded|at capacity)",
    re.IGNORECASE,
)

_MAX_KEPT_LINES = 500  # keep logs bounded; tail is what matters
# Status text fields are short labels capped tight, except "result", which
# carries the full substantive answer relayed to the user.
_MAX_STATUS_TEXT_CHARS = 300
_MAX_RESULT_CHARS = 4000

# CLI agents love ANSI colours; Jess should not attempt to pronounce \x1b[0m.
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07|[\r\x07]")
_QUESTION_MARKER_RE = re.compile(
    r"^\s*(?:follow[-_ ]?up[-_ ]?question|clarifying[-_ ]?question|question)\s*[:\-]\s*(.+)$",
    re.IGNORECASE,
)
_HERMES_SESSION_AGENTS = {"hermes", "hermes-yolo"}
_HERMES_SESSION_RE = re.compile(
    r"^(?:session_id:|Session:)\s*(\d{8}_\d{6}_[0-9a-f]{6})$", re.IGNORECASE
)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


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
    cwd: str | None = None  # resolved working dir this job ran in, for relaunch
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
    summary: str = ""  # short spoken label
    result: str = ""  # full substantive answer relayed into the LLM context
    announce_start: bool = False
    started_at: str = field(default_factory=_now_iso)
    finished_at: str = ""
    failure_kind: str = ""
    failure_detail: str = ""
    model_label: str = ""
    host_modified: bool = False  # the job touched the host app's own source
    _t0: float = field(default=0.0, repr=False)
    _last_status: float = field(default=0.0, repr=False)
    _host_before: str | None = field(default=None, repr=False)
    _launch_done: asyncio.Event = field(default_factory=asyncio.Event, repr=False)


def build_command(
    template: list[str], task: str, *, extra_args: list[str] | None = None
) -> list[str]:
    """Substitute placeholders in a backend command template."""
    command = [
        part.replace("{task}", task).replace("{python}", sys.executable) for part in template
    ]
    if extra_args:
        insert_at = (
            command.index("chat") + 1
            if Path(command[0]).stem.lower() == "hermes" and "chat" in command
            else 1
        )
        command[insert_at:insert_at] = extra_args
    return command


def detect_provider_failure(line: str) -> str | None:
    """Classify provider-side quota, throttling, or capacity failures."""
    if _QUOTA_RE.search(line):
        return "quota"
    if _RATE_LIMIT_RE.search(line):
        return "rate_limit"
    if _CAPACITY_RE.search(line):
        return "capacity"
    return None


def with_status_protocol(task: str) -> str:
    """Append the small stdout status contract understood by AgentBridge."""
    return f"{task}\n\n{_STATUS_PROTOCOL}"


def with_scope(task: str, cwd: str | None, preamble: str) -> str:
    """Prepend the scope preamble so agents know the cwd is not the subject.

    Without it, a coding agent handed a vague task treats whatever directory
    it is standing in as the thing to change (jess_runtime.log 2026-07-05
    12:35: CodePuppy "enabled YouTube" by editing this application's source).
    """
    if not preamble:
        return task
    return f"{preamble.format(cwd=cwd or 'unspecified')}\n\n{task}"


def resolve_cwd(cwd: str | None, workspace_dir: str | None) -> str | None:
    """Default a job to the neutral agent workspace, creating it on first use.

    ``cwd=None`` used to mean "inherit the host's working directory" -- i.e.
    this repository -- so agents woke up inside their own host's source tree.
    """
    if cwd:
        return cwd
    if not workspace_dir:
        return None
    Path(workspace_dir).mkdir(parents=True, exist_ok=True)
    return workspace_dir


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
            cap = _MAX_RESULT_CHARS if field_name == "result" else _MAX_STATUS_TEXT_CHARS
            status[field_name] = field_value.strip()[:cap]
    for field_name in ("step", "step_total"):
        field_value = value.get(field_name)
        if isinstance(field_value, int) and field_value > 0:
            status[field_name] = field_value
    if status in _PROTOCOL_EXAMPLE_STATUSES:
        return None  # the CLI echoed our own protocol examples back
    return status


def _incomplete_status_line(line: str) -> bool:
    """True when a status marker started but terminal wrapping split its JSON."""
    marker = line.find(_STATUS_MARKER)
    if marker < 0:
        return False
    payload = line[marker + len(_STATUS_MARKER) :].lstrip(" :")
    if not payload.startswith("{"):
        return False
    try:
        json.JSONDecoder().raw_decode(payload)
    except json.JSONDecodeError:
        return True
    return False


def error_tail(lines: list[str]) -> str | None:
    """Return the most recent error-shaped line near the end of the output."""
    tail = [line.strip() for line in lines if line.strip()][-_ERROR_TAIL_WINDOW:]
    for line in reversed(tail):
        if _ERROR_TAIL_RE.search(line):
            return line[:300]
    return None


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
    tail = [
        line.strip()
        for line in lines
        if line.strip()
        and not _SUMMARY_SKIP_RE.search(line.strip())
        and not line.strip().startswith(_STATUS_MARKER)
    ][-max_lines:]
    text = " ".join(tail)
    if len(text) > max_chars:
        text = text[: max_chars - 3] + "..."
    return text


def _split_questions(text: str) -> list[str]:
    """Split one line containing one or more questions into speakable chunks."""
    parts = re.findall(r"[^?]+\?", text)
    return [part.strip() for part in parts if part.strip()]


def follow_up_questions(job: AgentJob) -> list[str]:
    """Return the agent's questions for the user, preserving output order.

    Agents are encouraged to mark questions with ``QUESTION: ...`` or
    ``FOLLOW_UP_QUESTION: ...``; those are honoured wherever they appear. As a
    fallback, a single UNMARKED question counts only when it is the *last* line
    of the output -- i.e. the agent finished by asking. A question mark anywhere
    earlier is almost always the agent narrating its own reasoning ("Are you
    running cmd.exe?"); relaying that as if it needed the user's answer baffles
    them and their reply gets misrouted into new tasks (jess_runtime.log
    2026-07-07 03:47). Failed jobs are never treated as questions.
    """
    if job.status != STATUS_DONE:
        return []
    stripped = [line.strip() for line in job.lines if line.strip()]
    questions: list[str] = []
    for line in stripped:
        match = _QUESTION_MARKER_RE.match(line)
        if match:
            marked = match.group(1).strip()
            questions.extend(_split_questions(marked) or [marked])
    if not questions and stripped and stripped[-1].endswith("?"):
        questions.extend(_split_questions(stripped[-1]) or [stripped[-1]])
    return questions


def follow_up_question(job: AgentJob) -> str | None:
    """Compatibility wrapper: first follow-up question, if any."""
    questions = follow_up_questions(job)
    return questions[0] if questions else None


_CONFIRMATION_GATE_RE = re.compile(
    r"\brequesting\s+confirmation\b"
    r"|\bneeds?\s+(?:your\s+)?confirmation\b"
    r"|\bwaiting\s+for\s+(?:your\s+)?confirmation\b"
    r"|\bconfirm(?:ation)?\b[^.?!]{0,40}\bto\s+proceed\b"
    r"|\bsay\s+['\"]?confirm['\"]?\b",
    re.IGNORECASE,
)


def requests_confirmation(job: AgentJob) -> str | None:
    """Return the confirmation prompt if a "completed" job is really asking permission.

    Some agent backends are one-shot CLIs: instead of doing the work, they
    sometimes decide they need the user's OK first, print a "say 'confirm' to
    proceed" gate of their own, and exit -- which our harness would otherwise
    take at face value as a successful completion (jess_runtime.log 2026-07-06
    18:23 and 18:26: hermes-yolo "completed" with summary "Requesting
    confirmation to proceed" and the task was never actually run). The caller
    is expected to hold a fresh confirmation and relaunch on approval instead
    of announcing this as a finished result.
    """
    if job.status != STATUS_DONE:
        return None
    for candidate in (job.result, job.summary):
        if candidate and _CONFIRMATION_GATE_RE.search(candidate):
            return candidate
    return None


def result_detail(job: AgentJob) -> str:
    """Full substantive answer a finished job produced, for the LLM context.

    Prefers the structured ``result`` the agent reported; falls back to the raw
    output tail so that agents which print their answer to stdout without a
    ``result`` field still have it relayed rather than lost behind the summary.
    """
    if job.result:
        return job.result
    body = "\n".join(line for line in job.lines if line.strip()).strip()
    return body if body and body != job.summary else ""


_TASK_LABEL_MAX_CHARS = 48


def task_label(task: str) -> str:
    """A short, clean spoken reference to a task, or ``""`` if there isn't one.

    Status updates are spoken repeatedly (start, still-working, finish); reading
    a long user phrasing aloud each time is grating. But *truncating* a long
    sentence produces dangling fragments ("...people who put"), which is worse.
    So we only return the task when it is already short and complete; otherwise
    we return ``""`` and the caller falls back to a generic reference ("it").
    """
    text = " ".join((task or "").split())
    if text and len(text) <= _TASK_LABEL_MAX_CHARS:
        return text
    return ""


def announcement(job: AgentJob) -> str:
    """One-sentence-ish update for Jess to relay to the user."""
    tamper = (
        " Warning: this job modified my own source files -- review the working tree."
        if job.host_modified
        else ""
    )
    questions = follow_up_questions(job)
    if questions:
        joined = " ".join(questions)
        return f"Agent '{job.agent}' needs your input: {joined}{tamper}"
    summary = job.summary or summarize_output(job.lines)
    # A clean short reference when the task is brief, else a generic one -- never
    # a truncated fragment of a long user sentence.
    ref = task_label(job.task) or "the task"
    if job.status == STATUS_DONE:
        # "summary" is a short spoken LABEL ("I checked the current time"), not
        # necessarily the answer -- "result" is the actual substantive content
        # the user asked for ("The current local time is 7:13 PM..."). Speak
        # the real answer; a label alone leaves out the one thing worth hearing.
        answer = job.result or summary
        if not answer:
            return (
                f"Agent '{job.agent}' finished {ref} but returned no result to relay -- "
                f"re-run it to capture the output.{tamper}"
            )
        return f"{job.agent} finished: {answer}{tamper}"
    if job.status == STATUS_CANCELLED:
        return f"{job.agent} cancelled {ref}.{tamper}"
    if job.failure_kind == "quota":
        return (
            f"Agent '{job.agent}' failed because its current model or provider is out of "
            f"usage or quota. Say 'switch {job.agent} to OpenAI' and then 'retry'.{tamper}"
        )
    if job.failure_kind in {"rate_limit", "capacity"}:
        return (
            f"Agent '{job.agent}' failed because its provider is rate-limited or at capacity. "
            f"You can say 'switch {job.agent} to OpenAI'.{tamper}"
        )
    return f"{job.agent} FAILED {ref}. Last output: {summary}{tamper}"


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
        model_targets: dict | None = None,
        workspace_dir: str | None = None,
        scope_preamble: str = "",
        host_repo: str | None = None,
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
            model_targets: Agent/provider mappings for deterministic model overrides.
            workspace_dir: Default cwd for jobs started without one; None
                inherits the host process directory (the old behavior).
            scope_preamble: Text prepended to every task ({cwd} placeholder);
                empty disables it.
            host_repo: Git repository checked for modification by each job;
                None or empty disables the check.
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
        self._workspace_dir = workspace_dir
        self._scope_preamble = scope_preamble
        self._host_repo = host_repo
        self._model_targets = model_targets or {}
        self._model_overrides: dict[str, list[str]] = {}
        self._model_labels: dict[str, str] = {}
        self._session_ids: dict[str, str] = {}
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

    def has_active(self) -> bool:
        """True while any job is running, waiting, or blocked."""
        return any(job.status in _ACTIVE_STATUSES for job in self._jobs.values())

    def latest_active(self) -> AgentJob | None:
        """Return the newest active job, if any."""
        return next(
            (job for job in reversed(self._jobs.values()) if job.status in _ACTIVE_STATUSES),
            None,
        )

    def set_model_override(self, agent: str, provider: str) -> str | None:
        """Select a configured per-run model target; return its spoken label."""
        target = self._model_targets.get(agent, {}).get(provider)
        if not isinstance(target, dict) or not target.get("args") or not target.get("label"):
            return None
        self._model_overrides[agent] = list(target["args"])
        self._model_labels[agent] = str(target["label"])
        return self._model_labels[agent]

    # -- lifecycle ------------------------------------------------------------

    async def start(
        self, agent: str, task: str, cwd: str | None = None, *, announce_start: bool = False
    ) -> str:
        """Spawn a job and return its id immediately; output streams via events."""
        job = AgentJob(
            job_id=f"job-{next(self._counter)}",
            agent=agent,
            task=task.partition("\n\n[Untrusted conversation context:")[0],
            machine=self.machine_for(agent),
            announce_start=announce_start,
            model_label=self._model_labels.get(agent, ""),
        )
        job._t0 = time.monotonic()
        job._last_status = job._t0
        self._jobs[job.job_id] = job

        template = self._backends.get(agent)
        if template is None:
            result = await self._fail_fast(job, f"unknown agent backend '{agent}'")
            job._launch_done.set()
            return result

        cwd = resolve_cwd(cwd, self._workspace_dir)
        job.cwd = cwd
        job._host_before = await self._host_snapshot()
        if job.status == STATUS_CANCELLED:
            job.state = STATE_CANCELLED
            job.secs = round(time.monotonic() - job._t0, 1)
            job.summary = "Superseded before launch"
            job.finished_at = _now_iso()
            self._emit_job(job, "finished", summary=job.summary, secs=job.secs)
            await self._notify_finished(job)
            job._launch_done.set()
            return job.job_id
        command_task = (
            task
            if agent == "mock"
            else with_status_protocol(with_scope(task, cwd, self._scope_preamble))
        )
        command = build_command(template, command_task, extra_args=self._model_overrides.get(agent))
        if session_id := self._session_ids.get(agent):
            try:
                chat_index = command.index("chat") + 1
            except ValueError:
                pass
            else:
                command[chat_index:chat_index] = ["--resume", session_id]
        try:
            proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except (OSError, FileNotFoundError) as e:
            result = await self._fail_fast(job, f"could not launch {command[0]}: {e}")
            job._launch_done.set()
            return result

        self._procs[job.job_id] = proc
        job._launch_done.set()
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
        if job is None or job.status not in _ACTIVE_STATUSES:
            return
        job.status = STATUS_CANCELLED
        job.state = STATE_CANCELLED
        if proc is None:
            await job._launch_done.wait()
            proc = self._procs.get(job_id)
        if proc is None:
            return
        await self._terminate(proc)

    async def replace_latest(self, correction: str) -> str | None:
        """Cancel the newest active job, then restart it with user correction context."""
        job = self.latest_active()
        if job is None:
            return None
        await self.cancel(job.job_id)
        task = f"{job.task}\n\nUser correction: {correction}"
        return await self.start(job.agent, task, announce_start=True)

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
        job.finished_at = _now_iso()
        logger.warning(f"Agent job {job.job_id} failed to start: {reason}")
        self._emit_job(job, "finished", summary=reason)
        await self._notify_finished(job)
        return job.job_id

    async def _consume(self, job: AgentJob, proc: asyncio.subprocess.Process) -> None:
        """Drain stdout into the job's log, then wait for the exit code."""
        assert proc.stdout is not None
        pending_status: list[str] = []

        async def consume_status(status: dict) -> bool:
            self._apply_status(job, status)
            if status["state"] not in _TERMINAL_STATES:
                return False
            # Hermes query mode exits immediately after printing its session
            # summary, whose ID the next turn needs.
            if job.agent in _HERMES_SESSION_AGENTS:
                return False
            await asyncio.sleep(self._completion_grace_secs)
            await self._terminate(proc)
            job.returncode = await proc.wait()
            return True

        while True:
            if self._timeout_secs > 0:
                raw = await asyncio.wait_for(proc.stdout.readline(), self._timeout_secs)
            else:
                raw = await proc.stdout.readline()
            if not raw:
                break
            line = clean_line(raw.decode("utf-8", errors="replace"))
            if not line:
                continue
            if job.agent in _HERMES_SESSION_AGENTS and (
                session_match := _HERMES_SESSION_RE.fullmatch(line)
            ):
                self._session_ids[job.agent] = session_match.group(1)
                continue
            failure_kind = detect_provider_failure(line)
            if failure_kind:
                job.failure_kind = failure_kind
                job.failure_detail = line[:300]

            if pending_status:
                pending_status.append(line.strip())
                joined = " ".join(pending_status)
                status = parse_status_line(joined)
                if status is not None:
                    pending_status.clear()
                    if await consume_status(status):
                        return
                    continue
                if (
                    len(pending_status) < _STATUS_FRAGMENT_MAX_LINES
                    and len(joined) < _STATUS_FRAGMENT_MAX_CHARS
                ):
                    continue
                pending_status.clear()
                continue

            status = parse_status_line(line)
            if status is not None:
                if await consume_status(status):
                    return
                continue
            if _incomplete_status_line(line):
                pending_status = [line.strip()]
                continue
            job.lines.append(line)
            del job.lines[:-_MAX_KEPT_LINES]
            self._emit_job(job, "output", line=line)
            if failure_kind == "quota":
                job.status = STATUS_FAILED
                job.state = STATE_FAILED
                job.summary = "Current model/provider usage or quota is exhausted"
                await self._terminate(proc)
                job.returncode = await proc.wait()
                return
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
            await self._consume(job, proc)
        except TimeoutError:
            timed_out = True
            await self._terminate(proc)
            job.lines.append(
                f"[stopped: no output for {self._timeout_secs:.0f}s inactivity timeout]"
            )
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
            # No structured terminal marker arrived, so the exit code is the
            # only signal -- and agents that crash often still exit 0, so an
            # error-shaped output tail also counts as failure.
            error_line = error_tail(job.lines) if job.returncode == 0 else None
            failed = job.returncode != 0 or error_line is not None
            job.status = STATUS_FAILED if failed else STATUS_DONE
            job.state = STATE_FAILED if failed else STATE_COMPLETED
            if error_line and not job.summary:
                job.summary = error_line
            if failed and job.failure_kind and not job.summary:
                job.summary = job.failure_detail
        job.secs = round(time.monotonic() - job._t0, 1)
        summary = job.summary or summarize_output(job.lines)
        job.summary = summary
        job.finished_at = _now_iso()
        await self._check_host_repo(job)
        self._emit_job(job, "finished", summary=summary, secs=job.secs)
        logger.info(f"Agent job {job.job_id} [{job.agent}] -> {job.status}")

        await self._notify_finished(job)

    async def _host_snapshot(self) -> str | None:
        """``git status --porcelain`` of the host repo; None when disabled/unavailable."""
        if not self._host_repo:
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                self._host_repo,
                "status",
                "--porcelain",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), 10.0)
        except (TimeoutError, OSError):
            return None
        if proc.returncode != 0:
            return None
        return out.decode("utf-8", errors="replace")

    async def _check_host_repo(self, job: AgentJob) -> None:
        """Flag the job if the host repo's working tree changed during the run."""
        if job._host_before is None:
            return
        after = await self._host_snapshot()
        if after is not None and after != job._host_before:
            job.host_modified = True
            logger.warning(
                f"Agent job {job.job_id} [{job.agent}] modified the host repository "
                f"working tree during the run"
            )

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
                generic = job.action in {"", "Starting", "Still working"}
                if generic and time.monotonic() - job._t0 < min(
                    1.0, self._progress_interval_secs * 3
                ):
                    continue
                self._apply_status(
                    job,
                    {
                        "state": STATE_IN_PROGRESS,
                        "action": "Still working" if generic else job.action,
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
        # Regression: this used to only fire once ("and not job.last_completed_step"),
        # so "Last completed" froze at the FIRST milestone and never advanced.
        # Update on every milestone, using the explicit field if the CLI gave one,
        # else its action text for this same status.
        if state == STATE_STEP_COMPLETED and "last_completed_step" not in status:
            job.last_completed_step = job.action
        elif state in _TERMINAL_STATES and not job.last_completed_step:
            # Backend never reported a milestone at all -- better to show the
            # final summary than a permanent "-" once the job is actually done.
            job.last_completed_step = job.summary or job.action
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
            "result": job.result,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "failure_kind": job.failure_kind,
            "failure_detail": job.failure_detail,
            "model_label": job.model_label,
            "host_modified": job.host_modified,
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

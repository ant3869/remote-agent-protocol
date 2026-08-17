"""Brain-only Remote Agent Protocol coordinator.

This module is the low-VRAM counterpart to :mod:`remote_agent_protocol.session`.
It owns text turns, memory, routing, delegation, and Ollama calls, but it never
starts microphone capture, STT, TTS, or local speakers. External realtime voice
frontends can use it through ``openai_bridge``.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import asdict
from datetime import datetime

import aiohttp
from loguru import logger

from remote_agent_protocol import (
    agent_bridge,
    intent_router,
    job_store,
    lifecycle_ws,
    memory,
    ollama_models,
    remote_client,
    remote_protocol,
    voice_commands,
)
from remote_agent_protocol import config as cfg
from remote_agent_protocol import personas as persona_catalog
from remote_agent_protocol.personas import Persona
from remote_agent_protocol.session_processors import _MARKER_RE, is_placeholder_task


class LLMUnavailable(RuntimeError):
    """The local LLM endpoint refused or dropped the connection for this turn.

    Distinct from a model that answers badly: nothing is wrong with the request,
    so callers can say so plainly instead of reporting a server fault.
    """


class BrainSession:
    """Text-only assistant brain with RAP routing and delegation semantics."""

    def __init__(self, persona: Persona, on_event=None):
        """Initialize a text-only coordinator for one persona.

        Args:
            persona: Persona prompt/model defaults to use for assistant turns.
            on_event: Optional callback receiving transcript/routing/agent events.
        """
        self._persona = persona
        self._on_event = on_event
        self._messages = memory.load_memory(cfg.MEMORY_FILE, cfg.MEMORY_MAX_MSGS) if cfg.MEMORY_ENABLED else []
        self._router = intent_router.IntentRouter()
        self._routing_history: deque[dict] = deque(maxlen=25)
        self._pending_confirmations: dict[str, tuple[str, str, str | None, str]] = {}
        self._confirm_counter = 0
        self._force_confirm = False
        self._force_confirm_reason = ""
        self._last_user_text = ""
        self._control_turn = False
        self._cancel_wait_s = 5.0
        self._recently_denied: deque[tuple[str, str]] = deque(maxlen=5)
        self._recent_delegations: deque[tuple[float, str]] = deque(maxlen=20)
        self._default_agent_backend = cfg.AGENT_DEFAULT_BACKEND
        if persona.tool_user in cfg.AGENT_BACKENDS:
            self._default_agent_backend = persona.tool_user
        # Agents offered by other machines. Empty unless AGENT_REMOTE_HOSTS_JSON
        # names one; when it does, the registry heartbeats those hosts and the
        # bridge dispatches their "<host>:<agent>" names to whichever is online.
        self._remotes = remote_client.RemoteRegistry()
        self._bridge = agent_bridge.AgentBridge(
            cfg.AGENT_BACKENDS,
            self._on_agent_event,
            self._announce_agent_job,
            machines=cfg.AGENT_MACHINES,
            timeout_secs=cfg.AGENT_JOB_TIMEOUT_SECS,
            kill_grace_secs=cfg.AGENT_JOB_KILL_GRACE_SECS,
            progress_interval_secs=cfg.AGENT_PROGRESS_INTERVAL_SECS,
            completion_grace_secs=cfg.AGENT_COMPLETION_GRACE_SECS,
            on_persist=self._persist_job if cfg.AGENT_HISTORY_FILE else None,
            model_targets=cfg.AGENT_MODEL_TARGETS,
            workspace_dir=cfg.AGENT_WORKSPACE_DIR,
            scope_preamble=cfg.AGENT_SCOPE_PREAMBLE,
            host_repo=cfg.AGENT_HOST_REPO,
            remotes=self._remotes,
        )
        self._lifecycle_ws = (
            lifecycle_ws.LifecycleEventServer(
                host=cfg.LIFECYCLE_WS_HOST,
                port=cfg.LIFECYCLE_WS_PORT,
                path=cfg.LIFECYCLE_WS_PATH,
                queue_size=cfg.LIFECYCLE_WS_QUEUE_SIZE,
                on_status=self._emit,
            )
            if cfg.LIFECYCLE_WS_ENABLED
            else None
        )
        self._http: aiohttp.ClientSession | None = None
        self._turn_lock = asyncio.Lock()
        # Strong references to background work (see _spawn).
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        """Start background services that do not allocate audio/STT/TTS models."""
        self._http = aiohttp.ClientSession()
        if self._lifecycle_ws is not None:
            await self._lifecycle_ws.start()
        self._remotes.start()
        self._spawn(self._router.warmup(), "brain-intent-router-warmup")
        self._spawn(self._warm_chat_model(), "brain-chat-model-warmup")

    async def _warm_chat_model(self) -> None:
        """Make the reply model resident before the first turn asks for it.

        The router has always been preloaded; the model that actually answers
        was not, so the first spoken turn of a session paid its whole load.
        """
        model = getattr(self, "_model_override", None) or self._persona.model_name(cfg.LLM_MODEL)
        await asyncio.to_thread(
            ollama_models.preload, cfg.OLLAMA_HOST, model, cfg.LLM_KEEP_ALIVE
        )

    def _spawn(self, coro, name: str) -> None:
        """Run background work while holding a strong reference to it.

        asyncio keeps only a weak reference to a task, so a bare
        ``create_task`` can be collected before it runs -- and a collected
        delegation means the assistant said it dispatched work that never
        started. The bridge guards its own jobs the same way.
        """
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def stop(self) -> None:
        """Stop background services and persist short-term memory."""
        if self._lifecycle_ws is not None:
            await self._lifecycle_ws.stop()
        await self._remotes.stop()
        await self._bridge.shutdown()
        if self._http is not None:
            await self._http.close()
            self._http = None
        if cfg.MEMORY_ENABLED:
            memory.save_memory(cfg.MEMORY_FILE, self._messages[-cfg.MEMORY_MAX_MSGS :])

    async def complete(self, user_text: str, *, llm_content: str | None = None) -> str:
        """Serialize and process one complete text turn."""
        async with self._turn_lock:
            return await self._complete_unlocked(user_text, llm_content=llm_content)

    async def _complete_unlocked(self, user_text: str, *, llm_content: str | None = None) -> str:
        """Process one user text turn and return assistant text.

        Args:
            user_text: What the user actually said or typed. Drives the visible
                transcript, confirmation matching, and delegation routing.
            llm_content: Optional richer body for the model, used when a turn
                carries more than its plain text -- a multimodal bundle, say.
                Kept separate so scaffolding never reaches the transcript or the
                router, which both reason about what the user meant.
        """
        text = user_text.strip()
        if not text:
            return ""
        content = await self._turn_content(text, llm_content)

        self._messages.append({"role": "user", "content": content})
        assistant = await self._call_ollama()
        assistant = self._handle_delegate_markers(assistant)
        self._finish_turn(assistant)
        return assistant

    def _finish_turn(self, assistant: str) -> None:
        """Record one completed assistant turn in history, memory, and the UI."""
        self._messages.append({"role": "assistant", "content": assistant})
        if cfg.MEMORY_ENABLED:
            memory.save_memory(cfg.MEMORY_FILE, self._messages[-cfg.MEMORY_MAX_MSGS :])
        self._emit({"type": "transcript", "role": "assistant", "text": assistant})

    async def complete_stream(
        self, user_text: str, *, llm_content: str | None = None
    ) -> AsyncIterator[str]:
        """Serialize a turn and yield its text as it becomes speakable."""
        async with self._turn_lock:
            async for piece in self._complete_stream_unlocked(
                user_text, llm_content=llm_content
            ):
                yield piece

    async def _complete_stream_unlocked(
        self, user_text: str, *, llm_content: str | None = None
    ) -> AsyncIterator[str]:
        """Yield assistant text as it is generated, for realtime speech.

        Speaking cannot wait for a finished reply -- that is the whole latency
        budget of a spoken turn -- so text is released at sentence boundaries as
        the model produces it. Delegation markers must never be spoken, so once
        one starts the remainder is withheld and resolved when the reply ends.
        """
        text = user_text.strip()
        if not text:
            return
        content = await self._turn_content(text, llm_content)
        self._messages.append({"role": "user", "content": content})

        full = ""
        spoken = ""
        pending = ""
        withholding = False
        async for delta in self._stream_ollama():
            full += delta
            if withholding:
                continue
            pending += delta
            head = pending.find(_MARKER_START)
            if head != -1:
                ready, _ = _split_sentences(pending[:head])
                if ready:
                    spoken += ready
                    yield ready
                withholding = True
                continue
            ready, pending = _split_sentences(pending)
            if ready:
                spoken += ready
                yield ready

        cleaned = self._handle_delegate_markers(full)
        remainder = cleaned[len(spoken) :] if cleaned.startswith(spoken) else ("" if spoken else cleaned)
        if remainder.strip():
            yield remainder
        self._finish_turn(cleaned)

    async def _turn_content(self, text: str, llm_content: str | None) -> str:
        """Resolve what one user turn puts into history.

        Announce turns bypass routing entirely -- they are the app talking to
        itself about a finished agent job -- while everything else runs the
        normal confirmation and delegation path.
        """
        if text.startswith(ANNOUNCE_PREFIX):
            instruction = text[len(ANNOUNCE_PREFIX) :].strip()
            self._emit({"type": "sys", "text": "Voicing an agent job summary."})
            # The summary narration must never delegate: a marker here spawns a
            # fresh job every time a job finishes, breeding jobs indefinitely.
            self._control_turn = True
            return instruction
        self._emit({"type": "transcript", "role": "user", "text": text})
        self._last_user_text = text
        self._control_turn = False

        consumed = self._maybe_consume_confirmation(text)
        if consumed is not None:
            return consumed
        cancel_request = voice_commands.parse_agent_cancel(text, cfg.AGENT_SPOKEN_ALIASES)
        if cancel_request is not None:
            return await self._handle_agent_cancel(cancel_request)
        # Before the per-agent progress question: "is hermes up" asks whether
        # an agent can take work, not how an existing job is going.
        rollcall = voice_commands.parse_agent_rollcall(text, cfg.AGENT_SPOKEN_ALIASES)
        if rollcall is not None:
            return self._handle_agent_rollcall(rollcall[0])
        status_request = voice_commands.parse_agent_status(text, cfg.AGENT_SPOKEN_ALIASES)
        if status_request is not None:
            return self._handle_agent_status(status_request)
        parsed = await self._resolve_delegation(text)
        if parsed is not None:
            agent, task = parsed
            logger.info(f"Brain delegation -> [{agent}] {task}")
            return self._delegate_ack(agent, task)
        return llm_content or text

    async def _handle_agent_cancel(self, cancel_request: tuple[str | None, bool]) -> str:
        """Cancel matching active jobs for real, then let the persona narrate it.

        Marks the turn as a control turn so a stray delegation marker in the
        narration cannot spawn a brand-new job mid-cancellation.
        """
        agent, all_jobs = cancel_request
        self._control_turn = True
        # A job that refuses to die must not hold the turn lock hostage: one
        # hung cancel froze every later request into 20-second timeouts live.
        # Shield keeps the cancellation running after we stop waiting for it.
        cancel_task = asyncio.ensure_future(self._bridge.cancel_active(agent, all_jobs=all_jobs))
        try:
            count = await asyncio.wait_for(asyncio.shield(cancel_task), timeout=self._cancel_wait_s)
        except TimeoutError:
            logger.warning(f"Voice cancel still running after {self._cancel_wait_s}s; backgrounding")
            return (
                "[Agent update: cancellation is underway but at least one agent is slow "
                "to stop; it will finish in the background. Tell the user briefly; "
                "do not start any new work.]"
            )
        logger.info(f"Voice cancel -> agent={agent} all={all_jobs} cancelled={count}")
        if count:
            noun = "task" if count == 1 else "tasks"
            return (
                f"[Agent update: cancelled {count} active {noun}. "
                "Confirm briefly; do not start any new work.]"
            )
        return (
            "[Agent update: there were no matching active tasks to cancel. "
            "Tell the user briefly; do not start any new work.]"
        )

    def _handle_agent_rollcall(self, agent: str | None = None) -> str:
        """Answer "which agents are there?" from what RAP itself knows.

        An agent cannot report on its peers -- asked to, it guesses, and a
        guess delivered in the butler's voice reads exactly like a fact. RAP
        knows which backends are configured, whether each one can be launched,
        which remote machines are answering, and what is running right now, so
        the roll call is answered here and never delegated.
        """
        self._control_turn = True
        remote_states = {state["name"]: state for state in self._bridge.remote_hosts()}
        active = {}
        for job in self._bridge.active_jobs():
            active[job.agent] = active.get(job.agent, 0) + 1
        rows = []
        backends = [
            name
            for name in self._bridge.backend_names()
            if agent is None or name == agent or name.endswith(f":{agent}")
        ]
        for backend in backends:
            split = remote_protocol.split_backend_name(backend)
            if split is not None and split[0] in remote_states:
                state = remote_states[split[0]]
                ready = "ready" if state["online"] else f"unreachable ({state['error']})"
                where = state["machine"]
            else:
                status, detail = agent_bridge.executable_status(cfg.AGENT_BACKENDS.get(backend, []))
                ready = "ready" if status == "ok" else f"not runnable here ({detail})"
                where = self._bridge.machine_for(backend)
            busy = f", {active[backend]} job(s) running" if backend in active else ""
            rows.append(f"{backend} on {where}: {ready}{busy}")
        if not rows:
            missing = f"there is no agent backend named '{agent}'" if agent else (
                "no agent backends are configured"
            )
            return (
                f"[Agent roll call: {missing}. "
                "Answer from this; do not start any new work.]"
            )
        listed = "; ".join(rows)
        return (
            f"[Agent roll call: {listed}. This is Remote Agent Protocol's own check of each "
            "backend -- whether it can be started here and whether its machine is answering -- "
            "not a reply from the agents themselves. Report it as such, briefly, and do not "
            "start any new work.]"
        )

    def _handle_agent_status(self, status_request: tuple[str | None]) -> str:
        """Answer a progress question from live job state instead of delegating.

        Live sessions showed "can I get an update?" spawning a fresh job per
        polite follow-up; status is a read, never a write.
        """
        (agent,) = status_request
        self._control_turn = True
        jobs = self._bridge.active_jobs(agent)
        if not jobs:
            scope = f"for {agent}" if agent else "right now"
            return (
                f"[Agent status: no active agent tasks {scope}. "
                "Answer from this; do not start any new work.]"
            )
        lines = "; ".join(
            f"{job.agent} is {job.status} on '{agent_bridge.task_label(job.task)}'"
            for job in jobs
        )
        return f"[Agent status: {lines}. Answer from this; do not start any new work.]"

    async def _resolve_delegation(self, text: str) -> tuple[str, str] | None:
        decision = await self._router.route(text, self._default_agent_backend)
        self._record_routing(decision)
        if decision.action == intent_router.ACTION_NONE:
            return None
        self._force_confirm = decision.action == intent_router.ACTION_CONFIRM
        self._force_confirm_reason = decision.reason if self._force_confirm else ""
        return decision.agent, decision.task

    def _record_routing(self, decision: intent_router.RoutingDecision) -> None:
        row = asdict(decision)
        self._routing_history.append(row)
        self._emit({"type": "routing", **row})
        logger.info(
            f"Brain routing[{decision.source}] {decision.action} "
            f"intent={decision.intent} confidence={decision.confidence:.2f} "
            f"reason={decision.reason!r}"
        )

    def _delegate_ack(self, agent: str, task: str, cwd: str | None = None) -> str:
        force_confirm, self._force_confirm = self._force_confirm, False
        forced_reason, self._force_confirm_reason = self._force_confirm_reason, ""
        destructive = cfg.AGENT_CONFIRM_ENABLED and voice_commands.requires_confirmation(
            agent, task, destructive_words=cfg.AGENT_DESTRUCTIVE_WORDS
        )
        if force_confirm or destructive:
            reason = forced_reason or "this task changes files, installs software, or otherwise mutates the system"
            self._confirm_counter += 1
            token = f"confirm-{self._confirm_counter}"
            self._pending_confirmations[token] = (agent, task, cwd, reason)
            self._emit(
                {
                    "type": "agent_confirm",
                    "token": token,
                    "agent": agent,
                    "task": task,
                    "machine": self._bridge.machine_for(agent),
                    "reason": reason,
                    "transcript": self._last_user_text,
                }
            )
            return cfg.DELEGATION_CONFIRM_PROMPT.format(agent=agent, task=task)
        self._remember_delegation(task)
        self._spawn(
            self._bridge.start(agent, self._with_delegation_context(task), cwd),
            f"brain-delegate-{agent}",
        )
        return cfg.DELEGATION_ACK_PROMPT.format(agent=agent, task=task)

    def resolve_confirmation(self, token: str, decision: str) -> str | None:
        """Approve or deny one specific pending confirmation.

        Callers holding a token -- GUI buttons, for instance -- must come through
        here rather than replaying a spoken "yes", so the task they authorize is
        the task that runs. Returns ``None`` if the token is unknown or already
        resolved.
        """
        entry = self._pending_confirmations.pop(token, None)
        if entry is None:
            return None
        agent, task, cwd, reason = entry
        self._emit_confirm_resolved(token, agent, task, reason, decision)
        if decision == "approve":
            self._remember_delegation(task)
            self._spawn(
                self._bridge.start(agent, self._with_delegation_context(task), cwd),
                f"brain-confirmed-{agent}",
            )
            return cfg.AGENT_CONFIRM_APPROVED_PROMPT.format(agent=agent, task=task)
        self._recently_denied.append((agent, task.strip().lower()))
        return cfg.AGENT_CONFIRM_DENIED_PROMPT.format(agent=agent, task=task)

    def _maybe_consume_confirmation(self, text: str) -> str | None:
        if not self._pending_confirmations:
            return None
        decision = voice_commands.classify_confirmation_reply(text)
        if decision is None:
            return None
        # A spoken reply names no target, so it answers the newest prompt.
        token = next(reversed(self._pending_confirmations))
        return self.resolve_confirmation(token, decision)

    def _emit_confirm_resolved(self, token: str, agent: str, task: str, reason: str, decision: str) -> None:
        self._emit(
            {
                "type": "agent_confirm_resolved",
                "token": token,
                "agent": agent,
                "task": task,
                "reason": reason,
                "decision": decision,
            }
        )

    def _ollama_payload(self, *, stream: bool) -> dict:
        payload = {
            "model": getattr(self, "_model_override", self._persona.model_name(cfg.LLM_MODEL)),
            "messages": [
                {"role": "system", "content": self._system_instruction()},
                *self._messages[-cfg.MEMORY_MAX_MSGS :],
            ],
            "stream": stream,
            "keep_alive": cfg.LLM_KEEP_ALIVE,
        }
        if cfg.LLM_REASONING_EFFORT is not None:
            payload["reasoning_effort"] = cfg.LLM_REASONING_EFFORT
        return payload

    async def _stream_ollama(self) -> AsyncIterator[str]:
        """Yield assistant text deltas as the model produces them."""
        if self._http is None:
            raise RuntimeError("BrainSession.start() was not called")
        payload = self._ollama_payload(stream=True)
        try:
            async with self._http.post(
                f"{cfg.OLLAMA_BASE_URL}/chat/completions", json=payload, timeout=120
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise RuntimeError(f"Ollama chat failed {resp.status}: {body}")
                async for raw in resp.content:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = chunk.get("choices", [{}])[0].get("delta", {}).get("content")
                    if delta:
                        yield delta
        except aiohttp.ClientConnectionError as exc:
            raise LLMUnavailable(f"{cfg.OLLAMA_BASE_URL} is not reachable: {exc}") from exc
        await self._refresh_ollama_keep_alive(payload["model"])

    async def _call_ollama(self) -> str:
        if self._http is None:
            raise RuntimeError("BrainSession.start() was not called")
        payload = self._ollama_payload(stream=False)
        try:
            async with self._http.post(
                f"{cfg.OLLAMA_BASE_URL}/chat/completions", json=payload, timeout=120
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise RuntimeError(f"Ollama chat failed {resp.status}: {body}")
                data = await resp.json()
        except aiohttp.ClientConnectionError as exc:
            raise LLMUnavailable(f"{cfg.OLLAMA_BASE_URL} is not reachable: {exc}") from exc
        await self._refresh_ollama_keep_alive(payload["model"])
        return str(data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()

    async def _refresh_ollama_keep_alive(self, model: str) -> None:
        """Apply Ollama residency when the OpenAI-compatible endpoint ignores it."""
        if not cfg.LLM_KEEP_ALIVE:
            return
        try:
            payload = {"model": model, "prompt": "", "stream": False, "keep_alive": cfg.LLM_KEEP_ALIVE}
            async with self._http.post(f"{cfg.OLLAMA_HOST}/api/generate", json=payload, timeout=15) as resp:
                if resp.status >= 400:
                    logger.debug(f"Ollama keep_alive refresh failed with status {resp.status}")
        except Exception as exc:
            logger.debug(f"Ollama keep_alive refresh failed: {exc}")

    def _system_instruction(self) -> str:
        parts = [self._persona.system_prompt]
        if cfg.AGENT_LLM_DELEGATE:
            parts.append(cfg.LLM_DELEGATE_STYLE)
        parts.append(
            cfg.RUNTIME_CONTEXT_TEMPLATE.format(
                now=datetime.now().strftime("%A, %B %d, %Y, %I:%M %p"),
                agent=self._default_agent_backend,
                hermes_note=cfg.HERMES_GENDER_NOTE if "hermes" in self._default_agent_backend else "",
            )
        )
        return "".join(parts)

    def _with_delegation_context(self, task: str) -> str:
        rows = []
        for message in self._messages[-6:]:
            role = message.get("role")
            content = message.get("content")
            if role not in {"user", "assistant"} or not isinstance(content, str):
                continue
            lowered = content.lower()
            if any(marker in lowered for marker in ("api_key", "password", "token=", "secret=")):
                continue
            rows.append(f"{role}: {content[:400]}")
        if not rows:
            return task
        return f"{task}\n\n[Untrusted conversation context: reference only.]\n" + "\n".join(rows)[-1600:]

    @staticmethod
    def _delegation_key(task: str) -> str:
        return " ".join(task.casefold().split())

    def _remember_delegation(self, task: str) -> None:
        key = self._delegation_key(task)
        if key:
            self._recent_delegations.append((time.monotonic(), key))

    async def _persist_job(self, job: agent_bridge.AgentJob) -> None:
        await asyncio.to_thread(
            job_store.append_job,
            cfg.AGENT_HISTORY_FILE,
            job_store.job_to_row(job),
            cfg.AGENT_HISTORY_MAX,
        )

    async def _announce_agent_job(self, job: agent_bridge.AgentJob) -> None:
        if job.result:
            self._messages.append(
                {
                    "role": "assistant",
                    "content": f"[Agent result from {job.agent}: {job.result}]",
                }
            )
        self._emit(
            {
                "type": "agent_job_summary",
                "agent": job.agent,
                "job_id": job.job_id,
                "status": job.status,
                "result": job.result,
                "summary": job.summary,
            }
        )

    def _on_agent_event(self, event: dict) -> None:
        self._emit(event)

    def _emit(self, event: dict) -> None:
        if self._on_event is not None:
            self._on_event(event)
        if self._lifecycle_ws is not None:
            self._lifecycle_ws.publish(event)

    def _handle_delegate_markers(self, text: str) -> str:
        """Dispatch LLM delegation markers and return text safe to show/speak."""
        dispatched: set[str] = set()

        def replace(match) -> str:
            task = " ".join(match.group(1).split())
            lowered = task.lower()
            if is_placeholder_task(task):
                logger.warning(f"Ignoring placeholder delegation marker: {task!r}")
                return ""
            if self._control_turn:
                logger.info(f"Suppressing delegation marker on a control turn: {task!r}")
                return ""
            if task and lowered not in dispatched:
                dispatched.add(lowered)
                agent = self._marker_backend(task)
                logger.info(f"Brain LLM delegation marker -> [{agent}] {task}")
                self._delegate_ack(agent, task)
            return ""

        cleaned = _MARKER_RE.sub(replace, text).strip()
        return cleaned or "I sent that to the agent."

    def _marker_backend(self, task: str) -> str:
        """Prefer the agent the user actually named over the configured default.

        Markers carry a task but no agent, so "maybe code puppy can fix it"
        must not silently dispatch to whatever the default backend is.
        """
        named = voice_commands.named_backend(
            f"{self._last_user_text} {task}", cfg.AGENT_BACKENDS, cfg.AGENT_SPOKEN_ALIASES
        )
        return named or self._default_agent_backend


# Reserved prefix for app-initiated announce turns: the realtime frontend
# relays them back to the brain, which must summarize rather than route them.
ANNOUNCE_PREFIX = "[[announce]]"

# Opening of a delegation marker. Spotting it early lets streaming stop before
# any of the marker reaches the speakers.
_MARKER_START = "[["

# Greedy, so it matches through the LAST sentence terminator followed by space:
# a flush should release everything that is safely speakable, not one sentence.
_SENTENCE_END_RE = re.compile(r".*[.!?…][\"')\]]*(?=\s)", re.DOTALL)


def _split_sentences(text: str) -> tuple[str, str]:
    """Split into (complete sentences, trailing fragment)."""
    match = _SENTENCE_END_RE.match(text)
    if match is None:
        return "", text
    return text[: match.end()], text[match.end() :]


def default_brain_session(on_event=None) -> BrainSession:
    """Create the default persona brain session."""
    return BrainSession(persona_catalog.DEFAULT_PERSONA, on_event=on_event)

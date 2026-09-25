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
    conversation_dispatch,
    intent_router,
    job_store,
    lifecycle_ws,
    llm_endpoint,
    memory,
    ollama_models,
    remote_client,
    voice_commands,
)
from remote_agent_protocol import (
    agent_status_reporting as agent_status,
)
from remote_agent_protocol import config as cfg
from remote_agent_protocol import personas as persona_catalog
from remote_agent_protocol.control_plane import AgentControlPlane, AgentRegistry
from remote_agent_protocol.control_plane.adapters.base import AgentTask
from remote_agent_protocol.control_plane.adapters.factory import build_adapters
from remote_agent_protocol.control_plane.models import ControlResult, JobHandle
from remote_agent_protocol.conversation import ConversationEvents, SpeechText
from remote_agent_protocol.conversation_hub.events import BUTLER_INTERVENTION_STARTED
from remote_agent_protocol.conversation_hub.floor import BUTLER_ID
from remote_agent_protocol.conversation_hub.service import TurnDisposition
from remote_agent_protocol.conversation_hub_wiring import build_app_conversation_hub
from remote_agent_protocol.conversation_presentation import (
    present_butler_intervention,
    present_hub_result,
    present_no_dispatch_explanation,
)
from remote_agent_protocol.orchestration import telemetry as orchestration_telemetry
from remote_agent_protocol.orchestration.orchestrator import (
    OrchestrationContext,
    PersonaOrchestrator,
)
from remote_agent_protocol.orchestration.providers.copilot import CopilotProvider
from remote_agent_protocol.orchestration.providers.local import LocalProvider
from remote_agent_protocol.personas import Persona
from remote_agent_protocol.session_processors import (
    _MARKER_RE,
    is_placeholder_task,
    looks_like_delegation_promise,
)


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
        self._conversation = ConversationEvents(lambda: self._persona.name)
        self._announcement_sources: dict[str, dict] = {}
        self._messages = (
            memory.load_memory(cfg.MEMORY_FILE, cfg.MEMORY_MAX_MSGS) if cfg.MEMORY_ENABLED else []
        )
        if cfg.MEMORY_ENABLED:
            # VoiceSession already does this on load (session.py); brain mode
            # never had (2026-09-25 Phase B3), so old synthetic announce
            # relays kept getting resent to the model and repersisted forever.
            self._messages = memory.strip_ephemeral(
                self._messages,
                system_prefixes=(cfg.MEM0_MEMORY_HEADER,),
                drop_contents=(cfg.KICKOFF_RETURNING, cfg.KICKOFF_FIRST),
                drop_prefixes=cfg.EPHEMERAL_PROMPT_PREFIXES,
            )
        # Maps a control-turn's full wrapped message content (what the model
        # sees and what self._messages holds) back to Ant's actual words, so
        # persistence can store the real utterance without ever touching
        # self._messages itself -- see _record_user_turn and
        # _messages_for_persistence.
        self._persisted_text_for_wrapped: dict[str, str] = {}
        self._router = intent_router.IntentRouter()
        self._routing_history: deque[dict] = deque(maxlen=25)
        self._pending_confirmations: dict[str, tuple[str, str, str | None, str]] = {}
        self._confirm_counter = 0
        self._force_confirm = False
        self._force_confirm_reason = ""
        self._last_user_text = ""
        self._control_turn = False
        self._direct_reply: str | None = None
        self._cancel_wait_s = 5.0
        self._recently_denied: deque[tuple[str, str]] = deque(maxlen=5)
        self._recent_delegations: deque[tuple[float, str]] = deque(maxlen=20)
        # A sub-agent that keeps "finishing" by asking permission instead of
        # doing the work (agent_bridge.requests_confirmation) rather than a
        # broken/stuck one that never returns.
        self._agent_confirm_streak: dict[str, int] = {}
        # Whether _handle_delegate_markers actually dispatched a real marker
        # this call -- read by _maybe_correct_fabricated_response right after,
        # so a reply that only talks about agent work is never mistaken for
        # one that triggered it.
        self._last_marker_dispatched = False
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
            default_model_targets=cfg.AGENT_DEFAULT_MODEL_TARGETS,
            workspace_dir=cfg.AGENT_WORKSPACE_DIR,
            scope_preamble=cfg.AGENT_SCOPE_PREAMBLE,
            host_repo=cfg.AGENT_HOST_REPO,
            remotes=self._remotes,
        )
        agent_registry = AgentRegistry(cfg.DATA_DIR / "agent_registry.json")
        self._control_plane = AgentControlPlane(
            build_adapters(self._bridge, cfg.AGENT_BACKENDS, cfg.AGENT_MACHINES),
            registry=agent_registry,
            on_event=self._on_agent_event,
        )
        # Same Local/Cloud/Hybrid orchestration layer the full voice session
        # uses -- brain mode routes delegations through its own copy of the
        # dispatch path below, so without this the orchestrator (and its
        # concurrency/duplicate gate) would simply never run in this mode.
        self._orchestrator = PersonaOrchestrator(
            bridge=self._bridge,
            local_provider=LocalProvider(),
            cloud_provider=CopilotProvider(
                model_map=cfg.COPILOT_MODEL_MAP,
                reasoning_effort=cfg.COPILOT_REASONING_EFFORT or None,
            ),
            telemetry=orchestration_telemetry.TelemetryRecorder(cfg.ORCHESTRATION_TELEMETRY_FILE),
        )
        self._pending_structured_decision = None
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
        # Deviation from task-8-brief.md's suggested placement ("right after
        # self._control_plane = AgentControlPlane(...)"): build_conversation_hub
        # synchronously replays CHANNEL_RESTORED through on_event=_on_agent_event
        # when the durable store already has channels, and _emit unconditionally
        # references self._lifecycle_ws -- constructing the hub any earlier than
        # this raised AttributeError on a non-empty store.
        # The AgentConversationHub shares this exact AgentRegistry instance
        # (not a second one at the same path) so its evidence-based selector
        # reads the same staleness view the control plane just wrote. This is
        # the same hub instance full voice mode builds (same store path), so
        # switching RAP_MODE between restarts never forks conversation state.
        self._conversation_hub = build_app_conversation_hub(
            self._bridge, agent_registry, self._on_agent_event
        )
        self._pending_routing_source: str | None = None
        # A bare correction such as "check again" is meaningful only after a
        # named local liveness request. Keep the target in this session so the
        # correction cannot fall through to model-generated delegation.
        self._last_liveness_agent: str | None = None
        # job_id -> (channel_id, task_id) for every dispatch routed through
        # the conversation hub (see _dispatch_via_hub). _announce_agent_job
        # consults this to relay the hub's own ResultPresenter envelope
        # instead of raw bridge output for exactly those jobs, so a
        # hub-routed completion is never relayed twice.
        self._hub_dispatched_jobs: dict[str, tuple[str, str]] = {}
        # job_id -> the fire-and-forget handle_job_event task _on_agent_event
        # spawned for it. _announce_agent_job awaits the matching entry
        # before reading the hub's turns for a hub-dispatched job, so it
        # never races that task's own append (task-8 review round 1, #5).
        self._hub_job_event_tasks: dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        """Start background services that do not allocate audio/STT/TTS models."""
        self._http = aiohttp.ClientSession()
        if self._lifecycle_ws is not None:
            await self._lifecycle_ws.start()
        self._remotes.start()
        if llm_endpoint.cloud_only_enabled():
            logger.info("Cloud-only model policy active; skipping local LLM warmups")
        else:
            self._spawn(self._router.warmup(), "brain-intent-router-warmup")
            self._spawn(self._warm_chat_model(), "brain-chat-model-warmup")
        # Probe providers once so the orchestration panel shows real status on
        # first view instead of "not checked yet" until someone clicks Check now.
        self._spawn(self._orchestrator.refresh_provider_status(), "brain-provider-probe")

    async def _warm_chat_model(self) -> None:
        """Make the reply model resident before the first turn asks for it.

        The router has always been preloaded; the model that actually answers
        was not, so the first spoken turn of a session paid its whole load.
        """
        model = getattr(self, "_model_override", None) or self._persona.model_name(cfg.LLM_MODEL)
        await asyncio.to_thread(ollama_models.preload, cfg.OLLAMA_HOST, model, cfg.LLM_KEEP_ALIVE)

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
        tasks = [task for task in self._tasks if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        cleanup = [
            ("remote connections", self._remotes.stop),
            ("agent jobs", self._bridge.shutdown),
        ]
        if self._lifecycle_ws is not None:
            cleanup.insert(0, ("lifecycle server", self._lifecycle_ws.stop))
        for label, stop in cleanup:
            try:
                await stop()
            except Exception as exc:
                logger.warning(f"Could not stop {label}: {exc}")
        if self._http is not None:
            try:
                await self._http.close()
            finally:
                self._http = None
        if cfg.MEMORY_ENABLED:
            memory.save_memory(cfg.MEMORY_FILE, self._messages_for_persistence()[-cfg.MEMORY_MAX_MSGS :])

    async def complete(
        self, user_text: str, *, llm_content: str | None = None, delivery: str = "text_only"
    ) -> str:
        """Serialize and process one complete text turn."""
        async with self._turn_lock:
            return await self._complete_unlocked(
                user_text, llm_content=llm_content, delivery=delivery
            )

    async def _complete_unlocked(
        self, user_text: str, *, llm_content: str | None = None, delivery: str = "text_only"
    ) -> str:
        """Process one user text turn and return assistant text.

        Args:
            user_text: What the user actually said or typed. Drives the visible
                transcript, confirmation matching, and delegation routing.
            llm_content: Optional richer body for the model, used when a turn
                carries more than its plain text -- a multimodal bundle, say.
                Kept separate so scaffolding never reaches the transcript or the
                router, which both reason about what the user meant.
            delivery: Whether the caller owns playback or only displays text.
        """
        text = user_text.strip()
        if not text:
            return ""
        utterance = self._new_utterance(text, delivery)
        content = await self._turn_content(text, llm_content)
        if utterance["session_id"] != self._conversation.session_id:
            return ""

        self._record_user_turn(text, content)
        assistant = self._take_direct_reply()
        if assistant is None:
            assistant = await self._call_ollama()
        if utterance["session_id"] != self._conversation.session_id:
            return ""
        assistant = self._handle_delegate_markers(assistant)
        assistant = await self._maybe_correct_fabricated_response(
            assistant, dispatched=self._last_marker_dispatched
        )
        self._finish_turn(assistant, utterance)
        return SpeechText(assistant, utterance)

    def _new_utterance(self, text: str, delivery: str = "text_only") -> dict:
        match = re.match(r"\[\[announce\]\]\s*\[id=([\w:-]+)\]", text)
        source = self._announcement_sources.pop(match[1], {}) if match else {}
        return self._conversation.utterance(delivery=delivery, **source)

    def _record_user_turn(self, text: str, content: str) -> None:
        """Keep the user's wording alongside the outcome of local control actions."""
        if self._control_turn and not text.startswith(ANNOUNCE_PREFIX):
            wrapped = f"User request: {text}\n\nApplication context (not a new request):\n{content}"
            # The model still needs the full wrapped content THIS turn (it's
            # what self._messages holds and _ollama_payload sends verbatim);
            # only what gets PERSISTED should be Ant's actual words, not the
            # framework's scaffolding around them (2026-09-25 Phase B3).
            self._persisted_text_for_wrapped[wrapped] = text
            content = wrapped
            if len(self._persisted_text_for_wrapped) > cfg.MEMORY_MAX_MSGS:
                self._persisted_text_for_wrapped.pop(next(iter(self._persisted_text_for_wrapped)))
        self._messages.append({"role": "user", "content": content})

    def _persisted_view(self, message: dict) -> dict:
        """Swap a control-turn's wrapped content for Ant's real words, unwrapped ones as-is."""
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, str):
            return message
        real_text = self._persisted_text_for_wrapped.get(content)
        return {**message, "content": real_text} if real_text is not None else message

    def _messages_for_persistence(self) -> list[dict]:
        """A copy of self._messages fit to write to disk; self._messages itself is untouched.

        Rewrites a control-turn's wrapped content back to Ant's real words
        (see _record_user_turn), then drops pure framework relays the same
        way every other EPHEMERAL_PROMPT_PREFIXES entry already is.
        """
        rewritten = [self._persisted_view(message) for message in self._messages]
        return memory.strip_ephemeral(
            rewritten,
            system_prefixes=(cfg.MEM0_MEMORY_HEADER,),
            drop_contents=(cfg.KICKOFF_RETURNING, cfg.KICKOFF_FIRST),
            drop_prefixes=cfg.EPHEMERAL_PROMPT_PREFIXES,
        )

    def _finish_turn(self, assistant: str, utterance: dict | None = None) -> None:
        """Record one completed assistant turn in history, memory, and the UI."""
        if utterance and utterance["session_id"] != self._conversation.session_id:
            return
        self._messages.append({"role": "assistant", "content": assistant})
        if cfg.MEMORY_ENABLED:
            memory.save_memory(cfg.MEMORY_FILE, self._messages_for_persistence()[-cfg.MEMORY_MAX_MSGS :])
        self._emit(
            {**(utterance or self._conversation.utterance()), "text": assistant, "final": True}
        )

    async def complete_stream(
        self, user_text: str, *, llm_content: str | None = None
    ) -> AsyncIterator[str]:
        """Serialize a turn and yield its text as it becomes speakable."""
        async with self._turn_lock:
            async for piece in self._complete_stream_unlocked(user_text, llm_content=llm_content):
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
        utterance = self._new_utterance(text, "playback_unconfirmed")
        content = await self._turn_content(text, llm_content)
        if utterance["session_id"] != self._conversation.session_id:
            return
        self._record_user_turn(text, content)

        direct_reply = self._take_direct_reply()
        if direct_reply is not None:
            utterance.update(text=direct_reply, final=False, revision=utterance["revision"] + 1)
            self._emit(dict(utterance))
            yield SpeechText(direct_reply, utterance)
            self._finish_turn(direct_reply, utterance)
            return

        full = ""
        spoken = ""
        pending = ""
        withholding = False
        completed = False
        try:
            async for delta in self._stream_ollama():
                if utterance["session_id"] != self._conversation.session_id:
                    return
                full += delta
                if withholding:
                    continue
                pending += delta
                head = pending.find(_MARKER_START)
                if head != -1:
                    ready, _ = _split_sentences(pending[:head])
                    withholding = True
                else:
                    ready, pending = _split_sentences(pending)
                if ready:
                    spoken += ready
                    utterance.update(text=spoken, final=False, revision=utterance["revision"] + 1)
                    self._emit(dict(utterance))
                    yield SpeechText(ready, utterance)

            cleaned = self._handle_delegate_markers(full)
            corrected = await self._maybe_correct_fabricated_response(
                cleaned, dispatched=self._last_marker_dispatched
            )
            if corrected != cleaned:
                # Sentences may already have streamed out for this turn and
                # cannot be unsaid, so the correction is always spoken/yielded
                # as a follow-up remark rather than folded into the
                # remainder-since-spoken diff below, which would silently
                # discard it whenever anything had already streamed. The
                # persisted record keeps only the honest, corrected text --
                # never the fabrication -- so later turns are never grounded
                # in a claim that was never true.
                remainder = corrected
                cleaned = corrected
            else:
                remainder = (
                    cleaned[len(spoken) :]
                    if cleaned.startswith(spoken)
                    else ("" if spoken else cleaned)
                )
            if remainder.strip():
                utterance.update(text=cleaned, final=False, revision=utterance["revision"] + 1)
                self._emit(dict(utterance))
                yield SpeechText(remainder, utterance)
            self._finish_turn(cleaned, utterance)
            completed = True
        finally:
            if not completed and utterance.get("text"):
                self._emit({**utterance, "final": True, "delivery": "interrupted"})

    async def _turn_content(self, text: str, llm_content: str | None) -> str:
        """Resolve what one user turn puts into history.

        Announce turns bypass routing entirely -- they are the app talking to
        itself about a finished agent job -- while everything else runs the
        normal confirmation and delegation path.
        """
        self._direct_reply = None
        if text.startswith(ANNOUNCE_PREFIX):
            instruction = text[len(ANNOUNCE_PREFIX) :].strip()
            instruction = re.sub(r"^\[id=[\w:-]+\]\s*", "", instruction)
            # The summary narration must never delegate: a marker here spawns a
            # fresh job every time a job finishes, breeding jobs indefinitely.
            self._control_turn = True
            return instruction
        self._emit({"type": "transcript", "role": "user", "text": text})
        self._last_user_text = text
        self._control_turn = False

        if voice_commands.is_local_runtime_time_query(text):
            self._control_turn = True
            self._direct_reply = datetime.now().strftime("It is %I:%M %p on %A, %B %d, %Y, sir.")
            return "[Current local time supplied by the host; do not start any new work.]"

        if voice_commands.is_openclaw_auth_command_request(text, cfg.AGENT_SPOKEN_ALIASES):
            self._control_turn = True
            self._direct_reply = voice_commands.OPENCLAW_OPENAI_REAUTH_GUIDANCE
            return "[OpenClaw authentication command supplied by RAP; do not start any new work.]"

        if self._last_liveness_agent is not None and voice_commands.is_agent_liveness_followup(
            text
        ):
            return await self._handle_agent_rollcall(self._last_liveness_agent)

        if (
            self._last_liveness_agent is not None
            and voice_commands.is_agent_response_check_followup(text)
        ):
            return await self._handle_agent_response_check_followup(self._last_liveness_agent)

        # A followup is only recognized in the turn immediately after a
        # liveness/diagnostic/roll-call question (docstring contract on
        # is_agent_liveness_followup). Anything else consumes the slot so an
        # unrelated later turn -- "I need you to verify this address" -- can
        # never be hijacked into re-checking a stale remembered agent.
        self._last_liveness_agent = None

        diagnostic = voice_commands.parse_agent_diagnostic(text, cfg.AGENT_SPOKEN_ALIASES)
        if diagnostic is not None:
            if diagnostic[0] is not None:
                self._last_liveness_agent = diagnostic[0]
            return await self._handle_agent_diagnostic(*diagnostic)

        # A selection correction must not approve the old backend merely
        # because the utterance begins with "okay".
        if voice_commands.needs_agent_selection(text, cfg.AGENT_BACKENDS, cfg.AGENT_SPOKEN_ALIASES):
            cancel_request = voice_commands.parse_agent_cancel(text, cfg.AGENT_SPOKEN_ALIASES)
            if cancel_request is not None:
                return await self._handle_agent_cancel(cancel_request)
            excluded, different = voice_commands.agent_selection_constraints(
                text, cfg.AGENT_SPOKEN_ALIASES
            )
            if different and not excluded:
                current = self._bridge.latest_active()
                excluded.add(current.agent if current else self._default_agent_backend)
            if voice_commands.parse_agent_rollcall(text, cfg.AGENT_SPOKEN_ALIASES) is not None:
                return await self._handle_agent_rollcall(excluded=excluded)
            self._control_turn = True
            self._direct_reply = (
                "Which agent should I use instead, sir? I have not sent or reassigned any work."
            )
            return "[Agent selection needs a named destination; do not start any new work.]"

        cancel_request = voice_commands.parse_agent_cancel(text, cfg.AGENT_SPOKEN_ALIASES)
        if (
            cancel_request is not None
            and voice_commands.parse_delegation(text, cfg.AGENT_BACKENDS, cfg.AGENT_SPOKEN_ALIASES)
            is not None
        ):
            return await self._handle_agent_cancel(cancel_request)
        redirect_agent = voice_commands.parse_agent_redirect(text, cfg.AGENT_SPOKEN_ALIASES)
        if redirect_agent is not None:
            return await self._handle_agent_redirect(redirect_agent)
        # Before progress or confirmation: "okay, ping Codex" is a local
        # check, not approval of an unrelated pending delegation.
        rollcall = voice_commands.parse_agent_rollcall(text, cfg.AGENT_SPOKEN_ALIASES)
        if rollcall is not None:
            if rollcall[0] is not None:
                self._last_liveness_agent = rollcall[0]
            return await self._handle_agent_rollcall(rollcall[0])
        status_request = voice_commands.parse_agent_status(text, cfg.AGENT_SPOKEN_ALIASES)
        if status_request is not None:
            return await self._handle_agent_status(status_request)
        consumed = self._maybe_consume_confirmation(text)
        if consumed is not None:
            # A real dispatch (or denial) already happened deterministically;
            # the model is only being asked to narrate it. Left False, the
            # narration reply is free to invent its OWN [[delegate:]] marker
            # for the same task it is merely acknowledging, redispatching it
            # a second time (live failure: approving "ping each agent" ran it
            # twice, the second time from the acknowledgment reply itself).
            self._control_turn = True
            return consumed
        if cancel_request is not None:
            return await self._handle_agent_cancel(cancel_request)
        parsed = await self._resolve_delegation(text)
        if parsed is not None:
            agent, task = parsed
            logger.info(f"Brain delegation -> [{agent}] {task}")
            # Same reasoning as the confirmation branch above: this already
            # dispatched for real, so the reply generated from _delegate_ack's
            # framing must not be free to dispatch it again via its own marker.
            self._control_turn = True
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
            logger.warning(
                f"Voice cancel still running after {self._cancel_wait_s}s; backgrounding"
            )
            self._direct_reply = (
                "I requested cancellation, sir, but an agent is slow to stop. "
                "Cancellation is still running in the background."
            )
            return (
                "[Agent update: cancellation is underway but at least one agent is slow "
                "to stop; it will finish in the background. Tell the user briefly; "
                "do not start any new work.]"
            )
        logger.info(f"Voice cancel -> agent={agent} all={all_jobs} cancelled={count}")
        if count:
            noun = "task" if count == 1 else "tasks"
            self._direct_reply = f"I cancelled {count} active {noun}, sir."
            return (
                f"[Agent update: cancelled {count} active {noun}. "
                "Confirm briefly; do not start any new work.]"
            )
        self._direct_reply = "I found no matching active tasks to cancel, sir."
        return (
            "[Agent update: there were no matching active tasks to cancel. "
            "Tell the user briefly; do not start any new work.]"
        )

    async def _handle_agent_rollcall(
        self, agent: str | None = None, *, excluded: set[str] | None = None
    ) -> str:
        """Answer "which agents are there?" from what RAP itself knows.

        An agent cannot report on its peers -- asked to, it guesses, and a
        guess delivered in the butler's voice reads exactly like a fact. RAP
        knows which backends are configured, whether each one can be launched,
        which remote machines are answering, and what is running right now, so
        the roll call is answered here and never delegated.
        """
        self._control_turn = True
        rows, missing = await agent_status.collect_rollcall_rows(
            self._control_plane, agent, excluded=frozenset(excluded or ())
        )
        if missing is not None:
            self._direct_reply = f"I could not check another agent: {missing}."
        else:
            self._direct_reply = f"I checked through Remote Agent Protocol: {'; '.join(rows)}."
        return agent_status.format_rollcall(rows, missing)

    async def _handle_agent_diagnostic(self, agent: str | None, actual_response: bool) -> str:
        """Report RAP-owned evidence and optionally start fixed-response checks."""
        self._control_turn = True
        rows, missing = await agent_status.collect_diagnostic_rows(
            self._control_plane, agent, actual_response=actual_response
        )
        if missing is not None:
            self._direct_reply = f"I could not run an agent diagnostic: {missing}."
        else:
            self._direct_reply = (
                f"Agent diagnostic: {'; '.join(rows)}. {agent_status.DIAGNOSTIC_LIMITATION}"
            )
        return agent_status.format_diagnostic(rows, missing)

    async def _handle_agent_redirect(self, new_agent: str) -> str:
        """Move the current RAP-owned job through the coordinator, never another harness."""
        self._control_turn = True
        current = self._bridge.latest_active()
        if current is None:
            self._direct_reply = "There is no active RAP-owned task to redirect, sir."
            return "[No active RAP-owned task exists; do not start any new work.]"
        result = await self._control_plane.redirect_job(
            current.job_id,
            new_agent,
            AgentTask(current.task, cwd=current.cwd or None, announce_start=current.announce_start),
        )
        if isinstance(result, JobHandle):
            self._direct_reply = (
                f"I redirected the task from {current.agent} to {result.agent_id}, sir."
            )
            return (
                "[RAP completed the ordered cancellation and redirection; do not start new work.]"
            )
        assert isinstance(result, ControlResult)
        detail = result.error.detail if result.error else "the coordinator could not redirect it"
        self._direct_reply = f"I could not redirect the task: {detail}"
        return "[RAP attempted the requested redirection; do not start new work.]"

    async def _handle_agent_status(self, status_request: tuple[str | None]) -> str:
        """Answer a progress question from live job state instead of delegating.

        Live sessions showed "can I get an update?" spawning a fresh job per
        polite follow-up; status is a read, never a write.
        """
        (agent,) = status_request
        self._control_turn = True
        if agent is not None:
            snapshot = await self._control_plane.get_agent_status(agent, refresh=False)
            summary = agent_status.control_summary(agent, snapshot)
            self._direct_reply = f"{summary}."
            return f"[Agent status: {summary}. Answer from this; do not start any new work.]"
        results = await self._control_plane.list_agents(refresh=False)
        summary = "; ".join(
            agent_status.control_summary(backend, snapshot) for backend, snapshot in results.items()
        )
        self._direct_reply = f"{summary}."
        return f"[Agent status: {summary}. Answer from this; do not start any new work.]"

    async def _handle_agent_response_check_followup(self, agent: str) -> str:
        """Answer a check-state clarification from RAP's recorded evidence."""
        self._control_turn = True
        snapshot = await self._control_plane.get_agent_status(agent, refresh=False)
        self._direct_reply = agent_status.explain_response_check(agent, snapshot)
        return "[RAP supplied the current fixed-response-check state; do not start any new work.]"

    def _take_direct_reply(self) -> str | None:
        """Return one evidence-bound reply without giving the chat model room to alter it."""
        reply = self._direct_reply
        self._direct_reply = None
        return reply

    async def _resolve_delegation(self, text: str) -> tuple[str, str] | None:
        decision = await self._router.route(text, self._default_agent_backend)
        self._record_routing(decision)
        if decision.action == intent_router.ACTION_NONE:
            self._pending_structured_decision = None
            self._pending_routing_source = None
            await self._route_chat_turn_through_hub(text)
            return None
        self._force_confirm = decision.action == intent_router.ACTION_CONFIRM
        self._force_confirm_reason = decision.reason if self._force_confirm else ""
        # UNDERSTAND + ROUTE: may escalate orchestration reasoning (never the
        # task execution itself) to the cloud provider. _delegate_ack consumes
        # this structured decision rather than re-inferring the harness.
        structured = await self._orchestrator.evaluate(
            decision, OrchestrationContext(persona=self._persona.name)
        )
        self._pending_structured_decision = structured
        self._pending_routing_source = decision.source
        logger.info(
            f"Brain orchestration[{structured.route}] risk={structured.risk_score:.2f}"
            f" harness={structured.target_harness} reason={structured.reason_summary!r}"
        )
        return structured.target_harness, structured.task

    async def _route_chat_turn_through_hub(self, text: str) -> None:
        """Record a non-delegating turn with Butler so floor/transcript stay current.

        Shared with ``VoiceSession`` (see ``conversation_dispatch``); pure
        in-memory bookkeeping that must never break the ordinary chat
        pipeline that follows.
        """
        await conversation_dispatch.route_chat_turn_through_hub(
            self._conversation_hub, text, butler_id=BUTLER_ID
        )

    # -- persona orchestration (Local / Cloud / Hybrid) ----------------------

    def orchestration_status(self) -> dict:
        """Synchronous snapshot for the UI -- never touches the network itself."""
        return self._orchestrator.status()

    async def refresh_provider_status(self) -> dict:
        """Re-probe both providers; the result lands on the next UI poll."""
        return await self._orchestrator.refresh_provider_status()

    def set_orchestration_mode(self, mode: str) -> None:
        """Change the global orchestration mode; invalid values are logged and ignored."""
        try:
            self._orchestrator.set_mode(mode)
        except ValueError as exc:
            logger.warning(str(exc))

    def set_orchestration_quota_strategy(self, strategy: str) -> None:
        """Change the quota strategy; invalid values are logged and ignored."""
        try:
            self._orchestrator.set_quota_strategy(strategy)
        except ValueError as exc:
            logger.warning(str(exc))

    def set_persona_orchestration_override(self, persona: str, mode: str | None) -> None:
        """Set (``mode``) or clear (``None``) one persona's mode override."""
        try:
            self._orchestrator.set_persona_mode(persona, mode)
        except ValueError as exc:
            logger.warning(str(exc))

    def _gate_dispatch(self, agent: str, task: str, structured=None) -> str | None:
        """Global/per-harness concurrency + duplicate-task admission.

        The same centralized check ``session.VoiceSession`` applies, so brain
        mode cannot pile up duplicate or over-cap jobs either. Returns ``None``
        when the dispatch may proceed, else a human-readable denial reason.
        """
        if structured is not None:
            allowed, reason = self._orchestrator.admit(structured)
        else:
            allowed, reason = self._orchestrator.admit_by_task(agent, task)
        if allowed:
            return None
        logger.info(f"Orchestrator withheld delegation [{agent}]: {task} ({reason})")
        return reason

    def _record_routing(self, decision: intent_router.RoutingDecision) -> None:
        row = asdict(decision)
        self._routing_history.append(row)
        self._emit({"type": "routing", **row})
        logger.info(
            f"Brain routing[{decision.source}] {decision.action} "
            f"intent={decision.intent} confidence={decision.confidence:.2f} "
            f"reason={decision.reason!r}"
        )

    def _delegate_ack(
        self, agent: str, task: str, cwd: str | None = None, *, explicit: bool = False
    ) -> str:
        """Dispatch or hold a delegation.

        ``explicit`` marks a caller that already named a specific, certain
        target -- the GUI's manual "Delegate to X" button -- bypassing
        _resolve_delegation entirely. Without it such a call would fall
        through to the hub's evidence-based selection (case 3) like any
        other no-fresh-decision dispatch, silently overriding the agent the
        user explicitly picked.
        """
        force_confirm, self._force_confirm = self._force_confirm, False
        forced_reason, self._force_confirm_reason = self._force_confirm_reason, ""
        # Set only when this call came from _resolve_delegation's real routing
        # decision this turn; None for the LLM-marker caller below, which
        # reaches this method directly with no fresh routing decision.
        structured, self._pending_structured_decision = self._pending_structured_decision, None
        source, self._pending_routing_source = self._pending_routing_source, None
        destructive = cfg.AGENT_CONFIRM_ENABLED and voice_commands.requires_confirmation(
            agent, task, destructive_words=cfg.AGENT_DESTRUCTIVE_WORDS
        )
        if force_confirm or destructive:
            reason = (
                forced_reason
                or "this task changes files, installs software, or otherwise mutates the system"
            )
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
        # Concurrency/duplicate admission immediately before the real dispatch
        # (never before a confirmation hold -- a held task may never run).
        # This still gates on the router's own resolved (agent, task) proxy
        # even when the hub ends up choosing a different agent below (case
        # 3) -- the same admission check that ran here before the hub existed.
        deny_reason = self._gate_dispatch(agent, task, structured)
        if deny_reason is not None:
            return (
                f"[Not dispatched -- {deny_reason}. Tell the user in ONE short sentence "
                "why this wasn't started.]"
            )
        self._remember_delegation(task)
        # The explicit_agent_id rule (task-8-brief.md): ACTION_NONE never
        # reaches here (handled in _resolve_delegation); a fresh "explicit"
        # routing decision keeps its resolved target; everything else
        # (including a marker/self-repair guess with no fresh decision this
        # turn) defers to the hub's evidence-based selection.
        explicit_agent_id = (
            agent
            if explicit
            else (
                structured.target_harness
                if source == "explicit" and structured is not None
                else None
            )
        )
        dispatch_text = structured.task if structured is not None else task
        self._spawn(
            self._dispatch_via_hub(
                explicit_agent_id, self._with_delegation_context(dispatch_text), cwd=cwd
            ),
            f"brain-delegate-{agent}",
        )
        if explicit_agent_id is not None:
            return cfg.DELEGATION_ACK_PROMPT.format(agent=agent, task=task)
        return cfg.DELEGATION_ACK_PENDING_SELECTION_PROMPT.format(task=task)

    async def _dispatch_via_hub(
        self, explicit_agent_id: str | None, text: str, *, cwd: str | None = None
    ) -> None:
        """Route one already-admitted dispatch through the shared conversation hub.

        No current caller supplies a non-``None`` cwd -- every path that
        reaches here (``_delegate_ack``, the GUI's "Delegate to X" button,
        confirmation approval) defaults it to ``None``, and
        ``ConversationTurnRequest`` has no field to carry one, so it is
        dropped defensively rather than silently (see
        ``conversation_dispatch.warn_if_cwd_unsupported``).
        """
        conversation_dispatch.warn_if_cwd_unsupported(cwd)
        await conversation_dispatch.dispatch_via_hub(
            self._conversation_hub,
            self._hub_dispatched_jobs,
            explicit_agent_id,
            text,
            on_no_dispatch=self._relay_no_dispatch,
        )

    async def _relay_no_dispatch(self, disposition: TurnDisposition) -> None:
        """Relay the hub's no-dispatch explanation as a Brain-mode message.

        Brain mode has no TTS, so a dispatch that resolved to no dispatch
        after the ack already told the user work was starting (see
        ``_dispatch_via_hub``) becomes a message-history entry instead of
        speech -- the same pattern ``_relay_hub_intervention`` uses for a
        hub failure/stall (task-8 review round 1, #1).
        """
        explanation = present_no_dispatch_explanation(disposition.spoken_acknowledgment)
        self._messages.append({"role": "assistant", "content": explanation})

    async def _hold_agent_confirmation(self, job: agent_bridge.AgentJob, prompt_text: str) -> None:
        """A sub-agent "finished" by asking permission instead of a real result.

        Ported from VoiceSession._hold_agent_confirmation (session.py). Some
        backends are one-shot CLIs: the process already exited, so there is no
        live task to resume. Register a fresh pending confirmation -- the same
        mechanism used for our own pre-dispatch gate -- so a "confirm" (voice
        or the GUI's resolve_confirmation) relaunches the task; the relaunch
        text notes the approval so the agent does not just ask again
        immediately. If the same agent keeps doing this with no real result in
        between, stop looping and tell the user instead, same as session.py.

        Fixed prose rather than a fresh model call, matching the established
        pattern for async job-completion messages in this file (see
        _relay_no_dispatch/_relay_hub_intervention above): this runs from an
        arbitrary event-handler context outside _turn_lock, so calling
        _call_ollama here would race an in-progress turn.
        """
        streak = self._agent_confirm_streak.get(job.agent, 0) + 1
        self._agent_confirm_streak[job.agent] = streak
        if streak > cfg.AGENT_CONFIRM_LOOP_LIMIT:
            logger.warning(
                f"Agent '{job.agent}' asked for confirmation {streak} times in a row "
                f"with no result; giving up: {job.task!r}"
            )
            self._messages.append(
                {
                    "role": "assistant",
                    "content": (
                        f"[Agent update: '{job.agent}' keeps asking for confirmation on "
                        "the same task instead of doing it, and may be stuck. Trying a "
                        "different agent or rephrasing the request may help.]"
                    ),
                }
            )
            return
        self._confirm_counter += 1
        token = f"agent-confirm-{self._confirm_counter}"
        approved_task = (
            f"{job.task}\n\nThe user has already confirmed this action -- proceed "
            "without asking again."
        )
        mid_task_reason = "the agent needs your OK before continuing"
        self._pending_confirmations[token] = (job.agent, approved_task, job.cwd, mid_task_reason)
        self._emit(
            {
                "type": "agent_confirm",
                "token": token,
                "agent": job.agent,
                "task": job.task,
                "machine": self._bridge.machine_for(job.agent),
                "reason": mid_task_reason,
                "transcript": prompt_text,
            }
        )
        logger.info(f"Agent '{job.agent}' requested confirmation mid-task; holding: {job.task}")
        self._messages.append(
            {
                "role": "assistant",
                "content": (
                    f"[Agent update: '{job.agent}' needs your OK before continuing with "
                    f"'{job.task}'. Say or select 'confirm' to proceed, or 'cancel' to stop.]"
                ),
            }
        )

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
            deny_reason = self._gate_dispatch(agent, task)
            if deny_reason is not None:
                return f"[Not dispatched -- {deny_reason}.]"
            self._remember_delegation(task)
            # The confirmation prompt already named this specific agent to
            # the user, so approval dispatches to it directly rather than
            # deferring to the hub's evidence-based selection.
            self._spawn(
                self._dispatch_via_hub(agent, self._with_delegation_context(task), cwd=cwd),
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

    def _emit_confirm_resolved(
        self, token: str, agent: str, task: str, reason: str, decision: str
    ) -> None:
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

    def _endpoints(self) -> tuple[llm_endpoint.Endpoint, ...]:
        """Where to send this turn: the cloud persona first, then this machine's."""
        if llm_endpoint.cloud_only_enabled():
            return llm_endpoint.chain(
                llm_endpoint.BRAIN, cloud_model=getattr(self, "_cloud_model_override", None)
            )
        local_model = getattr(self, "_model_override", None) or self._persona.model_name(
            cfg.LLM_MODEL
        )
        return llm_endpoint.chain(llm_endpoint.BRAIN, local_model=local_model)

    def _ollama_payload(self, *, stream: bool, endpoint: llm_endpoint.Endpoint) -> dict:
        payload = {
            "model": endpoint.model,
            "messages": [
                {"role": "system", "content": self._system_instruction()},
                *self._messages[-cfg.MEMORY_MAX_MSGS :],
            ],
            "stream": stream,
        }
        if endpoint.cloud:
            # keep_alive and reasoning_effort are Ollama's own extensions; a
            # hosted API rejects unknown fields rather than ignoring them.
            # max_tokens is not a style choice here: providers reserve a
            # request's maximum possible cost up front, so an uncapped reply is
            # refused outright unless the balance could cover the model running
            # to its full output length.
            payload["max_tokens"] = cfg.CLOUD_LLM_MAX_TOKENS
            return payload
        payload["keep_alive"] = cfg.LLM_KEEP_ALIVE
        if cfg.LLM_REASONING_EFFORT is not None:
            payload["reasoning_effort"] = cfg.LLM_REASONING_EFFORT
        return payload

    async def _stream_ollama(self) -> AsyncIterator[str]:
        """Yield assistant text deltas, falling back to the local model if needed.

        A fallback only happens before the first delta. Once the user is hearing
        a reply, restarting it on another model would talk over itself, so a
        failure that late is raised rather than papered over.
        """
        endpoints = self._endpoints()
        failure: Exception | None = None
        for index, endpoint in enumerate(endpoints):
            spoke = False
            try:
                async for delta in self._stream_from(endpoint):
                    spoke = True
                    yield delta
                return
            except Exception as exc:
                if spoke or index + 1 >= len(endpoints):
                    raise
                failure = exc
                logger.warning(
                    f"{endpoint.label} did not answer ({exc}); "
                    f"falling back to {endpoints[index + 1].label}"
                )
        if failure is not None:
            raise failure

    async def _stream_from(self, endpoint: llm_endpoint.Endpoint) -> AsyncIterator[str]:
        """Yield assistant text deltas from one endpoint."""
        if self._http is None:
            raise RuntimeError("BrainSession.start() was not called")
        payload = self._ollama_payload(stream=True, endpoint=endpoint)
        timeout = cfg.CLOUD_LLM_TIMEOUT_SECS if endpoint.cloud else 120
        try:
            async with self._http.post(
                endpoint.chat_url, json=payload, headers=endpoint.headers, timeout=timeout
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise RuntimeError(f"{endpoint.label} chat failed {resp.status}: {body}")
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
            raise LLMUnavailable(f"{endpoint.base_url} is not reachable: {exc}") from exc
        if not endpoint.cloud:
            await self._refresh_ollama_keep_alive(payload["model"])

    async def _call_ollama(self) -> str:
        """One non-streamed reply, falling back to the local model if needed."""
        endpoints = self._endpoints()
        for index, endpoint in enumerate(endpoints):
            try:
                return await self._call_endpoint(endpoint)
            except Exception as exc:
                if index + 1 >= len(endpoints):
                    raise
                logger.warning(
                    f"{endpoint.label} did not answer ({exc}); "
                    f"falling back to {endpoints[index + 1].label}"
                )
        raise LLMUnavailable("no model endpoint is configured")

    async def _call_endpoint(self, endpoint: llm_endpoint.Endpoint) -> str:
        if self._http is None:
            raise RuntimeError("BrainSession.start() was not called")
        payload = self._ollama_payload(stream=False, endpoint=endpoint)
        timeout = cfg.CLOUD_LLM_TIMEOUT_SECS if endpoint.cloud else 120
        try:
            async with self._http.post(
                endpoint.chat_url, json=payload, headers=endpoint.headers, timeout=timeout
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    raise RuntimeError(f"{endpoint.label} chat failed {resp.status}: {body}")
                data = await resp.json()
        except aiohttp.ClientConnectionError as exc:
            raise LLMUnavailable(f"{endpoint.base_url} is not reachable: {exc}") from exc
        if not endpoint.cloud:
            await self._refresh_ollama_keep_alive(payload["model"])
        return str(data.get("choices", [{}])[0].get("message", {}).get("content", "")).strip()

    async def _refresh_ollama_keep_alive(self, model: str) -> None:
        """Apply Ollama residency when the OpenAI-compatible endpoint ignores it."""
        if not cfg.LLM_KEEP_ALIVE:
            return
        try:
            payload = {
                "model": model,
                "prompt": "",
                "stream": False,
                "keep_alive": cfg.LLM_KEEP_ALIVE,
            }
            async with self._http.post(
                f"{cfg.OLLAMA_HOST}/api/generate", json=payload, timeout=15
            ) as resp:
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
                hermes_note=cfg.HERMES_GENDER_NOTE
                if "hermes" in self._default_agent_backend
                else "",
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
        return (
            f"{task}\n\n[Untrusted conversation context: reference only.]\n"
            + "\n".join(rows)[-1600:]
        )

    @staticmethod
    def _delegation_key(task: str) -> str:
        return " ".join(task.casefold().split())

    def _remember_delegation(self, task: str) -> None:
        key = self._delegation_key(task)
        if key:
            self._recent_delegations.append((time.monotonic(), key))

    async def _persist_job(self, job: agent_bridge.AgentJob) -> None:
        if job.internal:
            return
        await asyncio.to_thread(
            job_store.append_job,
            cfg.AGENT_HISTORY_FILE,
            job_store.job_to_row(job),
            cfg.AGENT_HISTORY_MAX,
        )

    async def _announce_agent_job(self, job: agent_bridge.AgentJob) -> None:
        if job.internal:
            return
        # A sub-agent can "finish" by asking permission instead of returning
        # a real result (e.g. hermes-yolo completing with "Requesting
        # confirmation to proceed"). Ported from VoiceSession._announce_agent_job
        # (session.py) -- brain.py had no equivalent, so this was relayed as a
        # genuine completed answer with no way to actually approve it
        # (documented for session.py as jess_runtime.log 2026-07-06 18:26:
        # saying "confirm" after this happened did nothing at all; the same
        # gap was never ported here).
        confirmation_prompt = agent_bridge.requests_confirmation(job)
        if confirmation_prompt is not None:
            await self._hold_agent_confirmation(job, confirmation_prompt)
            return
        self._agent_confirm_streak.pop(job.agent, None)
        # TRACK -> RELAY: telemetry close-out. A no-op for jobs the
        # orchestrator did not route (e.g. a manual GUI dispatch).
        self._orchestrator.record_outcome(job)
        # A job routed through the conversation hub already has its result
        # recorded as a ConversationTurn with the hub's own ResultPresenter
        # envelope; relay from that instead of re-deriving a presentation
        # from raw bridge output here (the divergence Task 7 removed). Only
        # jobs dispatched via _dispatch_via_hub populate this map.
        hub_route = self._hub_dispatched_jobs.pop(job.job_id, None)
        relayed_from_hub = False
        if hub_route is not None and job.status == agent_bridge.STATUS_DONE:
            # _on_agent_event spawned handle_job_event fire-and-forget for
            # this same terminal event; without awaiting it here, a fast
            # path (e.g. persistence/commons disabled, so no earlier real
            # await yielded the loop) can reach this lookup before that task
            # has appended the agent turn, and silently fall back to raw
            # job.result below (task-8 review round 1, #5).
            pending = self._hub_job_event_tasks.get(job.job_id)
            if pending is not None and not pending.done():
                await pending
            channel_id, task_id = hub_route
            turn = next(
                (
                    candidate
                    for candidate in reversed(self._conversation_hub.turns(channel_id))
                    if candidate.task_id == task_id and candidate.speaker_role == "agent"
                ),
                None,
            )
            if turn is not None:
                presentation = present_hub_result(turn, for_brain=True)
                self._messages.append({"role": "assistant", "content": presentation.brain_text})
                relayed_from_hub = True
        if not relayed_from_hub and job.result:
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
                "failure_detail": job.failure_detail,
                # A result lands whenever the job happens to finish, which is
                # routinely a turn or two later and about something the user has
                # moved on from. Without the task, the spoken relay has no
                # subject and arrives as "it delivered nothing" about nothing.
                "task": job.task,
            }
        )

    def _on_agent_event(self, event: dict) -> None:
        if event.get("type") == "agent_job":
            self._spawn(
                self._control_plane.ingest_bridge_event(event),
                name=f"control-plane-{event.get('job_id', 'event')}",
            )
            if event.get("internal"):
                # A fixed response check is control-plane evidence, never a
                # conversation result.  In particular, fresh bridge job IDs
                # can resemble persisted IDs after a restart, so sending an
                # internal check through the hub could revive unrelated work.
                return
            # Independent state machine from the control plane above, over
            # the same bridge events.
            job_id = event.get("job_id")
            hub_task = asyncio.create_task(
                self._conversation_hub.handle_job_event(event),
                name=f"conversation-hub-{job_id or 'event'}",
            )
            self._tasks.add(hub_task)
            hub_task.add_done_callback(self._tasks.discard)
            if job_id:
                self._hub_job_event_tasks[job_id] = hub_task
                hub_task.add_done_callback(
                    lambda _task, jid=job_id: self._hub_job_event_tasks.pop(jid, None)
                )
        self._emit(event)
        if event.get("type") == "agent_conversation" and event.get("event") == (
            BUTLER_INTERVENTION_STARTED
        ):
            self._spawn(
                self._relay_hub_intervention(event),
                name=f"hub-intervention-{event.get('task_id', '')}",
            )

    async def _relay_hub_intervention(self, event: dict) -> None:
        """Append Butler's composed recovery line for a hub failure/stall.

        Brain mode has no TTS, so this becomes a message-history entry
        instead of speech -- the same ``present_butler_intervention``
        composition voice mode speaks, shared so both modes narrate the
        same recovery language for the same failure.
        """
        task_id = event.get("task_id") or None
        task_ref = self._conversation_hub.task(task_id) if task_id else None
        agent_id = task_ref.agent_id if task_ref is not None else ""
        line = present_butler_intervention(
            event.get("data") or {}, agent_id=agent_id, detail=event.get("detail", "")
        )
        self._messages.append({"role": "assistant", "content": line})

    def _emit(self, event: dict) -> None:
        event = self._conversation.stamp(event)
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
                if self._recently_delegated(task):
                    logger.info(
                        f"Ignoring duplicate LLM delegation marker for handled task: {task!r}"
                    )
                    return ""
                agent, explicit = self._marker_backend(task)
                logger.info(f"Brain LLM delegation marker -> [{agent}] {task}")
                self._delegate_ack(agent, task, explicit=explicit)
            return ""

        cleaned = _MARKER_RE.sub(replace, text).strip()
        self._last_marker_dispatched = bool(dispatched)
        return cleaned or "I sent that to the agent."

    def _recently_delegated(self, task: str) -> bool:
        """True if an equivalent marker task was dispatched within the TTL window.

        Ported from VoiceSession._recently_delegated (session.py). brain.py
        already wrote to _recent_delegations (via _remember_delegation, called
        from _delegate_ack) but never read it back, so nothing here ever
        actually deduped a repeated marker across separate LLM responses.
        """
        key = self._delegation_key(task)
        if not key:
            return False
        now = time.monotonic()
        ttl = max(30.0, cfg.AGENT_PROGRESS_INTERVAL_SECS * 3)
        while self._recent_delegations and now - self._recent_delegations[0][0] > ttl:
            self._recent_delegations.popleft()
        return any(recent_key == key for _, recent_key in self._recent_delegations)

    async def _maybe_correct_fabricated_response(self, text: str, *, dispatched: bool) -> str:
        """Replace a reply that claims agent work happened but never really did.

        Ported from VoiceSession._on_llm_response (session.py) -- BrainSession
        had no equivalent, so the persona was free to fabricate a complete
        "[Agent result from X: ...]"-shaped exchange with nothing behind it.
        Live incident (jess_runtime.log 2026-09-21 18:51-18:53): three
        delegation decisions were logged, zero real dispatches ever happened,
        and the persona invented three separate fake Hermes replies in a row;
        the user had to catch it turn by turn ("You didn't even talk to
        Hermes.").

        Unlike session.py, which injects the correction as a new async turn
        via _inject_and_run, brain.py's turn is still in flight here (this
        runs inside the same _turn_lock as the reply it is checking), so the
        correction is produced by calling the model again in place, grounded
        in the real hold this time instead of the fabricated one.
        """
        if dispatched or self._control_turn:
            return text
        if self._pending_confirmations or self._bridge.has_active():
            return text
        if not looks_like_delegation_promise(text):
            return text
        request = self._last_user_text.strip()
        if not request:
            logger.warning(f"LLM promised agent work without a request to dispatch: {text!r}")
            return text
        logger.warning(
            f"LLM promised agent work without a marker; correcting fabricated reply: {request!r}"
        )
        self._force_confirm = True
        self._force_confirm_reason = (
            "the assistant talked about agent work without a valid delegation marker"
        )
        agent, _explicit = self._marker_backend(request)
        ack = self._delegate_ack(agent, request)
        self._messages.append({"role": "user", "content": ack})
        return await self._call_ollama()

    def _marker_backend(self, task: str) -> tuple[str, bool]:
        """Prefer the agent the user actually named over the configured default.

        Markers carry a task but no agent, so "maybe code puppy can fix it"
        must not silently dispatch to whatever the default backend is.
        """
        agent = intent_router.select_marker_backend(
            self._last_user_text, task, self._default_agent_backend
        )
        named = voice_commands.named_backend(
            f"{self._last_user_text} {task}", cfg.AGENT_BACKENDS, cfg.AGENT_SPOKEN_ALIASES
        )
        # A marker is generated after the normal router has already declined
        # the spoken turn. Preserve a clear, named executor here; otherwise
        # the conversation hub sees no target and may convert the promised
        # dispatch into a no-op.
        return agent, named == agent


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

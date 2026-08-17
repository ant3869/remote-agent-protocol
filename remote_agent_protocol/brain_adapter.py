"""GUI session adapter for RAP brain-only mode.

The web GUI expects the richer ``VoiceSession`` control surface. In brain mode we
keep the same GUI but swap local mic/STT/TTS for a text-only ``BrainSession`` so
an external realtime frontend can own ears and mouth without creating a second
agent brain.
"""

from __future__ import annotations

import asyncio
import json
import queue
import time
from collections.abc import Iterator
from concurrent.futures import Future
from pathlib import Path
from typing import Any

from loguru import logger

from remote_agent_protocol import config as cfg
from remote_agent_protocol import job_store, voices
from remote_agent_protocol.brain import ANNOUNCE_PREFIX, BrainSession
from remote_agent_protocol.multimodal_prompt import MultimodalPromptBundle
from remote_agent_protocol.personas import Persona


class BrainSessionAdapter:
    """Small compatibility facade exposing the GUI-facing VoiceSession subset."""

    def __init__(self, persona: Persona, on_event=None):
        """Create a GUI-compatible facade around one text-only brain session."""
        self._on_event = on_event
        self._brain = BrainSession(persona, on_event=self._observe_event)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._muted = True
        self._voice_mode = "manual"
        self._model_override = persona.model_name(cfg.LLM_MODEL)

    def build(self) -> None:
        """VoiceSession compatibility hook; brain mode has no audio graph."""

    async def run(self) -> None:
        """Start brain services and wait until ``shutdown`` is called."""
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        self._discard_stale_announcements()
        await self._brain.start()
        self._emit({"type": "session", "state": "ready"})
        self._emit({"type": "sys", "text": "Brain mode ready; realtime audio is external."})
        try:
            await self._stop.wait()
        finally:
            await self._brain.stop()
            self._loop = None
            self._stop = None

    def shutdown(self) -> None:
        """Stop the brain loop from any GUI/server thread."""
        if self._loop is None or self._stop is None:
            return
        self._loop.call_soon_threadsafe(self._stop.set)

    def complete_text(self, text: str, timeout: float = 180.0) -> str:
        """Run one user text turn and return assistant text for realtime frontends."""
        future = self._submit(self._complete_with_activity(text))
        return str(future.result(timeout=timeout))

    def stream_text(self, text: str, timeout: float = 180.0) -> Iterator[str]:
        """Yield assistant text as it is generated, for realtime frontends.

        A spoken turn cannot wait for a finished reply, so this hands each
        speakable piece to the caller the moment the model produces it. The
        brain runs on its own loop, so pieces cross over via a queue.
        """
        pieces: queue.Queue = queue.Queue()
        done = object()

        async def pump() -> None:
            try:
                self._emit({"type": "turn", "event": "user_stopped"})
                async for piece in self._brain.complete_stream(text):
                    pieces.put(piece)
            except Exception as exc:  # surfaced on the consuming thread
                pieces.put(exc)
            finally:
                pieces.put(done)

        self._submit(pump())
        while True:
            piece = pieces.get(timeout=timeout)
            if piece is done:
                return
            if isinstance(piece, Exception):
                raise piece
            yield piece

    def send_text(self, text: str) -> None:
        """Submit one typed user turn without blocking the GUI thread."""
        self._submit(self._complete_with_activity(text))

    def send_multimodal_prompt(self, bundle: MultimodalPromptBundle) -> None:
        """Send one bundle: the request drives the UI, the render feeds the model."""
        summary = (
            bundle.final_user_instruction or bundle.text.edited_text or bundle.text.raw_text
        ).strip()
        # The render is never empty -- it always lays out its "(none)" sections --
        # so an empty turn has to be judged on the material the user supplied.
        if not summary and not bundle.attachments and not bundle.voice.transcript.strip():
            return
        content = bundle.agent_prompt()
        self._submit(
            self._complete_with_activity(
                summary or "Shared multimodal prompt", llm_content=content
            )
        )

    def speak_text(self, text: str) -> None:
        """Report text that brain mode cannot send to verbatim external TTS."""
        # The external frontend owns the speakers and exposes no verbatim-TTS
        # entry point, so brain mode can only report the phrase, never voice it.
        # Log it as a system note rather than a fake assistant turn, which would
        # otherwise desync the transcript from what was actually said aloud.
        if text.strip():
            self._emit({"type": "sys", "text": f"(not spoken in brain mode) {text.strip()}"})

    def announce_text(self, text: str) -> None:
        """Report an announcement through the brain-mode text fallback."""
        self.speak_text(text)

    def start_agent_task(self, agent: str, task: str, cwd: str | None = None) -> None:
        """Start an agent job through the brain event loop."""
        self._submit(self._start_agent_task(agent, task, cwd))

    def approve_agent_task(self, token: str) -> None:
        """Approve the pending agent confirmation identified by token."""
        self._submit(self._resolve_confirmation(token, "approve"))

    def deny_agent_task(self, token: str) -> None:
        """Deny the pending agent confirmation identified by token."""
        self._submit(self._resolve_confirmation(token, "deny"))

    async def _resolve_confirmation(self, token: str, decision: str) -> None:
        """Answer the confirmation the button names, not whichever is newest."""
        reply = self._brain.resolve_confirmation(token, decision)
        if reply is None:
            logger.warning(f"Confirmation {token!r} is unknown or already resolved")
            return
        self._brain._messages.append({"role": "assistant", "content": reply})  # noqa: SLF001
        self._emit({"type": "transcript", "role": "assistant", "text": reply})

    def cancel_agent_task(self, job_id: str) -> None:
        """Request cancellation of one active agent job."""
        self._submit(self._brain._bridge.cancel(job_id))  # noqa: SLF001 - compatibility facade

    def restart_conversation(self) -> None:
        """Clear short-term conversation context and notify the GUI."""
        self._brain._messages.clear()  # noqa: SLF001 - intentional adapter seam
        self._emit({"type": "sys", "text": "Brain conversation restarted."})

    def refresh_memories(self, query: str = "") -> None:
        """Publish the current short-term transcript rows to the GUI."""
        rows = []
        if cfg.MEMORY_ENABLED:
            rows = [
                {"scope": "short", "source": "transcript", "text": str(row.get("content", "")), "id": ""}
                for row in self._brain._messages[-cfg.MEMORY_MAX_MSGS :]  # noqa: SLF001
            ]
        self._emit({"type": "memory", "scope": "short", "rows": rows})

    def add_semantic_memory(self, text: str) -> None:
        """Log the unsupported semantic-memory write instead of faking success."""
        logger.info("Semantic memory add ignored in brain adapter until mem0 facade is added: %r", text)

    def delete_semantic_memory(self, memory_id: str) -> None:
        """Log the unsupported semantic-memory deletion."""
        logger.info("Semantic memory delete ignored in brain adapter: %s", memory_id)

    def forget_short_term_memory(self) -> None:
        """Clear and republish short-term transcript context."""
        self.restart_conversation()
        self.refresh_memories()

    def forget_semantic_memory(self) -> None:
        """Publish the empty semantic-memory view exposed in brain mode."""
        self._emit({"type": "memory", "scope": "semantic", "rows": []})

    def agent_history(self) -> list[dict]:
        """Return persisted terminal jobs for the brain-mode Agents panel."""
        if not cfg.AGENT_HISTORY_FILE:
            return []
        return job_store.load_history(cfg.AGENT_HISTORY_FILE, cfg.AGENT_HISTORY_MAX)

    def clear_agent_history(self) -> bool:
        """Clear persisted terminal jobs through the shared history store."""
        if not cfg.AGENT_HISTORY_FILE:
            return True
        return job_store.clear_history(cfg.AGENT_HISTORY_FILE)

    def agent_backends(self) -> list[str]:
        """Return configured agent backend names."""
        return self._brain._bridge.backend_names()  # noqa: SLF001

    def agent_machine(self, backend: str) -> str:
        """Return the configured machine label for a backend."""
        return self._brain._bridge.machine_for(backend)  # noqa: SLF001

    def remote_hosts(self) -> list[dict]:
        """Return configured remote agent machines and what they offer."""
        return self._brain._bridge.remote_hosts()  # noqa: SLF001

    def check_remote_hosts(self) -> None:
        """Re-run host discovery on the brain loop."""
        self._submit(self._brain._remotes.discover())  # noqa: SLF001

    def default_agent_backend(self) -> str:
        """Return the brain's current default agent backend."""
        return self._brain._default_agent_backend  # noqa: SLF001

    def set_default_agent_backend(self, backend: str) -> None:
        """Select a valid default backend and publish the change."""
        if backend not in cfg.AGENT_BACKENDS:
            return
        self._brain._default_agent_backend = backend  # noqa: SLF001
        self._emit({"type": "default_agent_changed", "agent": backend})

    def set_persona(self, persona: Persona) -> None:
        """Switch persona, carrying its model and voice with it.

        Brain mode has no TTS or LLM client of its own to reconfigure, so the
        persona's model and voice only take effect if pushed along the same
        paths the GUI's own dropdowns use.
        """
        self._brain._persona = persona  # noqa: SLF001
        self.set_model(persona.model_name(cfg.LLM_MODEL))
        self.set_voice(persona.voice)

    def set_model(self, model: str) -> None:
        """Select the Ollama model used by subsequent brain turns."""
        self._model_override = model.strip() or self._model_override
        self._brain._model_override = self._model_override  # type: ignore[attr-defined]  # noqa: SLF001

    def set_voice(self, voice: str) -> None:
        """Publish the chosen voice for the external realtime frontend to pick up."""
        voice = voice.strip()
        if not voice or not cfg.S2S_VOICE_FILE:
            return
        if not voices.is_valid(voice):
            # Personas can carry ids for local backends -- a voicebox UUID, say --
            # that mean nothing to the realtime frontend's Kokoro TTS. Publishing
            # one would swap a working voice for a broken one, so keep the
            # current voice and say why.
            logger.warning(f"Ignoring {voice!r}: not a Kokoro voice, so the frontend cannot speak it")
            return
        path = Path(cfg.S2S_VOICE_FILE)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Swap the file in atomically: the frontend polls it on its own clock,
            # and a torn read would hand its TTS a truncated voice id.
            staged = path.with_suffix(f"{path.suffix}.tmp")
            staged.write_text(f"{voice}\n", encoding="utf-8")
            staged.replace(path)
        except OSError as exc:
            logger.warning(f"Failed to update S2S voice file {path}: {exc}")

    def set_voice_mode(self, mode: str) -> None:
        """Record the externally synchronized input mode."""
        self._voice_mode = mode

    def set_muted(self, muted: bool) -> None:
        """Record the mute state already published by the GUI boundary."""
        self._muted = muted

    def set_push_to_talk(self, active: bool) -> None:
        """Ignore local PTT state because the external client owns the mic."""
        return None

    def set_context_active(self, active: bool) -> None:
        """Accept the full-session context hook as an intentional no-op."""
        return None

    def set_manual_prompt_mode(self, value: bool) -> None:
        """Accept the local composer-mode hook as an intentional no-op."""
        return None

    def set_startup_defaults(self, **kwargs: Any) -> None:
        """Apply persisted model, voice, and default-agent GUI selections."""
        model = str(kwargs.get("model") or "").strip()
        if model:
            self.set_model(model)
        # Republish on every boot so a frontend started after the GUI still picks
        # up the saved voice instead of falling back to its own launcher default.
        self.set_voice(str(kwargs.get("voice") or ""))
        backend = str(kwargs.get("default_agent_backend") or "").strip()
        if backend:
            self.set_default_agent_backend(backend)

    def set_voicebox_warmup_personas(self, personas: list[Persona]) -> None:
        """Ignore local Voicebox warmup because external TTS owns synthesis."""
        return None

    def _submit(self, coro) -> Future:
        if self._loop is None or self._loop.is_closed():
            raise RuntimeError("Brain session is not running yet")
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _complete_with_activity(self, text: str, *, llm_content: str | None = None) -> str:
        self._emit({"type": "turn", "event": "user_stopped"})
        self._emit({"type": "speaking", "value": False})
        answer = await self._brain.complete(text, llm_content=llm_content)
        return answer

    async def _start_agent_task(self, agent: str, task: str, cwd: str | None) -> None:
        self._brain._last_user_text = task  # noqa: SLF001
        ack = self._brain._delegate_ack(agent, task, cwd)  # noqa: SLF001
        self._brain._messages.append({"role": "assistant", "content": ack})  # noqa: SLF001
        self._emit({"type": "transcript", "role": "assistant", "text": ack})

    def _observe_event(self, event: dict) -> None:
        """Watch brain events on their way to the GUI for frontend side effects."""
        if event.get("type") == "agent_job_summary":
            self._publish_announcement(event)
        self._emit(event)

    def _publish_announcement(self, event: dict) -> None:
        """Ask the realtime frontend to voice a summary of a finished agent job.

        Brain mode owns no speakers, so a finished job would otherwise end in
        silence. The frontend polls this file and replays its text as a turn;
        the ``[[announce]]`` prefix tells the brain to summarize, not route.
        """
        if not cfg.S2S_ANNOUNCE_FILE:
            return
        agent = str(event.get("agent") or "an agent")
        status = str(event.get("status") or "finished")
        outcome = str(event.get("result") or event.get("summary") or "").strip()
        text = (
            f"{ANNOUNCE_PREFIX} [Agent job update: {agent} {status}. "
            f"Outcome: {outcome or 'no details were reported'}. "
            "Give the user a one-or-two sentence spoken summary of this result.]"
        )
        ident = f"{event.get('job_id', '')}:{status}"
        payload = {"id": ident, "text": text}
        path = Path(cfg.S2S_ANNOUNCE_FILE)
        queue_dir = path.with_suffix(f"{path.suffix}.queue")
        safe_id = "".join(char if char.isalnum() or char in "-_" else "_" for char in ident)
        queued = queue_dir / f"{time.time_ns()}-{safe_id}.json"
        staged = queued.with_suffix(".tmp")
        try:
            queue_dir.mkdir(parents=True, exist_ok=True)
            # One immutable file per result: simultaneous completions cannot
            # overwrite each other while the frontend is busy speaking.
            staged.write_text(json.dumps(payload), encoding="utf-8")
            staged.replace(queued)
        except OSError as exc:
            logger.warning(f"Failed to publish agent announcement {path}: {exc}")

    def _discard_stale_announcements(self) -> None:
        """Drop announcements left queued by an earlier run.

        The frontend deletes each entry as it speaks it, so anything still here
        at startup belongs to a session that ended -- usually because the
        frontend was not running when a delegated job finished. Speaking them
        now would report finished work as if it had just landed.
        """
        if not cfg.S2S_ANNOUNCE_FILE:
            return
        path = Path(cfg.S2S_ANNOUNCE_FILE)
        queue_dir = path.with_suffix(f"{path.suffix}.queue")
        stale = 0
        # ``.tmp`` too: a publish interrupted mid-write leaves one behind, and
        # nothing else ever cleans it up.
        for entry in (*queue_dir.glob("*.json"), *queue_dir.glob("*.tmp")):
            try:
                entry.unlink()
                stale += 1
            except OSError as exc:
                logger.warning(f"Could not discard stale announcement {entry}: {exc}")
        if stale:
            logger.info(f"Discarded {stale} announcement(s) queued before this run")

    def _emit(self, event: dict) -> None:
        if self._on_event is not None:
            self._on_event(event)

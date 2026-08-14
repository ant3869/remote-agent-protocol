"""Local web control center for Remote Agent Protocol."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import queue
import secrets
import sys
import threading
import time
import webbrowser
from collections import deque
from collections.abc import Callable
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from loguru import logger

from remote_agent_protocol import (
    agent_bridge,
    app_state,
    coqui_tts,
    dashboard,
    diagnostics,
    logging_setup,
    multimodal_prompt,
    ollama_models,
    openai_bridge,
    persona_config,
    personas,
    process_guard,
    tts_factory,
    voicebox,
    voices,
    wake_word,
)
from remote_agent_protocol import config as cfg
from remote_agent_protocol.avatar_audio import (
    AvatarAudioEnvelope,
    AvatarAudioEnvelopeHub,
    sse_data,
)
from remote_agent_protocol.brain_adapter import BrainSessionAdapter
from remote_agent_protocol.session import VoiceSession

logging_setup.setup_logging(cfg.DEBUG_MODE)

_STATIC_DIR = Path(__file__).with_name("web_app")
_AGENT_PROMPT_DEFAULTS = {
    "scopePreamble": cfg.AGENT_SCOPE_PREAMBLE,
    "statusProtocol": agent_bridge.status_protocol(),
    "delegateStyle": cfg.LLM_DELEGATE_STYLE,
    "dispatchAck": cfg.DELEGATION_ACK_PROMPT,
    "update": cfg.AGENT_UPDATE_PROMPT,
    "confirm": cfg.DELEGATION_CONFIRM_PROMPT,
    "confirmApproved": cfg.AGENT_CONFIRM_APPROVED_PROMPT,
    "confirmDenied": cfg.AGENT_CONFIRM_DENIED_PROMPT,
}
_AGENT_PROMPT_FIELDS = {
    "scopePreamble": {
        "label": "Agent scope preamble",
        "help": "Prepended to delegated tasks so agents know the scratch cwd is not the target.",
        "required": ["{cwd}"],
    },
    "statusProtocol": {
        "label": "Agent status protocol",
        "help": "Appended to delegated tasks; tells agents how to stream progress and concise results.",
        "required": [],
    },
    "delegateStyle": {
        "label": "Assistant delegation style",
        "help": "Added to the assistant prompt so chat replies dispatch truthfully and briefly.",
        "required": [],
    },
    "dispatchAck": {
        "label": "Dispatch acknowledgement",
        "help": "Injected after a task is sent to an agent.",
        "required": ["{agent}", "{task}"],
    },
    "update": {
        "label": "Agent update relay",
        "help": "Injected when agent progress or completion needs to be spoken.",
        "required": ["{update}"],
    },
    "confirm": {
        "label": "Confirmation request",
        "help": "Injected when a risky delegation is held for user approval.",
        "required": ["{agent}", "{task}"],
    },
    "confirmApproved": {
        "label": "Confirmation approved",
        "help": "Injected after the user approves a held delegation.",
        "required": ["{agent}"],
    },
    "confirmDenied": {
        "label": "Confirmation denied",
        "help": "Injected after the user cancels a held delegation.",
        "required": ["{agent}"],
    },
}


class WebVoiceApp:
    """Serve the web UI and bridge it to the voice session."""

    def __init__(self) -> None:
        """Initialize state shared by the HTTP UI and voice thread."""
        self._events_in: queue.Queue[dict] = queue.Queue()
        self._event_log: deque[dict] = deque(maxlen=800)
        self._event_id = 0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        # Bound to this process, not persisted: the server has no other auth,
        # so a foreign webpage open in the same browser could otherwise POST
        # /api/action directly (a "simple request" with Content-Type: text/
        # plain skips CORS preflight entirely) and dispatch or approve a real
        # delegated task. A random per-launch token in a custom header closes
        # that -- a cross-origin fetch() that sets a custom header stops being
        # a "simple request" and needs a preflight this server never approves.
        self._csrf_token = secrets.token_urlsafe(32)
        self._persona_config = persona_config.load_config()
        self._personas = persona_config.effective_personas(personas.PERSONAS, self._persona_config)
        self._app_state = app_state.load_state(cfg.APP_STATE_FILE)
        self._apply_agent_prompt_overrides()
        boot_name = app_state.resolve_persona_name(
            self._app_state.persona, self._persona_names(), cfg.DEFAULT_PERSONA_NAME
        )
        self._persona = self._persona_by_name(boot_name)
        self._model = self._app_state.model or self._persona.model_name(cfg.LLM_MODEL)
        self._voice = self._app_state.voice or self._persona.voice
        self._tts_provider = self._app_state.tts_provider or self._persona.voice_backend
        self._coqui_model = self._app_state.coqui_model or cfg.COQUI_TTS_MODEL
        self._coqui_speaker = self._app_state.coqui_speaker or cfg.COQUI_TTS_SPEAKER
        self._coqui_language = self._app_state.coqui_language or cfg.COQUI_TTS_LANGUAGE
        self._coqui_device = self._app_state.coqui_device or cfg.COQUI_TTS_DEVICE
        if self._tts_provider == "coqui" and self._coqui_speaker:
            self._voice = self._coqui_speaker
        self._voice_mode = multimodal_prompt.normalize_voice_mode(self._app_state.voice_mode)
        self._muted = True
        self._s2s_mute_ready = True
        # Seed above any status left by an earlier process, so a restart cannot
        # mistake a stale acknowledgement for its first command.
        self._s2s_mute_generation = time.time_ns()
        self._s2s_mute_transition_lock = threading.Lock()
        self._s2s_mode_ready = True
        self._s2s_mode_generation = 0
        self._s2s_mode_transition_lock = threading.Lock()
        self._session_state = "starting"
        self._health = {"ok": False, "label": "Ollama checking"}
        self._tts_health = {"ok": False, "label": "TTS checking"}
        self._vram = {"available": False, "label": "VRAM checking"}
        self._latency = dashboard.LatencyState()
        self._models = self._model_choices()
        self._voice_map = dict(voices.labelled() + voicebox.labelled_profiles())
        self._wake_status = self._initial_wake_status()
        self._pending_confirms: list[dict] = []
        # Resolved confirmations used to just vanish once approved/denied --
        # nothing but a transient transcript line recorded *why* a task was
        # held or what the user decided. Bounded so the Agents panel can show
        # recent history without growing unbounded over a long session.
        self._confirm_history: deque[dict] = deque(maxlen=30)
        self._agent_jobs: dict[str, dict] = {}
        self._avatar_audio = AvatarAudioEnvelopeHub()
        self._avatar_speaking = False
        self._session = self._new_session()
        if cfg.RAP_MODE == "brain":
            # A published command is pending, not confirmed. The consumer must
            # echo its generation and applied state before muteReady is truthful.
            self._s2s_mute_ready = False
            if self._write_s2s_mute_command(self._muted) is None:
                logger.warning("Could not publish the initial external mute command")
            initial_generation = self._write_s2s_voice_mode(self._voice_mode)
            # Publishing a request is not an acknowledgement. The external
            # frontend proves the matching generation is active through its
            # status file or authenticated input-state telemetry.
            self._s2s_mode_ready = False
            if initial_generation is None:
                logger.warning("Could not publish the initial external input mode")
        self._thread: threading.Thread | None = None
        self._event_thread: threading.Thread | None = None
        self._health_thread: threading.Thread | None = None
        self._actions: dict[str, Callable[[dict], dict | None]] = {
            "mute": self._action_mute,
            "voice_mode": self._action_voice_mode,
            "avatar_settings": self._action_avatar_settings,
            "ptt": self._action_ptt,
            "context_active": self._action_context_active,
            "persona": self._action_persona,
            "persona_create": self._create_persona,
            "persona_save": self._save_persona,
            "persona_duplicate": self._duplicate_persona,
            "persona_delete": self._delete_persona,
            "model": self._action_model,
            "voice": self._action_voice,
            "tts": self._action_tts,
            "tts_refresh": self._action_tts_refresh,
            "tts_test": self._action_tts_test,
            "tool_user": self._action_tool_user,
            "agent_prompts_save": self._save_agent_prompts,
            "send": self._action_send,
            "delegate": self._action_delegate,
            "restart_chat": self._action_restart_chat,
            "refresh_memory": self._action_refresh_memory,
            "memory_add": self._action_memory_add,
            "memory_delete": self._action_memory_delete,
            "memory_forget_short": self._action_memory_forget_short,
            "memory_forget_semantic": self._action_memory_forget_semantic,
            "approve": self._action_approve,
            "deny": self._action_deny,
            "start_ollama": self._action_start_ollama,
            "free_vram": self._action_free_vram,
            "export_diagnostics": self._action_export_diagnostics,
            "reboot_session": self._action_reboot_session,
        }

    def run(self) -> None:
        """Start the voice session, web server, and browser shell."""
        self._start_session_thread()
        self._event_thread = threading.Thread(target=self._event_pump, daemon=True)
        self._health_thread = threading.Thread(target=self._health_poller, daemon=True)
        self._event_thread.start()
        self._health_thread.start()

        server_port = cfg.S2S_BRIDGE_PORT if cfg.RAP_MODE == "brain" else 0
        try:
            server = ThreadingHTTPServer(("127.0.0.1", server_port), self._handler_class())
        except OSError as exc:
            if server_port == 0:
                raise
            # Brain mode serves the brain endpoint itself, so the standalone
            # bridge is an alternative to this GUI, never a companion to it.
            raise SystemExit(
                f"Port {server_port} is already in use, so the brain endpoint cannot start.\n"
                f"In brain mode the GUI already serves /v1/chat/completions; do not also run\n"
                f"scripts\\start_brain_bridge.bat. Stop whichever one you do not want, or set\n"
                f"S2S_BRIDGE_PORT to a free port.\n"
                f"Original error: {exc}"
            ) from exc
        cleanup_done = threading.Event()

        def _on_console_close() -> None:
            # Runs on Windows' own console-control thread when the user closes
            # the window rather than Ctrl+C. server.shutdown() makes
            # serve_forever() below return normally, so the SAME finally block
            # (not a second, duplicate cleanup here) does the real work; block
            # until it has, so Windows doesn't force-kill mid-cleanup.
            server.shutdown()
            cleanup_done.wait(timeout=10.0)

        process_guard.install_close_handler(_on_console_close)

        url = f"http://127.0.0.1:{server.server_address[1]}"
        print(f"Remote Agent Protocol web UI: {url}")
        webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self._stop_app()
            server.server_close()
            cleanup_done.set()

    def _start_session_thread(self) -> None:
        self._thread = threading.Thread(target=self._boot_thread, daemon=True)
        self._thread.start()

    def _join_thread(self, thread: threading.Thread | None, timeout: float = 5.0) -> None:
        if thread is None or thread is threading.current_thread():
            return
        thread.join(timeout=timeout)

    def _stop_session(self, *, unload_models: bool, announce: bool = False) -> None:
        try:
            self._session.shutdown()
        except Exception as exc:
            logger.warning(f"Voice session shutdown failed: {exc}")
        self._join_thread(self._thread)
        self._thread = None
        if unload_models:
            self._unload_ollama_models(announce=announce)

    def _stop_app(self) -> None:
        self._stop.set()
        self._avatar_audio.close()
        self._stop_session(unload_models=True)
        self._join_thread(self._event_thread, timeout=1.0)
        self._join_thread(self._health_thread, timeout=1.0)
        self._event_thread = None
        self._health_thread = None

    def _new_session(self) -> VoiceSession | BrainSessionAdapter:
        if cfg.RAP_MODE == "brain":
            session = BrainSessionAdapter(
                self._persona,
                on_event=self._events_in.put,
            )
        else:
            session = VoiceSession(
                self._persona,
                on_event=self._events_in.put,
                on_avatar_audio=self._avatar_audio.publish,
            )
        session.set_manual_prompt_mode(True)
        session.set_voice_mode(self._voice_mode)
        session.set_muted(self._muted)
        session.set_startup_defaults(
            model=self._model,
            voice=self._voice,
            voice_backend=self._tts_provider,
            voice_model=self._current_tts_model(),
            tts_options=self._current_tts_options(),
        )
        session.set_voicebox_warmup_personas(
            persona_config.voicebox_personas(personas.PERSONAS, self._persona_config)
        )
        if self._app_state.tool_user in cfg.AGENT_BACKENDS:
            session.set_default_agent_backend(self._app_state.tool_user)
        return session

    def _boot_thread(self) -> None:
        async def _boot() -> None:
            self._publish({"type": "session", "state": "building"})
            self._session.build()
            self._publish({"type": "speaking", "value": False})
            if cfg.RAP_MODE != "brain":
                self._publish({"type": "session", "state": "ready"})
            await self._session.run()
            self._publish({"type": "session", "state": "stopped"})

        try:
            asyncio.run(_boot())
        except Exception as exc:
            self._publish({"type": "session", "state": "failed"})
            self._publish(
                {"type": "transcript", "role": "assistant", "text": f"[pipeline crashed: {exc}]"}
            )

    def _event_pump(self) -> None:
        while not self._stop.is_set():
            try:
                evt = self._events_in.get(timeout=0.2)
            except queue.Empty:
                continue
            self._publish(evt)

    def _publish(self, evt: dict) -> None:
        evt = _json_safe(evt)
        with self._lock:
            self._fold_event(evt)
            self._event_id += 1
            row = {"id": self._event_id, **evt}
            self._event_log.append(row)

    def _fold_event(self, evt: dict) -> None:
        kind = evt.get("type")
        if kind == "session":
            self._session_state = str(evt.get("state", "unknown"))
        elif kind == "health":
            self._health = {"ok": bool(evt.get("ok")), "label": evt.get("label", "Ollama ?")}
        elif kind == "tts_health":
            self._tts_health = {"ok": bool(evt.get("ok")), "label": evt.get("label", "TTS ?")}
        elif kind == "vram":
            self._vram = {
                "available": bool(evt.get("available")),
                "label": evt.get("label", "VRAM ?"),
                "percent": evt.get("percent", 0),
                "usedMb": evt.get("used_mb", 0),
                "totalMb": evt.get("total_mb", 0),
                "gpuUtilPercent": evt.get("gpu_util_percent", 0),
            }
        elif kind == "metric":
            self._latency.update(evt.get("bucket", ""), evt.get("kind", ""), evt.get("value", 0.0))
        elif kind == "turn_timing":
            for bucket in ("stt", "llm", "tts", "total"):
                if bucket in evt:
                    self._latency.update(bucket, "processing", evt[bucket])
        elif kind == "turn":
            if evt.get("event") == "user_stopped":
                self._latency.mark_user_turn_complete()
            elif evt.get("event") == "bot_started":
                self._latency.mark_bot_started()
        elif kind == "agent_confirm":
            self._pending_confirms.append(evt)
        elif kind == "agent_confirm_resolved":
            token = evt.get("token")
            self._pending_confirms = [
                row for row in self._pending_confirms if row.get("token") != token
            ]
            self._confirm_history.append(
                {
                    "agent": evt.get("agent", ""),
                    "task": evt.get("task", ""),
                    "reason": evt.get("reason", ""),
                    "decision": evt.get("decision", ""),
                    "resolvedAt": datetime.now().isoformat(),
                }
            )
        elif kind == "agent_job":
            job_id = str(evt.get("job_id", ""))
            if job_id:
                old = self._agent_jobs.get(job_id, {})
                lines = list(old.get("lines") or [])
                if evt.get("event") == "output" and evt.get("line"):
                    lines.append(str(evt["line"]))
                    del lines[:-250]
                elif isinstance(evt.get("lines"), list):
                    lines = list(evt["lines"])[-250:]
                self._agent_jobs[job_id] = {**old, **evt, "lines": lines}
        elif kind == "default_agent_changed":
            agent = str(evt.get("agent", ""))
            if agent in cfg.AGENT_BACKENDS:
                self._save_state()
        elif kind == "wake":
            self._wake_status = {**self._wake_status, **evt}

    def _health_poller(self) -> None:
        while not self._stop.is_set():
            health = dashboard.ollama_health(cfg.OLLAMA_HOST)
            self._publish({"type": "health", "ok": health.ok, "label": health.label})
            tts = dashboard.tts_health(
                self._tts_provider,
                voicebox_url=voicebox.base_url(),
                has_cartesia_key=bool(tts_factory.load_env_value("CARTESIA_API_KEY")),
            )
            self._publish({"type": "tts_health", "ok": tts.ok, "label": tts.label})
            vram = dashboard.vram_status()
            self._publish(
                {
                    "type": "vram",
                    "available": vram.available,
                    "label": vram.label,
                    "percent": vram.percent,
                    "used_mb": vram.used_mb,
                    "total_mb": vram.total_mb,
                    "gpu_util_percent": vram.gpu_util_percent,
                }
            )
            self._stop.wait(4)

    def _persona_names(self) -> list[str]:
        return [persona.name for persona in self._personas]

    def _persona_by_name(self, name: str) -> personas.Persona:
        return next(
            (persona for persona in self._personas if persona.name == name), self._personas[0]
        )

    def _builtin_persona_names(self) -> set[str]:
        return {persona.name for persona in personas.PERSONAS}

    def _model_choices(self) -> list[str]:
        extra = [
            cfg.LLM_MODEL,
            getattr(self, "_model", ""),
            *[persona.model for persona in self._personas if persona.model],
        ]
        return sorted(
            set(ollama_models.available(cfg.OLLAMA_HOST)) | {model for model in extra if model}
        )

    def _tts_providers(self) -> list[dict]:
        return [
            {"id": "kokoro", "label": "Kokoro"},
            {"id": "voicebox", "label": "Voicebox"},
            {"id": "coqui", "label": "Coqui"},
            {"id": "cartesia", "label": "Cartesia"},
        ]

    def _current_tts_model(self) -> str | None:
        if self._tts_provider == "coqui":
            return self._coqui_model
        return self._persona.voice_model

    def _current_tts_options(self) -> dict:
        if self._tts_provider != "coqui":
            return self._persona.tts_options or {}
        options = {
            "speaker": self._coqui_speaker,
            "language": self._coqui_language,
            "device": self._coqui_device,
        }
        if voices.is_valid(self._persona.voice):
            options["fallback_voice"] = self._persona.voice
        return options

    def _apply_current_tts(self) -> None:
        self._session.set_tts(
            voice=self._voice,
            voice_backend=self._tts_provider,
            model=self._current_tts_model(),
            tts_options=self._current_tts_options(),
        )

    def _use_persona_tts_defaults(self, persona: personas.Persona) -> None:
        self._tts_provider = persona.voice_backend
        self._voice = persona.voice
        if persona.voice_backend == "coqui":
            options = persona.tts_options or {}
            self._coqui_model = persona.voice_model or self._coqui_model or cfg.COQUI_TTS_MODEL
            self._coqui_speaker = str(options.get("speaker") or persona.voice or "")
            self._coqui_language = str(options.get("language") or self._coqui_language or "")
            self._coqui_device = str(
                options.get("device") or self._coqui_device or cfg.COQUI_TTS_DEVICE
            )
            self._voice = self._coqui_speaker

    def _tts_payload(self, *, refresh: bool = False) -> dict:
        coqui = coqui_tts.status_payload(self._coqui_model, refresh=refresh)
        return {
            "providers": self._tts_providers(),
            "provider": self._tts_provider,
            "model": self._current_tts_model() or "",
            "voice": self._voice,
            "coqui": {
                **coqui,
                "speaker": self._coqui_speaker,
                "language": self._coqui_language,
                "device": self._coqui_device,
            },
        }

    def _reload_personas(self) -> None:
        self._persona_config = persona_config.load_config()
        self._personas = persona_config.effective_personas(personas.PERSONAS, self._persona_config)
        self._models = self._model_choices()
        self._session.set_voicebox_warmup_personas(
            persona_config.voicebox_personas(personas.PERSONAS, self._persona_config)
        )

    def _persona_payload(self, persona: personas.Persona) -> dict:
        builtin = persona.name in self._builtin_persona_names()
        return {
            "name": persona.name,
            "description": persona.blurb,
            "systemPrompt": persona.personality,
            "toneStyle": "Short conversational spoken replies",
            "model": persona.model or "",
            "effectiveModel": persona.model_name(cfg.LLM_MODEL),
            "voice": persona.voice,
            "voiceBackend": persona.voice_backend,
            "voiceModel": persona.voice_model or "",
            "ttsOptions": persona.tts_options or {},
            "toolUser": persona.tool_user or "",
            "builtin": builtin,
            "memory": {
                "enabled": cfg.MEMORY_ENABLED,
                "semanticEnabled": cfg.MEM0_ENABLED,
                "mode": "global + semantic" if cfg.MEM0_ENABLED else "global transcript",
                "canWrite": cfg.MEMORY_ENABLED,
                "canRetrieve": cfg.MEMORY_ENABLED,
            },
            "voiceDefaults": {
                "mode": self._voice_mode,
                "wakeWord": self._voice_mode == "wake_word",
            },
            "routing": {"defaultAgent": persona.tool_user or self._session.default_agent_backend()},
            "advanced": {
                "responseFormat": "Spoken style rule appended automatically",
                "contextHandling": "Uses the shared context composer and session transcript",
                "safety": "Uses the app's existing confirmation and refusal behavior",
            },
        }

    def _personas_payload(self) -> list[dict]:
        return [self._persona_payload(persona) for persona in self._personas]

    def _save_state(self) -> None:
        state = app_state.AppState(
            persona=self._persona.name,
            tool_user=self._session.default_agent_backend(),
            voice_mode=self._voice_mode,
            model=self._model,
            voice=self._voice,
            tts_provider=self._tts_provider,
            coqui_model=self._coqui_model,
            coqui_speaker=self._coqui_speaker,
            coqui_language=self._coqui_language,
            coqui_device=self._coqui_device,
            agent_prompts=self._app_state.agent_prompts,
            avatar_enabled=self._app_state.avatar_enabled,
            avatar_id=self._app_state.avatar_id,
            avatar_quality=self._app_state.avatar_quality,
            avatar_lip_sync=self._app_state.avatar_lip_sync,
            avatar_gaze=self._app_state.avatar_gaze,
            avatar_idle_motion=self._app_state.avatar_idle_motion,
            avatar_expression_intensity=self._app_state.avatar_expression_intensity,
            avatar_reduced_motion=self._app_state.avatar_reduced_motion,
            avatar_show_state=self._app_state.avatar_show_state,
            avatar_panel_collapsed=self._app_state.avatar_panel_collapsed,
        )
        app_state.save_state(cfg.APP_STATE_FILE, state)
        self._app_state = state

    def _agent_prompt_current(self) -> dict[str, str]:
        return {
            "scopePreamble": cfg.AGENT_SCOPE_PREAMBLE,
            "statusProtocol": agent_bridge.status_protocol(),
            "delegateStyle": cfg.LLM_DELEGATE_STYLE,
            "dispatchAck": cfg.DELEGATION_ACK_PROMPT,
            "update": cfg.AGENT_UPDATE_PROMPT,
            "confirm": cfg.DELEGATION_CONFIRM_PROMPT,
            "confirmApproved": cfg.AGENT_CONFIRM_APPROVED_PROMPT,
            "confirmDenied": cfg.AGENT_CONFIRM_DENIED_PROMPT,
        }

    def _agent_prompt_payload(self) -> dict:
        current = self._agent_prompt_current()
        return {
            key: {
                "key": key,
                "label": meta["label"],
                "help": meta["help"],
                "required": meta["required"],
                "default": _AGENT_PROMPT_DEFAULTS[key],
                "value": current[key],
                "custom": key in self._app_state.agent_prompts,
            }
            for key, meta in _AGENT_PROMPT_FIELDS.items()
        }

    def _agents_payload(self) -> dict:
        return {
            "jobs": list(self._agent_jobs.values()),
            "history": self._session.agent_history(),
            "prompts": self._agent_prompt_payload(),
            "status": self._status_payload(),
            "confirmHistory": list(reversed(self._confirm_history)),
        }

    def _cli_diagnostics_payload(self) -> dict:
        from remote_agent_protocol.cli_agents import get_all_cli_agents

        agents = get_all_cli_agents()
        return {
            "cli_agents": {
                agent.id: {"label": agent.label, "status": agent.get_status().to_dict()}
                for agent in agents
            }
        }

    def _apply_agent_prompt_overrides(self) -> None:
        clean: dict[str, str] = {}
        for key, value in (self._app_state.agent_prompts or {}).items():
            if key not in _AGENT_PROMPT_FIELDS or not isinstance(value, str):
                continue
            if self._prompt_missing_placeholders(key, value):
                continue
            clean[key] = value
        self._app_state.agent_prompts = clean
        values = {**_AGENT_PROMPT_DEFAULTS, **clean}
        cfg.AGENT_SCOPE_PREAMBLE = values["scopePreamble"]
        agent_bridge.set_status_protocol(values["statusProtocol"])
        cfg.LLM_DELEGATE_STYLE = values["delegateStyle"]
        cfg.DELEGATION_ACK_PROMPT = values["dispatchAck"]
        cfg.AGENT_UPDATE_PROMPT = values["update"]
        cfg.DELEGATION_CONFIRM_PROMPT = values["confirm"]
        cfg.AGENT_CONFIRM_APPROVED_PROMPT = values["confirmApproved"]
        cfg.AGENT_CONFIRM_DENIED_PROMPT = values["confirmDenied"]

    def _prompt_missing_placeholders(self, key: str, text: str) -> list[str]:
        required = _AGENT_PROMPT_FIELDS.get(key, {}).get("required", [])
        return [placeholder for placeholder in required if placeholder not in text]

    def _save_agent_prompts(self, payload: dict) -> dict:
        incoming = payload.get("prompts")
        if not isinstance(incoming, dict):
            return {"ok": False, "error": "prompts must be an object"}
        saved = dict(self._app_state.agent_prompts)
        for key, value in incoming.items():
            if key not in _AGENT_PROMPT_FIELDS:
                continue
            text = str(value)
            missing = self._prompt_missing_placeholders(key, text)
            if missing:
                return {
                    "ok": False,
                    "error": f"{_AGENT_PROMPT_FIELDS[key]['label']} must include "
                    + ", ".join(missing),
                    "prompts": self._agent_prompt_payload(),
                    "status": self._status_payload(),
                }
            if text == _AGENT_PROMPT_DEFAULTS[key]:
                saved.pop(key, None)
            else:
                saved[key] = text
        self._app_state.agent_prompts = saved
        self._apply_agent_prompt_overrides()
        self._session.set_agent_scope_preamble(cfg.AGENT_SCOPE_PREAMBLE)
        self._save_state()
        return {
            "ok": True,
            "message": "Agent prompts saved.",
            "prompts": self._agent_prompt_payload(),
            "status": self._status_payload(),
        }

    def _status_payload(self) -> dict:
        if cfg.RAP_MODE == "brain" and not self._s2s_mute_ready:
            self._refresh_s2s_mute_ready()
        active_statuses = {"running", "waiting", "blocked"}
        active_jobs = [
            job for job in self._agent_jobs.values() if job.get("status") in active_statuses
        ]
        agent_states: dict[str, dict] = {}
        for job in self._agent_jobs.values():
            agent_states[str(job.get("agent", "Agent"))] = job
        for job in active_jobs:
            agent_states[str(job.get("agent", "Agent"))] = job
        return {
            "appName": cfg.APP_NAME,
            "subtitle": "Premium local AI control center",
            "mode": cfg.RAP_MODE,
            "session": self._session_state,
            "muted": self._muted,
            "voiceMode": self._voice_mode,
            "inputControl": {
                "muteReady": self._s2s_mute_ready,
                "modeReady": self._s2s_mode_ready,
            },
            "persona": self._persona.name,
            "personaBlurb": self._persona.blurb,
            "model": self._model,
            "voice": self._voice,
            "toolUser": self._session.default_agent_backend(),
            "avatar": app_state.avatar_settings_payload(self._app_state),
            "personas": self._personas_payload(),
            "models": self._models,
            "voices": [
                {"label": label, "value": value} for label, value in self._voice_map.items()
            ],
            "agentBackends": self._session.agent_backends(),
            "agentMachines": {
                backend: self._session.agent_machine(backend)
                for backend in self._session.agent_backends()
            },
            "activeAgentCount": len(active_jobs),
            "agentStates": agent_states,
            "health": self._health,
            "ttsHealth": self._tts_health,
            "tts": self._tts_payload(),
            "latency": self._latency.values,
            "pendingConfirms": self._pending_confirms,
            "memoryEnabled": cfg.MEMORY_ENABLED,
            "semanticMemoryEnabled": cfg.MEM0_ENABLED,
            "wake": self._wake_payload(),
            "vram": self._vram,
        }

    def _initial_wake_status(self) -> dict:
        settings = wake_word.settings_from_config(
            cfg, enabled=cfg.WAKE_WORD_ENABLED or self._voice_mode == "wake_word"
        )
        target = settings.effective_targets[0]
        return {
            "type": "wake",
            "state": "inactive",
            "phase": "idle",
            "model": target.model,
            "model_path": target.model_path,
            "persona": target.persona,
            "score": None,
            "error": "",
            "window_secs": settings.active_window_secs,
            "threshold": target.threshold,
            "remaining_secs": 0.0,
            "detector_loaded": False,
            "passive": False,
            "available": [
                {
                    "model": item.model,
                    "persona": item.persona,
                    "model_path": item.model_path,
                    "threshold": item.threshold,
                }
                for item in settings.effective_targets
            ],
        }

    def _wake_payload(self) -> dict:
        phase = str(self._wake_status.get("phase") or "idle")
        if self._voice_mode != "wake_word":
            phase = "idle"
        return {
            **self._wake_status,
            "enabled": self._voice_mode == "wake_word",
            "phase": phase,
        }

    def _events_after(self, after: int) -> dict:
        with self._lock:
            events = [evt for evt in self._event_log if evt["id"] > after]
            latest = self._event_id
        return {"events": events, "latest": latest, "status": self._status_payload()}

    def _action(self, name: str, payload: dict) -> dict:
        handler = self._actions.get(name)
        if handler is None:
            return {
                "ok": False,
                "error": f"unknown action: {name}",
                "status": self._status_payload(),
            }
        result = handler(payload) or {"ok": True}
        result.setdefault("status", self._status_payload())
        return result

    def _action_mute(self, payload: dict) -> dict | None:
        requested = bool(payload.get("muted"))
        if cfg.RAP_MODE == "brain":
            with self._s2s_mute_transition_lock:
                generation = self._write_s2s_mute_command(requested)
                if generation is None:
                    self._s2s_mute_ready = False
                    return {"ok": False, "error": "Could not publish the external mute command."}
                acknowledged, error = self._await_s2s_mute(requested, generation)
                if not acknowledged:
                    # Do not invent state from an unacknowledged request. The
                    # callback may already have applied it, but only a matching
                    # status generation is authoritative across the process gap.
                    self._s2s_mute_ready = False
                    return {"ok": False, "error": error}
        self._s2s_mute_ready = True
        self._muted = requested
        self._session.set_muted(requested)
        return None

    def _ingest_avatar_envelope(self, payload: dict) -> None:
        """Publish a mouth envelope measured by the external realtime frontend.

        Brain mode never sees TTS audio, so loudness has to arrive from whoever
        owns the speakers for the avatar to lip-sync instead of sitting still.
        """
        try:
            rms = max(0.0, min(1.0, float(payload.get("rms", 0.0))))
            peak = max(0.0, min(1.0, float(payload.get("peak", 0.0))))
            sample_rate = max(0, int(payload.get("sample_rate", 0)))
            channels = max(1, int(payload.get("channels", 1)))
        except (TypeError, ValueError):
            return
        self._avatar_audio.publish(
            AvatarAudioEnvelope(
                rms=rms,
                peak=peak,
                voiced=bool(payload.get("voiced", rms >= 0.012)),
                sample_rate=sample_rate,
                channels=channels,
                timestamp=time.time(),
            )
        )
        # Envelopes arrive ~25x/second. The hub above keeps only the latest, but
        # the event log is a bounded history, so only announce the transition --
        # publishing every frame would evict real transcript events.
        speaking = rms > 0.0
        with self._lock:
            if speaking != self._avatar_speaking:
                self._avatar_speaking = speaking
                self._publish({"type": "speaking", "value": speaking})

    def _write_s2s_mute_command(self, muted: bool) -> int | None:
        if not cfg.S2S_MIC_MUTE_FILE:
            return None
        self._s2s_mute_generation += 1
        body = {"generation": self._s2s_mute_generation, "muted": muted}
        path = Path(cfg.S2S_MIC_MUTE_FILE)
        staged = path.with_suffix(f"{path.suffix}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            staged.write_text(json.dumps(body), encoding="utf-8")
            staged.replace(path)
            return self._s2s_mute_generation
        except OSError as exc:
            staged.unlink(missing_ok=True)
            logger.warning(f"Failed to update S2S mic mute command {path}: {exc}")
            return None

    def _refresh_s2s_mute_ready(self) -> None:
        """Reconcile startup/pending readiness without changing confirmed state."""
        if not cfg.S2S_MIC_MUTE_STATUS_FILE:
            return
        try:
            status = json.loads(
                Path(cfg.S2S_MIC_MUTE_STATUS_FILE).read_text(encoding="utf-8-sig")
            )
        except (OSError, json.JSONDecodeError):
            return
        self._s2s_mute_ready = (
            status.get("generation") == self._s2s_mute_generation
            and status.get("state") == "ready"
            and status.get("muted") == bool(self._muted)
        )

    def _await_s2s_mute(self, muted: bool, generation: int) -> tuple[bool, str]:
        timeout = max(0.0, cfg.S2S_MIC_MUTE_ACK_TIMEOUT)
        if timeout == 0 or not cfg.S2S_MIC_MUTE_STATUS_FILE:
            return False, "External microphone mute acknowledgement is unavailable."
        deadline = time.monotonic() + timeout
        path = Path(cfg.S2S_MIC_MUTE_STATUS_FILE)
        while time.monotonic() < deadline:
            try:
                status = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                time.sleep(0.05)
                continue
            if status.get("generation") != generation:
                time.sleep(0.05)
                continue
            if status.get("state") == "ready" and status.get("muted") is muted:
                return True, ""
            if status.get("state") == "error":
                return False, "The external microphone rejected the mute command."
            time.sleep(0.05)
        return False, "The external microphone did not acknowledge the mute command in time."

    def _write_s2s_voice_mode(self, mode: str) -> int | None:
        if not cfg.S2S_VOICE_MODE_FILE:
            return None
        if mode == multimodal_prompt.VOICE_MODE_WAKE_WORD:
            settings = wake_word.settings_from_config(cfg, enabled=True)
            selected = settings.effective_targets[0]
            available = [
                {
                    "model": item.model,
                    "persona": item.persona,
                    "model_path": item.model_path,
                    "threshold": item.threshold,
                }
                for item in settings.effective_targets
            ]
            self._wake_status = {
                **self._wake_status,
                "model": selected.model,
                "model_path": selected.model_path,
                "persona": selected.persona,
                "threshold": selected.threshold,
                "window_secs": settings.active_window_secs,
                "available": available,
            }
        target = self._wake_status.get("available", [{}])[0]
        self._s2s_mode_generation += 1
        body = {
            "generation": self._s2s_mode_generation,
            "mode": mode,
            "model": target.get("model", ""),
            "model_path": target.get("model_path", ""),
            "threshold": float(self._wake_status.get("threshold", cfg.WAKE_WORD_THRESHOLD)),
            "active_window_secs": float(
                self._wake_status.get("window_secs", cfg.WAKE_WORD_ACTIVE_WINDOW_SECS)
            ),
            # Separate from the window above: answering a question the butler
            # just asked deserves longer than reaching the mic after a wake word.
            "follow_up_window_secs": float(cfg.WAKE_WORD_FOLLOW_UP_SECS),
        }
        path = Path(cfg.S2S_VOICE_MODE_FILE)
        staged = path.with_suffix(f"{path.suffix}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            staged.write_text(json.dumps(body), encoding="utf-8")
            staged.replace(path)
            return self._s2s_mode_generation
        except OSError as exc:
            staged.unlink(missing_ok=True)
            logger.warning(f"Failed to update S2S input mode file {path}: {exc}")
            return None

    def _await_s2s_voice_mode(self, mode: str, generation: int) -> tuple[bool, str]:
        timeout = max(0.0, cfg.S2S_VOICE_MODE_ACK_TIMEOUT)
        if timeout == 0 or not cfg.S2S_VOICE_MODE_STATUS_FILE:
            return True, ""
        deadline = time.monotonic() + timeout
        path = Path(cfg.S2S_VOICE_MODE_STATUS_FILE)
        while time.monotonic() < deadline:
            try:
                status = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                time.sleep(0.05)
                continue
            if status.get("generation") != generation:
                time.sleep(0.05)
                continue
            state = status.get("state")
            if state == "ready" and status.get("mode") == mode:
                self._wake_status["phase"] = "passive" if mode == "wake_word" else "idle"
                self._wake_status["detector_loaded"] = mode == "wake_word"
                return True, ""
            if state == "error":
                self._wake_status["phase"] = "error"
                self._wake_status["detector_loaded"] = False
                return False, "The external wake-word detector could not load."
            time.sleep(0.05)
        return False, "The external microphone did not acknowledge the input mode in time."

    def _ingest_input_state(self, payload: dict) -> bool:
        allowed = {
            "idle",
            "waiting_for_wake_word",
            "wake_word_detected",
            "listening_for_command",
            "transcribing",
            "agent_responding",
            "follow_up_window",
            "returning_to_passive",
            "error",
        }
        try:
            generation = int(payload["generation"])
            phase = str(payload["phase"])
        except (KeyError, TypeError, ValueError):
            return False
        if generation != self._s2s_mode_generation or phase not in allowed:
            return False
        if self._voice_mode != "wake_word" and phase != "idle":
            return False
        self._s2s_mode_ready = True
        event = {"type": "wake", "phase": phase}
        if "remaining_secs" in payload:
            try:
                event["remaining_secs"] = max(0.0, float(payload["remaining_secs"]))
            except (TypeError, ValueError):
                return False
        for key in ("passive", "detector_loaded"):
            if key in payload:
                event[key] = bool(payload[key])
        if "error" in payload:
            event["error"] = str(payload["error"])[:300]
        self._publish(event)
        return True

    def _ingest_turn_timing(self, payload: dict) -> bool:
        try:
            stt = float(payload["stt_s"])
            audio = float(payload["first_audio_s"])
            response_value = payload.get("response_start_s")
            response = None if response_value is None else float(response_value)
        except (KeyError, TypeError, ValueError):
            return False
        if stt < 0 or audio < stt or (response is not None and not stt <= response <= audio):
            return False
        event = {
            "type": "turn_timing",
            "stt": round(stt, 3),
            "total": round(audio, 3),
        }
        if response is not None:
            event.update(
                llm=round(response - stt, 3),
                tts=round(audio - response, 3),
            )
        self._publish(event)
        return True

    def _action_voice_mode(self, payload: dict) -> dict | None:
        requested = multimodal_prompt.normalize_voice_mode(payload.get("mode"))
        if cfg.RAP_MODE == "brain" and requested == multimodal_prompt.VOICE_MODE_PUSH_TO_TALK:
            return {
                "ok": False,
                "error": "Push To Talk is unavailable while the external audio frontend owns the microphone.",
            }
        if cfg.RAP_MODE == "brain":
            with self._s2s_mode_transition_lock:
                confirmed_mode = self._voice_mode
                generation = self._write_s2s_voice_mode(requested)
                if generation is None:
                    return {
                        "ok": False,
                        "error": "Could not synchronize the external microphone input mode.",
                    }
                acknowledged, error = self._await_s2s_voice_mode(requested, generation)
                if not acknowledged:
                    # Roll back to the last acknowledged state. Hard-coding Free
                    # Talk here could falsely claim an open microphone after a
                    # failed Wake Word -> Free Talk transition.
                    recovery_generation = self._write_s2s_voice_mode(confirmed_mode)
                    recovered = recovery_generation is not None and self._await_s2s_voice_mode(
                        confirmed_mode, recovery_generation
                    )[0]
                    self._s2s_mode_ready = recovered
                    self._voice_mode = confirmed_mode
                    self._session.set_voice_mode(confirmed_mode)
                    self._save_state()
                    if not recovered:
                        label = confirmed_mode.replace("_", " ").title()
                        error += (
                            f" Rollback to {label} was not acknowledged; "
                            "input mode synchronization is unconfirmed."
                        )
                    return {"ok": False, "error": error}
                self._s2s_mode_ready = True
                self._voice_mode = requested
                self._session.set_voice_mode(requested)
                self._save_state()
                return None
        self._voice_mode = requested
        self._session.set_voice_mode(self._voice_mode)
        self._save_state()
        return None

    def _action_avatar_settings(self, payload: dict) -> None:
        self._app_state = app_state.normalize_avatar_settings(
            payload.get("settings"), self._app_state
        )
        self._save_state()

    def _action_ptt(self, payload: dict) -> None:
        self._session.set_push_to_talk(bool(payload.get("active")))

    def _action_context_active(self, payload: dict) -> None:
        self._session.set_context_active(bool(payload.get("active")))

    def _action_persona(self, payload: dict) -> None:
        self._persona = self._persona_by_name(str(payload.get("name", "")))
        self._model = self._persona.model_name(cfg.LLM_MODEL)
        self._use_persona_tts_defaults(self._persona)
        self._session.set_persona(self._persona)
        self._save_state()

    def _action_model(self, payload: dict) -> None:
        self._model = str(payload.get("model", ""))
        self._session.set_model(self._model)
        self._save_state()

    def _action_voice(self, payload: dict) -> None:
        self._voice = str(payload.get("voice", ""))
        self._session.set_voice(self._voice)
        self._save_state()

    def _action_tts(self, payload: dict) -> None:
        self._tts_provider = str(payload.get("provider") or self._tts_provider).strip()
        self._voice = str(payload.get("voice") or self._voice).strip()
        if self._tts_provider == "coqui":
            self._coqui_model = str(payload.get("model") or self._coqui_model).strip()
            self._coqui_speaker = str(payload.get("speaker") or "").strip()
            self._coqui_language = str(payload.get("language") or "").strip()
            self._coqui_device = str(payload.get("device") or self._coqui_device).strip()
            self._voice = self._coqui_speaker
        self._apply_current_tts()
        self._save_state()

    def _action_tts_refresh(self, payload: dict) -> dict:
        self._tts_payload(refresh=True)
        return {"ok": True, "status": self._status_payload()}

    def _action_tts_test(self, payload: dict) -> dict:
        if cfg.RAP_MODE == "brain":
            # The external frontend owns the speakers and offers no verbatim-TTS
            # entry point, so say so rather than reporting a queued phrase that
            # will never be heard.
            return {
                "ok": False,
                "message": "Brain mode: the realtime frontend speaks. Say something to hear the voice.",
                "status": self._status_payload(),
            }
        self._apply_current_tts()
        self._session.speak_text("This is the selected text to speech voice.")
        return {"ok": True, "message": "Test voice queued.", "status": self._status_payload()}

    def _action_tool_user(self, payload: dict) -> None:
        self._session.set_default_agent_backend(str(payload.get("backend", "")))
        self._save_state()

    def _action_send(self, payload: dict) -> None:
        self._session.send_multimodal_prompt(_bundle_from_payload(payload, self._voice_mode))

    def _action_delegate(self, payload: dict) -> None:
        bundle = _bundle_from_payload(payload, self._voice_mode)
        self._session.start_agent_task(self._session.default_agent_backend(), bundle.agent_prompt())

    def _action_restart_chat(self, payload: dict) -> None:
        self._session.restart_conversation()

    def _action_refresh_memory(self, payload: dict) -> None:
        self._session.refresh_memories(str(payload.get("query", "")))

    def _action_memory_add(self, payload: dict) -> dict | None:
        text = str(payload.get("text", "")).strip()
        if not text:
            return {"ok": False, "error": "memory text is empty"}
        self._session.add_semantic_memory(text)
        return None

    def _action_memory_delete(self, payload: dict) -> None:
        self._session.delete_semantic_memory(str(payload.get("id", "")))

    def _action_memory_forget_short(self, payload: dict) -> None:
        self._session.forget_short_term_memory()

    def _action_memory_forget_semantic(self, payload: dict) -> None:
        self._session.forget_semantic_memory()

    def _action_approve(self, payload: dict) -> None:
        self._session.approve_agent_task(str(payload.get("token", "")))

    def _action_deny(self, payload: dict) -> None:
        self._session.deny_agent_task(str(payload.get("token", "")))

    def _action_start_ollama(self, payload: dict) -> None:
        threading.Thread(target=self._start_ollama, daemon=True).start()

    def _action_free_vram(self, payload: dict) -> None:
        threading.Thread(target=self._free_vram, daemon=True).start()

    def _action_export_diagnostics(self, payload: dict) -> None:
        threading.Thread(target=self._export_diagnostics, daemon=True).start()

    def _action_reboot_session(self, payload: dict) -> None:
        self._reboot_session()

    def _create_persona(self, payload: dict) -> dict:
        source = self._persona_by_name(str(payload.get("source", self._persona.name)))
        name = self._unique_persona_name("New Persona")
        self._persona_config.custom_personas[name] = persona_config.override_from_persona(
            personas.Persona(
                name=name,
                voice=source.voice,
                voice_backend=source.voice_backend,
                voice_model=source.voice_model,
                tts_options=source.tts_options,
                personality=source.personality,
                blurb="",
                model=source.model,
                tool_user=source.tool_user,
            )
        )
        persona_config.save_config(self._persona_config)
        self._reload_personas()
        self._activate_persona(name)
        return {"ok": True, "message": f"Created {name}.", "status": self._status_payload()}

    def _save_persona(self, payload: dict) -> dict:
        original = str(payload.get("originalName", "")).strip()
        name = str(payload.get("name", "")).strip()
        if not name:
            return {
                "ok": False,
                "error": "Persona name is required.",
                "status": self._status_payload(),
            }
        if not str(payload.get("systemPrompt", "")).strip():
            return {
                "ok": False,
                "error": "System prompt is required.",
                "status": self._status_payload(),
            }
        builtin_names = self._builtin_persona_names()
        if original in builtin_names and name != original:
            return {
                "ok": False,
                "error": "Built-in personas cannot be renamed. Duplicate it first.",
                "status": self._status_payload(),
            }
        if name != original and name in self._persona_names():
            return {
                "ok": False,
                "error": f"{name} already exists.",
                "status": self._status_payload(),
            }
        override = self._override_from_payload(payload)
        if not persona_config.valid_tool_user(override.tool_user):
            return {
                "ok": False,
                "error": f"Unknown tool user: {override.tool_user}",
                "status": self._status_payload(),
            }
        if original in builtin_names:
            self._persona_config.personas[original] = override
        else:
            self._persona_config.custom_personas.pop(original, None)
            self._persona_config.custom_personas[name] = override
        persona_config.save_config(self._persona_config)
        self._reload_personas()
        if original == self._persona.name or name == self._persona.name:
            self._activate_persona(name)
        return {"ok": True, "message": f"Saved {name}.", "status": self._status_payload()}

    def _duplicate_persona(self, payload: dict) -> dict:
        source = self._persona_by_name(str(payload.get("name", self._persona.name)))
        name = self._unique_persona_name(f"{source.name} Copy")
        self._persona_config.custom_personas[name] = persona_config.override_from_persona(
            personas.Persona(
                name=name,
                voice=source.voice,
                voice_backend=source.voice_backend,
                voice_model=source.voice_model,
                tts_options=source.tts_options,
                personality=source.personality,
                blurb=source.blurb,
                model=source.model,
                tool_user=source.tool_user,
            )
        )
        persona_config.save_config(self._persona_config)
        self._reload_personas()
        self._activate_persona(name)
        return {
            "ok": True,
            "message": f"Duplicated {source.name}.",
            "status": self._status_payload(),
        }

    def _delete_persona(self, payload: dict) -> dict:
        name = str(payload.get("name", "")).strip()
        if not name:
            return {
                "ok": False,
                "error": "Persona name is required.",
                "status": self._status_payload(),
            }
        if name in self._builtin_persona_names():
            self._persona_config.personas.pop(name, None)
            message = f"Reset {name} to built-in defaults."
        else:
            self._persona_config.custom_personas.pop(name, None)
            message = f"Deleted {name}."
        persona_config.save_config(self._persona_config)
        self._reload_personas()
        if self._persona.name == name:
            self._activate_persona(
                app_state.resolve_persona_name("", self._persona_names(), cfg.DEFAULT_PERSONA_NAME)
            )
        return {"ok": True, "message": message, "status": self._status_payload()}

    def _override_from_payload(self, payload: dict) -> persona_config.PersonaOverride:
        backend = str(payload.get("voiceBackend", "")).strip() or cfg.TTS_BACKEND
        tts_options = {}
        if backend == "coqui":
            tts_options = {
                "speaker": str(payload.get("coquiSpeaker", "")).strip(),
                "language": str(payload.get("coquiLanguage", "")).strip(),
                "device": str(payload.get("coquiDevice", "")).strip() or cfg.COQUI_TTS_DEVICE,
            }
        return persona_config.PersonaOverride(
            voice=str(payload.get("voice", "")).strip() or self._persona.voice,
            voice_backend=backend,
            voice_model=str(payload.get("voiceModel", "")).strip() or None,
            tts_options={k: v for k, v in tts_options.items() if v},
            personality=str(payload.get("systemPrompt", "")).strip(),
            blurb=str(payload.get("description", "")).strip(),
            model=str(payload.get("model", "")).strip() or None,
            tool_user=str(payload.get("toolUser", "")).strip() or None,
        )

    def _activate_persona(self, name: str) -> None:
        self._persona = self._persona_by_name(name)
        self._model = self._persona.model_name(cfg.LLM_MODEL)
        self._use_persona_tts_defaults(self._persona)
        self._session.set_persona(self._persona)
        self._save_state()

    def _unique_persona_name(self, stem: str) -> str:
        names = set(self._persona_names())
        if stem not in names:
            return stem
        index = 2
        while f"{stem} {index}" in names:
            index += 1
        return f"{stem} {index}"

    def _start_ollama(self) -> None:
        try:
            dashboard.start_ollama_app()
            self._publish({"type": "sys", "text": "Ollama tray app started."})
        except Exception as exc:
            self._publish({"type": "sys", "text": f"Could not start Ollama: {exc}"})

    def _free_vram(self) -> None:
        self._unload_ollama_models(announce=True)

    def _unload_ollama_models(self, *, announce: bool) -> None:
        try:
            count = dashboard.stop_loaded_models(cfg.OLLAMA_HOST)
            message = f"Unloaded {count} Ollama model(s)."
            if announce:
                self._publish({"type": "sys", "text": message})
            else:
                logger.info(message)
        except Exception as exc:
            message = f"Could not unload models: {exc}"
            if announce:
                self._publish({"type": "sys", "text": message})
            else:
                logger.warning(message)

    def _export_diagnostics(self) -> None:
        try:
            report = diagnostics.build_report(
                session_snapshot=self._session.export_snapshot(),
                tts_backend=self._tts_provider,
                ollama=self._health,
                tts=self._tts_health,
                latency_line=dashboard.format_latency_line(self._latency),
                jobs=self._session.agent_history(),
                devices=diagnostics.audio_devices(),
            )
            path = diagnostics.write_bundle(cfg.DATA_DIR, report)
            self._publish({"type": "sys", "text": f"Diagnostics written to {path}"})
        except Exception as exc:
            self._publish({"type": "sys", "text": f"Diagnostics export failed: {exc}"})

    def _reboot_session(self) -> None:
        self._publish({"type": "session", "state": "rebooting"})
        self._stop_session(unload_models=True, announce=True)
        self._session = self._new_session()
        self._session.set_muted(self._muted)
        self._start_session_thread()

    def _stream_avatar_audio(self, handler: BaseHTTPRequestHandler) -> None:
        """Stream the newest normalized TTS envelope to one local browser client."""
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("Connection", "keep-alive")
        handler.end_headers()
        sequence = 0
        try:
            while not self._stop.is_set():
                sequence, envelope, closed = self._avatar_audio.wait_after(sequence, timeout=10.0)
                if closed or self._stop.is_set():
                    break
                payload = b": keepalive\n\n" if envelope is None else sse_data(sequence, envelope)
                handler.wfile.write(payload)
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _handler_class(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            # SSE chunks here are small and latency-critical: each one is a
            # sentence the frontend can start speaking. Nagle would hold them
            # back to coalesce, turning a stream into one late delivery.
            disable_nagle_algorithm = True

            def do_GET(self) -> None:
                # Same contract as do_POST: never leave a request unanswered.
                try:
                    self._dispatch_get()
                except Exception as exc:
                    logger.exception(f"GET {self.path} failed: {exc}")
                    try:
                        self._send_json(
                            {"error": {"message": str(exc), "type": "server_error"}},
                            status=HTTPStatus.INTERNAL_SERVER_ERROR,
                        )
                    except Exception:
                        pass

            def _dispatch_get(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/api/avatar-audio":
                    app._stream_avatar_audio(self)
                    return
                if parsed.path == "/health":
                    # This endpoint gates dependency-ordered stack startup. It
                    # must report whether the brain can accept requests, not
                    # whether the later-starting physical mic client has already
                    # acknowledged its command files. Mic/mode readiness stays
                    # explicit in the payload and /api/status so the UI remains
                    # fail-closed without deadlocking the launcher.
                    brain_ready = app._session_state == "ready"
                    ready = cfg.RAP_MODE != "brain" or brain_ready
                    payload = {
                        "ok": ready,
                        "model": cfg.S2S_BRIDGE_MODEL,
                        "mode": cfg.RAP_MODE,
                    }
                    if cfg.RAP_MODE == "brain":
                        payload["session"] = app._session_state
                        payload["muteReady"] = app._s2s_mute_ready
                        payload["modeReady"] = app._s2s_mode_ready
                    self._send_json(
                        payload,
                        status=HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE,
                    )
                    return
                if parsed.path == "/v1/models":
                    self._send_json({"object": "list", "data": [{"id": cfg.S2S_BRIDGE_MODEL, "object": "model"}]})
                    return
                if parsed.path == "/api/status":
                    self._send_json(app._status_payload())
                    return
                if parsed.path == "/api/agents":
                    self._send_json(app._agents_payload())
                    return
                if parsed.path == "/api/events":
                    after = int(parse_qs(parsed.query).get("after", ["0"])[0] or 0)
                    self._send_json(app._events_after(after))
                    return
                if parsed.path == "/api/cli-diagnostics":
                    self._send_json(app._cli_diagnostics_payload())
                    return
                self._send_static(parsed.path)

            def do_POST(self) -> None:
                # Any handler raising past this point would leave the request
                # without a response, so the caller hangs until its own timeout.
                # The realtime frontend sits behind this endpoint, so a hang
                # costs a whole conversation turn; answer with an error instead.
                try:
                    self._dispatch_post()
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError) as exc:
                    # Expected when barge-in cancels the frontend's streaming
                    # HTTP request. The brain turn may already have completed;
                    # dumping a full traceback only buries actual failures.
                    logger.info(f"POST {self.path} client disconnected: {exc}")
                except Exception as exc:
                    logger.exception(f"POST {self.path} failed: {exc}")
                    try:
                        self._send_json(
                            {"error": {"message": str(exc), "type": "server_error"}},
                            status=HTTPStatus.INTERNAL_SERVER_ERROR,
                        )
                    except Exception:
                        # Headers already went out (a stream, say); nothing left
                        # to say on this connection.
                        pass

            def _dispatch_post(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/v1/chat/completions":
                    if not self._authorized_s2s():
                        self._send_json({"error": {"message": "unauthorized", "type": "authentication_error"}}, status=HTTPStatus.UNAUTHORIZED)
                        return
                    payload = self._read_json()
                    text = openai_bridge._latest_user_text(payload)
                    if not text:
                        self._send_json({"error": {"message": "no user message", "type": "invalid_request_error"}}, status=HTTPStatus.BAD_REQUEST)
                        return
                    if not hasattr(app._session, "complete_text"):
                        self._send_json({"error": {"message": "GUI session is not in brain mode", "type": "server_error"}}, status=HTTPStatus.CONFLICT)
                        return
                    model = str(payload.get("model") or cfg.S2S_BRIDGE_MODEL)
                    streaming = bool(payload.get("stream")) and cfg.S2S_BRIDGE_STREAMING
                    if streaming and hasattr(app._session, "stream_text"):
                        # The whole point of streaming here: the frontend starts
                        # speaking on the first sentence instead of waiting for
                        # the model to finish the reply.
                        self._send_chat_stream(app._session.stream_text(text), model)
                        return
                    answer = app._session.complete_text(text)
                    if streaming:
                        self._send_chat_stream([answer], model)
                        return
                    self._send_json(openai_bridge._chat_completion(answer, model))
                    return
                if parsed.path == "/api/stack-shutdown":
                    if not self._authorized_s2s():
                        self._send_json(
                            {"error": {"message": "unauthorized"}},
                            status=HTTPStatus.UNAUTHORIZED,
                        )
                        return
                    if cfg.RAP_MODE != "brain":
                        self._send_json(
                            {"error": {"message": "stack shutdown is brain-mode only"}},
                            status=HTTPStatus.CONFLICT,
                        )
                        return
                    self._send_json({"ok": True})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                if parsed.path == "/api/avatar-envelope":
                    if not self._authorized_s2s():
                        self._send_json({"error": {"message": "unauthorized"}}, status=HTTPStatus.UNAUTHORIZED)
                        return
                    app._ingest_avatar_envelope(self._read_json())
                    self._send_json({"ok": True})
                    return
                if parsed.path == "/api/turn-timing":
                    if not self._authorized_s2s():
                        self._send_json({"error": {"message": "unauthorized"}}, status=HTTPStatus.UNAUTHORIZED)
                        return
                    accepted = app._ingest_turn_timing(self._read_json())
                    self._send_json(
                        {"ok": accepted},
                        status=HTTPStatus.OK if accepted else HTTPStatus.BAD_REQUEST,
                    )
                    return
                if parsed.path == "/api/input-state":
                    if not self._authorized_s2s():
                        self._send_json(
                            {"error": {"message": "unauthorized"}},
                            status=HTTPStatus.UNAUTHORIZED,
                        )
                        return
                    accepted = app._ingest_input_state(self._read_json())
                    self._send_json(
                        {"ok": accepted},
                        status=HTTPStatus.OK if accepted else HTTPStatus.CONFLICT,
                    )
                    return
                if parsed.path != "/api/action":
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                if not secrets.compare_digest(
                    self.headers.get("X-Session-Token", ""), app._csrf_token
                ):
                    self.send_error(HTTPStatus.FORBIDDEN)
                    return
                payload = self._read_json()
                self._send_json(app._action(str(payload.get("action", "")), payload))

            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def _send_static(self, path: str) -> None:
                rel = "index.html" if path in {"", "/"} else path.lstrip("/")
                target = (_STATIC_DIR / rel).resolve()
                if _STATIC_DIR.resolve() not in target.parents and target != _STATIC_DIR.resolve():
                    self.send_error(HTTPStatus.FORBIDDEN)
                    return
                if not target.exists() or not target.is_file():
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                data = target.read_bytes()
                if rel == "index.html":
                    data = data.replace(
                        b"__CSRF_TOKEN_PLACEHOLDER__", app._csrf_token.encode("ascii")
                    )
                self.send_response(HTTPStatus.OK)
                self.send_header(
                    "Content-Type", mimetypes.guess_type(target.name)[0] or "text/plain"
                )
                self.send_header("Content-Length", str(len(data)))
                if rel.startswith("assets/avatars/"):
                    self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                else:
                    self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(data)

            def _read_json(self) -> dict:
                length = int(self.headers.get("Content-Length", "0") or 0)
                if not length:
                    return {}
                try:
                    return json.loads(self.rfile.read(length).decode("utf-8"))
                except ValueError:
                    return {}

            def _authorized_s2s(self) -> bool:
                key = cfg.S2S_BRIDGE_API_KEY
                header = self.headers.get("Authorization", "")
                if secrets.compare_digest(header, f"Bearer {key}"):
                    return True
                return secrets.compare_digest(self.headers.get("X-API-Key") or "", key)

            def _send_chat_stream(self, pieces, model: str) -> None:
                """Relay assistant text as SSE, flushing each piece as it arrives."""
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                chunk_id = "chatcmpl-gui"
                created = int(datetime.now().timestamp())

                def write(chunk: dict) -> None:
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()

                first = True
                for piece in pieces:
                    delta = {"content": piece}
                    if first:
                        delta = {"role": "assistant", "content": piece}
                        first = False
                    write(openai_bridge._stream_chunk(chunk_id, model, created, delta))
                write(openai_bridge._stream_chunk(chunk_id, model, created, {}, finish_reason="stop"))
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

            def _send_json(self, payload: dict, *, status: HTTPStatus = HTTPStatus.OK) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler


def _bundle_from_payload(
    payload: dict, voice_mode: str
) -> multimodal_prompt.MultimodalPromptBundle:
    bundle = multimodal_prompt.MultimodalPromptBundle(user_id=cfg.MEM0_USER_ID)
    bundle.voice_mode = voice_mode
    voice = str(payload.get("voiceDraft", "")).strip()
    notes = str(payload.get("notes", "")).strip()
    prompt = str(payload.get("prompt", "")).strip()
    if voice:
        bundle.add_voice_transcript(voice)
    bundle.set_text(notes)
    bundle.set_final_instruction(prompt)
    for item in payload.get("attachments", []):
        if not isinstance(item, dict):
            continue
        ref = str(item.get("reference", "")).strip()
        if ref:
            bundle.add_attachment(
                multimodal_prompt.attachment_from_reference(
                    ref, note=str(item.get("note", "")).strip()
                )
            )
    bundle.context_signals = multimodal_prompt.context_signals(
        "\n".join([voice, notes, prompt]),
        has_attachments=bool(bundle.attachments),
        draft_active=bool(voice or notes or prompt or bundle.attachments),
    )
    return bundle


def _json_safe(value):
    """Return a JSON-serializable copy of event/status payload values."""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    item = getattr(value, "item", None)
    if callable(item):
        return _json_safe(item())
    return str(value)


def run() -> None:
    """Launch the web UI. The sole entry point -- __main__.py just calls this."""
    if not process_guard.acquire_single_instance_lock():
        print("Remote Agent Protocol is already running.")
        sys.exit(1)
    process_guard.close_previous_instance()
    process_guard.write_lock()
    try:
        WebVoiceApp().run()
    finally:
        process_guard.release_lock()


if __name__ == "__main__":
    run()

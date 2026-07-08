"""Local web control center for Remote Agent Protocol."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import queue
import threading
import time
import webbrowser
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from remote_agent_protocol import (
    app_state,
    dashboard,
    diagnostics,
    logging_setup,
    multimodal_prompt,
    ollama_models,
    persona_config,
    personas,
    tts_factory,
    voicebox,
    voices,
)
from remote_agent_protocol import config as cfg
from remote_agent_protocol.session import VoiceSession

logging_setup.setup_logging(cfg.DEBUG_MODE)

_STATIC_DIR = Path(__file__).with_name("web_app")


class WebVoiceApp:
    """Serve the web UI and bridge it to the voice session."""

    def __init__(self) -> None:
        """Initialize state shared by the HTTP UI and voice thread."""
        self._events_in: queue.Queue[dict] = queue.Queue()
        self._event_log: deque[dict] = deque(maxlen=800)
        self._event_id = 0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._persona_config = persona_config.load_config()
        self._personas = persona_config.effective_personas(personas.PERSONAS, self._persona_config)
        self._app_state = app_state.load_state(cfg.APP_STATE_FILE)
        boot_name = app_state.resolve_persona_name(
            self._app_state.persona, self._persona_names(), cfg.DEFAULT_PERSONA_NAME
        )
        self._persona = self._persona_by_name(boot_name)
        self._model = self._persona.model_name(cfg.LLM_MODEL)
        self._voice = self._persona.voice
        self._voice_mode = multimodal_prompt.normalize_voice_mode(self._app_state.voice_mode)
        self._muted = False
        self._session_state = "starting"
        self._health = {"ok": False, "label": "Ollama checking"}
        self._tts_health = {"ok": False, "label": "TTS checking"}
        self._latency = dashboard.LatencyState()
        self._models = self._model_choices()
        self._voice_map = dict(voices.labelled() + voicebox.labelled_profiles())
        self._pending_confirms: list[dict] = []
        self._agent_jobs: dict[str, dict] = {}
        self._session = self._new_session()
        self._thread: threading.Thread | None = None

    def run(self) -> None:
        """Start the voice session, web server, and browser shell."""
        self._thread = threading.Thread(target=self._boot_thread, daemon=True)
        self._thread.start()
        threading.Thread(target=self._event_pump, daemon=True).start()
        threading.Thread(target=self._health_poller, daemon=True).start()

        server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_class())
        url = f"http://127.0.0.1:{server.server_address[1]}"
        print(f"Remote Agent Protocol web UI: {url}")
        webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self._stop.set()
            self._session.shutdown()
            server.server_close()

    def _new_session(self) -> VoiceSession:
        session = VoiceSession(self._persona, on_event=self._events_in.put)
        session.set_manual_prompt_mode(True)
        session.set_voice_mode(self._voice_mode)
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
        self._fold_event(evt)
        with self._lock:
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
        elif kind == "metric":
            self._latency.update(evt.get("bucket", ""), evt.get("kind", ""), evt.get("value", 0.0))
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
        elif kind == "agent_job":
            job_id = str(evt.get("job_id", ""))
            if job_id:
                self._agent_jobs[job_id] = {**self._agent_jobs.get(job_id, {}), **evt}

    def _health_poller(self) -> None:
        while not self._stop.is_set():
            health = dashboard.ollama_health(cfg.OLLAMA_HOST)
            self._publish({"type": "health", "ok": health.ok, "label": health.label})
            tts = dashboard.tts_health(
                cfg.TTS_BACKEND,
                voicebox_url=voicebox.base_url(),
                has_cartesia_key=bool(tts_factory.load_env_value("CARTESIA_API_KEY")),
            )
            self._publish({"type": "tts_health", "ok": tts.ok, "label": tts.label})
            self._stop.wait(4)

    def _persona_names(self) -> list[str]:
        return [persona.name for persona in self._personas]

    def _persona_by_name(self, name: str) -> personas.Persona:
        return next(
            (persona for persona in self._personas if persona.name == name), self._personas[0]
        )

    def _model_choices(self) -> list[str]:
        extra = [cfg.LLM_MODEL] + [persona.model for persona in self._personas if persona.model]
        return sorted(set(ollama_models.available(cfg.OLLAMA_HOST)) | set(extra))

    def _save_state(self) -> None:
        app_state.save_state(
            cfg.APP_STATE_FILE,
            app_state.AppState(
                persona=self._persona.name,
                tool_user=self._session.default_agent_backend(),
                voice_mode=self._voice_mode,
            ),
        )

    def _status_payload(self) -> dict:
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
            "session": self._session_state,
            "muted": self._muted,
            "voiceMode": self._voice_mode,
            "persona": self._persona.name,
            "personaBlurb": self._persona.blurb,
            "model": self._model,
            "voice": self._voice,
            "toolUser": self._session.default_agent_backend(),
            "personas": [
                {
                    "name": p.name,
                    "blurb": p.blurb,
                    "voice": p.voice,
                    "model": p.model_name(cfg.LLM_MODEL),
                }
                for p in self._personas
            ],
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
            "latency": self._latency.values,
            "pendingConfirms": self._pending_confirms,
            "memoryEnabled": cfg.MEMORY_ENABLED,
            "semanticMemoryEnabled": cfg.MEM0_ENABLED,
        }

    def _events_after(self, after: int) -> dict:
        with self._lock:
            events = [evt for evt in self._event_log if evt["id"] > after]
            latest = self._event_id
        return {"events": events, "latest": latest, "status": self._status_payload()}

    def _action(self, name: str, payload: dict) -> dict:
        if name == "mute":
            self._muted = bool(payload.get("muted"))
            self._session.set_muted(self._muted)
        elif name == "voice_mode":
            self._voice_mode = multimodal_prompt.normalize_voice_mode(payload.get("mode"))
            self._session.set_voice_mode(self._voice_mode)
            self._save_state()
        elif name == "ptt":
            self._session.set_push_to_talk(bool(payload.get("active")))
        elif name == "context_active":
            self._session.set_context_active(bool(payload.get("active")))
        elif name == "persona":
            self._persona = self._persona_by_name(str(payload.get("name", "")))
            self._model = self._persona.model_name(cfg.LLM_MODEL)
            self._voice = self._persona.voice
            self._session.set_persona(self._persona)
            self._save_state()
        elif name == "model":
            self._model = str(payload.get("model", ""))
            self._session.set_model(self._model)
        elif name == "voice":
            self._voice = str(payload.get("voice", ""))
            self._session.set_voice(self._voice)
        elif name == "tool_user":
            self._session.set_default_agent_backend(str(payload.get("backend", "")))
            self._save_state()
        elif name == "send":
            self._session.send_multimodal_prompt(_bundle_from_payload(payload, self._voice_mode))
        elif name == "delegate":
            bundle = _bundle_from_payload(payload, self._voice_mode)
            self._session.start_agent_task(
                self._session.default_agent_backend(), bundle.agent_prompt()
            )
        elif name == "restart_chat":
            self._session.restart_conversation()
        elif name == "refresh_memory":
            self._session.refresh_memories(str(payload.get("query", "")))
        elif name == "approve":
            self._session.approve_agent_task(str(payload.get("token", "")))
        elif name == "deny":
            self._session.deny_agent_task(str(payload.get("token", "")))
        elif name == "start_ollama":
            threading.Thread(target=self._start_ollama, daemon=True).start()
        elif name == "free_vram":
            threading.Thread(target=self._free_vram, daemon=True).start()
        elif name == "export_diagnostics":
            threading.Thread(target=self._export_diagnostics, daemon=True).start()
        elif name == "reboot_session":
            self._reboot_session()
        else:
            return {"ok": False, "error": f"unknown action: {name}"}
        return {"ok": True, "status": self._status_payload()}

    def _start_ollama(self) -> None:
        try:
            dashboard.start_ollama_app()
            self._publish({"type": "sys", "text": "Ollama tray app started."})
        except Exception as exc:
            self._publish({"type": "sys", "text": f"Could not start Ollama: {exc}"})

    def _free_vram(self) -> None:
        try:
            count = dashboard.stop_loaded_models(cfg.OLLAMA_HOST)
            self._publish({"type": "sys", "text": f"Unloaded {count} Ollama model(s)."})
        except Exception as exc:
            self._publish({"type": "sys", "text": f"Could not unload models: {exc}"})

    def _export_diagnostics(self) -> None:
        try:
            report = diagnostics.build_report(
                session_snapshot=self._session.export_snapshot(),
                tts_backend=cfg.TTS_BACKEND,
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
        self._session.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._session = self._new_session()
        self._model = self._persona.model_name(cfg.LLM_MODEL)
        self._voice = self._persona.voice
        self._session.set_muted(self._muted)
        self._thread = threading.Thread(target=self._boot_thread, daemon=True)
        self._thread.start()

    def _handler_class(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == "/api/status":
                    self._send_json(app._status_payload())
                    return
                if parsed.path == "/api/events":
                    after = int(parse_qs(parsed.query).get("after", ["0"])[0] or 0)
                    self._send_json(app._events_after(after))
                    return
                self._send_static(parsed.path)

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path != "/api/action":
                    self.send_error(HTTPStatus.NOT_FOUND)
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
                self.send_response(HTTPStatus.OK)
                self.send_header(
                    "Content-Type", mimetypes.guess_type(target.name)[0] or "text/plain"
                )
                self.send_header("Content-Length", str(len(data)))
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

            def _send_json(self, payload: dict) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(HTTPStatus.OK)
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


def run() -> None:
    """Launch the web UI."""
    WebVoiceApp().run()


if __name__ == "__main__":
    run()

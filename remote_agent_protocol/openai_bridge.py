"""Loopback OpenAI-compatible bridge for external realtime voice frontends."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
import uuid
from collections.abc import Iterator
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from loguru import logger

from remote_agent_protocol import app_state, logging_setup, persona_config, personas
from remote_agent_protocol import config as cfg
from remote_agent_protocol.brain import BrainSession


class BrainBridgeRuntime:
    """Own a BrainSession on a dedicated asyncio loop for sync HTTP handlers."""

    def __init__(self) -> None:
        """Create the loop, boot persona, and brain session."""
        self.loop = asyncio.new_event_loop()
        self.events: list[dict] = []
        self.brain = BrainSession(self._boot_persona(), on_event=self._on_event)
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="rap-brain-loop")

    def start(self) -> None:
        """Start the brain event loop and async session services."""
        self._thread.start()
        self.submit(self.brain.start()).result(timeout=30)

    def stop(self) -> None:
        """Stop the async brain session and join the loop thread."""
        try:
            self.submit(self.brain.stop()).result(timeout=30)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self._thread.join(timeout=5)

    def complete(self, text: str, timeout: float = 180.0) -> str:
        """Run one text turn through the brain from an HTTP handler thread."""
        return self.submit(self.brain.complete(text)).result(timeout=timeout)

    def stream(self, text: str, timeout: float = 180.0) -> Iterator[str]:
        """Yield brain output incrementally across the async/thread boundary."""
        pieces: queue.Queue[object] = queue.Queue()
        done = object()

        async def pump() -> None:
            try:
                async for piece in self.brain.complete_stream(text):
                    pieces.put(piece)
            except Exception as exc:
                pieces.put(exc)
            finally:
                pieces.put(done)

        self.submit(pump())
        while True:
            piece = pieces.get(timeout=timeout)
            if piece is done:
                return
            if isinstance(piece, Exception):
                raise piece
            yield str(piece)

    def submit(self, coro):
        """Schedule a coroutine on the brain loop."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _on_event(self, event: dict) -> None:
        self.events.append(event)
        del self.events[:-200]

    @staticmethod
    def _boot_persona() -> personas.Persona:
        config = persona_config.load_config()
        effective = persona_config.effective_personas(personas.PERSONAS, config)
        state = app_state.load_state(cfg.APP_STATE_FILE)
        name = app_state.resolve_persona_name(state.persona, [p.name for p in effective], cfg.DEFAULT_PERSONA_NAME)
        return next((p for p in effective if p.name == name), effective[0])


def run_server() -> None:
    """Run the bridge forever until Ctrl+C."""
    logging_setup.setup_logging(cfg.DEBUG_MODE)
    runtime = BrainBridgeRuntime()
    runtime.start()
    server = ThreadingHTTPServer((cfg.S2S_BRIDGE_HOST, cfg.S2S_BRIDGE_PORT), _handler_class(runtime))
    url = f"http://{cfg.S2S_BRIDGE_HOST}:{cfg.S2S_BRIDGE_PORT}/v1"
    logger.info(f"Remote Agent Protocol brain bridge listening at {url}")
    print(f"Remote Agent Protocol brain bridge: {url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        runtime.stop()


def _handler_class(runtime: BrainBridgeRuntime):
    class Handler(BaseHTTPRequestHandler):
        server_version = "RemoteAgentProtocolBrain/1.0"
        disable_nagle_algorithm = True

        def do_GET(self) -> None:  # noqa: N802 - stdlib hook
            path = urlparse(self.path).path
            if path == "/health":
                self._json({"ok": True, "model": cfg.S2S_BRIDGE_MODEL})
                return
            if path == "/v1/models":
                self._json({"object": "list", "data": [{"id": cfg.S2S_BRIDGE_MODEL, "object": "model"}]})
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802 - stdlib hook
            path = urlparse(self.path).path
            if path != "/v1/chat/completions":
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if not self._authorized():
                self._json({"error": {"message": "unauthorized", "type": "authentication_error"}}, status=401)
                return
            try:
                payload = self._read_json()
                user_text = _latest_user_text(payload)
                if not user_text:
                    self._json({"error": {"message": "no user message", "type": "invalid_request_error"}}, status=400)
                    return
                model = str(payload.get("model") or cfg.S2S_BRIDGE_MODEL)
                if bool(payload.get("stream")) and cfg.S2S_BRIDGE_STREAMING:
                    self._stream_chat_completion(runtime.stream(user_text), model)
                    return
                answer = runtime.complete(user_text)
                self._json(_chat_completion(answer, model))
            except Exception as exc:  # pragma: no cover - exercised by integration smoke
                logger.opt(exception=exc).error("Brain bridge request failed")
                self._json({"error": {"message": str(exc), "type": "server_error"}}, status=500)

        def log_message(self, fmt: str, *args: Any) -> None:
            logger.debug("brain-bridge " + fmt, *args)

        def _authorized(self) -> bool:
            header = self.headers.get("Authorization", "")
            if header == f"Bearer {cfg.S2S_BRIDGE_API_KEY}":
                return True
            # OpenAI-compatible local clients sometimes pass the key in a nonstandard header.
            return self.headers.get("X-API-Key") == cfg.S2S_BRIDGE_API_KEY

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError("JSON body must be an object")
            return value

        def _json(self, payload: dict, *, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _stream_chat_completion(self, pieces: Iterator[str], model: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
            created = int(time.time())
            first = True
            for piece in pieces:
                delta = {"content": piece}
                if first:
                    delta["role"] = "assistant"
                    first = False
                payload = _stream_chunk(chunk_id, model, created, delta)
                self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode())
                self.wfile.flush()
            payload = _stream_chunk(chunk_id, model, created, {}, finish_reason="stop")
            self.wfile.write(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    return Handler


def _latest_user_text(payload: dict) -> str:
    messages = payload.get("messages") or []
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [str(item.get("text", "")) for item in content if isinstance(item, dict)]
            return "\n".join(part for part in parts if part).strip()
    return ""


def _chat_completion(answer: str, model: str) -> dict:
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": answer}, "finish_reason": "stop"}],
    }


def _stream_chunk(chunk_id: str, model: str, created: int, delta: dict, *, finish_reason: str | None = None) -> dict:
    return {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


if __name__ == "__main__":
    run_server()

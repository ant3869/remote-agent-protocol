"""A minimal, controllable OpenAI-compatible HTTP server for provider_tests.py.

Not a stand-in for any specific provider -- just enough of the shape
(``GET /models`` or ``/key``, ``POST /chat/completions``) to drive every
stage of the test pipeline (reach, auth, catalog, chat, tool call, JSON)
without a real network call. A test mutates ``server.script`` between
requests to switch behavior; every request received is recorded on
``script.requests`` for assertions.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class FakeServerScript:
    """What the fake server does next. A test mutates this in place."""

    models_status: int = 200
    models_body: dict[str, Any] = field(default_factory=lambda: {"data": []})
    # Separate from models_* so a preset with its own key-info path (like
    # OpenRouter's /key) can be tested independently of the /models catalog.
    key_status: int = 200
    key_body: dict[str, Any] = field(default_factory=dict)
    chat_status: int = 200
    chat_body: dict[str, Any] = field(default_factory=dict)
    # A model test makes up to three POSTs (chat, tool call, JSON) that each
    # need a different body; set this to serve them in order, one per call,
    # falling back to ``chat_body`` once exhausted.
    chat_bodies: list[dict[str, Any]] = field(default_factory=list)
    delay_secs: float = 0.0
    require_auth: bool = False
    expected_key: str = ""
    requests: list[dict[str, Any]] = field(default_factory=list)

    def next_chat_body(self) -> dict[str, Any]:
        """The body for the next POST, consuming ``chat_bodies`` in order."""
        if self.chat_bodies:
            return self.chat_bodies.pop(0)
        return self.chat_body


def closed_port() -> int:
    """A loopback port nothing is listening on, for a reach-failure test."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class FakeOpenAIServer:
    """A threaded HTTP server on an ephemeral loopback port."""

    def __init__(self) -> None:
        self.script = FakeServerScript()
        script = self.script

        class Handler(BaseHTTPRequestHandler):
            def _authorized(self) -> bool:
                if not script.require_auth:
                    return True
                return self.headers.get("Authorization") == f"Bearer {script.expected_key}"

            def _write_json(self, status: int, body: dict) -> None:
                if script.delay_secs:
                    time.sleep(script.delay_secs)
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                script.requests.append({"method": "GET", "path": self.path})
                if not self._authorized():
                    self._write_json(401, {"error": {"message": "unauthorized"}})
                    return
                if "/key" in self.path:
                    self._write_json(script.key_status, script.key_body)
                    return
                if "/models" in self.path:
                    self._write_json(script.models_status, script.models_body)
                    return
                self._write_json(404, {"error": {"message": "not found"}})

            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                length = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    payload = json.loads(raw.decode("utf-8")) if raw else {}
                except ValueError:
                    payload = {}
                script.requests.append({"method": "POST", "path": self.path, "json": payload})
                if not self._authorized():
                    self._write_json(401, {"error": {"message": "unauthorized"}})
                    return
                self._write_json(script.chat_status, script.next_chat_body())

            def log_message(self, *_args: Any) -> None:
                return  # silence the test run

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        """The provider base URL for this server, e.g. ``http://127.0.0.1:PORT/v1``."""
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def start(self) -> None:
        """Start serving on a background thread."""
        self._thread.start()

    def stop(self) -> None:
        """Shut down the server and release its socket."""
        self._server.shutdown()
        self._server.server_close()

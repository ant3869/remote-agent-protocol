"""Loopback WebSocket fan-out for normalized agent lifecycle events."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime

from loguru import logger
from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

_FIELDS = (
    "job_id",
    "agent",
    "machine",
    "task",
    "status",
    "state",
    "action",
    "tool",
    "step",
    "step_total",
    "last_completed_step",
    "summary",
    "elapsed_secs",
    "secs",
    "failure_kind",
    "failure_detail",
    "model_label",
    "host_modified",
)
_PROGRESS_STATES = {
    "in_progress",
    "tool_running",
    "step_completed",
    "waiting",
    "blocked",
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def normalize_event(event: dict, *, sequence: int, received_at: str) -> dict | None:
    """Return the stable public v1 envelope, excluding raw agent output."""
    if event.get("type") != "agent_job" or not all(
        event.get(field) for field in ("job_id", "agent", "task")
    ):
        return None
    internal = event.get("event")
    if internal == "started":
        public = "started"
        timestamp = event.get("started_at") or received_at
    elif internal == "progress" and event.get("state") in _PROGRESS_STATES:
        public = event["state"]
        timestamp = received_at
    elif internal == "finished":
        status = event.get("status")
        public = "completed" if status == "done" else status
        if public not in {"completed", "failed", "cancelled"}:
            return None
        timestamp = event.get("finished_at") or received_at
    else:
        return None
    payload = {
        "schema_version": 1,
        "sequence": sequence,
        "event": public,
        "timestamp": timestamp,
    }
    payload.update({field: event[field] for field in _FIELDS if event.get(field) not in (None, "")})
    return payload


class LifecycleEventServer:
    """Broadcast lifecycle JSON without backpressuring the voice event loop."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        path: str = "/events",
        queue_size: int = 64,
        on_status: Callable[[dict], None] | None = None,
    ):
        """Initialize a loopback lifecycle server."""
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("lifecycle WebSocket host must be loopback")
        self.host = host
        self.port = port
        self.path = path
        self._queue_size = queue_size
        self._on_status = on_status
        self._server = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: dict[ServerConnection, asyncio.Queue[str]] = {}
        self._sequence = 0

    async def start(self) -> bool:
        """Bind the server; report a visible degraded state on failure."""
        if self._server is not None:
            return True
        self._loop = asyncio.get_running_loop()
        try:
            self._server = await serve(self._handle_client, self.host, self.port)
        except OSError as exc:
            logger.warning(f"Lifecycle WebSocket unavailable on {self.host}:{self.port}: {exc}")
            self._status("degraded", str(exc))
            return False
        if self.port == 0:
            self.port = self._server.sockets[0].getsockname()[1]
        logger.info(f"Lifecycle WebSocket listening on ws://{self.host}:{self.port}{self.path}")
        self._status("ready", "")
        return True

    def publish(self, event: dict) -> None:
        """Schedule one event in callback order; safe from non-loop threads."""
        if self._loop is None or self._loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            self._publish_now(event)
        else:
            self._loop.call_soon_threadsafe(self._publish_now, event)

    def _publish_now(self, event: dict) -> None:
        self._sequence += 1
        payload = normalize_event(event, sequence=self._sequence, received_at=_utc_now())
        if payload is None:
            return
        message = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        for connection, queue in list(self._clients.items()):
            if queue.full():
                self._clients.pop(connection, None)
                asyncio.create_task(connection.close(code=1013, reason="lifecycle client too slow"))
            else:
                queue.put_nowait(message)

    async def _handle_client(self, connection: ServerConnection) -> None:
        request = connection.request
        if request is None or request.path != self.path:
            await connection.close(code=1008, reason="unsupported path")
            return
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=self._queue_size)
        self._clients[connection] = queue
        closed = asyncio.create_task(connection.wait_closed())
        try:
            while True:
                pending_message = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait(
                    {closed, pending_message}, return_when=asyncio.FIRST_COMPLETED
                )
                if closed in done:
                    pending_message.cancel()
                    await asyncio.gather(pending_message, return_exceptions=True)
                    break
                await connection.send(pending_message.result())
        except ConnectionClosed:
            pass
        finally:
            self._clients.pop(connection, None)
            if not closed.done():
                closed.cancel()
            await asyncio.gather(closed, return_exceptions=True)

    async def stop(self) -> None:
        """Close clients and release the listening port; safe to call twice."""
        server, self._server = self._server, None
        clients = list(self._clients)
        self._clients.clear()
        if clients:
            await asyncio.gather(
                *(client.close(code=1001, reason="session stopping") for client in clients),
                return_exceptions=True,
            )
        if server is not None:
            server.close()
            await server.wait_closed()
        self._loop = None

    def _status(self, state: str, error: str) -> None:
        if self._on_status is None:
            return
        try:
            self._on_status(
                {
                    "type": "lifecycle_ws",
                    "state": state,
                    "url": f"ws://{self.host}:{self.port}{self.path}",
                    "error": error,
                }
            )
        except Exception as exc:
            logger.warning(f"Lifecycle WebSocket status callback raised: {exc}")

"""Serve this machine's agent backends to an authenticated Remote Agent Protocol peer.

Run this on the machine that has the agents -- the laptop, the workstation --
and RAP on another machine can delegate to them exactly as if they were local::

    python -m remote_agent_protocol.remote_host --host 0.0.0.0 --port 8790

It offers the three routes in :mod:`remote_agent_protocol.remote_protocol`:
capability discovery, heartbeat, and job execution whose output streams back a
line at a time. Nothing else. In particular it does not read the conversation,
persist history, or accept a command to run -- only the *name* of a backend this
machine has already been configured with, so a peer can never turn this into a
general remote shell.

Two deliberate refusals: it will not start without ``AGENT_REMOTE_TOKEN``, and
it binds loopback unless told otherwise. Agents run arbitrary tools on this
machine, so reaching this port must be an authorized act, not an accident of
being on the same network.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from itertools import count
from urllib.parse import urlparse

from loguru import logger

from remote_agent_protocol import config as cfg
from remote_agent_protocol import logging_setup, remote_protocol
from remote_agent_protocol.agent_bridge import (
    build_command,
    resolve_cwd,
    with_scope,
    with_status_protocol,
)

_JOB_IDS = count(1)


class RemoteAgentHost:
    """Runs local agent backends on behalf of an authenticated peer."""

    def __init__(
        self,
        *,
        backends: dict | None = None,
        machine: str = "",
        token: str = "",
        workspace_dir: str | None = None,
        scope_preamble: str | None = None,
        kill_grace_secs: float = 3.0,
    ):
        """Initialize the host.

        Args:
            backends: Agent names mapped to command templates; defaults to this
                machine's configured backends.
            machine: Display name peers show for jobs run here.
            token: Shared bearer secret every request must carry.
            workspace_dir: Default working directory for jobs.
            scope_preamble: Scope text appended to each task; the host applies it
                because only this machine knows the real path a job will run in.
            kill_grace_secs: Grace before a cancelled job is killed outright.
        """
        self._backends = dict(cfg.AGENT_BACKENDS if backends is None else backends)
        self._machine = machine or cfg.AGENT_LOCAL_MACHINE
        self._token = token or cfg.AGENT_REMOTE_TOKEN
        self._workspace_dir = cfg.AGENT_WORKSPACE_DIR if workspace_dir is None else workspace_dir
        self._scope_preamble = (
            cfg.AGENT_SCOPE_PREAMBLE if scope_preamble is None else scope_preamble
        )
        self._kill_grace_secs = kill_grace_secs
        self._started_at = time.monotonic()
        self._lock = threading.Lock()
        self._running: dict[str, subprocess.Popen] = {}

    # -- protocol answers ---------------------------------------------------

    def capabilities(self) -> remote_protocol.HostCapabilities:
        """Describe what this machine offers a peer."""
        return remote_protocol.HostCapabilities(
            machine=self._machine,
            agents=tuple(sorted(self._backends)),
            workspace=str(self._workspace_dir or ""),
            heartbeat_secs=cfg.AGENT_REMOTE_HEARTBEAT_SECS,
        )

    def heartbeat(self) -> remote_protocol.Heartbeat:
        """Report liveness and current load."""
        with self._lock:
            active = sum(1 for proc in self._running.values() if proc.poll() is None)
        return remote_protocol.Heartbeat(
            machine=self._machine,
            active_jobs=active,
            uptime_secs=time.monotonic() - self._started_at,
        )

    def cancel(self, job_id: str) -> bool:
        """Stop a running job; False if it is unknown or already finished."""
        with self._lock:
            proc = self._running.get(job_id)
        if proc is None or proc.poll() is not None:
            return False
        proc.terminate()
        try:
            proc.wait(timeout=self._kill_grace_secs)
        except subprocess.TimeoutExpired:
            proc.kill()
        return True

    def run_job(self, request: remote_protocol.JobRequest, emit) -> None:
        """Run one job, handing each protocol event to ``emit`` as it happens.

        The agent's own stdout is relayed verbatim: RAP's status markers, tool
        chatter, and provider errors all mean the same thing whichever machine
        produced them, so the peer parses them with the same code it uses for a
        local job.
        """
        template = self._backends.get(request.agent)
        if template is None:
            emit(remote_protocol.error_event(f"unknown agent backend '{request.agent}'"))
            emit(remote_protocol.exit_event(127))
            return

        cwd = resolve_cwd(request.cwd or None, self._workspace_dir)
        task = with_status_protocol(with_scope(request.task, cwd, self._scope_preamble))
        command = build_command(template, task, extra_args=list(request.extra_args))
        # Same Windows shim problem as the local launcher: CreateProcess only
        # auto-appends .EXE, so a .CMD entry point has to be resolved first.
        if resolved := shutil.which(command[0]):
            command[0] = resolved

        job_id = f"remote-{next(_JOB_IDS)}"
        try:
            proc = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                # Pinned for the same reason as every other agent launch here:
                # the OS-locale codec crashes the reader on any non-cp1252 byte.
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            emit(remote_protocol.error_event(f"could not launch {command[0]}: {exc}"))
            emit(remote_protocol.exit_event(127))
            return

        with self._lock:
            self._running[job_id] = proc
        emit(remote_protocol.started_event(job_id))
        logger.info(f"Remote job {job_id} [{request.agent}] started for a peer")
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                emit(remote_protocol.line_event(line.rstrip("\r\n")))
            code = proc.wait()
        except BaseException:
            # Usually the peer hanging up mid-job. Nobody is left to receive
            # this agent's answer and its id is about to be forgotten, so stop
            # it here rather than leaving it running unreachable.
            logger.warning(f"Remote job {job_id} lost its peer; stopping the agent")
            proc.kill()
            proc.wait()
            raise
        finally:
            with self._lock:
                self._running.pop(job_id, None)
        emit(remote_protocol.exit_event(code))
        logger.info(f"Remote job {job_id} [{request.agent}] exited with {code}")

    # -- server -------------------------------------------------------------

    def handler_class(self):
        """Build the request handler bound to this host."""
        host = self

        class Handler(BaseHTTPRequestHandler):
            # Job output is a live stream; buffering it would defeat the point.
            disable_nagle_algorithm = True
            protocol_version = "HTTP/1.0"

            def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
                logger.debug(f"remote-host {self.address_string()} {format % args}")

            def do_GET(self) -> None:
                if not self._authorized():
                    return
                path = urlparse(self.path).path
                if path == remote_protocol.CAPABILITIES_PATH:
                    self._send_json(host.capabilities().to_payload())
                elif path == remote_protocol.HEARTBEAT_PATH:
                    self._send_json(host.heartbeat().to_payload())
                else:
                    self._send_json({"error": "unknown route"}, status=HTTPStatus.NOT_FOUND)

            def do_POST(self) -> None:
                if not self._authorized():
                    return
                path = urlparse(self.path).path
                if path == remote_protocol.JOBS_PATH:
                    self._start_job()
                    return
                prefix = f"{remote_protocol.JOBS_PATH}/"
                if path.startswith(prefix) and path.endswith("/cancel"):
                    job_id = path[len(prefix) : -len("/cancel")]
                    self._send_json({"cancelled": host.cancel(job_id)})
                    return
                self._send_json({"error": "unknown route"}, status=HTTPStatus.NOT_FOUND)

            def _start_job(self) -> None:
                request = remote_protocol.JobRequest.from_payload(self._read_json())
                if request is None:
                    self._send_json({"error": "invalid job request"}, status=HTTPStatus.BAD_REQUEST)
                    return
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()

                def emit(event: str) -> None:
                    self.wfile.write(f"{event}\n".encode())
                    self.wfile.flush()

                try:
                    host.run_job(request, emit)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    # The peer hung up (an interrupted turn, usually). The job's
                    # own process is reaped by run_job's finally block.
                    logger.info("Peer disconnected from a running remote job")
                except Exception as exc:
                    logger.exception(f"Remote job failed: {exc}")

            def _authorized(self) -> bool:
                if remote_protocol.authorized(self.headers.get("Authorization", ""), host._token):
                    return True
                self._send_json({"error": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
                return False

            def _read_json(self) -> object:
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    return json.loads(self.rfile.read(length) or b"{}")
                except (json.JSONDecodeError, ValueError):
                    return None

            def _send_json(self, payload: dict, *, status: HTTPStatus = HTTPStatus.OK) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        return Handler

    def serve(self, host: str, port: int) -> None:
        """Serve until interrupted."""
        server = ThreadingHTTPServer((host, port), self.handler_class())
        agents = ", ".join(sorted(self._backends)) or "none configured"
        logger.info(f"Remote agent host '{self._machine}' on {host}:{port} offering {agents}")
        if host not in ("127.0.0.1", "localhost", "::1"):
            logger.warning(
                f"Reachable from the network on {host}:{port}. Every request is bearer-token "
                "checked, but anyone holding AGENT_REMOTE_TOKEN can run these agents here."
            )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info("Stopping the remote agent host")
        finally:
            server.server_close()


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m remote_agent_protocol.remote_host``."""
    logging_setup.setup_logging(cfg.DEBUG_MODE)
    parser = argparse.ArgumentParser(description="Serve this machine's agents to a RAP peer.")
    parser.add_argument("--host", default=cfg.AGENT_REMOTE_BIND_HOST)
    parser.add_argument("--port", type=int, default=cfg.AGENT_REMOTE_BIND_PORT)
    parser.add_argument("--machine", default=cfg.AGENT_LOCAL_MACHINE)
    args = parser.parse_args(argv)

    if not cfg.AGENT_REMOTE_TOKEN:
        logger.error(
            "AGENT_REMOTE_TOKEN is not set. Agents run real tools on this machine, so this "
            "host refuses to serve without a shared secret. Put the same value in the peer's "
            ".env and try again."
        )
        return 2
    RemoteAgentHost(machine=args.machine).serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())

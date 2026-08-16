"""Talk to remote agent hosts: discover them, watch them, run jobs on them.

The registry keeps one entry per configured host, learns which agents it offers,
and re-checks it on a heartbeat so a laptop that goes to sleep stops being
offered as a delegation target instead of failing a job minutes later.

:class:`RemoteProcess` is the piece that makes remote agents cheap: it presents
a running remote job through the same handful of members the bridge uses on a
local ``asyncio.subprocess.Process`` -- ``stdout.readline()``, ``wait()``,
``returncode``, ``terminate()``, ``kill()``. Everything downstream of the launch
(status parsing, progress heartbeats, silence timeouts, cancellation, host-repo
checks) then works on a remote job without knowing that it is one.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import aiohttp
from loguru import logger

from remote_agent_protocol import config as cfg
from remote_agent_protocol import remote_protocol

# Output lines held here before the stream is made to wait. A local pipe gets
# this limit from the OS; a network stream has to impose its own.
_MAX_BUFFERED_LINES = 500


@dataclass
class RemoteHostState:
    """What the registry currently believes about one host."""

    name: str
    url: str
    online: bool = False
    machine: str = ""
    agents: tuple[str, ...] = ()
    active_jobs: int = 0
    error: str = ""
    checked_at: float = 0.0

    def backend_names(self) -> list[str]:
        """Qualified names this host's agents are delegated to by."""
        return [remote_protocol.remote_backend_name(self.name, agent) for agent in self.agents]

    def to_payload(self) -> dict:
        """Return the status shape the GUI and doctor read."""
        return {
            "name": self.name,
            "url": self.url,
            "online": self.online,
            "machine": self.machine or self.name,
            "agents": list(self.agents),
            "activeJobs": self.active_jobs,
            "error": self.error,
        }


class _RemoteStdout:
    """The ``stdout`` half of :class:`RemoteProcess`."""

    def __init__(self, lines: asyncio.Queue):
        self._lines = lines

    async def readline(self) -> bytes:
        """Return the next output line, or b"" once the job has ended."""
        line = await self._lines.get()
        return b"" if line is None else line


class RemoteProcess:
    """A remote job wearing the shape of a local subprocess."""

    def __init__(self, client: RemoteHostClient, job_id_holder: dict):
        """Wrap one streaming job started by ``client``."""
        self._client = client
        self._ids = job_id_holder
        self._lines: asyncio.Queue = asyncio.Queue(maxsize=_MAX_BUFFERED_LINES)
        self.stdout = _RemoteStdout(self._lines)
        self.returncode: int | None = None
        self._finished = asyncio.Event()
        self._cancel_task: asyncio.Task | None = None
        # Strong reference to the task draining the stream. asyncio keeps only a
        # weak one, and a collected pump would silently strand the job.
        self._pump_task: asyncio.Task | None = None

    async def wait(self) -> int:
        """Wait for the remote agent to exit and return its code."""
        await self._finished.wait()
        return self.returncode if self.returncode is not None else -1

    def terminate(self) -> None:
        """Ask the host to stop this job (best effort, fire and forget)."""
        job_id = self._ids.get("job_id")
        if job_id:
            # Held until it completes: a collected task never sends the
            # cancellation, and the far machine keeps working.
            self._cancel_task = asyncio.ensure_future(self._client.cancel(job_id))

    def kill(self) -> None:
        """Stop waiting on this job, whether or not the host is still there.

        The local escalation after ``terminate``. A local kill is guaranteed by
        the OS; a remote one is a request over a network that may be exactly
        what has gone wrong, so this also ends the job on *this* side rather
        than leaving the caller waiting on a machine that stopped answering.
        """
        self.terminate()
        self._finish(-1)

    async def _feed(self, line: str) -> None:
        """Hand one line to the reader, waiting while it falls behind.

        A local pipe gets this backpressure from the OS. Without it a chatty
        agent's output would accumulate here as fast as the network delivers it.
        """
        await self._lines.put(f"{line}\n".encode())

    def _finish(self, code: int) -> None:
        if self.returncode is None:
            self.returncode = code
        while True:
            try:
                self._lines.put_nowait(None)
                break
            except asyncio.QueueFull:
                # The end marker matters more than the oldest buffered line:
                # without it the reader waits forever on a finished job.
                self._lines.get_nowait()
        self._finished.set()


class RemoteHostClient:
    """One authenticated peer: discovery, heartbeat, and job streaming."""

    def __init__(self, name: str, url: str, token: str, *, timeout_secs: float = 10.0):
        """Initialize a client for the host named ``name`` at ``url``."""
        self.name = name
        self.url = url.rstrip("/")
        self._token = token
        self._timeout_secs = timeout_secs

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"}

    async def capabilities(self) -> remote_protocol.HostCapabilities | None:
        """Ask the host what it offers, or None if it cannot be trusted to say."""
        payload = await self._get(remote_protocol.CAPABILITIES_PATH)
        capabilities = remote_protocol.HostCapabilities.from_payload(payload)
        if capabilities is None:
            return None
        if capabilities.protocol != remote_protocol.PROTOCOL_VERSION:
            logger.warning(
                f"Remote host '{self.name}' speaks protocol {capabilities.protocol or '?'}, "
                f"this one speaks {remote_protocol.PROTOCOL_VERSION}; not using it"
            )
            return None
        return capabilities

    async def heartbeat(self) -> remote_protocol.Heartbeat | None:
        """Ask the host whether it is still there."""
        return remote_protocol.Heartbeat.from_payload(
            await self._get(remote_protocol.HEARTBEAT_PATH)
        )

    async def cancel(self, job_id: str) -> bool:
        """Ask the host to stop one running job."""
        url = f"{self.url}{remote_protocol.JOBS_PATH}/{job_id}/cancel"
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    url,
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=self._timeout_secs),
                ) as response,
            ):
                return response.status == 200
        except (aiohttp.ClientError, TimeoutError) as exc:
            logger.warning(f"Could not cancel remote job {job_id} on '{self.name}': {exc}")
            return False

    async def start_job(self, request: remote_protocol.JobRequest) -> RemoteProcess:
        """Start a job and return it as a subprocess-shaped handle.

        The stream is drained by a background task so the caller can read lines
        at its own pace, exactly as it would from a local pipe.
        """
        ids: dict = {}
        process = RemoteProcess(self, ids)
        ready = asyncio.Event()
        process._pump_task = asyncio.ensure_future(self._pump(request, process, ids, ready))
        # Wait for the host to accept the job (or fail it) before handing back a
        # handle, so a launch failure is reported as one instead of as silence.
        await ready.wait()
        return process

    async def _pump(
        self,
        request: remote_protocol.JobRequest,
        process: RemoteProcess,
        ids: dict,
        ready: asyncio.Event,
    ) -> None:
        url = f"{self.url}{remote_protocol.JOBS_PATH}"
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    url,
                    json=request.to_payload(),
                    headers=self._headers(),
                    # No total timeout: an agent job legitimately runs for minutes.
                    # The bridge's own silence timeout is what bounds it.
                    timeout=aiohttp.ClientTimeout(total=None, sock_connect=self._timeout_secs),
                ) as response,
            ):
                if response.status != 200:
                    await process._feed(
                        f"remote host '{self.name}' refused the job ({response.status})"
                    )
                    ready.set()
                    process._finish(126)
                    return
                ready.set()
                async for raw in response.content:
                    event = remote_protocol.decode_event(raw.decode("utf-8", "replace"))
                    if event is None:
                        continue
                    kind = event.get("type")
                    if kind == "started":
                        ids["job_id"] = str(event.get("job_id", ""))
                    elif kind == "line":
                        await process._feed(str(event.get("text", "")))
                    elif kind == "error":
                        await process._feed(f"remote host error: {event.get('message', '')}")
                    elif kind == "exit":
                        process._finish(int(event.get("code", 0)))
                        return
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            await process._feed(f"remote host '{self.name}' is unreachable: {exc}")
            ready.set()
        finally:
            ready.set()
            # A stream that ends without an exit event still has to end the job,
            # or the bridge waits on a process that will never report.
            process._finish(process.returncode if process.returncode is not None else 1)

    async def _get(self, path: str) -> object:
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(
                    f"{self.url}{path}",
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=self._timeout_secs),
                ) as response,
            ):
                if response.status != 200:
                    return None
                return await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError):
            return None


class RemoteRegistry:
    """Tracks configured remote hosts and the agents they currently offer."""

    def __init__(self, hosts: dict | None = None, *, heartbeat_secs: float | None = None):
        """Initialize from ``AGENT_REMOTE_HOSTS``-shaped configuration."""
        configured = cfg.AGENT_REMOTE_HOSTS if hosts is None else hosts
        self._clients: dict[str, RemoteHostClient] = {}
        self._states: dict[str, RemoteHostState] = {}
        for name, entry in configured.items():
            url = str(entry.get("url") or "").strip()
            if not url:
                logger.warning(f"Remote host '{name}' has no url; ignoring it")
                continue
            token = str(entry.get("token") or cfg.AGENT_REMOTE_TOKEN)
            self._clients[name] = RemoteHostClient(name, url, token)
            self._states[name] = RemoteHostState(name=name, url=url)
        self._heartbeat_secs = (
            cfg.AGENT_REMOTE_HEARTBEAT_SECS if heartbeat_secs is None else heartbeat_secs
        )
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def __bool__(self) -> bool:
        """Whether any remote host is configured at all."""
        return bool(self._clients)

    def states(self) -> list[RemoteHostState]:
        """Current belief about every configured host, in configuration order."""
        return list(self._states.values())

    def backend_names(self) -> list[str]:
        """Qualified names of every agent currently offered by an online host."""
        return [
            name
            for state in self._states.values()
            if state.online
            for name in state.backend_names()
        ]

    def machine_for(self, backend: str) -> str:
        """Display machine for a qualified remote backend name, or "" if unknown."""
        split = remote_protocol.split_backend_name(backend)
        if split is None:
            return ""
        state = self._states.get(split[0])
        return state.machine or state.name if state else ""

    def client_for(self, backend: str) -> tuple[RemoteHostClient, str] | None:
        """Resolve a qualified name to its host client and bare agent name.

        Only an *online* host resolves. A sleeping laptop must stop being a
        delegation target the moment the heartbeat says so, rather than
        accepting a job that can no longer reach it.
        """
        split = remote_protocol.split_backend_name(backend)
        if split is None:
            return None
        host, agent = split
        client = self._clients.get(host)
        state = self._states.get(host)
        if client is None or state is None or not state.online or agent not in state.agents:
            return None
        return client, agent

    def offline_reason(self, backend: str) -> str | None:
        """Explain why a configured remote name is unusable, or None if it isn't.

        Lets the caller say "the laptop is asleep" instead of "no such agent"
        for a backend the operator can see in their own configuration.
        """
        split = remote_protocol.split_backend_name(backend)
        if split is None:
            return None
        host, agent = split
        state = self._states.get(host)
        if state is None:
            return None
        if not state.online:
            return f"remote host '{host}' is offline ({state.error or 'not reachable'})"
        if agent not in state.agents:
            return f"remote host '{host}' does not offer an agent named '{agent}'"
        return None

    async def discover(self) -> None:
        """Refresh every host once: capabilities when new, heartbeat when known."""
        await asyncio.gather(*(self._refresh(name) for name in self._clients))

    def start(self) -> None:
        """Begin heartbeating in the background (no-op without hosts)."""
        if not self._clients or self._task is not None:
            return
        self._stop = asyncio.Event()
        self._task = asyncio.ensure_future(self._heartbeat_loop())

    async def stop(self) -> None:
        """Stop the heartbeat loop."""
        if self._task is None:
            return
        self._stop.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # a shutdown must not fail on a dead network
            logger.warning(f"Remote host heartbeat stopped with an error: {exc}")
        self._task = None

    async def _heartbeat_loop(self) -> None:
        while not self._stop.is_set():
            await self.discover()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._heartbeat_secs)
            except TimeoutError:
                continue

    async def _refresh(self, name: str) -> None:
        client = self._clients[name]
        state = self._states[name]
        was_online = state.online
        # Agents can be installed or removed on the far side while it runs, so
        # ask for capabilities whenever a host is (re)appearing, and settle for
        # the cheaper heartbeat once its list is known.
        if not state.agents or not was_online:
            capabilities = await client.capabilities()
            if capabilities is None:
                self._mark_offline(state, was_online, "no compatible answer")
                return
            state.machine = capabilities.machine
            state.agents = capabilities.agents
        beat = await client.heartbeat()
        if beat is None:
            self._mark_offline(state, was_online, "no heartbeat")
            return
        state.online = True
        state.error = ""
        state.active_jobs = beat.active_jobs
        state.checked_at = time.time()
        if not was_online:
            offered = ", ".join(state.agents) or "no agents"
            logger.info(f"Remote host '{name}' ({state.machine}) is online offering {offered}")

    def _mark_offline(self, state: RemoteHostState, was_online: bool, reason: str) -> None:
        state.online = False
        state.error = reason
        state.active_jobs = 0
        state.checked_at = time.time()
        if was_online:
            logger.warning(f"Remote host '{state.name}' went offline ({reason})")

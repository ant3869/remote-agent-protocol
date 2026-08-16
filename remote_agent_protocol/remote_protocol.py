"""Wire contract for authenticated remote agent backends.

Agent backends have always been local subprocess commands, which limited
delegation to whatever is installed on the machine running RAP. This module is
the one protocol that lets another machine offer its agents instead, and it
answers exactly three questions:

- **capabilities**: which agents does that machine have, and under what name?
- **heartbeat**: is it still there, and what is it doing right now?
- **jobs**: run this task and stream its output back, line by line.

Every request carries a bearer token, so a host on the LAN is a deliberate,
authenticated peer rather than anything that can reach the port. The job stream
is newline-delimited JSON rather than SSE: the consumer on the RAP side is the
same line-oriented reader that drains a local subprocess, so keeping one line
per event lets a remote agent reuse the local streaming, status-parsing, and
cancellation paths unchanged.
"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass, field

# Bumped only for a breaking change. A host and client that disagree refuse each
# other with an explicit message instead of failing later in a confusing way.
PROTOCOL_VERSION = "1"

CAPABILITIES_PATH = "/v1/capabilities"
HEARTBEAT_PATH = "/v1/health"
JOBS_PATH = "/v1/jobs"

#: Separator between a remote host name and one of its agents, e.g. ``laptop:hermes``.
REMOTE_NAME_SEPARATOR = ":"


def remote_backend_name(host: str, agent: str) -> str:
    """Return the qualified backend name a discovered remote agent is known by."""
    return f"{host}{REMOTE_NAME_SEPARATOR}{agent}"


def split_backend_name(name: str) -> tuple[str, str] | None:
    """Split a qualified remote name, or None when it names a local backend."""
    host, separator, agent = name.partition(REMOTE_NAME_SEPARATOR)
    if not separator or not host or not agent:
        return None
    return host, agent


@dataclass(frozen=True)
class HostCapabilities:
    """What one remote machine offers, as it describes itself."""

    machine: str
    agents: tuple[str, ...] = ()
    protocol: str = PROTOCOL_VERSION
    workspace: str = ""
    heartbeat_secs: float = 15.0

    def to_payload(self) -> dict:
        """Return the JSON body a host answers discovery with."""
        return {
            "protocol": self.protocol,
            "machine": self.machine,
            "agents": list(self.agents),
            "workspace": self.workspace,
            "heartbeat_secs": self.heartbeat_secs,
        }

    @classmethod
    def from_payload(cls, payload: object) -> HostCapabilities | None:
        """Parse a discovery body, or None when it is not one.

        Deliberately tolerant about unknown keys and strict about the ones it
        uses: a peer running a newer minor version stays usable, while a
        malformed or unrelated service is rejected rather than half-trusted.
        """
        if not isinstance(payload, dict):
            return None
        machine = str(payload.get("machine") or "").strip()
        agents = payload.get("agents")
        if not machine or not isinstance(agents, list):
            return None
        heartbeat = payload.get("heartbeat_secs", 15.0)
        return cls(
            machine=machine,
            agents=tuple(str(agent) for agent in agents if str(agent).strip()),
            protocol=str(payload.get("protocol") or ""),
            workspace=str(payload.get("workspace") or ""),
            heartbeat_secs=float(heartbeat) if isinstance(heartbeat, int | float) else 15.0,
        )


@dataclass(frozen=True)
class Heartbeat:
    """A host's liveness answer: reachable, compatible, and how busy."""

    machine: str
    active_jobs: int = 0
    protocol: str = PROTOCOL_VERSION
    uptime_secs: float = 0.0

    def to_payload(self) -> dict:
        """Return the JSON body a host answers a heartbeat with."""
        return {
            "protocol": self.protocol,
            "machine": self.machine,
            "active_jobs": self.active_jobs,
            "uptime_secs": round(self.uptime_secs, 1),
        }

    @classmethod
    def from_payload(cls, payload: object) -> Heartbeat | None:
        """Parse a heartbeat body, or None when it is not one."""
        if not isinstance(payload, dict) or not str(payload.get("machine") or "").strip():
            return None
        active = payload.get("active_jobs", 0)
        uptime = payload.get("uptime_secs", 0.0)
        return cls(
            machine=str(payload["machine"]).strip(),
            active_jobs=int(active) if isinstance(active, int) else 0,
            protocol=str(payload.get("protocol") or ""),
            uptime_secs=float(uptime) if isinstance(uptime, int | float) else 0.0,
        )


@dataclass(frozen=True)
class JobRequest:
    """One task handed to a remote machine."""

    agent: str
    task: str
    cwd: str = ""
    extra_args: tuple[str, ...] = field(default_factory=tuple)

    def to_payload(self) -> dict:
        """Return the JSON body that starts this job."""
        return {
            "protocol": PROTOCOL_VERSION,
            "agent": self.agent,
            "task": self.task,
            "cwd": self.cwd,
            "extra_args": list(self.extra_args),
        }

    @classmethod
    def from_payload(cls, payload: object) -> JobRequest | None:
        """Parse a job request, or None when the body cannot start one."""
        if not isinstance(payload, dict):
            return None
        agent = str(payload.get("agent") or "").strip()
        task = str(payload.get("task") or "")
        if not agent or not task.strip():
            return None
        extra = payload.get("extra_args")
        return cls(
            agent=agent,
            task=task,
            cwd=str(payload.get("cwd") or ""),
            extra_args=tuple(str(item) for item in extra) if isinstance(extra, list) else (),
        )


def started_event(job_id: str) -> str:
    """Encode the first stream line, which names the job for later cancellation."""
    return _encode({"type": "started", "job_id": job_id})


def line_event(text: str) -> str:
    """Encode one line of agent output."""
    return _encode({"type": "line", "text": text})


def exit_event(code: int) -> str:
    """Encode the terminal event carrying the agent's exit code."""
    return _encode({"type": "exit", "code": int(code)})


def error_event(message: str) -> str:
    """Encode a host-side failure that ended the job before its agent could."""
    return _encode({"type": "error", "message": message})


def decode_event(raw: str) -> dict | None:
    """Decode one stream line, or None if it is blank or not an event."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) and "type" in event else None


def authorized(header_value: str, token: str) -> bool:
    """Whether an ``Authorization`` header carries the expected bearer token.

    An empty configured token means the host has no shared secret, which is
    never treated as "allow everyone": a host without a token cannot be talked
    to at all, because agents run arbitrary commands on the machine offering
    them.
    """
    if not token:
        return False
    prefix = "Bearer "
    if not header_value.startswith(prefix):
        return False
    # Constant-time: this compares a secret against attacker-supplied input.
    return hmac.compare_digest(header_value[len(prefix) :].strip(), token)


def _encode(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False)

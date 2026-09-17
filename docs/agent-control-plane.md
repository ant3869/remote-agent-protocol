# Live Agent Control Plane

The control plane provides evidence-backed awareness of the configured local
agent harnesses. It sits above `AgentBridge`: adapters gather status evidence
and expose only supported controls, while `AgentBridge` continues to launch,
stream, and cancel RAP-owned subprocesses.

## Status evidence

An observation records independent `presence`, `activity`, and `health`
dimensions. A configured executable alone is not reported as ready. The local
CLI adapters use a bounded `--version` probe to establish `reachable`; active
RAP jobs are projected from structured bridge events. The current adapters do
not claim external-session inspection because the one-shot CLIs do not expose a
reliable, safe source for it.

Observations include a source, timestamp, expiry, capabilities, and a bounded
diagnostic detail. The registry persists last-known observations in
`data/agent_registry.json`, but loads every persisted value as stale. A fresh
probe is required before a new process presents a status as current.

## Coordinator behavior

All-agent availability questions run direct adapter probes concurrently. Each
probe emits a `agent_control` lifecycle event, so transcript activity can show
which harness is being contacted while later probes are still running. A single
timeout returns an `unknown` result for that harness and does not hide results
from the others.

The control plane exposes typed list, status, job inspection, dispatch,
cancellation, and redirection operations. Dispatch remains routed through the
existing `AgentBridge` safety boundary. RAP-owned jobs can be cancelled by
their specific id. External interruption is unavailable unless an adapter adds
verified support; it is never simulated by killing a generic process.

## Adapter capabilities

The first implementation covers Claude Code, Codex, Hermes, Code Puppy, and
OpenClaw when each is configured. Every local adapter supports bounded
discovery/probing, RAP-job progress, dispatch, and RAP-job cancellation. Since
these are one-shot CLIs rather than daemon services, a status request does not
launch them; their executable is verified immediately before a bridge dispatch.

## User interface

The web Agents roster keeps evidence-backed presence, activity, health, and
machine data separate from live job rows. Probe start/result events are visible
as operational transcript messages. While one or more probes are active, the
existing avatar moves into its thinking presentation; reduced-motion behavior
continues to be honored by the avatar renderer.

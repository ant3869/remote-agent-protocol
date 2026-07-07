# Wargame: WebSocket Lifecycle Events

Source brief: `tasks/websocket-lifecycle-events.md`

## Mission boundary

`AgentBridge._emit_job` already produces normalized `agent_job` dictionaries, and `VoiceSession._on_agent_event` forwards them through the same callback consumed by the GUI. The `websockets` package is already a base dependency. The smallest safe design is a local fan-out subscriber on that existing event boundary, not a second lifecycle model or a new framework.

## Move 1 — Freeze the external event contract

**Move / Action:** Define a versioned JSON envelope for only lifecycle events: schema version, event name, timestamp, job ID, agent, machine, task, status/state, action/tool/step, summary, elapsed time, and optional failure metadata. Specify absent fields as omitted or null consistently.

**Expected Observation:** Recorded started, progress/tool/blocked, completed, and failed events validate and serialize with deterministic keys/types. Internal-only objects never reach JSON encoding.

**Likely Failure & Counter Move:** Current dictionaries evolve and expose accidental fields. Build the payload from an allowlist at the socket boundary and reject/diagnose invalid required fields without breaking GUI delivery.

**Forks & Triggers:** If consumers need full logs, create a later opt-in endpoint; do not include stdout lines in v1. If timestamps are missing upstream, stamp receipt time in UTC and label it as server time.

## Move 2 — Add a bounded in-process broadcaster

**Move / Action:** Create one broadcaster owned by `VoiceSession` with a bounded queue per client. Feed it from `_on_agent_event` after the GUI callback. Schedule cross-thread publication onto the session event loop.

**Expected Observation:** GUI behavior is unchanged; one, two, or zero connected clients receive the same normalized event; a client callback never blocks `AgentBridge` or Tk event delivery.

**Likely Failure & Counter Move:** A slow client fills memory or backpressures the audio loop. Use small bounded queues and disconnect the lagging client with a clear close reason when full; never await socket writes inside `_on_agent_event`.

**Forks & Triggers:** If all lifecycle callbacks already execute on the session loop, use `put_nowait`. If another thread calls the boundary, use `loop.call_soon_threadsafe`; drop only after recording a warning and applying the chosen slow-client policy.

## Move 3 — Start the existing dependency on the session loop

**Move / Action:** Use `websockets.asyncio.server.serve` at configured host/port/path and start it beside the pipeline runner. Retain the server handle and client tasks for orderly shutdown.

**Expected Observation:** `ws://127.0.0.1:8765/events` accepts a client after session startup; `/events` is the only accepted path; the pipeline remains responsive while idle and while sending events.

**Likely Failure & Counter Move:** Port binding fails and tears down voice startup. Apply the product decision from the ledger: either fail the session deliberately or emit a visible degraded-health event and continue voice without the API; never fail silently.

**Forks & Triggers:** If host is loopback, no remote-network exposure exists. If host is non-loopback, require authentication and an explicit insecure-transport decision before binding.

## Move 4 — Establish connect and disconnect semantics

**Move / Action:** On `/events`, register a client queue, optionally send one bounded active-job snapshot, stream events, and unregister in `finally`. Reject other paths and malformed subscription messages.

**Expected Observation:** Connecting/disconnecting repeatedly leaves client count at zero and produces no unhandled task exception. One client closing during broadcast does not affect others or voice.

**Likely Failure & Counter Move:** Send raises after disconnect while a broadcast is queued. Keep writer ownership inside the client task, catch normal WebSocket closure there, and always remove the queue in `finally`.

**Forks & Triggers:** If replay/snapshot semantics are not approved, send only events occurring after connection. If snapshot is approved, derive it from the bridge’s active-job state and mark payloads as snapshots.

## Move 5 — Preserve ordering and terminal delivery

**Move / Action:** Publish lifecycle events in callback order per session and ensure each job’s terminal event follows all accepted progress events. Add a monotonically increasing server sequence number.

**Expected Observation:** For each job, clients observe started before progress before exactly one terminal event. Multiple jobs may interleave, but sequence numbers establish total server receipt order.

**Likely Failure & Counter Move:** Fire-and-forget publication reorders concurrent events. Put events onto one loop-owned ingress queue before fan-out; do not spawn one send task per event.

**Forks & Triggers:** If an event arrives before `started`, forward it with sequence order but log the upstream violation. If duplicate terminal events occur, fix/dedupe at the bridge boundary rather than hiding inconsistent state per client.

## Move 6 — Shut down without orphaning sockets

**Move / Action:** Stop accepting clients, close active connections, await writer tasks with a bounded grace period, then continue the existing bridge/pipeline shutdown. Make repeated shutdown idempotent.

**Expected Observation:** GUI reboot and normal exit release port 8765 immediately; no “task destroyed,” closed-loop, or subprocess cleanup warnings appear; a new session can bind the same port.

**Likely Failure & Counter Move:** Session rebuild starts a second server before the first releases the port. Tie server ownership to the old session’s awaited shutdown and expose readiness only after successful bind.

**Forks & Triggers:** If a client ignores close, cancel its writer after the grace period. If port release still races on Windows, await `wait_closed()` before constructing the replacement session.

## Move 7 — Fault-inject the public boundary

**Move / Action:** Run a local client check for all lifecycle states, two clients, abrupt disconnect, slow reader/queue overflow, invalid path, invalid internal event, port collision, session reboot, and voice work during broadcast.

**Expected Observation:** Valid clients receive parseable ordered JSON; faulty clients are isolated; GUI lifecycle updates and spoken completion remain intact; port collision follows the chosen startup policy.

**Likely Failure & Counter Move:** Happy-path tests miss loop/thread interaction. Invoke `_on_agent_event` from the same worker context used by `AgentBridge` and assert that the callback returns immediately while delivery completes asynchronously.

**Forks & Triggers:** If queue overflow affects only the slow client, disconnect it and pass. If any socket failure delays audio/lifecycle callbacks or crashes the session, do not release.

## Abort Conditions

- Abort non-loopback binding without approved authentication, origin policy, and transport-security posture.
- Abort release if a slow or disconnected client can block or crash the voice/session loop.
- Abort release if event ordering can place completion before start/progress for the same job.
- Abort startup when port-binding behavior is neither visible nor covered by an explicit fail/degrade policy.
- Abort release if shutdown leaves the port bound or produces orphaned task/closed-loop warnings.
- Abort schema v1 if required fields, replay semantics, or sensitive-field policy remain undefined.

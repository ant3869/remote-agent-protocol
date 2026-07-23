# Lifecycle WebSocket API

Remote Agent Protocol exposes future agent lifecycle events to local dashboards
at `ws://127.0.0.1:8765/events`. Version 1 is read-only, loopback-only, has no
replay, and never includes raw subprocess output.

Configure the port and bounded per-client queue with
`LIFECYCLE_WS_PORT` and `LIFECYCLE_WS_QUEUE_SIZE`. Set
`LIFECYCLE_WS_ENABLED=false` to disable the server. A bind failure is reported
in the GUI and logs while the voice session continues.

Every message is one JSON object:

```json
{
  "schema_version": 1,
  "sequence": 12,
  "event": "tool_running",
  "timestamp": "2026-07-06T03:00:00.000+00:00",
  "job_id": "job-4",
  "agent": "hermes",
  "machine": "Main PC",
  "task": "Research the API",
  "status": "running",
  "state": "tool_running",
  "tool": "browser"
}
```

`event` is one of `started`, `in_progress`, `tool_running`, `step_completed`,
`waiting`, `blocked`, `completed`, `failed`, or `cancelled`. Optional fields are
omitted when unavailable. Sequence numbers establish server receipt order and
reset when the voice session is rebuilt.

Clients that cannot keep up with their bounded queue are closed with WebSocket
code `1013`; a disconnected or slow client never blocks the audio pipeline.

# Mission Brief: WebSocket Server for Remote Lifecycle Events

## Objective
The project currently manages agent jobs via a local GUI. We need to expose the agent lifecycle events over a local WebSocket API so external clients or secondary dashboards can subscribe to the state of background agents.

## Requirements
- Implement an asyncio-compatible WebSocket server (e.g., using the `websockets` library).
- Hook into the existing event bus or manager that currently broadcasts state changes to the local GUI.
- Serialize the normalized lifecycle events (started, tool running, blocked, completed, failed) into clean JSON payloads.
- Run the WebSocket server concurrently with the main Pipecat audio pipeline without blocking the main event loop.

## Success Criteria
- An external client can connect to `ws://localhost:8765/events` and receive real-time JSON payloads when a background agent changes state.
- Disconnecting a WebSocket client does not crash the server or interrupt the voice switchboard.
import asyncio
import json
import unittest

from websockets.asyncio.client import connect

from remote_agent_protocol import lifecycle_ws


def event(kind="started", **overrides):
    row = {
        "type": "agent_job",
        "event": kind,
        "job_id": "job-1",
        "agent": "hermes",
        "machine": "Main PC",
        "task": "research the API",
        "status": "running",
        "state": "started",
        "started_at": "2026-07-06T01:02:03-05:00",
    }
    row.update(overrides)
    return row


class LifecyclePayloadTests(unittest.TestCase):
    def test_started_payload_is_versioned_allowlisted_json(self):
        payload = lifecycle_ws.normalize_event(
            event(line="secret raw output"), sequence=3, received_at="server-time"
        )

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["sequence"], 3)
        self.assertEqual(payload["event"], "started")
        self.assertNotIn("line", payload)
        json.dumps(payload)

    def test_progress_uses_normalized_state_and_terminal_status(self):
        progress = lifecycle_ws.normalize_event(
            event("progress", state="tool_running", tool="browser"),
            sequence=1,
            received_at="now",
        )
        finished = lifecycle_ws.normalize_event(
            event("finished", status="failed", state="failed", failure_kind="quota"),
            sequence=2,
            received_at="later",
        )

        self.assertEqual(progress["event"], "tool_running")
        self.assertEqual(finished["event"], "failed")
        self.assertEqual(finished["failure_kind"], "quota")

    def test_raw_output_and_invalid_events_are_not_public(self):
        self.assertIsNone(
            lifecycle_ws.normalize_event(event("output"), sequence=1, received_at="now")
        )
        self.assertIsNone(
            lifecycle_ws.normalize_event({"type": "sys"}, sequence=1, received_at="now")
        )


class LifecycleServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_receives_only_future_events_in_order(self):
        server = lifecycle_ws.LifecycleEventServer(port=0)
        self.assertTrue(await server.start())
        try:
            server.publish(event(task="before connect"))
            async with connect(f"ws://127.0.0.1:{server.port}/events") as client:
                server.publish(event(task="first"))
                server.publish(event("progress", state="blocked", task="second"))
                first = json.loads(await asyncio.wait_for(client.recv(), 1))
                second = json.loads(await asyncio.wait_for(client.recv(), 1))

            self.assertEqual([first["sequence"], second["sequence"]], [2, 3])
            self.assertEqual([first["task"], second["task"]], ["first", "second"])
            self.assertEqual(second["event"], "blocked")
        finally:
            await server.stop()

    async def test_invalid_path_is_rejected_without_stopping_server(self):
        server = lifecycle_ws.LifecycleEventServer(port=0)
        self.assertTrue(await server.start())
        try:
            async with connect(f"ws://127.0.0.1:{server.port}/wrong") as client:
                await asyncio.wait_for(client.wait_closed(), 1)
                self.assertEqual(client.close_code, 1008)

            async with connect(f"ws://127.0.0.1:{server.port}/events") as client:
                server.publish(event())
                self.assertEqual(json.loads(await client.recv())["event"], "started")
        finally:
            await server.stop()

    async def test_port_collision_reports_degraded_and_voice_can_continue(self):
        statuses = []
        first = lifecycle_ws.LifecycleEventServer(port=0)
        self.assertTrue(await first.start())
        second = lifecycle_ws.LifecycleEventServer(port=first.port, on_status=statuses.append)
        try:
            self.assertFalse(await second.start())
            self.assertEqual(statuses[-1]["state"], "degraded")
        finally:
            await second.stop()
            await first.stop()

    async def test_stop_releases_port_for_session_rebuild(self):
        first = lifecycle_ws.LifecycleEventServer(port=0)
        self.assertTrue(await first.start())
        port = first.port
        await first.stop()

        replacement = lifecycle_ws.LifecycleEventServer(port=port)
        try:
            self.assertTrue(await replacement.start())
        finally:
            await replacement.stop()


if __name__ == "__main__":
    unittest.main()

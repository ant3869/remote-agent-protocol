import asyncio
import unittest

from remote_agent_protocol import personas
from remote_agent_protocol import session as session_mod


class FakeBridge:
    """Stand-in for AgentBridge that records dispatches instead of spawning."""

    def __init__(self):
        self.started: list[tuple[str, str, str | None]] = []

    def machine_for(self, agent):
        return "local"

    def backend_names(self):
        return ["hermes", "mock", "hermes-yolo"]

    async def start(self, agent, task, cwd=None):
        self.started.append((agent, task, cwd))


class ConfirmationGateTests(unittest.TestCase):
    def setUp(self):
        self.events: list[dict] = []
        self.session = session_mod.VoiceSession(
            personas.DEFAULT_PERSONA, on_event=self.events.append
        )
        self.bridge = FakeBridge()
        self.session._bridge = self.bridge

    def _types(self):
        return [e.get("type") for e in self.events]

    def test_elevated_backend_alone_dispatches_immediately(self):
        # Picking hermes-yolo is itself the risk acknowledgment; a plain task
        # runs right away instead of holding for confirmation.
        async def scenario():
            ack = self.session._delegate_ack("hermes-yolo", "clean up downloads")
            await asyncio.sleep(0.01)  # let the spawned start() run
            return ack

        ack = asyncio.run(scenario())
        self.assertIn("hermes-yolo", ack)
        self.assertEqual(self.session._pending_confirmations, {})
        self.assertEqual(self.bridge.started, [("hermes-yolo", "clean up downloads", None)])

    def test_destructive_task_on_elevated_backend_is_still_held(self):
        self.session._delegate_ack("hermes-yolo", "delete the old files")
        self.assertEqual(len(self.session._pending_confirmations), 1)
        self.assertEqual(self.bridge.started, [])
        self.assertIn("agent_confirm", self._types())

    def test_destructive_task_is_held(self):
        self.session._delegate_ack("hermes", "delete the old files")
        self.assertEqual(len(self.session._pending_confirmations), 1)
        self.assertEqual(self.bridge.started, [])

    def test_plain_task_dispatches_immediately(self):
        async def scenario():
            ack = self.session._delegate_ack("mock", "search the web for cats")
            await asyncio.sleep(0.01)  # let the spawned start() run
            return ack

        ack = asyncio.run(scenario())
        self.assertIn("mock", ack)
        self.assertEqual(self.bridge.started, [("mock", "search the web for cats", None)])
        self.assertEqual(self.session._pending_confirmations, {})

    def test_voice_deny_drops_the_held_job(self):
        self.session._delegate_ack("hermes", "delete the old files")
        ack = self.session._maybe_consume_confirmation("no, cancel that")
        self.assertIsNotNone(ack)
        self.assertIn("hermes", ack)
        self.assertEqual(self.bridge.started, [])
        self.assertEqual(self.session._pending_confirmations, {})
        resolved = [e for e in self.events if e.get("type") == "agent_confirm_resolved"]
        self.assertEqual(resolved[-1]["decision"], "deny")

    def test_voice_approve_runs_the_held_job(self):
        async def scenario():
            self.session._delegate_ack("hermes", "delete the old files")
            ack = self.session._maybe_consume_confirmation("yes, go ahead")
            await asyncio.sleep(0.01)
            return ack

        ack = asyncio.run(scenario())
        self.assertIn("hermes", ack)
        self.assertEqual(self.bridge.started, [("hermes", "delete the old files", None)])
        self.assertEqual(self.session._pending_confirmations, {})

    def test_non_confirmation_reply_falls_through(self):
        self.session._delegate_ack("hermes", "delete the old files")
        # A real question, not a yes/no -- must not resolve the pending job.
        self.assertIsNone(self.session._maybe_consume_confirmation("what will it delete?"))
        self.assertEqual(len(self.session._pending_confirmations), 1)


if __name__ == "__main__":
    unittest.main()

import asyncio
import itertools
import unittest

from remote_agent_protocol import agent_bridge, personas
from remote_agent_protocol import config as cfg
from remote_agent_protocol import session as session_mod
from remote_agent_protocol.conversation_hub.floor import FloorDecision
from remote_agent_protocol.conversation_hub.service import TurnDisposition


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


class _FakeTaskReference:
    """Just enough of ``TaskReference`` for ``dispatch_via_hub`` to read ``attempt_id``."""

    def __init__(self, attempt_id: str):
        self.attempt_id = attempt_id


class FakeHub:
    """Stand-in for AgentConversationHub that records dispatches instead of routing.

    Task 8 moved real dispatch off ``AgentBridge.start()`` and onto
    ``AgentConversationHub.handle_turn()`` (session.py's ``_dispatch_via_hub`` ->
    ``conversation_dispatch.dispatch_via_hub``). The real hub talks to real
    control-plane adapters, which is real I/O this suite must never perform --
    every test that reached dispatch through the production hub either failed
    outright or hung indefinitely. This records what was asked for the same
    way ``FakeBridge`` used to, at the seam the app now actually dispatches
    through.
    """

    def __init__(self):
        self.dispatched: list[tuple[str | None, str]] = []
        self._ids = itertools.count(1)
        self._attempts: dict[str, str] = {}

    async def handle_turn(self, request) -> TurnDisposition:
        self.dispatched.append((request.explicit_agent_id, request.text))
        task_id = f"task-{next(self._ids)}"
        attempt_id = f"attempt-{task_id}"
        self._attempts[task_id] = attempt_id
        agent_id = request.explicit_agent_id or "mock"
        return TurnDisposition(
            channel_id=f"agent:{agent_id}",
            target_id=agent_id,
            task_id=task_id,
            floor_decision=FloorDecision(
                kind="dispatch",
                target_id=agent_id,
                task_id=task_id,
                requires_clarification=False,
                requires_butler_selection=False,
                reason_code="test",
                next_floor_id=None,
            ),
            spoken_acknowledgment=None,
        )

    def task(self, task_id: str) -> _FakeTaskReference | None:
        attempt_id = self._attempts.get(task_id)
        return None if attempt_id is None else _FakeTaskReference(attempt_id)


class ConfirmationGateTests(unittest.TestCase):
    def setUp(self):
        self.events: list[dict] = []
        self.session = session_mod.VoiceSession(
            personas.DEFAULT_PERSONA, on_event=self.events.append
        )
        self.bridge = FakeBridge()
        self.session._bridge = self.bridge
        self.hub = FakeHub()
        self.session._conversation_hub = self.hub

    def _types(self):
        return [e.get("type") for e in self.events]

    def test_elevated_backend_alone_dispatches_immediately(self):
        # Picking hermes-yolo is itself the risk acknowledgment; a plain task
        # runs right away instead of holding for confirmation. A direct
        # _delegate_ack call (bypassing _resolve_delegation) carries no fresh
        # routing decision, so per the explicit_agent_id rule (session.py
        # module docstring) the hub -- not this call's `agent` argument --
        # picks the real executor; the ack names no agent for the same
        # reason (DELEGATION_ACK_PENDING_SELECTION_PROMPT).
        async def scenario():
            ack = self.session._delegate_ack("hermes-yolo", "clean up downloads")
            await asyncio.sleep(0.01)  # let the spawned dispatch run
            return ack

        ack = asyncio.run(scenario())
        self.assertIn("clean up downloads", ack)
        self.assertEqual(self.session._pending_confirmations, {})
        self.assertEqual(self.hub.dispatched, [(None, "clean up downloads")])

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
        # Same explicit_agent_id rule as above: a direct call names no fresh
        # routing decision, so the hub -- not "mock" -- picks the executor.
        async def scenario():
            ack = self.session._delegate_ack("mock", "search the web for cats")
            await asyncio.sleep(0.01)  # let the spawned dispatch run
            return ack

        ack = asyncio.run(scenario())
        self.assertIn("search the web for cats", ack)
        self.assertEqual(self.hub.dispatched, [(None, "search the web for cats")])
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
        # Approval names the already-confirmed agent explicitly (session.py
        # _maybe_consume_confirmation), unlike a fresh direct _delegate_ack
        # call, so this ack and dispatch still carry "hermes".
        async def scenario():
            self.session._delegate_ack("hermes", "delete the old files")
            ack = self.session._maybe_consume_confirmation("yes, go ahead")
            await asyncio.sleep(0.01)
            return ack

        ack = asyncio.run(scenario())
        self.assertIn("hermes", ack)
        self.assertEqual(self.hub.dispatched, [("hermes", "delete the old files")])
        self.assertEqual(self.session._pending_confirmations, {})

    def test_no_do_that_approves_the_held_job(self):
        async def scenario():
            self.session._delegate_ack("hermes", "delete the old files")
            ack = self.session._maybe_consume_confirmation("No, I want you to do that.")
            await asyncio.sleep(0.01)
            return ack

        ack = asyncio.run(scenario())
        self.assertIn("hermes", ack)
        self.assertEqual(self.hub.dispatched, [("hermes", "delete the old files")])

    def test_non_confirmation_reply_falls_through(self):
        self.session._delegate_ack("hermes", "delete the old files")
        # A real question, not a yes/no -- must not resolve the pending job.
        self.assertIsNone(self.session._maybe_consume_confirmation("what will it delete?"))
        self.assertEqual(len(self.session._pending_confirmations), 1)


def _confirmation_gate_job(agent="hermes-yolo", task="search junk email", cwd=None):
    """A "completed" job whose result is actually a sub-agent confirmation gate.

    Mirrors jess_runtime.log 2026-07-06 18:26: hermes-yolo "completed" with
    summary "Requesting confirmation to proceed" instead of doing the work.
    """
    job = agent_bridge.AgentJob(job_id="j", agent=agent, task=task, cwd=cwd)
    job.status = agent_bridge.STATUS_DONE
    job.summary = "Requesting confirmation to proceed"
    job.result = (
        "I'm about to search your junk email; please say 'confirm' to proceed or 'cancel' to stop."
    )
    return job


def _real_completion_job(agent="hermes-yolo", task="search junk email"):
    job = agent_bridge.AgentJob(job_id="j2", agent=agent, task=task)
    job.status = agent_bridge.STATUS_DONE
    job.summary = "Found two important emails"
    job.result = "1. Security alert\n2. Invoice reminder"
    return job


class SubAgentConfirmationRelaunchTests(unittest.TestCase):
    """A sub-agent that "finishes" by asking for permission must trigger a
    real confirmation hold, not be announced as a completed result -- and
    approving it must actually relaunch the task (jess_runtime.log 2026-07-06
    18:26: saying "confirm" after this happened did nothing at all)."""

    def setUp(self):
        self.events: list[dict] = []
        self.session = session_mod.VoiceSession(
            personas.DEFAULT_PERSONA, on_event=self.events.append
        )
        self.bridge = FakeBridge()
        self.session._bridge = self.bridge
        self.hub = FakeHub()
        self.session._conversation_hub = self.hub

    def _types(self):
        return [e.get("type") for e in self.events]

    def test_confirmation_gate_registers_a_pending_confirmation(self):
        job = _confirmation_gate_job(cwd="C:/work")
        asyncio.run(self.session._announce_agent_job(job))

        self.assertEqual(len(self.session._pending_confirmations), 1)
        self.assertEqual(self.bridge.started, [])  # not relaunched yet
        self.assertIn("agent_confirm", self._types())
        confirm_evt = next(e for e in self.events if e["type"] == "agent_confirm")
        self.assertEqual(confirm_evt["agent"], "hermes-yolo")
        self.assertEqual(confirm_evt["task"], "search junk email")  # clean, no note appended

    def test_confirming_actually_relaunches_the_task(self):
        async def scenario():
            job = _confirmation_gate_job(cwd="C:/work")
            await self.session._announce_agent_job(job)
            ack = self.session._maybe_consume_confirmation("confirm")
            await asyncio.sleep(0.01)
            return ack

        ack = asyncio.run(scenario())
        self.assertIsNotNone(ack)
        self.assertEqual(len(self.hub.dispatched), 1)
        agent, task = self.hub.dispatched[0]
        self.assertEqual(agent, "hermes-yolo")
        # cwd cannot survive a hub dispatch -- ConversationTurnRequest has no
        # cwd field, so it is dropped with a logged warning rather than
        # silently (conversation_dispatch.warn_if_cwd_unsupported, task-8
        # review round 1 #3). Not a regression to guard here; the original
        # working-dir assertion asserted a guarantee the current dispatch
        # path cannot make.
        self.assertIn("search junk email", task)
        # The relaunch tells the agent it already has permission, so it does
        # not just print the same confirmation gate again.
        self.assertIn("already confirmed", task.lower())
        self.assertEqual(self.session._pending_confirmations, {})

    def test_canceling_the_relaunch_confirmation_drops_it(self):
        async def scenario():
            job = _confirmation_gate_job()
            await self.session._announce_agent_job(job)
            return self.session._maybe_consume_confirmation("cancel")

        ack = asyncio.run(scenario())
        self.assertIsNotNone(ack)
        self.assertEqual(self.bridge.started, [])
        self.assertEqual(self.session._pending_confirmations, {})

    def test_genuine_completion_is_not_held_for_confirmation(self):
        async def scenario():
            job = _real_completion_job()
            await self.session._announce_agent_job(job)

        asyncio.run(scenario())
        self.assertEqual(self.session._pending_confirmations, {})
        self.assertNotIn("agent_confirm", self._types())

    def test_repeated_confirmation_requests_stop_looping(self):
        # If the same agent keeps "finishing" by asking for confirmation with
        # no real result in between, we must stop relaunching it forever and
        # tell the user instead -- not silently hold yet another confirmation.
        async def scenario():
            for _ in range(cfg.AGENT_CONFIRM_LOOP_LIMIT):
                await self.session._announce_agent_job(_confirmation_gate_job())
                self.session._pending_confirmations.clear()  # simulate each being resolved
            confirm_events_before = sum(1 for e in self.events if e["type"] == "agent_confirm")
            await self.session._announce_agent_job(_confirmation_gate_job())
            confirm_events_after = sum(1 for e in self.events if e["type"] == "agent_confirm")
            return confirm_events_before, confirm_events_after

        before, after = asyncio.run(scenario())
        self.assertEqual(after, before)  # the one past the limit registered no new hold
        self.assertEqual(self.session._pending_confirmations, {})

    def test_streak_resets_after_a_real_result(self):
        async def scenario():
            await self.session._announce_agent_job(_confirmation_gate_job())
            self.session._pending_confirmations.clear()
            await self.session._announce_agent_job(_real_completion_job())
            return self.session._agent_confirm_streak.get("hermes-yolo", 0)

        streak = asyncio.run(scenario())
        self.assertEqual(streak, 0)


if __name__ == "__main__":
    unittest.main()

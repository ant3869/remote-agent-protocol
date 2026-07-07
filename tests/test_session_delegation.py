import asyncio
import unittest
from unittest.mock import patch

from pipecat.frames.frames import TTSSpeakFrame
from remote_agent_protocol import agent_bridge, personas, session, session_processors


class RecordingWorker:
    def __init__(self):
        self.frames = []

    async def queue_frames(self, frames):
        self.frames.extend(frames)


class RecordingBridge:
    def __init__(self):
        self.selected = []
        self.started = []

    def set_model_override(self, agent, provider):
        self.selected.append((agent, provider))
        return "OpenAI GPT-5.5"

    async def start(self, agent, task, *args, **kwargs):
        self.started.append((agent, task))
        return "job-retry"


class CorrectingBridge(RecordingBridge):
    def __init__(self):
        super().__init__()
        self.corrections = []

    def has_active(self):
        return True

    async def replace_latest(self, correction):
        self.corrections.append(correction)
        return "job-replacement"


class RecordingLifecycleServer:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


class SessionDelegationTests(unittest.TestCase):
    def test_implicit_delegation_uses_runtime_default_backend(self):
        parsed = session_processors.resolve_delegation(
            "go and find me the top 5 trending github repos",
            default_backend="code-puppy",
        )

        self.assertEqual(parsed, ("code-puppy", "go and find me the top 5 trending github repos"))

    def test_explicit_agent_beats_runtime_default(self):
        parsed = session_processors.resolve_delegation(
            "ask code puppy to add tests to the repo",
            default_backend="hermes-yolo",
        )

        self.assertEqual(parsed, ("code-puppy", "add tests to the repo"))

    def test_markerless_promise_becomes_a_real_confirmation(self):
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA)
        spawned = []

        def close_spawned(coro, *, name):
            coro.close()
            spawned.append(name)

        voice_session._spawn = close_spawned
        voice_session._last_user_text = "get the Bentonville forecast"

        voice_session._on_llm_response("I shall summon the Bat Computer immediately.", False)

        self.assertEqual(
            list(voice_session._pending_confirmations.values()),
            [(voice_session._default_agent_backend, "get the Bentonville forecast", None)],
        )
        self.assertEqual(spawned, ["markerless-promise-confirm"])

    def _session_with_recorders(self):
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA)
        voice_session._bridge = RecordingBridge()
        spawned = []

        def close_spawned(coro, *, name):
            coro.close()
            spawned.append(name)

        voice_session._spawn = close_spawned
        return voice_session, spawned

    def test_llm_delegate_ignored_on_ack_turn(self):
        # Regression: an ack/confirm turn already narrates an app-initiated
        # agent action. A [[delegate:]] marker in that reply must not
        # double-dispatch, nor re-hold a job whose confirmation prompt would
        # loop (jess_runtime.log 2026-07-07 00:52 double job / 00:55 loop).
        voice_session, spawned = self._session_with_recorders()
        voice_session._agent_ack_turn = True

        voice_session._llm_delegate("search work emails for a zip file today")

        self.assertEqual(spawned, [])
        self.assertEqual(voice_session._pending_confirmations, {})

    def test_llm_delegate_still_fires_on_a_normal_turn(self):
        # The guard must not break a genuine first-time marker delegation.
        voice_session, spawned = self._session_with_recorders()
        voice_session._agent_ack_turn = False

        voice_session._llm_delegate("search the web for mechanical keyboards")

        self.assertEqual(spawned, [f"delegate-{voice_session._default_agent_backend}"])
        self.assertEqual(voice_session._pending_confirmations, {})


class AgentVoiceStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_events_are_published_to_lifecycle_server_without_announcements(self):
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA)
        lifecycle = RecordingLifecycleServer()
        voice_session._lifecycle_ws = lifecycle
        event = {
            "type": "agent_job",
            "event": "started",
            "job_id": "job-1",
            "agent": "hermes",
            "task": "research",
        }

        with patch.object(session.cfg, "AGENT_ANNOUNCE", False):
            voice_session._on_agent_event(event)

        self.assertEqual(lifecycle.events, [event])

    async def test_wake_persona_is_applied_before_callback_returns(self):
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA)
        applied = []

        async def apply(persona):
            applied.append(persona.name)

        voice_session._apply_persona = apply

        await voice_session._apply_wake_persona("Jarvis")

        self.assertEqual(voice_session._persona.name, "Jarvis")
        self.assertEqual(applied, ["Jarvis"])

    async def test_repeated_wake_persona_only_refreshes_the_window(self):
        jarvis = personas.by_name("Jarvis")
        voice_session = session.VoiceSession(jarvis)
        applied = []

        async def apply(persona):
            applied.append(persona.name)

        voice_session._apply_persona = apply

        await voice_session._apply_wake_persona("Jarvis")

        self.assertEqual(applied, [])

    async def test_active_job_correction_is_cancelled_and_replaced(self):
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA)
        bridge = CorrectingBridge()
        voice_session._bridge = bridge

        prompt = await voice_session._maybe_handle_model_control("Wait, actually use httpx instead")

        self.assertEqual(bridge.corrections, ["actually use httpx instead"])
        self.assertIn("restarted", prompt.lower())

    async def test_pending_job_correction_updates_confirmation_without_launch(self):
        events = []
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA, events.append)
        voice_session._pending_confirmations["confirm-1"] = (
            "hermes",
            "write the client",
            None,
        )

        prompt = await voice_session._maybe_handle_model_control("Actually use httpx instead")

        self.assertEqual(
            voice_session._pending_confirmations["confirm-1"][1],
            "write the client\n\nUser correction: use httpx instead",
        )
        self.assertEqual(events[-1]["type"], "agent_confirm")
        self.assertIn("confirm", prompt.lower())

    async def test_delegated_task_includes_bounded_untrusted_context(self):
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA)

        class Context:
            def get_messages(self):
                return [
                    {"role": "user", "content": "The target project is Atlas."},
                    {"role": "assistant", "content": "Understood."},
                ]

        voice_session._context = Context()

        task = voice_session._with_delegation_context("add unit tests")

        self.assertTrue(task.startswith("add unit tests"))
        self.assertIn("untrusted conversation context", task.lower())
        self.assertIn("Atlas", task)

    async def test_background_task_failures_are_logged(self):
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA)

        async def fail():
            raise RuntimeError("boom")

        with patch.object(session.logger, "error") as logged:
            voice_session._spawn(fail(), name="broken-task")
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        logged.assert_called_once()
        self.assertIn("broken-task", logged.call_args.args[0])

    async def test_quota_failure_is_remembered_for_spoken_recovery(self):
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA)
        worker = RecordingWorker()
        voice_session._worker = worker
        job = agent_bridge.AgentJob("job-1", "code-puppy", "find VR games")
        job.status = agent_bridge.STATUS_FAILED
        job.failure_kind = "quota"

        await voice_session._announce_agent_job(job)

        self.assertEqual(voice_session._model_recovery, ("code-puppy", "find VR games"))
        self.assertIn("usage", worker.frames[0].text.lower())

    async def test_contextual_switch_selects_failed_agent_without_retrying(self):
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA)
        bridge = RecordingBridge()
        voice_session._bridge = bridge
        voice_session._model_recovery = ("code-puppy", "find VR games")

        prompt = await voice_session._maybe_handle_model_control("change to OpenAI")

        self.assertEqual(bridge.selected, [("code-puppy", "openai")])
        self.assertEqual(bridge.started, [])
        self.assertIn("OpenAI GPT-5.5", prompt)

    async def test_switch_and_retry_replays_failed_task_once(self):
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA)
        bridge = RecordingBridge()
        voice_session._bridge = bridge
        voice_session._model_recovery = ("code-puppy", "find VR games")

        prompt = await voice_session._maybe_handle_model_control(
            "switch Code Puppy to OpenAI and retry"
        )

        self.assertEqual(bridge.started, [("code-puppy", "find VR games")])
        self.assertIsNone(voice_session._model_recovery)
        self.assertIn("retrying", prompt.lower())

    async def test_completion_uses_direct_tts_without_llm(self):
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA)
        worker = RecordingWorker()
        voice_session._worker = worker
        job = agent_bridge.AgentJob("job-1", "code-puppy", "draw a dog")
        job.status = agent_bridge.STATUS_DONE
        job.summary = "Dog drawn in Paint"

        await voice_session._announce_agent_job(job)

        self.assertEqual(len(worker.frames), 1)
        self.assertIsInstance(worker.frames[0], TTSSpeakFrame)
        self.assertIn("Dog drawn in Paint", worker.frames[0].text)

    async def test_long_progress_event_is_spoken_once(self):
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA)
        worker = RecordingWorker()
        voice_session._worker = worker
        event = {
            "type": "agent_job",
            "event": "progress",
            "job_id": "job-1",
            "agent": "code-puppy",
            "task": "draw a dog",
            "state": "in_progress",
            "action": "Still working",
            "elapsed_secs": 30,
        }

        voice_session._on_agent_event(event)
        voice_session._on_agent_event(event)
        await asyncio.sleep(0)

        self.assertEqual(len(worker.frames), 1)
        self.assertIsInstance(worker.frames[0], TTSSpeakFrame)

    async def test_destructive_confirmation_explains_why_and_shows_transcript(self):
        events = []
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA, events.append)
        voice_session._last_user_text = "delete the files in my downloads folder"

        voice_session._delegate_ack("hermes", "delete the files in my downloads folder")

        confirm_events = [e for e in events if e["type"] == "agent_confirm"]
        self.assertEqual(len(confirm_events), 1)
        self.assertIn("mutates the system", confirm_events[0]["reason"])
        self.assertEqual(confirm_events[0]["transcript"], "delete the files in my downloads folder")

    async def test_router_forced_confirmation_uses_the_routing_reason(self):
        events = []
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA, events.append)
        voice_session._last_user_text = "why did the music stop playing"
        voice_session._force_confirm = True
        voice_session._force_confirm_reason = "classifier task shares no word with the transcript"

        voice_session._delegate_ack("hermes", "reorganize the photo archive")

        confirm_events = [e for e in events if e["type"] == "agent_confirm"]
        self.assertEqual(
            confirm_events[0]["reason"], "classifier task shares no word with the transcript"
        )

    async def test_denied_task_is_flagged_if_proposed_again(self):
        events = []
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA, events.append)
        voice_session._remember_denial("hermes", "delete the files in my downloads folder")

        voice_session._delegate_ack("hermes", "delete the files in my downloads folder")

        confirm_events = [e for e in events if e["type"] == "agent_confirm"]
        self.assertIn("denied", confirm_events[0]["reason"])

    async def test_manual_start_event_is_spoken(self):
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA)
        worker = RecordingWorker()
        voice_session._worker = worker

        voice_session._on_agent_event(
            {
                "type": "agent_job",
                "event": "started",
                "job_id": "job-1",
                "agent": "code-puppy",
                "task": "draw a dog",
                "announce_start": True,
            }
        )
        await asyncio.sleep(0)

        self.assertEqual(len(worker.frames), 1)
        self.assertIn("started", worker.frames[0].text)


if __name__ == "__main__":
    unittest.main()

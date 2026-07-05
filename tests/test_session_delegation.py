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


class AgentVoiceStatusTests(unittest.IsolatedAsyncioTestCase):
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

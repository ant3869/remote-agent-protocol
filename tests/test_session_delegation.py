import asyncio
import unittest

from pipecat.frames.frames import TTSSpeakFrame
from remote_agent_protocol import agent_bridge, personas, session


class RecordingWorker:
    def __init__(self):
        self.frames = []

    async def queue_frames(self, frames):
        self.frames.extend(frames)


class SessionDelegationTests(unittest.TestCase):
    def test_implicit_delegation_uses_runtime_default_backend(self):
        parsed = session.resolve_delegation(
            "go and find me the top 5 trending github repos",
            default_backend="code-puppy",
        )

        self.assertEqual(parsed, ("code-puppy", "go and find me the top 5 trending github repos"))

    def test_explicit_agent_beats_runtime_default(self):
        parsed = session.resolve_delegation(
            "ask code puppy to add tests to the repo",
            default_backend="hermes-yolo",
        )

        self.assertEqual(parsed, ("code-puppy", "add tests to the repo"))


class AgentVoiceStatusTests(unittest.IsolatedAsyncioTestCase):
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

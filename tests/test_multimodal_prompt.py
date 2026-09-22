import asyncio
import itertools
import unittest

from pipecat.frames.frames import LLMRunFrame
from remote_agent_protocol import multimodal_prompt, personas, session
from remote_agent_protocol.conversation_hub.floor import FloorDecision
from remote_agent_protocol.conversation_hub.service import TurnDisposition


class FakeContext:
    def __init__(self):
        self.messages = []

    def add_message(self, message):
        self.messages.append(message)

    def get_messages(self):
        return list(self.messages)


class FakeWorker:
    def __init__(self):
        self.frames = []

    async def queue_frames(self, frames):
        self.frames.extend(frames)


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


class FakeMemoryClient:
    def __init__(self):
        self.added = []
        self.reset_count = 0

    def add(self, **kwargs):
        self.added.append(kwargs)

    def reset(self):
        self.reset_count += 1


class FakeMem0:
    def __init__(self, memories=None):
        self.memory_client = FakeMemoryClient()
        self._memories = list(memories or [])

    async def get_memories(self):
        return list(self._memories)


class MultimodalPromptBundleTests(unittest.TestCase):
    def test_voice_text_image_and_link_are_one_agent_prompt(self):
        bundle = multimodal_prompt.MultimodalPromptBundle(
            conversation_id="conversation-1",
            user_id="user-1",
        )
        bundle.add_voice_transcript("Look at the highlighted part.")
        bundle.set_text("The error appears after login.")
        bundle.add_attachment(
            multimodal_prompt.PromptAttachment(
                multimodal_prompt.ATTACHMENT_IMAGE,
                "screenshot.png",
                caption="Login error",
                user_note="Second screenshot is the relevant one.",
                attachment_id="image1",
            )
        )
        bundle.add_attachment(
            multimodal_prompt.attachment_from_reference(
                "https://example.com/bug", note="This link explains the repro."
            )
        )
        bundle.set_final_instruction("Diagnose the bug.")

        prompt = bundle.agent_prompt()

        self.assertIn("## User Voice Transcript\nLook at the highlighted part.", prompt)
        self.assertIn("## Typed User Notes\nThe error appears after login.", prompt)
        self.assertIn("Image 1: screenshot.png", prompt)
        self.assertIn("https://example.com/bug", prompt)
        self.assertIn("Diagnose the bug.", prompt)
        self.assertIn("voice, typed_note, image_image1", prompt)

    def test_draft_updates_do_not_create_agent_work(self):
        bundle = multimodal_prompt.MultimodalPromptBundle()

        bundle.add_voice_transcript("Do not send yet.")
        bundle.set_text("Still collecting notes.")
        bundle.add_attachment(multimodal_prompt.attachment_from_reference("screenshot.png"))

        self.assertEqual(multimodal_prompt.send_intent("Do not send yet."), "hold")
        self.assertEqual(bundle.send_mode, "manual")

    def test_context_signals_and_voice_mode_are_serialized(self):
        bundle = multimodal_prompt.MultimodalPromptBundle()
        bundle.voice_mode = multimodal_prompt.VOICE_MODE_WAKE_WORD
        bundle.send_reason = "voice_send_intent"
        bundle.context_signals = multimodal_prompt.context_signals("look at this screenshot")

        row = bundle.to_dict()

        self.assertEqual(row["voice_mode"], "wake_word")
        self.assertEqual(row["send_reason"], "voice_send_intent")
        self.assertTrue(row["context_detection"]["has_context"])
        self.assertEqual(row["context_detection"]["signals"], ["context_reference"])

    def test_preference_candidates_cover_all_modalities(self):
        bundle = multimodal_prompt.MultimodalPromptBundle()
        bundle.add_voice_transcript("Never send my voice note until I press send.")
        bundle.set_text("Always call this agent Hermes-Agent.")
        bundle.add_attachment(
            multimodal_prompt.PromptAttachment(
                multimodal_prompt.ATTACHMENT_IMAGE,
                "shot.png",
                user_note="When I send screenshots with voice notes, treat them as one prompt.",
            )
        )

        candidates = bundle.preference_candidates()

        self.assertEqual(len(candidates), 3)
        self.assertTrue(any("Hermes-Agent" in item for item in candidates))

    def test_task_local_negative_constraint_is_not_remembered(self):
        bundle = multimodal_prompt.MultimodalPromptBundle()
        bundle.set_text("Audit the codebase, but don't change any code.")

        self.assertEqual(bundle.preference_candidates(), [])

    def test_remove_attachment_updates_context_order(self):
        bundle = multimodal_prompt.MultimodalPromptBundle()
        bundle.add_attachment(
            multimodal_prompt.PromptAttachment(
                multimodal_prompt.ATTACHMENT_FILE,
                "notes.txt",
                attachment_id="gone",
            )
        )

        bundle.remove_attachment("gone")

        self.assertEqual(bundle.attachments, [])
        self.assertEqual(bundle.context_order, [])


class SessionMultimodalPromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_agent_in_multimodal_request_bypasses_default(self):
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA)
        voice_session._context = FakeContext()
        voice_session._worker = FakeWorker()
        voice_session._conversation_hub = FakeHub()
        voice_session._default_agent_backend = "mock"
        calls = []
        real_delegate_ack = voice_session._delegate_ack

        def spy_delegate_ack(agent, task, cwd=None):
            calls.append((agent, task))
            return real_delegate_ack(agent, task, cwd)

        voice_session._delegate_ack = spy_delegate_ack
        bundle = multimodal_prompt.MultimodalPromptBundle()
        bundle.set_final_instruction(
            "can you have code puppy figure out why hermes is not responding in telegram?"
        )

        await voice_session._send_multimodal_prompt(bundle)
        await asyncio.sleep(0.01)  # let the spawned dispatch run

        # parse_delegation named code-puppy, not the "mock" default. Per the
        # explicit_agent_id rule (session.py module docstring), a direct
        # _delegate_ack call carries no fresh routing decision, so the hub --
        # not this agent argument -- ultimately picks the executor; dispatch
        # still reaches the hub with the right task text either way.
        self.assertEqual(calls[0][0], "code-puppy")
        self.assertTrue(
            calls[0][1].startswith("figure out why hermes is not responding in telegram")
        )
        self.assertEqual(len(voice_session._conversation_hub.dispatched), 1)

    async def test_session_sends_bundle_as_one_llm_turn(self):
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA)
        context = FakeContext()
        worker = FakeWorker()
        voice_session._context = context
        voice_session._worker = worker
        bundle = multimodal_prompt.MultimodalPromptBundle()
        bundle.add_voice_transcript("Look at the highlighted part.")
        bundle.set_text("Here is the correction.")
        bundle.add_attachment(multimodal_prompt.attachment_from_reference("screenshot.png"))
        bundle.set_final_instruction("Explain the issue.")

        await voice_session._send_multimodal_prompt(bundle)

        self.assertEqual(len(context.messages), 1)
        self.assertIn("## Attached Images", context.messages[0]["content"])
        self.assertEqual(len(worker.frames), 1)
        self.assertIsInstance(worker.frames[0], LLMRunFrame)

    async def test_session_writes_preferences_from_voice_text_and_attachment_notes(self):
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA)
        voice_session._context = FakeContext()
        voice_session._worker = FakeWorker()
        voice_session._mem0_service = FakeMem0()
        bundle = multimodal_prompt.MultimodalPromptBundle()
        bundle.add_voice_transcript("Never send my voice note until I press send.")
        bundle.set_text("Always call this agent Hermes-Agent.")
        bundle.add_attachment(
            multimodal_prompt.PromptAttachment(
                multimodal_prompt.ATTACHMENT_LINK,
                "https://example.com",
                user_note="When I send links with voice notes, treat them as one prompt.",
            )
        )

        await voice_session._send_multimodal_prompt(bundle)

        added = voice_session._mem0_service.memory_client.added
        self.assertEqual(len(added), 3)
        self.assertTrue(all(row["metadata"]["source"] == "multimodal_prompt" for row in added))
        self.assertTrue(all(row["metadata"]["fact_key"] for row in added))
        self.assertTrue(all(row["infer"] is False for row in added))
        self.assertFalse(any("Remember that" in row["messages"][0]["content"] for row in added))

    async def test_manual_memory_skips_alias_duplicate(self):
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA)
        voice_session._mem0_service = FakeMem0(
            [{"id": "existing", "memory": "User's GPU is an RTX 5060 Ti."}]
        )

        await voice_session._add_semantic_memory("Ant's GPU is an RTX 5060 Ti.")

        added = voice_session._mem0_service.memory_client.added
        self.assertEqual(added, [])

    async def test_forget_semantic_resets_mem0_store(self):
        voice_session = session.VoiceSession(personas.DEFAULT_PERSONA)
        voice_session._mem0_service = FakeMem0()

        await voice_session._forget_semantic_memory()

        self.assertEqual(voice_session._mem0_service.memory_client.reset_count, 1)


if __name__ == "__main__":
    unittest.main()

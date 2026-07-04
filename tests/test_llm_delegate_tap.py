"""LLMDelegateTap -- the LLM's [[delegate: ...]] markers must dispatch for real
and must never be spoken aloud or shown in the transcript."""

import unittest

from pipecat.frames.frames import (
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
)
from pipecat.tests.utils import run_test
from remote_agent_protocol.session_processors import LLMDelegateTap


def _spoken_text(down_frames) -> str:
    return "".join(f.text for f in down_frames if isinstance(f, LLMTextFrame))


class LLMDelegateTapTests(unittest.IsolatedAsyncioTestCase):
    async def test_marker_dispatches_and_is_stripped(self):
        tasks: list[str] = []
        down, _ = await run_test(
            LLMDelegateTap(tasks.append),
            frames_to_send=[
                LLMFullResponseStartFrame(),
                LLMTextFrame(text="On it, sir. [[delegate: find the closest fireworks stand]]"),
                LLMFullResponseEndFrame(),
            ],
        )
        self.assertEqual(tasks, ["find the closest fireworks stand"])
        self.assertEqual(_spoken_text(down).strip(), "On it, sir.")

    async def test_marker_split_across_streamed_tokens(self):
        tasks: list[str] = []
        down, _ = await run_test(
            LLMDelegateTap(tasks.append),
            frames_to_send=[
                LLMFullResponseStartFrame(),
                LLMTextFrame(text="Sending that over. [[dele"),
                LLMTextFrame(text="gate: get directions to the nearest sta"),
                LLMTextFrame(text="nd]] Anything else?"),
                LLMFullResponseEndFrame(),
            ],
        )
        self.assertEqual(tasks, ["get directions to the nearest stand"])
        self.assertEqual(_spoken_text(down).strip(), "Sending that over.  Anything else?")

    async def test_plain_text_passes_untouched(self):
        tasks: list[str] = []
        down, _ = await run_test(
            LLMDelegateTap(tasks.append),
            frames_to_send=[
                LLMFullResponseStartFrame(),
                LLMTextFrame(text="It is precisely half past two, sir."),
                LLMFullResponseEndFrame(),
            ],
        )
        self.assertEqual(tasks, [])
        self.assertEqual(_spoken_text(down), "It is precisely half past two, sir.")

    async def test_non_marker_brackets_pass_through(self):
        tasks: list[str] = []
        down, _ = await run_test(
            LLMDelegateTap(tasks.append),
            frames_to_send=[
                LLMFullResponseStartFrame(),
                LLMTextFrame(text="A [[footnote]] is not a marker."),
                LLMFullResponseEndFrame(),
            ],
        )
        self.assertEqual(tasks, [])
        self.assertEqual(_spoken_text(down), "A [[footnote]] is not a marker.")

    async def test_duplicate_markers_dispatch_once(self):
        tasks: list[str] = []
        await run_test(
            LLMDelegateTap(tasks.append),
            frames_to_send=[
                LLMFullResponseStartFrame(),
                LLMTextFrame(
                    text="[[delegate: check the weather]] [[delegate: check the weather]]"
                ),
                LLMFullResponseEndFrame(),
            ],
        )
        self.assertEqual(tasks, ["check the weather"])

    async def test_unterminated_marker_is_dropped_not_spoken(self):
        tasks: list[str] = []
        down, _ = await run_test(
            LLMDelegateTap(tasks.append),
            frames_to_send=[
                LLMFullResponseStartFrame(),
                LLMTextFrame(text="Right away. [[delegate: search the"),
                LLMFullResponseEndFrame(),
            ],
        )
        self.assertEqual(tasks, [])
        self.assertEqual(_spoken_text(down).strip(), "Right away.")

    async def test_marker_tolerates_spacing_and_case(self):
        tasks: list[str] = []
        down, _ = await run_test(
            LLMDelegateTap(tasks.append),
            frames_to_send=[
                LLMFullResponseStartFrame(),
                LLMTextFrame(text="Okay. [[ Delegate : look up tonight's weather ]]"),
                LLMFullResponseEndFrame(),
            ],
        )
        self.assertEqual(tasks, ["look up tonight's weather"])
        self.assertEqual(_spoken_text(down).strip(), "Okay.")


if __name__ == "__main__":
    unittest.main()

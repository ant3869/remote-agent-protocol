"""Narration writes a fresh line every time, and degrades without stalling speech."""

import asyncio
import unittest
from unittest.mock import patch

from remote_agent_protocol import narration


def _moment(kind=narration.KIND_WORKING, **kwargs):
    return narration.Moment(kind=kind, agent="code-puppy", task="draw a dog", **kwargs)


class CleanLineTests(unittest.TestCase):
    def test_strips_speaker_prefix_quotes_and_markdown(self):
        cleaned = narration._clean_line('  "**Jess:** she is *still* poking at it."  ')

        self.assertEqual(cleaned, "she is still poking at it.")

    def test_keeps_at_most_two_sentences(self):
        cleaned = narration._clean_line("One. Two. Three. Four.")

        self.assertEqual(cleaned, "One. Two.")

    def test_long_line_is_clipped_on_a_word_boundary(self):
        cleaned = narration._clean_line("word " * 200)

        self.assertLessEqual(len(cleaned), narration.SPOKEN_MAX_CHARS)
        self.assertTrue(cleaned.endswith("."))
        self.assertNotIn("wor.", cleaned)


class RepetitionTests(unittest.TestCase):
    def test_near_restatement_counts_as_repeat(self):
        recent = ["Code Puppy is still grinding through the renderer."]

        self.assertTrue(
            narration._too_similar("Code Puppy is grinding through the renderer still.", recent)
        )

    def test_different_line_is_not_a_repeat(self):
        recent = ["Code Puppy is still grinding through the renderer."]

        self.assertFalse(narration._too_similar("Hermes wants a decision about the token.", recent))

    def test_fallback_pool_does_not_repeat_itself_back_to_back(self):
        narrator = narration.Narrator("Jess")

        spoken = [narrator.fallback(_moment(narration.KIND_IDLE)) for _ in range(3)]

        self.assertEqual(len(set(spoken)), len(spoken))


class GenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_narrator_is_inert_until_the_session_enables_it(self):
        """Constructing a session must never reach the network; tests do it constantly."""
        narrator = narration.Narrator("Jess")

        with patch.object(narrator, "_generate", side_effect=AssertionError("called")):
            line = await narrator.line(_moment())

        self.assertTrue(line)

    async def test_enable_respects_the_config_kill_switch(self):
        narrator = narration.Narrator("Jess")

        with patch.object(narration.cfg, "NARRATION_ENABLED", False):
            narrator.enable()

        self.assertFalse(narrator._enabled)

    async def test_generated_line_is_used_and_remembered(self):
        narrator = narration.Narrator("Jess", enabled=True)

        async def generate(_unused):
            return "she's elbow-deep in the renderer."

        with patch.object(narrator, "_generate", generate):
            line = await narrator.line(_moment())

        self.assertEqual(line, "she's elbow-deep in the renderer.")
        self.assertIn(line, list(narrator._recent))

    async def test_a_repeated_generation_falls_back_instead_of_echoing(self):
        narrator = narration.Narrator("Jess", enabled=True)
        narrator.remember("Code Puppy is still grinding through the renderer.")

        async def generate(_unused):
            return "Code Puppy is grinding through the renderer still."

        with patch.object(narrator, "_generate", generate):
            line = await narrator.line(_moment())

        self.assertNotEqual(line, "Code Puppy is grinding through the renderer still.")
        self.assertTrue(line)

    async def test_prefetched_line_is_spoken_without_generating_again(self):
        narrator = narration.Narrator("Jess", enabled=True)
        calls = []

        async def generate(moment):
            calls.append(moment)
            return "it's rebuilding the ear layer."

        with patch.object(narrator, "_generate", generate):
            await narrator.prefetch("job-1", _moment())
            line = await narrator.line(_moment(), key="job-1")

        self.assertEqual(line, "it's rebuilding the ear layer.")
        self.assertEqual(len(calls), 1)  # spoken from the prefetch, not regenerated

    async def test_switching_persona_drops_the_previous_characters_phrasings(self):
        narrator = narration.Narrator("Jess", "You are flirty and fast.", enabled=True)
        narrator.remember("something Jess would say")

        async def generate(_unused):
            return "a line written while Jess was speaking."

        with patch.object(narrator, "_generate", generate):
            await narrator.prefetch("job-1", _moment())

        narrator.set_persona("Noir", "You are a hard-boiled detective.")

        self.assertEqual(list(narrator._recent), [])
        self.assertIsNone(narrator.take_prefetched("job-1"))

    async def test_a_dead_ollama_degrades_to_a_stock_line_rather_than_silence(self):
        narrator = narration.Narrator(
            "Jess", host="http://127.0.0.1:1", enabled=True, timeout_secs=0.25
        )

        line = await asyncio.wait_for(narrator.line(_moment()), timeout=5)

        self.assertTrue(line)


if __name__ == "__main__":
    unittest.main()

import unittest

from remote_agent_protocol import memory_manager


class MemoryManagerTests(unittest.TestCase):
    def test_normalizes_mem0_list_items(self):
        raw = [{"id": "abc", "memory": "Ant likes fast replies", "score": 0.9}]

        rows = memory_manager.normalize_memories(raw)

        self.assertEqual(
            rows,
            [
                {
                    "id": "abc",
                    "scope": "semantic",
                    "source": "semantic",
                    "text": "Ant likes fast replies",
                    "score": 0.9,
                    "metadata": {},
                }
            ],
        )

    def test_normalizes_mem0_dict_results(self):
        raw = {
            "results": [
                {"id": "m1", "text": "Has an RTX 5060 Ti", "metadata": {"source": "manual_gui"}}
            ]
        }

        rows = memory_manager.normalize_memories(raw)

        self.assertEqual(
            rows,
            [
                {
                    "id": "m1",
                    "scope": "semantic",
                    "source": "manual_gui",
                    "text": "Has an RTX 5060 Ti",
                    "score": None,
                    "metadata": {"source": "manual_gui"},
                }
            ],
        )

    def test_uses_fallback_id_when_missing(self):
        rows = memory_manager.normalize_memories([{"memory": "No id here"}])

        self.assertEqual(rows[0]["id"], "")
        self.assertEqual(rows[0]["text"], "No id here")

    def test_memory_display_line_includes_score_when_available(self):
        row = {"id": "abcdef123", "text": "Likes Jess", "score": 0.876}

        self.assertEqual(memory_manager.display_line(row), "abcdef12 | 0.88 | Likes Jess")

    def test_transcript_rows_are_human_readable(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]

        self.assertEqual(memory_manager.transcript_rows(messages), ["You: hello", "Jess: hi there"])

    def test_transcript_memory_rows_are_display_ready(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]

        self.assertEqual(
            memory_manager.transcript_memory_rows(messages),
            [
                {
                    "id": "turn-1",
                    "scope": "short",
                    "source": "transcript",
                    "role": "user",
                    "label": "You",
                    "text": "hello",
                    "score": None,
                },
                {
                    "id": "turn-2",
                    "scope": "short",
                    "source": "transcript",
                    "role": "assistant",
                    "label": "Jess",
                    "text": "hi there",
                    "score": None,
                },
            ],
        )

    def test_manual_memory_message_trims_and_prefixes_user_role(self):
        self.assertEqual(
            memory_manager.manual_memory_message("  Ant prefers short replies.  "),
            {"role": "user", "content": "Remember that Ant prefers short replies."},
        )

    def test_manual_memory_message_does_not_double_prefix(self):
        self.assertEqual(
            memory_manager.manual_memory_message("remember that Ant likes VR."),
            {"role": "user", "content": "Remember that Ant likes VR."},
        )

    def test_manual_memory_message_rejects_empty_text(self):
        with self.assertRaises(ValueError):
            memory_manager.manual_memory_message("   ")

    def test_fact_key_collapses_instruction_alias_and_first_person_variants(self):
        variants = {
            memory_manager.fact_key("Remember that Ant's GPU is an RTX 5060 Ti."),
            memory_manager.fact_key("User's GPU is an RTX 5060 Ti."),
            memory_manager.fact_key("The user's GPU is an RTX 5060 Ti."),
            memory_manager.fact_key("my GPU is an RTX 5060 Ti"),
            memory_manager.fact_key("SuperHands' GPU is an RTX 5060 Ti."),
            memory_manager.fact_key("SuperHands GPU is an RTX 5060 Ti."),
        }

        self.assertEqual(variants, {"user's gpu is an rtx 5060 ti"})

    def test_fact_key_preserves_alias_values_for_goes_by_facts(self):
        key = memory_manager.fact_key("The user goes by Ant and SuperHands.")

        self.assertEqual(key, "user goes by ant and superhands")


if __name__ == "__main__":
    unittest.main()

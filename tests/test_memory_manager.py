import unittest

from remote_agent_protocol import memory_manager


class MemoryManagerTests(unittest.TestCase):
    def test_normalizes_mem0_list_items(self):
        raw = [{"id": "abc", "memory": "Ant likes fast replies", "score": 0.9}]

        rows = memory_manager.normalize_memories(raw)

        self.assertEqual(rows, [{"id": "abc", "text": "Ant likes fast replies", "score": 0.9}])

    def test_normalizes_mem0_dict_results(self):
        raw = {"results": [{"id": "m1", "text": "Has an RTX 5060 Ti"}]}

        rows = memory_manager.normalize_memories(raw)

        self.assertEqual(rows, [{"id": "m1", "text": "Has an RTX 5060 Ti", "score": None}])

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


if __name__ == "__main__":
    unittest.main()
